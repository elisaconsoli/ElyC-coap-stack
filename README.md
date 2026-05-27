# CoAP Stack — Simulatore di Infrastruttura IoT

> Progetto didattico per le classi terze degli Istituti Tecnici Superiori (ITS).  
> Versione **CoAP** dello stack IoT. Stessa infrastruttura, protocollo di comunicazione completamente diverso da MQTT.

---

## Indice

1. [Cos'è questo progetto e perché CoAP](#1-cosè-questo-progetto-e-perché-coap)
2. [CoAP vs MQTT — le differenze fondamentali](#2-coap-vs-mqtt--le-differenze-fondamentali)
3. [Architettura del sistema](#3-architettura-del-sistema)
4. [Componenti](#4-componenti)
5. [Prerequisiti e avvio rapido](#5-prerequisiti-e-avvio-rapido)
6. [Interfacce web](#6-interfacce-web)
7. [Configurazione](#7-configurazione)
8. [Reti Docker](#8-reti-docker)
9. [Come CoAP garantisce l'affidabilità](#9-come-coap-garantisce-laffidabilità)
10. [Chaos Monkey — Simulazione dei guasti](#10-chaos-monkey--simulazione-dei-guasti)
11. [Laboratorio pratico — Verificare zero data loss](#11-laboratorio-pratico--verificare-zero-data-loss)
12. [Comandi utili](#12-comandi-utili)
13. [Troubleshooting](#13-troubleshooting)
14. [Struttura del progetto](#14-struttura-del-progetto)
15. [Glossario CoAP](#15-glossario-coap)

---

## 1. Cos'è questo progetto e perché CoAP

### Il problema che risolve

In un'aula scolastica, in una fabbrica o in un ospedale ci sono decine o centinaia di sensori che misurano temperatura, umidità, consumi energetici. Questi sensori devono trasmettere dati in modo affidabile anche quando:
- la rete è instabile
- il server ricevente si riavvia
- c'è un'interruzione temporanea del collegamento

La domanda è: **quale protocollo scegliere per garantire che nessun dato venga perso?**

Questo progetto risponde implementando **CoAP (Constrained Application Protocol)**, un'alternativa leggera ad HTTP e MQTT progettata specificamente per dispositivi IoT con risorse limitate.

### Chi usa CoAP nel mondo reale?

- **Smart meters** (contatori elettrici intelligenti) su reti LTE-M
- **Sensori agricoli** su LoRa e 6LoWPAN
- **Dispositivi medici** su Bluetooth Low Energy
- **Sensori industriali** su Thread/Zigbee
- **Sistemi di building automation** (riscaldamento, ventilazione, luci)

---

## 2. CoAP vs MQTT — le differenze fondamentali

Quando studi IoT incontri sempre questi due protocolli. Capire quando usare l'uno o l'altro è una competenza fondamentale.

### La filosofia

```
MQTT = sistema postale
  Il sensore imbuca una lettera (publish) in un ufficio postale (broker).
  Il broker la recapita a tutti gli abbonati (subscriber).
  Il sensore non sa chi la riceve — il broker è l'intermediario.

CoAP = telefonata diretta
  Il sensore chiama direttamente il server (POST).
  Il server risponde (ACK).
  Non c'è intermediario — se il server non risponde, il sensore richiama.
```

### Confronto tecnico

| Caratteristica | MQTT | CoAP |
|---|---|---|
| **Modello** | Publish/Subscribe (tramite broker) | Client/Server (stile REST/HTTP) |
| **Trasporto** | TCP | **UDP** |
| **Porta standard** | 1883 | **5683** |
| **Intermediario** | Broker obbligatorio (Mosquitto) | Nessuno: comunicazione diretta |
| **Connessione** | Connessione TCP persistente | **Senza connessione** (ogni msg indipendente) |
| **Conferma ricezione** | QoS 0/1/2 | **CON** (Confirmable) / **NON** |
| **Sessioni** | Persistent session (broker ricorda il client) | **Non esiste**: ogni messaggio è autonomo |
| **Header** | 2 byte fissi + variabile | **4 byte fissi** (più leggero) |
| **Ispirazione** | Messaggistica asincrona | **HTTP in miniatura** |
| **Crittografia** | TLS (su TCP) | **DTLS** (su UDP) |
| **Multicast** | Non nativo | **Sì**, nativo in UDP |

### Quando scegliere quale

**Scegli MQTT quando:**
- Hai molti subscriber che devono ricevere lo stesso messaggio
- Hai bisogno di persistenza lato broker (sessioni, code)
- La rete è TCP-friendly (LAN, WiFi, 4G)
- Vuoi fan-out: un dato arriva a 50 sistemi contemporaneamente

**Scegli CoAP quando:**
- Il dispositivo ha memoria molto limitata (<10 KB RAM, es. Arduino nano)
- Vuoi integrazione naturale con HTTP e REST API
- Hai bisogno di multicast UDP
- La rete è radio (6LoWPAN, Thread, Zigbee, LoRa)
- Preferisci il modello request/response (sai esattamente a chi parli)

### In questo progetto

Con CoAP eliminiamo due componenti rispetto alla versione MQTT:

```
MQTT Stack:   sensore → [Mosquitto] → [Telegraf] → InfluxDB
CoAP Stack:   sensore →              [coap-gateway] → InfluxDB
```

Mosquitto e Telegraf non esistono. Il gateway CoAP fa tutto: riceve i dati, bufferizza, scrive su InfluxDB.

---

## 3. Architettura del sistema

```
╔══════════════════════════════════════════════════════════════╗
║                      COAP_SENSOR_NET                         ║
║                                                              ║
║  ┌──────────┐ ┌──────────┐ ┌──────────┐    ┌──────────┐     ║
║  │sensor-01 │ │sensor-02 │ │sensor-03 │·· │sensor-10 │     ║
║  │ aiocoap  │ │ aiocoap  │ │ aiocoap  │    │ aiocoap  │     ║
║  └────┬─────┘ └────┬─────┘ └────┬─────┘    └────┬─────┘     ║
║       │             │             │               │          ║
║       └─────────────┴─────────────┴───────────────┘          ║
║                   CoAP POST CON (UDP 5683)                   ║
║                   ┌─────────▼─────────┐                     ║
║                   │   COAP-GATEWAY    │◄──────────────────┐  ║
║                   │ aiocoap server    │                   │  ║
║                   │ batch→InfluxDB    │                   │  ║
║                   └─────────┬─────────┘                   │  ║
╚═════════════════════════════│═══════════════════════════════╝
                              │
╔═════════════════════════════│═══════════════════════════════╗
║                      COAP_CORE_NET                          ║
║                             │ HTTP :8086                    ║
║            ┌────────────────┴──────────────┐               ║
║            │                               │               ║
║  ┌─────────▼───────┐             ┌──────────▼──────────┐   ║
║  │    INFLUXDB     │             │      NODE-RED        │   ║
║  │  time-series DB │◄────────────│  polling ogni 5s     │   ║
║  └─────────────────┘    Flux     └──────────┬───────────┘   ║
║                                             │ SMTP          ║
║                                   ┌─────────▼──────────┐   ║
║                                   │     MAILHOG         │   ║
║                                   │   SMTP fake         │   ║
║                                   └────────────────────┘   ║
║  ┌─────────────────────────────────────────────────────┐    ║
║  │  CHAOS MONKEY  →  curl HTTP → node-red:1880         │────┘║
║  │  network disconnect/connect                          │    ║
║  └─────────────────────────────────────────────────────┘    ║
╚═════════════════════════════════════════════════════════════╝
```

### Flusso dei dati

```
1. Sensore genera temperatura ogni 5s
        │
        ▼ CoAP POST CON (UDP)
2. Gateway riceve, risponde ACK, bufferizza
        │
        ▼ HTTP batch write (ogni 1s)
3. InfluxDB salva i punti
        │
        ▼ Flux query (ogni 5s da Node-RED)
4. Node-RED aggiorna dashboard
        │
        ▼ (se soglie superate)
5. Mailhog riceve email di alert
```

---

## 4. Componenti

| Servizio Docker | Container | Tecnologia | Ruolo |
|---|---|---|---|
| `sensor01`…`sensor10` | `sensor-01`…`sensor-10` | Python 3.11 + aiocoap | Inviano temperatura via POST CON ogni 5s |
| `coap-gateway` | `coap-gateway` | Python 3.11 + aiocoap + influxdb-client | Riceve POST, scrive su InfluxDB in batch |
| `nodered` | `node-red` | Node-RED | Polling InfluxDB, dashboard, alerting, chaos via HTTP |
| `influxdb` | `influxdb` | InfluxDB v2 | Database time-series |
| `mailhog` | `mailhog-coap` | Mailhog | Server SMTP fake per le email di alert |
| `chaos` | `network-chaos` | Bash + Docker CLI + curl | Chaos Monkey: guasti via HTTP (non MQTT) |

**Componenti presenti in MQTT ma eliminati in CoAP:**
- ~~`mosquitto`~~ (Mosquitto broker) → non serve: CoAP non ha broker
- ~~`gateway`~~ (Telegraf) → sostituito da `coap-gateway` in Python

### Porte esposte

| Porta Host | Container | Protocollo | URL |
|---|---|---|---|
| `1880` | node-red | TCP | http://localhost:1880 (editor) |
| `1880/ui` | node-red | TCP | http://localhost:1880/ui (dashboard) |
| `5683` | coap-gateway | **UDP** | `coap://localhost:5683` |
| `8086` | influxdb | TCP | http://localhost:8086 |
| `5025` | mailhog-coap | TCP | http://localhost:5025 |
| `4025` | mailhog-coap | TCP | SMTP host (non usato direttamente) |

---

## 5. Prerequisiti e avvio rapido

### Prerequisiti

| Strumento | Versione minima | Verifica |
|---|---|---|
| Docker Desktop | 4.x | `docker --version` |
| Docker Compose v2 | integrato | `docker compose version` |
| RAM disponibile | 3 GB | (meno della versione MQTT: nessun Mosquitto/Telegraf) |
| Spazio disco | 2 GB | per le immagini Docker |

### Prima installazione

```bash
# Entra nella cartella del progetto
cd coap-stack

# Costruisci le immagini personalizzate (sensor, coap-gateway, nodered, chaos)
# La prima volta: 3–5 minuti (scarica le immagini base)
docker compose build

# Avvia tutti i container
docker compose up -d

# Verifica lo stato
docker compose ps
```

Output atteso:
```
NAME              IMAGE                     STATUS          PORTS
coap-gateway      coap-stack-coap-gateway   Up              0.0.0.0:5683->5683/udp
influxdb          influxdb:2                Up (healthy)    0.0.0.0:8086->8086/tcp
mailhog-coap      mailhog/mailhog           Up              0.0.0.0:5025->8025/tcp
network-chaos     coap-stack-chaos          Up
node-red          coap-stack-nodered        Up              0.0.0.0:1880->1880/tcp
sensor-01         coap-stack-sensor01       Up
...
sensor-10         coap-stack-sensor10       Up
```

### Verificare che tutto funzioni

```bash
# Dati che arrivano al gateway
docker compose logs -f coap-gateway
# Atteso: [INFLUX] ✓ Scritti 20 punti in batch | totale=20 | buf=0

# Sensori che inviano
docker compose logs sensor01 --tail 5
# Atteso: [TX] sensor-01 → 22.4°C | seq=5 | latency=4.2ms

# Chaos in attesa
docker compose logs chaos --tail 5
# Atteso: Prossimo evento #1 tra 87s
```

Dopo 30 secondi, apri http://localhost:1880/ui — dovresti vedere il grafico con i dati dei sensori.

### Fermare lo stack

```bash
docker compose down           # ferma i container (dati InfluxDB conservati)
docker compose down -v        # ferma + cancella il volume InfluxDB
```

---

## 6. Interfacce web

### Node-RED Dashboard — http://localhost:1880/ui

Mostra:
- **Grafico temperatura**: una curva per sensore, si aggiorna ogni 5 secondi (polling)
- La dashboard **non è real-time** come in MQTT: c'è un ritardo massimo di 5s

> Differenza MQTT vs CoAP: in MQTT il grafico si aggiornava al millisecondo appena il sensore pubblicava. In CoAP aggiorna al massimo ogni 5s (ciclo di polling).

### Node-RED Editor — http://localhost:1880

| Tab | Meccanismo | Funzione |
|---|---|---|
| **Dashboard** | inject → influxdb-in (Flux) → chart | Grafico temperatura polling |
| **Alerting** | inject → influxdb-in → threshold check → email | Alert soglie temperatura |
| **Chaos Events** | http in POST `/chaos/events` → email | Notifiche guasti chaos |

### InfluxDB — http://localhost:8086

- **Username**: `admin` / **Password**: `admin123`
- **Org**: `its` / **Bucket**: `iot`

Query Flux per verificare i dati:
```flux
from(bucket: "iot")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "temperatura" and r._field == "value")
  |> sort(columns: ["_time"])
```

### Mailhog — http://localhost:5025

Riceve le email di:
- **Alert temperatura** (>30°C o <16°C), controllate ogni 30s
- **Evento chaos DROP** — inizio guasto simulato
- **Evento chaos RESTORE** — ripristino avvenuto

---

## 7. Configurazione

### File `.env`

```env
# InfluxDB
INFLUX_USER=admin
INFLUX_PASSWORD=admin123
INFLUX_ORG=its
INFLUX_BUCKET=iot
INFLUX_TOKEN=my-super-token

# Chaos Monkey
CHAOS_ENABLED=true
CHAOS_DROP_INTERVAL=90    # secondi medi tra eventi
CHAOS_DROP_DURATION=20    # durata disconnessione
```

### Variabili sensori (docker-compose.yml)

| Variabile | Default | Effetto |
|---|---|---|
| `COAP_SERVER` | `coap-gateway` | Hostname del server CoAP |
| `COAP_PORT` | `5683` | Porta UDP |
| `SEND_INTERVAL` | `5` | Secondi tra misure |
| `SENSOR_ID` | `sensor-01` | ID univoco (tag InfluxDB) |

### Variabili gateway (docker-compose.yml)

| Variabile | Default | Effetto |
|---|---|---|
| `INFLUX_URL` | `http://influxdb:8086` | Endpoint InfluxDB |
| `INFLUX_TOKEN` | `my-super-token` | Token autenticazione |
| `INFLUX_ORG` | `its` | Organizzazione |
| `INFLUX_BUCKET` | `iot` | Bucket di destinazione |
| `COAP_PORT` | `5683` | Porta UDP in ascolto |

---

## 8. Reti Docker

```
coap_sensor_net:  sensor-01..10 ←UDP→ coap-gateway
coap_core_net:    coap-gateway ←HTTP→ influxdb
                  node-red ←HTTP→ influxdb
                  node-red ←SMTP→ mailhog
                  chaos ←HTTP→ node-red
```

| Container | coap_sensor_net | coap_core_net |
|---|---|---|
| `sensor-01`…`sensor-10` | ✅ | ❌ |
| `coap-gateway` | ✅ | ✅ |
| `node-red` | ❌ | ✅ |
| `influxdb` | ❌ | ✅ |
| `mailhog-coap` | ❌ | ✅ |
| `network-chaos` | ✅ | ✅ |

**Differenza chiave da MQTT:**  
In MQTT, il broker (Mosquitto) era il ponte tra le due reti. In CoAP, è il `coap-gateway` a stare su entrambe le reti: riceve UDP dalla sensor_net e scrive HTTP sulla core_net.

---

## 9. Come CoAP garantisce l'affidabilità

### Messaggi CON (Confirmable) — equivalente di QoS=1

```
Sensore                           Gateway
   │                                 │
   │──── POST CON (MID=0xA1B2) ────►│  Il gateway riceve il dato
   │◄─── ACK 2.04 CHANGED ───────────│  Conferma ricezione
   │                                 │
```

Se il gateway non risponde, il sensore ritrasmette automaticamente:
```
Tentativo 1:  dopo  2s
Tentativo 2:  dopo  4s
Tentativo 3:  dopo  8s
Tentativo 4:  dopo 16s
Timeout:      ~45s (o 15s con il nostro limite)
→ dato salvato nel buffer locale del sensore
```

### Deduplicazione tramite Message ID

Ogni messaggio CoAP ha un **MID** (Message ID) a 16 bit. Se il gateway riceve due volte lo stesso MID (ritrasmissione), lo ignora ma risponde comunque con ACK. InfluxDB non riceve duplicati.

### I tre livelli di protezione

```
Livello 1: Buffer locale sensore (RAM, max 500 msg)
    Si attiva quando: il gateway non risponde entro 15s
    Capacità: 500 × 5s = ~41 minuti di dati offline

Livello 2: Retransmission CoAP (automatica in aiocoap)
    Si attiva quando: l'ACK non arriva entro 2s
    Tentativi: fino a 4 (totale ~45s)

Livello 3: Buffer interno gateway (RAM, max 2000 msg)
    Si attiva quando: InfluxDB non risponde
    Capacità: 2000 punti / (10 sensori × 0.2 msg/s) = ~16 minuti
```

### Differenza rispetto a MQTT

| Scenario | MQTT (iot-stack) | CoAP (questo progetto) |
|---|---|---|
| Sensore offline e riavviato | Dati nel broker (persistent session) → **zero perdita** | Dati in RAM sensore → **persi al riavvio** |
| Gateway offline | Mosquitto accoda → **zero perdita** | Buffer RAM sensore → perdita dopo 41 min |
| InfluxDB offline | Telegraf buffer interno | Gateway buffer interno → stesso comportamento |
| Broker offline | Sensore bufferizza localmente | N/A (non c'è broker) |

**CoAP è meno resiliente di MQTT in caso di riavvii**: il broker MQTT persiste su disco, i buffer CoAP sono in RAM.

---

## 10. Chaos Monkey — Simulazione dei guasti

Il container `network-chaos` inietta guasti di rete ogni ~90 secondi. Usa `docker network disconnect/connect` per isolare i container.

### Tipi di evento

| Evento | Probabilità | Cosa succede |
|---|---|---|
| Caduta sensore singolo | 60% | Un sensore attiva il buffer locale |
| Caduta multi-sensore | 20% | Due sensori offline simultaneamente |
| Caduta gateway | 20% | Tutti i 10 sensori vanno in [OFFLINE] |
| Caduta Node-RED | raro | Dashboard si ferma; polling fallisce |

### Notifiche (HTTP invece di MQTT)

```bash
# Il chaos monkey notifica Node-RED via curl:
curl -X POST http://node-red:1880/chaos/events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"drop_gateway","phase":"DROP",...}'
```

Node-RED riceve la notifica sul tab **Chaos Events** e invia un'email a Mailhog.

### Controllare il chaos

```bash
# Segui gli eventi
docker compose logs -f chaos

# Disabilita temporaneamente: modifica .env → CHAOS_ENABLED=false
docker compose up -d --force-recreate chaos

# Simula manualmente una caduta
docker network disconnect coap_sensor_net sensor-05
docker compose logs -f sensor05     # vedi [OFFLINE] dopo ~15s
docker network connect coap_sensor_net sensor-05
docker compose logs sensor05 | tail -5  # vedi [BUFFER] flush
```

---

## 11. Laboratorio pratico — Verificare zero data loss

### Obiettivo

Dimostrare che i messaggi CON + buffer locale garantiscono zero perdita di dati durante una disconnessione breve.

### Passo 1: Annota il seq attuale

```bash
docker compose logs sensor03 | grep "\[TX\]" | tail -3
```
Esempio: `[TX] sensor-03 → 22.4°C | seq=100 | latency=4ms`

### Passo 2: Disconnetti il sensore

```bash
docker network disconnect coap_sensor_net sensor-03
```

### Passo 3: Osserva il buffer locale

```bash
docker compose logs -f sensor03
```

Dopo ~15s (il nostro timeout CON):
```
[OFFLINE] sensor-03 → 23.1°C | seq=101 | buffer=1/500
[OFFLINE] sensor-03 → 22.8°C | seq=102 | buffer=2/500
[OFFLINE] sensor-03 → 21.9°C | seq=103 | buffer=3/500
[OFFLINE] sensor-03 → 24.2°C | seq=104 | buffer=4/500
```

### Passo 4: Riconnetti

```bash
docker network connect coap_sensor_net sensor-03
```

Immediatamente:
```
[COAP] ✓ Server raggiungibile. Svuoto buffer (4 messaggi)...
[BUFFER] Flush: seq=101 → ACK
[BUFFER] Flush: seq=102 → ACK
[BUFFER] Flush: seq=103 → ACK
[BUFFER] Flush: seq=104 → ACK
[TX] sensor-03 → 25.1°C | seq=105 | latency=3ms
```

### Passo 5: Verifica in InfluxDB

```flux
from(bucket: "iot")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "temperatura"
     and r._field == "value"
     and r.sensor_id == "sensor-03")
  |> sort(columns: ["_time"])
```

Conta i punti: devi trovare tutti i seq da 100 a 105 **senza buchi**.

---

## 12. Comandi utili

### Stack

```bash
docker compose up -d                          # avvia tutto
docker compose down                           # ferma (dati conservati)
docker compose down -v                        # ferma + cancella InfluxDB
docker compose build --no-cache              # ricostruisci immagini
docker compose restart coap-gateway          # riavvia solo il gateway
docker compose restart nodered               # riavvia Node-RED
docker compose restart chaos                  # riavvia Chaos Monkey
```

### Log e monitoraggio

```bash
docker compose logs -f coap-gateway          # dati ricevuti + scritti
docker compose logs -f sensor03              # log sensore 03
docker compose logs -f chaos                 # eventi chaos

# Conta messaggi ricevuti dal gateway
docker compose logs coap-gateway | grep -c "\[RX\]"

# Conta scritture InfluxDB riuscite
docker compose logs coap-gateway | grep -c "✓ Scritti"

# Sequenza sensore (cerca buchi nel seq)
docker compose logs sensor03 | grep "\[TX\]" | grep -oP "seq=\d+" | sort -t= -k2 -n
```

### Test CoAP manuale

```bash
# Invia un POST di test al gateway
docker exec coap-gateway python3 -c "
import asyncio, aiocoap, json, time

async def test():
    ctx = await aiocoap.Context.create_client_context()
    payload = json.dumps({
        'sensor_id': 'test',
        'temperatura': 99.9,
        'timestamp': time.time(),
        'seq': 0
    }).encode()
    req = aiocoap.Message(
        code=aiocoap.POST,
        uri='coap://localhost:5683/iot/temperatura',
        payload=payload,
        mtype=aiocoap.CON,
    )
    resp = await ctx.request(req).response
    print('Gateway risponde:', resp.code)

asyncio.run(test())
"
```

### InfluxDB

```bash
docker exec influxdb influx ping
docker exec influxdb influx bucket list --org its --token my-super-token

# Quanti punti nell'ultima ora?
docker exec influxdb influx query --org its --token my-super-token \
  'from(bucket:"iot") |> range(start:-1h) |> count()'
```

---

## 13. Troubleshooting

### Il gateway riceve ma il buffer si riempie / InfluxDB non scrive

```bash
# Verifica errori di scrittura
docker compose logs coap-gateway | grep "\[ERR\]"

# Controlla che InfluxDB sia raggiungibile dal gateway
docker exec coap-gateway curl -s http://influxdb:8086/health
# Risposta attesa: {"name":"influxdb","status":"pass",...}

# Se InfluxDB non risponde: controlla i log
docker compose logs influxdb --tail 20

# Riavvia il gateway (riprende a scrivere)
docker compose restart coap-gateway
```

### Il grafico Node-RED non mostra dati

1. Apri InfluxDB (http://localhost:8086) → Data Explorer → verifica che ci siano punti nel bucket `iot`
2. Se InfluxDB ha dati ma il grafico no:
   - Tab Dashboard → nodo `influxdb-in` → deve avere pallino **verde**
   - Controlla che il nodo `function` invii `msg.payload` come **numero puro** (non `{x,y}`)
   - Riavvia Node-RED: `docker compose restart nodered`

### I sensori stampano solo [OFFLINE]

Il gateway non è raggiungibile. Possibili cause:

```bash
# 1. Il gateway è in esecuzione?
docker compose ps coap-gateway

# 2. Il gateway è sulla coap_sensor_net?
docker inspect coap-gateway --format '{{json .NetworkSettings.Networks}}' | python3 -m json.tool

# 3. InfluxDB bloccava il gateway? (wait_for_influxdb loop infinito)
docker compose logs coap-gateway | head -20

# Fix: riavvia il gateway dopo che InfluxDB è healthy
docker compose restart coap-gateway
```

### Nessuna email in Mailhog

```bash
# Verifica che il chaos sia in esecuzione
docker compose logs chaos | tail -10

# Test manuale della notifica HTTP
docker exec network-chaos curl -v -X POST \
  -H "Content-Type: application/json" \
  -d '{"event_type":"test","phase":"DROP","container":"test","duration":5,"timestamp":0,"event_count":0}' \
  http://node-red:1880/chaos/events

# Verifica Mailhog
docker compose logs mailhog-coap --tail 10
```

### Container in `Restarting` o `Exit`

```bash
# Leggi il messaggio di errore
docker compose logs nome-servizio --tail 30

# Causa comune per coap-gateway: aiocoap non installato correttamente
# Fix: ricostruisci l'immagine
docker compose build coap-gateway --no-cache
docker compose up -d coap-gateway
```

### Reset completo

```bash
docker compose down -v
docker compose up -d
```

---

## 14. Struttura del progetto

```
coap-stack/
│
├── docker-compose.yml       ← 12 servizi, 2 reti (sensor_net UDP, core_net TCP)
├── .env                     ← Variabili InfluxDB e chaos
├── README.md                ← Questo file
│
├── sensor/                  ← Client CoAP Python asyncio
│   ├── sensor.py            ← POST CON ogni 5s, buffer locale, flush al restore
│   ├── requirements.txt     ← aiocoap>=0.4.7
│   ├── Dockerfile           ← python:3.11-slim
│   └── README.md            ← Guida con 7 esempi di modifica
│
├── coap-gateway/            ← Server CoAP + bridge InfluxDB
│   ├── gateway.py           ← aiocoap server UDP 5683, batch write, buffer interno
│   ├── requirements.txt     ← aiocoap + influxdb-client
│   ├── Dockerfile           ← python:3.11-slim
│   └── README.md            ← Guida con 7 esempi + architettura interna
│
├── nodered/                 ← Dashboard e alerting (polling InfluxDB)
│   ├── Dockerfile           ← Node-RED + influxdb + dashboard + email
│   ├── data/
│   │   ├── flows.json       ← 3 tab: Dashboard, Alerting, Chaos Events HTTP
│   │   ├── flows_cred.json  ← Token InfluxDB
│   │   └── settings.js      ← credentialSecret: false
│   └── README.md            ← Guida con 8 esempi + regole ui_chart
│
└── chaos/                   ← Chaos Monkey
    ├── chaos.sh             ← 4 eventi, notifiche via curl HTTP
    ├── Dockerfile           ← docker:cli + bash + curl (no mosquitto)
    └── README.md            ← Guida con 8 esempi + confronto MQTT
```

---

## 15. Glossario CoAP

| Termine | Definizione |
|---|---|
| **CoAP** | Constrained Application Protocol (RFC 7252). Protocollo REST-like per IoT, basato su UDP. |
| **CON** | Confirmable message. Il destinatario deve rispondere con ACK. Equivale a QoS=1 di MQTT. |
| **NON** | Non-confirmable message. Fire and forget. Equivale a QoS=0 di MQTT. |
| **ACK** | Acknowledgement. Risposta del server che conferma la ricezione di un messaggio CON. |
| **MID** | Message ID. Numero a 16 bit per identificare e deduplicare i messaggi CoAP. |
| **2.04 CHANGED** | Codice risposta CoAP per POST/PUT riuscito. Equivalente HTTP 204. |
| **4.00 BAD REQUEST** | Payload malformato. Equivalente HTTP 400. |
| **5.00 INTERNAL SERVER ERROR** | Errore lato server. Equivalente HTTP 500. |
| **Observe** | Estensione CoAP (RFC 7641) per ricevere aggiornamenti automatici — simile a MQTT subscribe. |
| **DTLS** | Datagram TLS. Cifratura per CoAP su UDP. Equivalente a TLS/MQTT su TCP. |
| **aiocoap** | Libreria Python asyncio per CoAP. Gestisce retransmission e deduplicazione automaticamente. |
| **Resource** | Risorsa CoAP identificata da URI. Es: `coap://gateway:5683/iot/temperatura`. |
| **Batch write** | Scrittura di N punti InfluxDB in una sola chiamata HTTP. Molto più efficiente della scrittura punto per punto. |
| **Polling** | Tecnica in cui Node-RED interroga InfluxDB a intervalli fissi invece di ricevere dati in push. |
| **Retransmission** | Ritrasmissione automatica di un messaggio CON se l'ACK non arriva. aiocoap: 4 tentativi (2+4+8+16s). |
| **Deduplication** | Il gateway riconosce messaggi CON duplicati (stesso MID) e risponde con ACK senza elaborarli di nuovo. |
| **coap_sensor_net** | Rete Docker UDP tra sensori e gateway CoAP. |
| **coap_core_net** | Rete Docker TCP tra gateway, InfluxDB, Node-RED, Mailhog. |
| **Buffer locale** | Coda in RAM del sensore. Attiva quando il gateway non risponde. Max 500 messaggi (~41 min a 5s/msg). |
| **Buffer interno** | Coda in RAM del gateway. Attiva quando InfluxDB non risponde. Max 2000 punti (~16 min). |
