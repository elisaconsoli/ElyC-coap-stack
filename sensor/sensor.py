"""
sensor.py — Simulatore di sensore di temperatura IoT con CoAP
=============================================================
Progetto didattico ITS 3° anno — Protocollo CoAP (RFC 7252)

Differenze rispetto a MQTT:
- Non c'è un broker: il sensore è CLIENT CoAP, il gateway è SERVER
- UDP (non TCP): nessuna connessione persistente
- Messaggi CON (Confirmable): il server deve rispondere con ACK
  → se non risponde, aiocoap ritrasmette (max ~45s con 4 tentativi)
- Buffer locale: copre i periodi in cui il gateway non è raggiungibile

Flusso dati:
  sensore --[POST CON]--> coap-gateway:5683/iot/temperatura
"""

# ── IMPORT: carica i moduli necessari ─────────────────────────
import asyncio    # Event loop asincrono: gestisce coroutine e I/O non bloccante senza thread multipli
import aiocoap    # Libreria CoAP asincrona lato CLIENT: invia messaggi CON via UDP con gestione ACK
import json       # json.dumps: dizionario Python → stringa JSON (serializzazione per il payload CoAP)
import random     # random.gauss (distribuzione normale), random.random (0.0-1.0), random.uniform (range)
import time       # time.time() (Unix timestamp float), time.monotonic() (clock che non va mai indietro)
import os         # os.getenv: legge le variabili d'ambiente iniettate da docker-compose.yml
import socket     # socket.getaddrinfo: risoluzione DNS (converte hostname → indirizzo IP)
from collections import deque   # deque = buffer FIFO con capacità massima; scarta il più vecchio se pieno

# ──────────────────────────────────────────────────────────────
# Configurazione da variabili d'ambiente
# ──────────────────────────────────────────────────────────────
# Le variabili d'ambiente sono iniettate da docker-compose.yml nella sezione 'environment'
# os.getenv("NOME", "default"): restituisce il valore della variabile o il default se non esiste
COAP_SERVER   = os.getenv("COAP_SERVER",   "coap-gateway")   # Hostname del gateway (DNS interno Docker)
COAP_PORT     = int(os.getenv("COAP_PORT", "5683"))          # int() necessario: le env var sono sempre stringhe
SEND_INTERVAL = int(os.getenv("SEND_INTERVAL", "5"))         # Secondi tra un invio e l'altro
SENSOR_ID     = os.getenv("SENSOR_ID", "sensor-01")         # Identificativo univoco del sensore

# f-string (Python 3.6+): le espressioni dentro {} vengono valutate e inserite nella stringa
# Risultato esempio: "coap://coap-gateway:5683/iot/temperatura"
COAP_URI = f"coap://{COAP_SERVER}:{COAP_PORT}/iot/temperatura"

# Timeout in secondi per aspettare l'ACK dal server dopo un POST CON
# 15.0 (float) perché asyncio.wait_for() accetta secondi come float
COAP_TIMEOUT = 15.0

# Buffer locale: deque con capacità massima LOCAL_BUFFER_MAX
# maxlen=N: quando il buffer è pieno e si aggiunge un elemento → il più vecchio viene scartato (FIFO automatico)
# Questo previene memory leak durante periodi lunghi di disconnessione dal gateway
LOCAL_BUFFER_MAX = 500
local_buffer = deque(maxlen=LOCAL_BUFFER_MAX)   # Istanza globale del buffer, condivisa tra le funzioni

# Dizionario dei contatori operativi: struttura dati chiave→valore
# I dizionari sono mutabili e modificabili da qualsiasi funzione che li riceve per riferimento
stats = {
    "sent":     0,   # Messaggi consegnati con successo (ACK ricevuto dal gateway)
    "buffered": 0,   # Messaggi salvati nel buffer locale (gateway non raggiungibile)
    "dropped":  0,   # Messaggi persi perché il buffer era pieno (overflow)
    "flushed":  0,   # Messaggi del buffer inviati dopo il ritorno online del gateway
}

seq = 0   # Numero di sequenza progressivo: variabile globale incrementata a ogni ciclo del loop


# ──────────────────────────────────────────────────────────────
# Generazione valore temperatura simulato
# ──────────────────────────────────────────────────────────────
def genera_temperatura() -> float:
    # 'def' = definisce una funzione (non asincrona, esecuzione sincrona e immediata)
    # '-> float' = type hint di ritorno: documenta che la funzione restituisce un float (non obbligatorio a runtime)
    """
    Simula un sensore di temperatura con piccole variazioni casuali.
    Range normale: 18–28 °C. Occasionalmente produce valori anomali
    per testare il sistema di alerting.
    """
    base = 22.0                         # Temperatura centrale di riferimento in gradi Celsius
    rumore = random.gauss(0, 2.0)       # Campiona dalla distribuzione normale gaussiana
    # random.gauss(mu, sigma): mu=0 (media centrata su 0), sigma=2.0 (deviazione standard = 2°C)
    # La maggior parte dei valori sarà tra -4°C e +4°C rispetto alla base (entro 2 sigma)

    # random.random(): genera un float uniforme in [0.0, 1.0)
    # < 0.025 → probabilità 2.5%: circa 1 misurazione su 40 è anomala (per testare gli alert)
    if random.random() < 0.025:
        rumore += random.uniform(10, 15)    # random.uniform(a, b): float casuale tra a e b → picco ALTO
    elif random.random() < 0.025:           # 'elif' = else-if: eseguito solo se il primo 'if' è False
        rumore -= random.uniform(8, 12)     # Sottrazione: picco BASSO (temperatura molto fredda)

    return round(base + rumore, 2)   # round(numero, ndigits): arrotonda a 2 cifre decimali
    # Esempio: round(22.0 + 1.234567, 2) → 23.23


# ──────────────────────────────────────────────────────────────
# Invio singolo messaggio CON (Confirmable)
# ──────────────────────────────────────────────────────────────
async def try_send(protocol, uri: str, payload_json: str) -> bool:
    # 'async def' → coroutine: questa funzione può sospendersi con 'await'
    # 'protocol' → oggetto aiocoap.Context (client UDP, creato in main)
    # 'uri: str' → type hint: uri deve essere una stringa (es. "coap://coap-gateway:5683/iot/temperatura")
    # 'payload_json: str' → il payload JSON già serializzato (stringa)
    # '-> bool' → restituisce True se l'invio è riuscito, False altrimenti
    """
    Invia un POST CON al gateway CoAP.

    CON = Confirmable: il server DEVE rispondere con ACK.
    Se non risponde, aiocoap ritrasmette automaticamente.
    Noi aspettiamo al massimo COAP_TIMEOUT secondi.

    Restituisce True se il server ha risposto con 2.xx (successo).
    """
    # aiocoap.Message(): costruisce un messaggio CoAP
    request = aiocoap.Message(
        code=aiocoap.POST,                          # Metodo CoAP POST (equivalente HTTP POST): invia dati al server
        uri=uri,                                    # URI destinazione completo (schema + host + porta + path)
        payload=payload_json.encode("utf-8"),       # .encode("utf-8"): converte str → bytes (CoAP trasmette bytes)
        mtype=aiocoap.CON,                          # CON = Confirmable: richiede ACK esplicito dal server
    )
    try:
        # asyncio.wait_for(coro, timeout): esegue la coroutine con un limite di tempo
        # Se il timeout scade prima che la coroutine finisca → lancia asyncio.TimeoutError
        response = await asyncio.wait_for(
            protocol.request(request).response,     # .response = coroutine che aspetta la risposta ACK
            timeout=COAP_TIMEOUT                    # Abbandona dopo COAP_TIMEOUT secondi
        )
        return response.code.is_successful()        # is_successful(): True se il codice è 2.xx (es. 2.04 CHANGED)
    except asyncio.TimeoutError:   # Il gateway non ha risposto entro COAP_TIMEOUT secondi
        return False               # Segnala fallimento al chiamante
    except Exception:              # Qualsiasi altro errore (rete non disponibile, pacchetto perso, ecc.)
        return False


# ──────────────────────────────────────────────────────────────
# Attesa disponibilità DNS del gateway
# ──────────────────────────────────────────────────────────────
async def wait_for_server():
    """
    Aspetta che il nome host del gateway sia risolvibile via DNS.

    Nota: CoAP usa UDP, non TCP → non possiamo fare un "connect"
    per verificare la raggiungibilità. Verifichiamo solo il DNS,
    poi confidiamo nel meccanismo CON/ACK per la consegna effettiva.
    """
    print(f"[WAIT] Risoluzione DNS per {COAP_SERVER}:{COAP_PORT}...")
    while True:   # Loop infinito: esce con 'return' solo quando il DNS risponde correttamente
        try:
            # socket.getaddrinfo: risolve hostname → lista di (famiglia, tipo, proto, canonname, sockaddr)
            # Lancia socket.gaierror se il hostname non esiste o il DNS non è raggiungibile
            socket.getaddrinfo(COAP_SERVER, COAP_PORT)
            print(f"[WAIT] DNS ok → {COAP_SERVER} raggiungibile")
            return   # DNS risolto: esce dalla funzione (il while si ferma)
        except socket.gaierror:   # 'gai' = getaddrinfo; errore tipico: "Name or service not known"
            print(f"[WAIT] DNS non ancora pronto, riprovo tra 3s...")
            await asyncio.sleep(3)   # Attende 3 secondi cedendo il controllo all'event loop


# ──────────────────────────────────────────────────────────────
# Loop principale del sensore
# ──────────────────────────────────────────────────────────────
async def main():
    global seq   # 'global': senza questa dichiarazione, 'seq += 1' creerebbe una variabile locale
    # Python scoping (LEGB rule): Local → Enclosing → Global → Built-in
    # 'global' forza la scrittura nella variabile globale invece di crearne una locale

    # Blocco di log iniziale: utile per debug e monitoraggio con 'docker logs sensor-01'
    print("=" * 55)                              # Stringa "=" moltiplicata per 55 → separatore visivo
    print(f"[BOOT] Sensore CoAP avviato")
    print(f"[BOOT]   SENSOR_ID     = {SENSOR_ID}")
    print(f"[BOOT]   COAP_SERVER   = {COAP_SERVER}")
    print(f"[BOOT]   COAP_PORT     = {COAP_PORT}")
    print(f"[BOOT]   COAP_URI      = {COAP_URI}")
    print(f"[BOOT]   SEND_INTERVAL = {SEND_INTERVAL}s")
    print(f"[BOOT]   BUFFER_MAX    = {LOCAL_BUFFER_MAX} messaggi")
    print(f"[BOOT]   COAP_TIMEOUT  = {COAP_TIMEOUT}s")
    print(f"[BOOT]   Tipo messaggio: CON (Confirmable)")
    print("=" * 55)

    await wait_for_server()   # Blocca qui finché il DNS del gateway non risponde correttamente

    # create_client_context(): inizializza il contesto CoAP lato client
    # Apre un socket UDP locale e prepara i meccanismi di ritrasmissione automatica per i messaggi CON
    protocol = await aiocoap.Context.create_client_context()

    server_era_offline = False   # Flag booleano: True quando l'ultimo invio è fallito (gateway irraggiungibile)
    # Serve per rilevare il momento in cui il gateway torna online → avvia il flush del buffer

    print(f"[COAP] Contesto client CoAP creato, inizio trasmissione")

    while True:   # Loop principale: si ripete all'infinito, una volta ogni SEND_INTERVAL secondi
        loop_start = time.monotonic()   # Registra il tempo di inizio del ciclo
        # time.monotonic(): clock monotono (non va mai indietro, immune agli aggiustamenti NTP)
        # Usato per misurare il tempo trascorso e calcolare quanto dormire alla fine del ciclo

        seq += 1                              # Incrementa il numero di sequenza (operatore +=)
        temperatura = genera_temperatura()    # Chiama la funzione e ottiene il valore simulato
        ts = time.time()                      # Unix timestamp corrente (float, secondi dall'epoch 1970)

        # Costruisce il payload come dizionario Python (poi serializzato in JSON)
        payload = {
            "sensor_id":   SENSOR_ID,         # str: identificativo del sensore
            "temperatura": temperatura,        # float: valore simulato della temperatura
            "ts":          ts,                 # float: timestamp della misurazione (lato sensore)
            "seq":         seq,               # int: numero sequenza per rilevare messaggi persi sul gateway
            "buffered":    len(local_buffer), # int: quanti messaggi sono in attesa nel buffer locale
        }
        payload_json = json.dumps(payload)    # json.dumps: dizionario → stringa JSON
        # Esempio: '{"sensor_id": "sensor-01", "temperatura": 23.45, "ts": 1716300000.123, ...}'

        # ── Tentativo di invio al gateway ──────────────────
        ok = await try_send(protocol, COAP_URI, payload_json)
        # 'await' esegue la coroutine try_send e attende il risultato
        # 'ok' sarà True se il gateway ha risposto con 2.04 CHANGED, False altrimenti

        if ok:   # L'invio è riuscito: il gateway ha ricevuto il messaggio e risposto con ACK
            stats["sent"] += 1   # Incrementa il contatore dei messaggi consegnati con successo

            # Controlla se il gateway era offline prima di questo invio riuscito
            if server_era_offline and len(local_buffer) > 0:
                # 'and' = operatore logico AND: True solo se ENTRAMBE le condizioni sono True
                buf_size = len(local_buffer)   # Salva la dimensione iniziale per il messaggio di log
                print(f"[BUFFER] Gateway tornato online! Flush di {buf_size} messaggi in coda...")
                flushed = 0   # Contatore locale: quanti messaggi del buffer sono stati inviati in questo flush

                while local_buffer:   # 'while deque_non_vuota': continua finché c'è qualcosa nel buffer
                    old_payload = local_buffer.popleft()   # Preleva il messaggio più vecchio (FIFO, O(1))
                    old_json = json.dumps(old_payload)      # Ri-serializza il dizionario in JSON
                    flush_ok = await try_send(protocol, COAP_URI, old_json)   # Prova a inviarlo

                    if flush_ok:
                        flushed += 1              # Contatore locale del flush corrente
                        stats["flushed"] += 1     # Aggiorna il contatore globale
                    else:
                        local_buffer.appendleft(old_payload)   # Reinserisce in TESTA (non perdiamo il messaggio)
                        # appendleft(): inserisce all'inizio della deque (O(1))
                        break   # Interrompe il flush: il gateway è di nuovo offline

                    await asyncio.sleep(0.1)   # Piccola pausa tra invii del flush (rate limiting: ~10 msg/s)

                print(f"[BUFFER] Flush completato: {flushed}/{buf_size} messaggi recuperati")

            server_era_offline = False   # Resetta il flag: siamo di nuovo online

            # Formattazione numerica nelle f-string:
            # ':05d' = intero con minimo 5 cifre, padding con zeri a sinistra (es. 00042)
            # ':6.2f' = float con 6 caratteri totali e 2 decimali, padding con spazi (es. ' 23.45')
            print(
                f"[TX] seq={seq:05d} | {SENSOR_ID} | {temperatura:6.2f}°C | "
                f"buf={len(local_buffer)} | sent={stats['sent']} | flushed={stats['flushed']}"
            )

        else:   # L'invio è fallito: timeout o errore di rete
            server_era_offline = True   # Segnala che il gateway non risponde

            # Controlla se il buffer è PIENO prima di aggiungere il nuovo elemento
            buf_was_full = len(local_buffer) == LOCAL_BUFFER_MAX   # '==' confronto: restituisce bool

            local_buffer.append(payload)   # Aggiunge in CODA: se piena, deque scarta automaticamente il più vecchio

            if buf_was_full:   # Il buffer era pieno → il messaggio più vecchio è stato scartato (overflow)
                stats["dropped"] += 1
                print(
                    f"[DROP] seq={seq:05d} | Buffer pieno ({LOCAL_BUFFER_MAX}), "
                    f"messaggio più vecchio scartato | dropped={stats['dropped']}"
                )
            else:   # Il buffer aveva spazio: il messaggio è stato salvato (nessuna perdita)
                stats["buffered"] += 1
                print(
                    f"[OFFLINE] seq={seq:05d} | Gateway non raggiungibile → "
                    f"buffer={len(local_buffer)}/{LOCAL_BUFFER_MAX} | "
                    f"buffered={stats['buffered']}"
                )

        # ── Calcolo del tempo di attesa per il prossimo ciclo ──────────
        elapsed = time.monotonic() - loop_start      # Secondi trascorsi dall'inizio del ciclo (float)
        sleep_time = max(0.0, SEND_INTERVAL - elapsed)
        # max(a, b): restituisce il massimo tra a e b
        # Se elapsed > SEND_INTERVAL (es. il timeout ha impiegato più di 5s), sleep_time = 0.0
        # Questo garantisce che il sensore non aspetti più del necessario
        await asyncio.sleep(sleep_time)   # Dorme il tempo rimanente senza bloccare l'event loop


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # '__name__' è una variabile speciale Python:
    # - vale "__main__" se lo script è lanciato direttamente: python sensor.py
    # - vale il nome del modulo se importato: import sensor (utile per i test)
    try:
        asyncio.run(main())   # asyncio.run(): crea l'event loop, esegue main(), poi chiude il loop
        # Disponibile da Python 3.7; è il modo corretto per avviare una coroutine main
    except KeyboardInterrupt:        # Ctrl+C nel terminale → Python lancia KeyboardInterrupt
        print(f"\n[COAP] Sensore {SENSOR_ID} fermato")
        print(f"[COAP] Statistiche finali: {stats}")   # Stampa il dizionario stats completo alla fine
