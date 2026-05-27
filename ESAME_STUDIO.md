# Guida di Studio — CoAP IoT Stack
## Protocolli IoT, Gateway, Firewall e Analisi del Bug
### Preparazione Esame Fine Anno — ITS 3° Anno

---

## Indice

1. [Descrizione del progetto](#1-descrizione-del-progetto)
2. [Il Bug: analisi completa](#2-il-bug-analisi-completa)
3. [Componenti del sistema — dettaglio](#3-componenti-del-sistema--dettaglio)
4. [Teoria: Protocolli IoT](#4-teoria-protocolli-iot)
5. [Teoria: Gateway IoT](#5-teoria-gateway-iot)
6. [Teoria: Firewall e sicurezza di rete](#6-teoria-firewall-e-sicurezza-di-rete)
7. [Reti Docker come modello di segmentazione](#7-reti-docker-come-modello-di-segmentazione)
8. [Domande tipiche da esame](#8-domande-tipiche-da-esame)

---

## 1. Descrizione del progetto

### Cos'è questo stack

Questo progetto simula un'infrastruttura IoT industriale reale con:

- **10 sensori** che misurano temperatura ogni 5 secondi
- **Un gateway CoAP** che riceve i dati dai sensori e li scrive su un database
- **Un database InfluxDB** per le serie temporali
- **Una dashboard Node-RED** per visualizzare i dati e inviare alert
- **Un server email finto (Mailhog)** per ricevere le notifiche
- **Un Chaos Monkey** che simula guasti di rete per testare la resilienza

### Flusso dei dati (pipeline)

```
sensore-01..10
    │
    │  CoAP POST CON  (UDP porta 5683)
    ▼
coap-gateway
    │
    │  HTTP batch write  (TCP porta 8086)
    ▼
InfluxDB
    │
    │  Flux query ogni 5s (polling)
    ▼
Node-RED (dashboard + alert email)
    │
    │  SMTP
    ▼
Mailhog (inbox finta)
```

### Perché CoAP e non MQTT?

| Scenario | Scegli CoAP | Scegli MQTT |
|---|---|---|
| Dispositivo con <10 KB RAM | ✅ | ❌ |
| Rete radio (6LoWPAN, Thread) | ✅ | ❌ |
| Serve multicast UDP nativo | ✅ | ❌ |
| Modello REST (sai a chi parli) | ✅ | ❌ |
| Molti subscriber per lo stesso dato | ❌ | ✅ |
| Serve persistenza lato broker | ❌ | ✅ |
| Rete TCP stabile (LAN, WiFi) | ❌ | ✅ |

---

## 2. Il Bug: analisi completa

### 2.1 Dove si trova

Il bug si trova in **`coap-gateway/gateway.py`**, funzione `sync_write_batch`, righe 106–108.

### 2.2 Cosa fa il codice sbagliato

In `coap-gateway/gateway.py` (riga 106):

```python
# CODICE SBAGLIATO
if "timestamp" in data:
    p = p.time(int(data["timestamp"] * 1e9))
```

Il gateway cerca nel dizionario ricevuto la chiave `"timestamp"`.

### 2.3 Perché è sbagliato

In `sensor/sensor.py` (riga 198), il sensore costruisce il payload così:

```python
payload = {
    "sensor_id":   SENSOR_ID,
    "temperatura": temperatura,
    "ts":          ts,          # ← la chiave si chiama "ts", NON "timestamp"
    "seq":         seq,
    "buffered":    len(local_buffer),
}
```

Il sensore invia la chiave **`"ts"`**, non `"timestamp"`.

### 2.4 Conseguenza pratica

La condizione `if "timestamp" in data` è **sempre False**. Il blocco `.time(...)` non viene mai eseguito.

Il risultato è che ogni punto scritto su InfluxDB riceve come timestamp **l'ora di ricezione del gateway** (il momento in cui viene eseguita la scrittura), non **l'ora in cui il sensore ha effettuato la misurazione**.

```
Situazione SENZA bug:
  sensore misura 22.5°C alle 10:00:00
  → punto in InfluxDB con timestamp 10:00:00 ✅

Situazione CON bug:
  sensore misura 22.5°C alle 10:00:00
  messaggio in buffer per 10 minuti (gateway offline)
  messaggio flushed alle 10:10:00
  → punto in InfluxDB con timestamp 10:10:00 ❌ (sbagliato di 10 minuti!)
```

### 2.5 Impatto sul sistema

1. **Dati bufferizzati con timestamp errato**: quando un sensore accumula messaggi nel buffer locale durante un'interruzione e poi li invia tutti in burst al ritorno online, tutti quei punti vengono scritti con il timestamp sbagliato. Il grafico mostrerà un "muro verticale" di dati invece di una distribuzione corretta nel tempo.

2. **Impossibile rilevare gap temporali**: la serie temporale sembra continua anche quando il sensore era offline, perché i punti si "accatastano" nel momento del flush.

3. **Query di anomalia falsate**: le query Flux che cercano periodi di silenzio (assenza di dati) non trovano nulla, perché i dati arrivano tutti insieme con timestamp sbagliato.

4. **Il laboratorio "zero data loss" dimostra risultati scorretti**: anche se tutti i 500 messaggi del buffer arrivano, i loro timestamp nel database non corrispondono ai secondi in cui la temperatura è stata misurata.

### 2.6 Come correggere il bug

La correzione è semplice: cambiare la chiave da `"timestamp"` a `"ts"`.

In `coap-gateway/gateway.py`, righe 106–108:

```python
# PRIMA (sbagliato):
if "timestamp" in data:
    p = p.time(int(data["timestamp"] * 1e9))

# DOPO (corretto):
if "ts" in data:
    p = p.time(int(data["ts"] * 1e9))
```

### 2.7 Perché questo tipo di bug è pericoloso

Questo bug appartiene alla categoria dei **bug silenziosi**: il programma non si blocca, non genera eccezioni, non stampa errori. Tutto sembra funzionare. I dati arrivano, il grafico si aggiorna, le email partono. Solo analizzando attentamente il contenuto del database si nota che i timestamp sono sbagliati.

In sistemi IoT reali, timestamp errati possono causare:
- Falsi allarmi o mancanza di allarmi nelle analisi temporali
- Fatturazione errata nei sistemi di smart metering
- Diagnosi sbagliate in sistemi medicali
- Dati di audit non validi ai fini legali

### 2.8 Come trovare questo bug: metodo sistematico

```
1. Esegui il laboratorio pratico (sezione 11 del README)
2. Disconnetti un sensore per 2 minuti → accumula ~24 messaggi in buffer
3. Riconnetti il sensore → osserva il flush
4. Apri InfluxDB Data Explorer
5. Query Flux per quel sensore nell'ultimo quarto d'ora
6. Osserva i timestamp: tutti e 24 i punti del buffer hanno lo stesso timestamp?
   → Se sì, il bug è confermato
7. Ora cerca nel codice: dove viene impostato il timestamp del punto InfluxDB?
   → gateway.py, funzione sync_write_batch
8. Confronta la chiave cercata con quella inviata dal sensore
   → "timestamp" vs "ts" → trovato il bug
```

---

## 3. Componenti del sistema — dettaglio

### 3.1 Sensore (`sensor/sensor.py`)

**Ruolo**: client CoAP che simula un dispositivo fisico di rilevazione temperatura.

**Tecnologie**: Python 3.11, libreria `aiocoap`, `asyncio`.

**Comportamento**:
- Genera una temperatura simulata con distribuzione gaussiana (media 22°C, deviazione 2°C)
- Con probabilità 2.5% genera un picco alto (+10-15°C) per testare gli alert
- Invia un POST CoAP di tipo CON (Confirmable) ogni 5 secondi
- Se non riceve ACK entro 15 secondi → salva il messaggio nel buffer locale
- Quando il gateway torna online → svuota il buffer (flush) inviando i messaggi accumulati

**Buffer locale**:
- Struttura dati: `collections.deque` con `maxlen=500`
- Capacità: 500 messaggi × 5 secondi = ~41 minuti di autonomia offline
- Comportamento quando pieno: scarta automaticamente il messaggio più vecchio (FIFO)

**Variabili d'ambiente**:
```
COAP_SERVER    → hostname del gateway (default: coap-gateway)
COAP_PORT      → porta UDP (default: 5683)
SEND_INTERVAL  → secondi tra misure (default: 5)
SENSOR_ID      → identificativo univoco (es: sensor-01)
```

**Caso d'uso reale**: un nodo Arduino o ESP32 in un campo agricolo che misura umidità del suolo e la trasmette via LoRa a un gateway fisico.

---

### 3.2 Gateway CoAP (`coap-gateway/gateway.py`)

**Ruolo**: server CoAP che fa da ponte tra la rete dei sensori (UDP) e il database (HTTP).

**Tecnologie**: Python 3.11, `aiocoap` (lato server), `influxdb-client`.

**Comportamento**:
- Ascolta sulla porta UDP 5683 come server CoAP
- Registra la risorsa `/iot/temperatura` che gestisce le richieste POST
- Risponde **sempre** con `2.04 CHANGED` al sensore, anche se InfluxDB è offline
- Bufferizza i dati in RAM (buffer interno, max 2000 punti)
- Un task asyncio separato (`influx_writer_loop`) svuota il buffer ogni secondo con una scrittura batch

**Perché rispondere sempre ACK?**
Se il gateway non rispondesse quando InfluxDB è down, il sensore terrebbe il dato nel suo buffer locale (41 min di autonomia). Invece, rispondendo ACK sempre, il gateway "prende in carico" il dato: ora è il gateway ad essere responsabile della consegna a InfluxDB. I due buffer operano in sequenza, non in parallelo.

**Architettura interna (asyncio)**:
```
event loop asyncio
    ├── server CoAP (riceve POST, risponde ACK) → asyncio task
    └── influx_writer_loop (batch write ogni 1s) → asyncio task
         └── sync_write_batch() → ThreadPoolExecutor (thread separato)
```

La scrittura su InfluxDB usa un `ThreadPoolExecutor` perché il client InfluxDB è sincrono (bloccante). Eseguirlo direttamente nell'event loop asyncio bloccherebbe la ricezione dei messaggi CoAP.

**Variabili d'ambiente**:
```
INFLUX_URL     → http://influxdb:8086
INFLUX_TOKEN   → token di autenticazione
INFLUX_ORG     → organizzazione InfluxDB (its)
INFLUX_BUCKET  → bucket destinazione (iot)
COAP_PORT      → porta UDP in ascolto (5683)
```

**Caso d'uso reale**: un Raspberry Pi installato in una fabbrica che raccoglie dati da decine di sensori Zigbee e li invia a un cloud InfluxDB.

---

### 3.3 InfluxDB (`influxdb:2`)

**Ruolo**: database time-series per dati IoT.

**Caratteristiche specifiche per IoT**:
- Ogni punto è associato a un timestamp nanosecondi
- I dati si organizzano in **measurement** (come tabelle), **tag** (indicizzati, usati per filtrare) e **field** (valori numerici, aggregabili)
- Retention policy: si possono cancellare automaticamente i dati più vecchi di N giorni
- Linguaggio **Flux** per le query (funzionale, non SQL)

**Schema dei dati in questo progetto**:
```
measurement: temperatura
tags:         sensor_id (es: "sensor-03")
fields:       value (float, temperatura in °C)
              seq (int, numero sequenza)
timestamp:    nanoseconds epoch (unix time × 1e9)
```

**Query Flux tipica**:
```flux
from(bucket: "iot")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "temperatura")
  |> filter(fn: (r) => r.sensor_id == "sensor-03")
  |> sort(columns: ["_time"])
```

**Caso d'uso reale**: Grafana + InfluxDB è la combinazione più usata in ambito industriale per il monitoraggio di macchinari, consumi energetici, dati ambientali.

---

### 3.4 Node-RED (`nodered/`)

**Ruolo**: strumento di integrazione low-code per dashboard, alerting e gestione eventi.

**Flussi configurati (3 tab)**:

| Tab | Trigger | Cosa fa |
|---|---|---|
| Dashboard | inject ogni 5s | Query Flux → grafico temperatura |
| Alerting | inject ogni 30s | Query Flux → check soglie → email se >30°C o <16°C |
| Chaos Events | HTTP POST su `/chaos/events` | Riceve evento dal chaos monkey → email Mailhog |

**Differenza MQTT vs CoAP nel dashboard**:
- Con MQTT: il grafico si aggiornava in real-time (push da broker a Node-RED via subscribe)
- Con CoAP: il grafico aggiorna al massimo ogni 5 secondi (polling InfluxDB a intervalli fissi)

**Caso d'uso reale**: sistemi SCADA (Supervisory Control and Data Acquisition) industriali, building automation, home automation.

---

### 3.5 Mailhog

**Ruolo**: server SMTP finto per testare l'invio email senza configurare un provider reale.

**Perché è utile nei test**: nei progetti didattici e in sviluppo non si vuole inviare email vere per ogni test. Mailhog cattura tutto ciò che arriva sulla porta SMTP (1025) e lo mostra in una inbox web (porta 8025, esposta come 5025 sull'host).

**Caso d'uso reale**: sostituito in produzione con AWS SES, SendGrid, o un relay SMTP aziendale. Il codice Node-RED non cambia: cambia solo l'indirizzo del server SMTP.

---

### 3.6 Chaos Monkey (`chaos/chaos.sh`)

**Ruolo**: simulatore di guasti di rete per testare la resilienza del sistema.

**Come funziona**: usa `docker network disconnect` e `docker network connect` per isolare fisicamente i container dalla rete Docker, simulando un'interruzione di cavo o una perdita di segnale radio.

**Tipi di evento**:

| Evento | Frequenza | Effetto |
|---|---|---|
| `drop_single_sensor` | 40% | Un sensore va offline → buffer locale si attiva |
| `drop_multi_sensor` | 20% | Tre sensori offline simultaneamente |
| `drop_gateway` | 20% | Gateway offline → TUTTI i 10 sensori bufferizzano |
| `drop_nodered` | 20% | Dashboard offline → i dati continuano ad arrivare ma non si vedono |

**Notifica eventi**: il chaos monkey invia una notifica HTTP POST a Node-RED per ogni evento (inizio e fine guasto). Node-RED genera un'email Mailhog con il dettaglio del guasto.

**Perché non usa MQTT per notificare?** Questo è CoAP Stack: non esiste un broker Mosquitto. Le notifiche viaggiano via HTTP diretto a Node-RED.

---

## 4. Teoria: Protocolli IoT

### 4.1 Il problema dei dispositivi constrained

Un dispositivo IoT "constrained" (vincolato) è un dispositivo con risorse hardware molto limitate:
- CPU: 8-32 MHz (contro i 3 GHz di un PC)
- RAM: 2-256 KB (contro i 16 GB di un PC)
- Flash: 32-512 KB
- Batteria: ricaricata da pannello solare o sostituita ogni 1-5 anni
- Connettività: radio (LoRa, Zigbee, 6LoWPAN) con bandwidth limitata

I protocolli standard come HTTP/1.1 sono troppo pesanti per questi dispositivi (header HTTP verbosi, overhead TCP, TLS richiede molta CPU). Nascono così protocolli specifici per IoT.

---

### 4.2 CoAP (Constrained Application Protocol) — RFC 7252

**Cos'è**: versione ultralleggera di HTTP progettata per UDP e dispositivi constrained.

**Caratteristiche chiave**:

```
Trasporto:  UDP (non TCP)
Header:     4 byte fissi (vs 200+ byte di HTTP)
Porta:      5683 (IANA ufficiale)
Crittografia: DTLS (Datagram TLS, equivalente di TLS su UDP)
Multicast:  nativo (UDP lo supporta)
```

**Tipi di messaggio**:

| Tipo | Sigla | Comportamento | Analogo |
|---|---|---|---|
| Confirmable | CON | Il destinatario DEVE rispondere con ACK | TCP (con ACK) |
| Non-confirmable | NON | Fire and forget, nessuna conferma | UDP puro |
| Acknowledgement | ACK | Risposta a un CON | TCP ACK |
| Reset | RST | Segnala che il messaggio non può essere elaborato | TCP RST |

**Codici di risposta CoAP** (simili a HTTP):

| Codice | Significato | Equivalente HTTP |
|---|---|---|
| 2.01 Created | Risorsa creata | 201 |
| 2.04 Changed | POST/PUT riuscito | 204 |
| 4.00 Bad Request | Payload malformato | 400 |
| 4.04 Not Found | Risorsa non trovata | 404 |
| 5.00 Internal Server Error | Errore server | 500 |

**Meccanismo CON/ACK (affidabilità)**:
```
Client                    Server
  │                          │
  │──── CON (MID=0x7A1B) ──►│
  │                          │  (elabora il messaggio)
  │◄─── ACK (MID=0x7A1B) ───│
  │                          │

Se ACK non arriva entro 2s → ritrasmissione:
  Tentativo 1: dopo  2s
  Tentativo 2: dopo  4s
  Tentativo 3: dopo  8s
  Tentativo 4: dopo 16s
  → Totale ~30s prima di dichiarare timeout
```

**Deduplicazione**: ogni messaggio CON ha un Message ID (MID) a 16 bit. Se il server riceve due volte lo stesso MID, risponde ACK ma non elabora il duplicato. Questo gestisce il caso in cui la risposta ACK si perda e il client ritrasmetta.

**Estensione Observe (RFC 7641)**: permette a un client di "sottoscriversi" a una risorsa. Il server invia aggiornamenti automaticamente quando il valore cambia. È l'unico meccanismo push nativo di CoAP, simile a MQTT subscribe.

---

### 4.3 MQTT (Message Queuing Telemetry Transport)

**Cos'è**: protocollo publish/subscribe asincrono basato su TCP con un broker centrale.

**Caratteristiche chiave**:
```
Trasporto:  TCP (non UDP)
Header:     2 byte fissi
Porta:      1883 (IANA), 8883 (TLS)
Broker:     obbligatorio (Mosquitto, HiveMQ, AWS IoT Core...)
Modello:    publish/subscribe
```

**Come funziona**:
```
Publisher (sensore)         Broker (Mosquitto)       Subscriber (Node-RED)
      │                           │                         │
      │─── PUBLISH temp/22.5 ────►│                         │
      │                           │─── PUBLISH temp/22.5 ──►│
      │                           │                         │
```

**Quality of Service (QoS)**:

| Livello | Garanzia | Come funziona |
|---|---|---|
| QoS 0 | Al più una volta (fire & forget) | Nessuna conferma |
| QoS 1 | Almeno una volta | ACK obbligatorio; duplicati possibili |
| QoS 2 | Esattamente una volta | Handshake a 4 messaggi; nessun duplicato |

**Persistent session**: se il client si disconnette con `cleanSession=false`, il broker conserva le sottoscrizioni e i messaggi QoS 1/2 non consegnati. Quando il client si riconnette, riceve tutti i messaggi accumulati. Questo è il meccanismo che in MQTT garantisce zero perdita anche in caso di riavvio del sensore.

**Differenza critica con CoAP**:
- MQTT: il broker persiste su disco → dati sopravvivono al riavvio del broker
- CoAP: il buffer è in RAM → dati persi se il gateway si riavvia

---

### 4.4 HTTP/HTTPS

**Usato in questo stack per**:
- Gateway → InfluxDB (API REST, porta 8086)
- Node-RED → InfluxDB (query Flux, porta 8086)
- Chaos Monkey → Node-RED (notifiche eventi, porta 1880)
- Browser → Node-RED (interfaccia web, porta 1880)
- Browser → InfluxDB (interfaccia web, porta 8086)

**Perché non si usa HTTP per i sensori?** HTTP è progettato per TCP e ha overhead elevato (header verbosi, handshake TCP + TLS). Su una rete LoRa con bandwidth di 250 bps, un singolo header HTTP occuperebbe tutta la banda disponibile.

---

### 4.5 Confronto finale dei protocolli

| Caratteristica | HTTP | MQTT | CoAP |
|---|---|---|---|
| Trasporto | TCP | TCP | **UDP** |
| Modello | req/resp | pub/sub | req/resp |
| Header overhead | Alto (200+ byte) | Basso (2 byte) | **Minimo (4 byte)** |
| Broker necessario | No | **Sì** | No |
| Multicast | No | No | **Sì** |
| Batteria (consumo) | Alto | Medio | **Basso** |
| Crittografia | TLS | TLS | **DTLS** |
| IoT constrained | No | Medio | **Sì** |
| Web/REST integration | **Nativa** | Tramite bridge | **Sì** |
| Persistenza offline | No | Sì (broker) | Limitata (RAM) |

---

## 5. Teoria: Gateway IoT

### 5.1 Cos'è un gateway IoT

Un gateway IoT è un dispositivo o software che fa da **ponte tra la rete dei sensori** (spesso con protocolli wireless a bassa potenza) **e la rete IP** (LAN aziendale o Internet).

```
[Sensori campo]           [Gateway IoT]              [Cloud/Server]
  Zigbee/LoRa      ──►   [traduzione  ]    ──►    HTTP/MQTT/AMQP
  6LoWPAN/BLE      ──►   [protocollo  ]    ──►    InfluxDB/MQTT broker
  CoAP/UDP         ──►   [buffering   ]    ──►    REST API
```

### 5.2 Funzioni di un gateway

**1. Traduzione di protocollo (protocol bridging)**
Il gateway converte i dati dal protocollo della rete sensoristica (es. CoAP/UDP) al protocollo del backend (es. HTTP/TCP). Senza gateway, InfluxDB dovrebbe capire CoAP — che non fa parte delle sue funzionalità.

**2. Buffering e resilienza**
In questo progetto il gateway bufferizza in RAM fino a 2000 punti quando InfluxDB non è disponibile. In dispositivi reali il buffer è su flash (SD card, eMMC) per sopravvivere ai riavvii.

**3. Aggregazione e preprocessing**
Il gateway può aggregare dati di più sensori (es. calcolare la media di temperatura per zona), ridurre la frequenza (da 10 misure/s a 1/minuto prima del cloud) e filtrare dati anomali.

**4. Sicurezza**
Il gateway è spesso il punto dove si termina la crittografia della rete sensoristica (DTLS per CoAP, mTLS per Zigbee) e si inizia quella verso il cloud (HTTPS/TLS). I sensori non devono mai essere esposti direttamente a Internet.

**5. Edge computing**
I gateway moderni eseguono logica locale: se la temperatura supera 80°C → spegni il macchinario immediatamente, senza aspettare la risposta dal cloud. Questo è il paradigma del **fog computing** (nebbia = vicino al sensore, non in cloud).

### 5.3 Tipi di gateway

| Tipo | Esempio | Caratteristiche |
|---|---|---|
| Gateway hardware dedicato | Raspberry Pi, BeagleBone | Economico, customizzabile |
| Gateway commerciale | Cisco IOx, Dell Edge | Robusto, certificato industriale |
| Gateway software | Questo progetto (Python) | Flessibile, containerizzato |
| Gateway cloud | AWS IoT Greengrass | Gestito, aggiornamento automatico |

### 5.4 Il gateway in questo progetto

Il `coap-gateway` (Python) implementa solo alcune funzioni di un gateway reale:
- ✅ Traduzione protocollo (CoAP UDP → HTTP TCP per InfluxDB)
- ✅ Buffering interno (2000 punti in RAM)
- ✅ Batch write (aggregazione per efficienza)
- ❌ Nessuna crittografia (DTLS non configurato, solo rete Docker privata)
- ❌ Nessun preprocessing/filtering
- ❌ Nessun edge computing

### 5.5 Posizionamento del gateway nelle reti Docker

Il gateway è l'**unico container su entrambe le reti**:
- `coap_sensor_net`: comunica con i sensori via UDP
- `coap_core_net`: comunica con InfluxDB via HTTP

Questa è la simulazione della segmentazione di rete: i sensori non possono raggiungere direttamente InfluxDB. Il gateway è l'unico "ponticello" tra le due reti — esattamente come in un'architettura IoT reale.

---

## 6. Teoria: Firewall e sicurezza di rete

### 6.1 Cos'è un firewall

Un firewall è un sistema di sicurezza che controlla il traffico di rete in ingresso e uscita basandosi su regole predefinite. Può essere:
- **Hardware**: dispositivo fisico dedicato (Cisco ASA, Fortinet)
- **Software**: installato su un sistema operativo (iptables su Linux, Windows Firewall)
- **Cloud**: servizio gestito (AWS Security Groups, Azure NSG)

### 6.2 Tipi di firewall

**Packet filtering (stateless)**
Analizza ogni pacchetto individualmente: IP sorgente, IP destinazione, porta, protocollo. Non mantiene memoria delle connessioni precedenti.

```
Regola: CONSENTI TCP porta 8086 da 10.0.1.0/24 verso 10.0.2.5
→ Ogni pacchetto TCP sulla porta 8086 da quella subnet viene accettato
```

**Stateful firewall**
Tiene traccia delle connessioni: sa se un pacchetto è parte di una connessione TCP già stabilita (SYN visto, ACK atteso). Più sicuro: blocca pacchetti che non appartengono a connessioni legittime.

**Application layer firewall (WAF)**
Analizza il contenuto applicativo (payload HTTP, SQL nelle query, ecc.). Può bloccare attacchi SQL injection, XSS, anche se usano porte legittime.

**Next-Generation Firewall (NGFW)**
Combina stateful + deep packet inspection + IDS/IPS + reputazione IP + TLS inspection.

### 6.3 Come si applicano le regole firewall a questo stack

In questo progetto le "regole firewall" sono implementate tramite **reti Docker isolate**. È un modello semplificato ma che rispecchia i principi reali.

**Regole implicite della rete Docker**:

```
coap_sensor_net:
  sensor-01..10 → coap-gateway : UDP 5683   PERMESSO
  sensor-01..10 → influxdb     : qualsiasi  BLOCCATO (influxdb non è su questa rete)
  sensor-01..10 → nodered      : qualsiasi  BLOCCATO

coap_core_net:
  coap-gateway  → influxdb     : TCP 8086   PERMESSO
  nodered       → influxdb     : TCP 8086   PERMESSO
  nodered       → mailhog      : TCP 1025   PERMESSO (SMTP)
  chaos         → nodered      : TCP 1880   PERMESSO (HTTP)
  sensor-01..10 → influxdb     : qualsiasi  BLOCCATO (sensori non sono su core_net)
```

In un firewall reale (iptables), queste regole si scriverebbero:
```bash
# Permetti sensori → gateway su UDP 5683
iptables -A FORWARD -s 192.168.1.0/24 -d 192.168.1.254 -p udp --dport 5683 -j ACCEPT

# Blocca sensori → InfluxDB (segmentazione)
iptables -A FORWARD -s 192.168.1.0/24 -d 192.168.2.10 -j DROP

# Permetti gateway → InfluxDB su TCP 8086
iptables -A FORWARD -s 192.168.1.254 -d 192.168.2.10 -p tcp --dport 8086 -j ACCEPT
```

### 6.4 Segmentazione di rete: principio del minimo privilegio

La divisione in `coap_sensor_net` e `coap_core_net` è un'applicazione del **principio del minimo privilegio** (Principle of Least Privilege):

> Ogni componente del sistema deve poter accedere **solo** alle risorse strettamente necessarie per svolgere la sua funzione.

In questo progetto:
- I sensori **non devono** parlare con InfluxDB direttamente → sono solo su sensor_net
- I sensori **non devono** vedere Node-RED → sono solo su sensor_net
- Node-RED **non deve** ricevere dati dai sensori direttamente → è solo su core_net

Questo principio riduce la superficie di attacco: se un sensore viene compromesso (firmware malformato, man-in-the-middle), l'attaccante può solo raggiungere il gateway CoAP, non l'intero backend.

### 6.5 DMZ (Demilitarized Zone)

Una DMZ è una zona di rete intermedia, accessibile sia da Internet che dalla rete interna, ma separata da entrambe tramite firewall.

```
Internet
    │
    │  firewall esterno
    ▼
[   DMZ   ]   ← server web, gateway IoT pubblico
    │
    │  firewall interno
    ▼
[Rete interna]  ← database, sistemi critici
```

Analogia con questo progetto:
- `coap_sensor_net` = DMZ (i sensori sono "campo aperto", potenzialmente compromettibili)
- `coap_core_net` = rete interna (database, dashboard)
- `coap-gateway` = il firewall interno che media (sta su entrambe le reti con regole precise)

### 6.6 UDP vs TCP nel contesto della sicurezza

CoAP usa **UDP**, che crea alcune sfide di sicurezza specifiche:

| Problema | Descrizione | Soluzione |
|---|---|---|
| IP spoofing | UDP non ha handshake, facile falsificare l'IP sorgente | DTLS (autenticazione certificati) |
| Amplification attack | Server risponde con messaggi grandi a richieste piccole con IP falsificato | Rate limiting, DTLS |
| Replay attack | Un attaccante registra e ri-invia messaggi | Token CoAP, timestamp nei messaggi |

In questo progetto queste protezioni **non sono implementate** (è un ambiente didattico su rete Docker privata). In produzione, il gateway CoAP userebbe DTLS e autenticazione dei sensori.

---

## 7. Reti Docker come modello di segmentazione

### 7.1 Docker networking

Docker gestisce le comunicazioni tra container tramite reti virtuali. Ogni rete è un segmento isolato: i container su reti diverse non possono comunicare direttamente.

**Tipi di rete Docker**:

| Driver | Caratteristiche | Uso tipico |
|---|---|---|
| `bridge` | Rete privata sull'host, container comunicano via hostname | Sviluppo, produzione single-host |
| `host` | Container condivide la rete dell'host | Performance massima, meno isolamento |
| `overlay` | Rete multi-host (Docker Swarm) | Cluster distribuiti |
| `none` | Nessuna rete | Container completamente isolato |

In questo progetto si usa `bridge` per entrambe le reti.

### 7.2 DNS interno Docker

Docker include un DNS resolver interno: ogni container è raggiungibile dagli altri container nella stessa rete usando il **nome del servizio** come hostname.

```yaml
# docker-compose.yml
services:
  sensor-01:
    environment:
      COAP_SERVER: coap-gateway   # ← Docker risolve "coap-gateway" → IP interno
```

Quando `sensor-01` fa `socket.getaddrinfo("coap-gateway", 5683)`, Docker risponde con l'IP del container `coap-gateway`. Non serve configurare nulla di extra.

### 7.3 Volumi Docker

```yaml
volumes:
  influxdb_data:     # Volume nominato: Docker lo gestisce in /var/lib/docker/volumes/

services:
  influxdb:
    volumes:
      - influxdb_data:/var/lib/influxdb2  # mount: volume → percorso nel container
```

I dati di InfluxDB sopravvivono a `docker compose down` ma vengono cancellati con `docker compose down -v`.

Il bind mount di Node-RED è diverso:
```yaml
  nodered:
    volumes:
      - ./nodered/data:/data   # bind mount: cartella host → cartella container
```

Qui i file sono visibili direttamente su disco host (puoi modificare `flows.json` con un editor).

### 7.4 Il socket Docker (`/var/run/docker.sock`)

Il chaos monkey monta il socket Unix del daemon Docker:
```yaml
  chaos:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

Questo permette al container `chaos` di eseguire comandi Docker dall'interno del container stesso (pattern "Docker-in-Docker lite"). Il chaos monkey può così eseguire `docker network disconnect` per isolare altri container.

**Implicazione di sicurezza**: montare `/var/run/docker.sock` dà al container accesso completo al daemon Docker → equivale a privilegi di root sull'host. In produzione questo va evitato o ristretto con socket proxy (come Tecnativa Docker Socket Proxy).

---

## 8. Domande tipiche da esame

### 8.1 Domande sul bug

**D: Qual è il bug presente nel progetto?**

R: In `gateway.py`, la funzione `sync_write_batch` cerca la chiave `"timestamp"` nel dizionario ricevuto dal sensore (`if "timestamp" in data`), ma il sensore invia la chiave `"ts"` (vedi `sensor.py`, costruzione del payload). La condizione è sempre False, quindi InfluxDB riceve tutti i punti con il timestamp di ricezione del gateway invece del timestamp di misurazione del sensore.

**D: Quale conseguenza ha il bug sul sistema di buffering?**

R: I dati accumulati nel buffer locale del sensore durante un'interruzione vengono flushed al ritorno online con il timestamp sbagliato (l'ora del flush, non l'ora della misurazione). Il database mostra tutti i dati storici concentrati nel momento del flush invece di essere distribuiti nel periodo di offline.

**D: Il bug causa errori o il sistema funziona?**

R: Il sistema funziona senza errori visibili. È un bug silenzioso: dati arrivano, grafico si aggiorna, nessuna eccezione. Solo analizzando i timestamp in InfluxDB si nota l'anomalia.

---

### 8.2 Domande su CoAP

**D: Perché CoAP usa UDP invece di TCP?**

R: UDP è senza connessione, non richiede handshake iniziale (3-way handshake TCP), non mantiene stato. Questo riduce l'overhead computazionale e la latenza, fondamentale per dispositivi con CPU lenta e batteria limitata. La consegna affidabile è gestita a livello applicativo dai messaggi CON/ACK.

**D: Cosa succede se l'ACK non arriva?**

R: aiocoap ritrasmette automaticamente: dopo 2s, poi 4s, poi 8s, poi 16s (backoff esponenziale). Se nessuna risposta arriva entro ~30s, il messaggio è considerato perso e il sensore lo mette nel buffer locale.

**D: Cosa differenzia un messaggio CON da un NON?**

R: CON (Confirmable) richiede una risposta ACK esplicita dal destinatario. Se ACK non arriva, il mittente ritrasmette. NON (Non-confirmable) è fire and forget: inviato una volta, senza conferma. CON equivale a QoS=1 di MQTT, NON equivale a QoS=0.

**D: Cos'è il Message ID in CoAP?**

R: È un numero a 16 bit presente nell'header di ogni messaggio CoAP. Serve per abbinare la risposta ACK alla richiesta CON corrispondente e per la deduplicazione: se il server riceve due volte lo stesso MID (per ritrasmissione), risponde ACK ma non elabora il duplicato.

---

### 8.3 Domande su gateway e architettura

**D: Perché il gateway risponde sempre ACK anche quando InfluxDB è offline?**

R: Perché è il gateway a fare da garante della consegna al database. Se rispondesse "errore" al sensore, il sensore continuerebbe a bufferizzare (41 min di autonomia). Invece, con ACK sempre positivo, il gateway "promette" di consegnare il dato a InfluxDB prima o poi. Il buffer interno del gateway copre il periodo di indisponibilità di InfluxDB.

**D: Quali sono le due reti Docker e perché esistono?**

R: `coap_sensor_net` connette sensori e gateway CoAP (traffico UDP). `coap_core_net` connette gateway, InfluxDB, Node-RED, Mailhog (traffico HTTP/SMTP). La separazione implementa il principio del minimo privilegio: i sensori non possono raggiungere direttamente il database, riducendo la superficie di attacco.

**D: Perché il gateway usa un ThreadPoolExecutor per scrivere su InfluxDB?**

R: Il client InfluxDB Python è sincrono (bloccante): aspetta la risposta HTTP prima di restituire il controllo. Se venisse eseguito direttamente nell'event loop asyncio, bloccherebbe la ricezione di nuovi messaggi CoAP per tutta la durata della chiamata HTTP. `run_in_executor` delega la chiamata bloccante a un thread separato, permettendo all'event loop di continuare a gestire i messaggi CoAP in parallelo.

---

### 8.4 Domande su firewall e sicurezza

**D: Come si applica il principio del minimo privilegio in questo progetto?**

R: Tramite la segmentazione in due reti Docker: i sensori sono solo sulla `coap_sensor_net` e non possono raggiungere InfluxDB, Node-RED o Mailhog. Solo il gateway CoAP è su entrambe le reti, fungendo da unico punto di accesso controllato tra zona sensori e zona backend.

**D: Quali rischi di sicurezza introduce il montaggio di `/var/run/docker.sock`?**

R: Dà accesso completo al daemon Docker dal container. Chi compromette il container chaos può avviare, fermare, eliminare qualsiasi container, montare volumi, eseguire comandi su altri container. È equivalente a root sull'host. In produzione si usa un socket proxy (es. Tecnativa Docker Socket Proxy) che permette solo le operazioni necessarie.

**D: Perché CoAP su UDP è più difficile da proteggere con firewall rispetto a TCP?**

R: UDP è stateless: ogni pacchetto è indipendente. Un firewall stateless non può distinguere un pacchetto legittimo (risposta a una richiesta) da uno iniettato. TCP invece ha il flag SYN/ACK: il firewall stateful può tracciare le connessioni e bloccare pacchetti che non appartengono a sessioni stabilite. Per UDP è necessario DTLS (autenticazione e cifratura) per garantire autenticità.

---

### 8.5 Domande su InfluxDB e time series

**D: Qual è la differenza tra tag e field in InfluxDB?**

R: I tag sono indicizzati e usati per filtrare/raggruppare (es. `sensor_id`). I field sono i valori misurabili, aggregabili con funzioni (media, min, max). Non si possono fare operazioni matematiche sui tag. In questo progetto: `sensor_id` è tag, `value` (temperatura) e `seq` sono field.

**D: Perché il timestamp viene moltiplicato per 1e9?**

R: InfluxDB memorizza i timestamp in nanosecondi. Il timestamp Unix standard (`time.time()` in Python) è in secondi (float). Per convertire: `secondi × 1_000_000_000 = nanosecondi`. La conversione a `int` è necessaria perché InfluxDB accetta solo interi per i timestamp.

---

*Documento generato per la preparazione all'esame di fine anno — ITS 3° Anno — CoAP Stack IoT*
