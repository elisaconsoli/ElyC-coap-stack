"""
gateway.py — Gateway CoAP → InfluxDB
=====================================
Progetto didattico ITS 3° anno — Protocollo CoAP (RFC 7252)

Ruolo: SERVER CoAP che riceve i dati dai sensori e li scrive in InfluxDB.

Principi chiave:
1. Risponde SEMPRE con ACK 2.04 CHANGED al sensore (anche se InfluxDB è down)
   → il sensore sa che il gateway ha ricevuto il dato
2. Buffer interno (deque max 2000) per i dati quando InfluxDB non è disponibile
3. Task asyncio separato (influx_writer_loop) che svuota TUTTO il buffer ogni 1s (batch write)
4. Scritture InfluxDB in ThreadPoolExecutor (client sincrono, non blocca il loop)

Flusso:
  sensore --[POST CON UDP 5683]--> gateway --[HTTP]--> InfluxDB:8086
                                     ↑
                                   [ACK] sempre, subito
"""

# ── IMPORT: carica i moduli (librerie) necessari ──────────────
import asyncio              # Modulo stdlib per programmazione asincrona: event loop, coroutine, Task
import aiocoap              # Libreria CoAP asincrona: implementa RFC 7252 su UDP con asyncio
import aiocoap.resource as resource  # Sottomodulo di aiocoap: Resource (base per risorse), Site (router)
import json                 # Serializzazione/deserializzazione JSON: json.loads (str→dict), json.dumps (dict→str)
import os                   # Interfaccia col sistema operativo: os.getenv legge variabili d'ambiente
import time                 # Funzioni temporali: time.time() restituisce il timestamp Unix corrente (float)
from collections import deque  # deque = double-ended queue: lista con appendleft/popleft O(1) e maxlen automatico
from concurrent.futures import ThreadPoolExecutor  # Pool di thread per eseguire codice bloccante fuori dall'event loop
import urllib.request       # Client HTTP minimalista della stdlib (evita dipendenza da 'requests')
import urllib.error         # Eccezioni HTTP della stdlib (URLError, HTTPError)

from influxdb_client import InfluxDBClient, Point  # Client ufficiale InfluxDB v2: connessione + costruttore punti
from influxdb_client.client.write_api import SYNCHRONOUS  # Costante: scrittura sincrona (blocca il thread fino a conferma)

# ──────────────────────────────────────────────────────────────
# Configurazione da variabili d'ambiente
# ──────────────────────────────────────────────────────────────
# os.getenv("NOME", "default"): legge la variabile d'ambiente; se non esiste restituisce il default
# Le variabili vengono iniettate da docker-compose.yml nella sezione 'environment' del servizio
INFLUX_URL    = os.getenv("INFLUX_URL",    "http://influxdb:8086")  # URL base di InfluxDB (hostname Docker)
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN",  "my-super-token")        # Token API per autenticazione InfluxDB v2
INFLUX_ORG    = os.getenv("INFLUX_ORG",    "its")                   # Organizzazione InfluxDB (come un namespace)
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "iot")                   # Bucket = "database" dove scrivere i dati
COAP_PORT     = int(os.getenv("COAP_PORT", "5683"))                 # int(): le env var sono sempre stringhe, int() converte

# deque(maxlen=N): struttura dati FIFO con capacità massima
# Quando è piena e si aggiunge un elemento → il più vecchio viene scartato automaticamente
# Questo previene memory leak: senza maxlen il buffer crescerebbe senza limiti
INTERNAL_BUFFER_MAX = 2000                               # Numero massimo di misurazioni in attesa
internal_buffer = deque(maxlen=INTERNAL_BUFFER_MAX)      # Istanza del buffer, condivisa tra le coroutine

# ThreadPoolExecutor(max_workers=1): pool con UN solo thread worker
# max_workers=1 perché influxdb_client è sincrono e non thread-safe con writer multipli simultanei
# I thread del pool eseguono la funzione bloccante senza fermare l'event loop asyncio
executor = ThreadPoolExecutor(max_workers=1)

# Dizionario Python per i contatori operativi: chiave (str) → valore (int)
# I dizionari sono mutabili e passati per riferimento → qualsiasi funzione può aggiornare stats
stats = {
    "received": 0,   # messaggi CoAP ricevuti dai sensori
    "written":  0,   # punti scritti su InfluxDB con successo
    "buffered": 0,   # punti in attesa nel buffer interno
    "errors":   0,   # errori di scrittura InfluxDB
}

# Variabili globali inizializzate a None (Python usa None come valore "assente", equivalente a null)
# Saranno assegnate in main() dopo l'health check di InfluxDB
influx_client = None   # Istanza di InfluxDBClient (connessione al database)
write_api = None       # API di scrittura ricavata dal client (usata in sync_write_batch)


# ──────────────────────────────────────────────────────────────
# Scrittura sincrona su InfluxDB — BATCH (eseguita in executor)
# ──────────────────────────────────────────────────────────────
def sync_write_batch(batch: list):
    """
    Scrive un BATCH di punti su InfluxDB in una singola chiamata HTTP.
    Molto più efficiente di scrivere un punto alla volta.

    Throughput: con 10 sensori × 1 msg/5s = 2 msg/s in ingresso,
    il batch svuota l'intera coda in ogni ciclo → latenza < 1s.

    Lancia eccezione in caso di errore (il chiamante reinserisce nel buffer).
    """
    # 'batch: list' → type hint: dice che batch deve essere una lista (non verificato a runtime)
    # La funzione è SINCRONA (non async): verrà eseguita in un thread separato tramite executor

    points = []     # Lista vuota che raccoglierà gli oggetti Point prima della scrittura batch

    for data in batch:   # Itera ogni dizionario del batch (uno per misurazione)

        # Point("temperatura"): costruisce un punto InfluxDB con measurement name "temperatura"
        # Il method chaining (a.b().c().d()) funziona perché ogni metodo restituisce 'self'
        # .tag(): aggiunge un tag → indicizzato, usato per filtrare/raggruppare (non aggregabile)
        # .field(): aggiunge un campo → valore numerico, aggregabile (media, min, max, ecc.)
        p = (
            Point("temperatura")                                          # Measurement = "tabella" InfluxDB
            .tag("sensor_id", data.get("sensor_id", "unknown"))           # Tag: identifica il sensore
            .field("value",   float(data["temperatura"]))                 # Campo: temperatura in °C
            .field("seq",     int(data.get("seq", 0)))                    # Campo: numero sequenza (rileva gap)
        )
        # data.get("chiave", default): accede al dizionario restituendo default se la chiave manca
        # float() e int() convertono esplicitamente: robustezza se il sensore manda tipi diversi

        if "ts" in data:                     # Operatore 'in': True se la chiave esiste nel dizionario
            p = p.time(int(data["ts"] * 1e9))
            # InfluxDB richiede timestamp in nanosecondi (int)
            # data["ts"] = Unix epoch in secondi (float) inviato dal sensore → ×1e9 → nanosecondi → int()

        points.append(p)   # .append(): aggiunge in coda alla lista (O(1))

    # Scrittura batch: UNA sola chiamata HTTP con tutti i punti → molto più efficiente
    # write_api è la variabile globale inizializzata in main()
    write_api.write(bucket=INFLUX_BUCKET, record=points)   # record=lista → InfluxDB scrive tutto in una volta


# ──────────────────────────────────────────────────────────────
# Task: svuota il buffer interno verso InfluxDB (batch write)
# ──────────────────────────────────────────────────────────────
async def influx_writer_loop():
    """
    Task asyncio che gira in background ogni secondo.

    PRIMA (problema): scriveva 1 punto ogni 2s = 0.5 msg/s.
    Con 10 sensori a 5s = 2 msg/s in entrata, il buffer si riempiva.

    ORA (fix): svuota TUTTO il buffer in un unico batch HTTP per ciclo.
    Una sola chiamata all'API InfluxDB → throughput limitato solo dalla rete.
    In caso di errore reinserisce il batch nel buffer e riprova.
    """
    # 'async def' → coroutine: può sospendersi con 'await' cedendo il controllo all'event loop
    loop = asyncio.get_event_loop()   # Ottiene il riferimento all'event loop corrente (serve per run_in_executor)
    print("[INFLUX] Writer loop avviato — batch mode (ogni 1s svuota tutto il buffer)")

    while True:   # Loop infinito: questa coroutine gira per tutta la vita del processo
        await asyncio.sleep(1)   # Cede il controllo all'event loop per 1 secondo; altri task girano nel frattempo

        if not internal_buffer:   # 'not deque' → True se la deque è vuota (deque implementa __bool__)
            continue               # 'continue' salta al prossimo ciclo del while

        # Drena TUTTO il buffer in un batch (max 500 per chiamata, per sicurezza)
        batch = []                                      # Lista temporanea per il batch corrente
        while internal_buffer and len(batch) < 500:    # Continua mentre c'è roba E il batch non è troppo grande
            batch.append(internal_buffer.popleft())    # popleft(): rimuove e restituisce il primo elemento FIFO (O(1))

        if not batch:    # Controllo difensivo (non dovrebbe succedere, ma evita crash imprevisti)
            continue

        try:
            # run_in_executor(executor, funzione, *args): esegue la funzione in un thread del pool
            # Necessario perché sync_write_batch fa I/O HTTP sincrono (bloccante)
            # 'await' aspetta che il thread finisca senza bloccare l'event loop asyncio
            await loop.run_in_executor(executor, sync_write_batch, batch)

            stats["written"] += len(batch)   # '+=' operatore di assegnazione composta: stats["written"] = stats["written"] + len(batch)
            stats["errors"] = 0              # Resetta il contatore errori consecutivi dopo un successo

            print(
                f"[INFLUX] ✓ Scritti {len(batch)} punti in batch | "   # f-string: {} valutato a runtime
                f"totale={stats['written']} | buf={len(internal_buffer)}"
            )

        except Exception as e:         # 'Exception' = classe base di tutte le eccezioni Python (cattura tutto)
            stats["errors"] += 1
            for item in reversed(batch):            # reversed(): itera la lista al contrario (ordine cronologico preservato)
                internal_buffer.appendleft(item)    # appendleft(): reinserisce in testa → i dati più vecchi restano davanti
            print(
                f"[ERR] Batch write fallito (tentativo #{stats['errors']}): {e} | "
                f"buf={len(internal_buffer)}"
            )


# ──────────────────────────────────────────────────────────────
# Risorsa CoAP: /iot/temperatura
# ──────────────────────────────────────────────────────────────
class TemperaturaResource(resource.Resource):
    """
    Risorsa CoAP che gestisce i POST dai sensori.

    Regola fondamentale: risponde SEMPRE con 2.04 CHANGED, anche se
    InfluxDB è down. Il dato viene messo nel buffer interno e scritto
    appena InfluxDB torna disponibile.

    Questo garantisce che il sensore non tenga il dato nel suo buffer
    locale più del necessario — è il gateway che si occupa della persistenza.
    """
    # 'class Nome(Base)': definisce una classe che eredita da resource.Resource (ereditarietà singola)
    # resource.Resource è la classe base di aiocoap per le risorse CoAP
    # Ereditando da essa, otteniamo gratuitamente la gestione del protocollo CoAP

    async def render_post(self, request):
        # 'async def' = metodo asincrono (coroutine): può usare 'await'
        # 'self' = riferimento all'istanza della classe (come 'this' in Java/C++)
        # 'request' = oggetto aiocoap.Message con il messaggio CoAP ricevuto
        # 'render_post' è il nome SPECIFICO di aiocoap per gestire il metodo CoAP POST
        # aiocoap chiama automaticamente render_post quando arriva un POST su questa risorsa
        try:
            raw = request.payload.decode("utf-8")    # .payload = bytes del corpo del messaggio CoAP
            # .decode("utf-8") converte bytes → str (il sensore ha fatto .encode("utf-8"))
            data = json.loads(raw)                    # json.loads: deserializza JSON str → dizionario Python
            stats["received"] += 1                    # Incrementa il contatore di messaggi ricevuti

            data["gw_ts"] = time.time()              # Aggiunge un campo al dizionario: timestamp di ricezione gateway
            # time.time() → float: secondi da 1 Jan 1970 UTC (Unix epoch); es. 1716300000.123

            if len(internal_buffer) == INTERNAL_BUFFER_MAX:
                # Il buffer è pieno: la deque scarta automaticamente il più vecchio con maxlen
                # Qui logghiamo solo l'evento (la deque gestisce lo scarto da sola)
                print(f"[BUFFER] Buffer interno pieno ({INTERNAL_BUFFER_MAX}), dato più vecchio scartato")

            internal_buffer.append(data)              # Aggiunge il dizionario in CODA al buffer (FIFO)
            stats["buffered"] = len(internal_buffer)  # len() su deque è O(1)

            if stats["received"] % 20 == 1:           # '%' = modulo (resto della divisione); True al 1°, 21°, 41°...
                print(
                    f"[RX] ricevuti={stats['received']} | "
                    f"scritti={stats['written']} | "
                    f"buf={len(internal_buffer)} | "
                    f"ultimo: {data.get('sensor_id')} → {data.get('temperatura')}°C"
                )

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Tupla di eccezioni nel 'except': cattura entrambe con un solo blocco
            # json.JSONDecodeError: il payload non è JSON valido (es. dati corrotti)
            # UnicodeDecodeError: il payload non è codificato in UTF-8 valido
            print(f"[ERR] Payload non valido: {e} | raw={request.payload[:100]}")
            # [:100] = slice: prende i primi 100 byte per il log (evita log enormi)
        except Exception as e:       # Fallback: cattura qualsiasi altra eccezione imprevista
            print(f"[ERR] Errore inatteso: {e}")

        # Questa riga è FUORI dal try/except: viene eseguita SEMPRE (anche dopo un'eccezione)
        # aiocoap.CHANGED = codice di risposta CoAP 2.04 (equivalente HTTP 200 per POST/PUT)
        return aiocoap.Message(code=aiocoap.CHANGED)   # Costruisce e ritorna il messaggio di risposta


# ──────────────────────────────────────────────────────────────
# Attesa health check InfluxDB
# ──────────────────────────────────────────────────────────────
async def wait_for_influxdb():
    """
    Aspetta che InfluxDB sia pronto (endpoint /health risponde 200).
    Riprova ogni 5 secondi con log.
    """
    health_url = f"{INFLUX_URL}/health"   # f-string: costruisce l'URL → es. "http://influxdb:8086/health"
    print(f"[INFLUX] Attendo InfluxDB su {health_url}...")
    attempt = 0                           # Contatore dei tentativi (usato solo nel log)

    while True:       # Loop infinito: esce solo con 'return' quando InfluxDB risponde correttamente
        attempt += 1
        try:
            # urllib.request.urlopen: apre una connessione HTTP e legge la risposta (sincrono)
            # 'with ... as resp': context manager → chiude la connessione in modo sicuro al termine del blocco
            # timeout=5: lancia urllib.error.URLError se non risponde in 5 secondi
            with urllib.request.urlopen(health_url, timeout=5) as resp:
                if resp.status == 200:                           # HTTP 200 = OK
                    body = json.loads(resp.read().decode())      # Legge il body (bytes) → decodifica → parsing JSON
                    print(f"[INFLUX] InfluxDB pronto! Status: {body.get('status')}")
                    return   # 'return' senza valore: esce dalla funzione (interrompe il while True)
        except Exception as e:   # urlopen lancia eccezioni per: timeout, rifiuto connessione, 404, ecc.
            print(f"[INFLUX] Tentativo {attempt}: InfluxDB non pronto ({e}), riprovo tra 5s...")
        await asyncio.sleep(5)   # Cede il controllo all'event loop per 5s prima di riprovare


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────
async def main():
    global influx_client, write_api
    # 'global': dichiara che influx_client e write_api si riferiscono alle variabili GLOBALI
    # Senza 'global', l'assegnazione creerebbe variabili LOCALI alla funzione (Python scoping rule)

    print("=" * 55)                              # '*' su stringhe = ripetizione: "=" ripetuto 55 volte
    print(f"[BOOT] CoAP Gateway avviato")
    print(f"[BOOT]   INFLUX_URL    = {INFLUX_URL}")
    print(f"[BOOT]   INFLUX_ORG    = {INFLUX_ORG}")
    print(f"[BOOT]   INFLUX_BUCKET = {INFLUX_BUCKET}")
    print(f"[BOOT]   COAP_PORT     = {COAP_PORT}/udp")
    print(f"[BOOT]   BUFFER_MAX    = {INTERNAL_BUFFER_MAX} punti")
    print(f"[BOOT]   Risorsa CoAP  = /iot/temperatura")
    print("=" * 55)

    # Passo 1: aspetta che InfluxDB sia raggiungibile e sano
    await wait_for_influxdb()   # 'await' = esegui la coroutine e aspetta che finisca

    # Passo 2: inizializza il client InfluxDB con le credenziali
    influx_client = InfluxDBClient(   # Costruttore: crea la connessione HTTP verso InfluxDB
        url=INFLUX_URL,               # Argomenti keyword (nome=valore): l'ordine non importa
        token=INFLUX_TOKEN,
        org=INFLUX_ORG,
    )
    # write_api(): ottiene l'oggetto per scrivere punti
    # SYNCHRONOUS: ogni scrittura blocca il thread finché InfluxDB conferma (o lancia eccezione)
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)
    print(f"[INFLUX] Client InfluxDB inizializzato")

    # Passo 3: crea il server CoAP e registra le risorse
    root = resource.Site()   # Site(): contenitore radice delle risorse CoAP (come un router in Flask/Express)

    # add_resource(path, risorsa): mappa il path CoAP alla classe che gestisce le richieste
    # ["iot", "temperatura"] = path CoAP /iot/temperatura (lista di segmenti, non stringa)
    root.add_resource(["iot", "temperatura"], TemperaturaResource())

    # Risorsa standard RFC 6690: risponde a GET /.well-known/core con la lista delle risorse disponibili
    # Utile per il debug con 'coap-client -m get coap://localhost/.well-known/core'
    root.add_resource(
        [".well-known", "core"],                          # Path standard per discovery CoAP
        resource.WKCResource(root.get_resources_as_linkheader)   # Genera automaticamente la lista in formato Link
    )

    # Avvia il server CoAP: bind su tutte le interfacce IPv4 sulla porta UDP specificata
    # "0.0.0.0" = ascolta su TUTTE le interfacce di rete del container (necessario per ricevere dall'esterno)
    await aiocoap.Context.create_server_context(root, bind=("0.0.0.0", COAP_PORT))
    print(f"[BOOT] Server CoAP in ascolto su 0.0.0.0:{COAP_PORT}/udp")
    print(f"[BOOT] Pronto a ricevere POST su coap://0.0.0.0:{COAP_PORT}/iot/temperatura")

    # Passo 4: avvia il writer loop come Task in background (non aspetta che finisca)
    asyncio.ensure_future(influx_writer_loop())
    # ensure_future(): schedula la coroutine come Task nell'event loop
    # Gira IN PARALLELO con il server CoAP (concorrenza cooperativa, non threading)

    # Passo 5: mantieni il processo vivo con un Future che non viene mai risolto
    print("[BOOT] Gateway operativo. Ctrl+C per fermare.")
    await asyncio.get_event_loop().create_future()
    # create_future() crea un oggetto Future senza mai chiamare .set_result() → 'await' blocca per sempre
    # Il server CoAP e il writer loop girano nell'event loop finché il processo non viene terminato


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # '__name__' è una variabile speciale Python:
    # - vale "__main__" quando lo script è eseguito direttamente (python gateway.py)
    # - vale il nome del modulo quando è importato (import gateway)
    # Questo pattern permette di importare il file nei test senza eseguire il codice
    try:
        asyncio.run(main())   # asyncio.run(): crea l'event loop, esegue main() fino alla fine, poi chiude il loop
    except KeyboardInterrupt:        # Ctrl+C nel terminale → genera KeyboardInterrupt
        print(f"\n[BOOT] Gateway fermato")
        print(f"[BOOT] Statistiche finali: {stats}")   # Stampa il dizionario stats intero
        if influx_client:            # Verifica che il client sia stato inizializzato (non sia None)
            influx_client.close()    # Chiude la connessione HTTP verso InfluxDB in modo pulito (libera risorse)
