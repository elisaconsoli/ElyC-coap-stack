# Cartella `nodered/` — Dashboard e Alerting (versione CoAP)

## Cos'è questa cartella?

Qui vive la configurazione di **Node-RED** per la versione CoAP del progetto.

Node-RED è una piattaforma di programmazione visuale basata su flussi. Permette di collegare visivamente nodi di input, elaborazione e output senza scrivere molto codice.

### La differenza fondamentale rispetto alla versione MQTT

Nella versione MQTT, Node-RED **riceveva** i dati in modo passivo: si iscriveva al broker e i dati arrivavano automaticamente appena i sensori pubblicavano.

Con CoAP non c'è broker. Node-RED deve **andare a chiedere** i dati:

```
MQTT:   Sensore → Broker → [Node-RED riceve automaticamente] → Dashboard
CoAP:   Sensore → Gateway → InfluxDB ← [Node-RED chiede ogni 5s] → Dashboard
```

Questo si chiama **polling** e ha vantaggi e svantaggi:

| | MQTT (push) | CoAP (polling) |
|---|---|---|
| **Latenza aggiornamento** | Immediata (< 1s) | Fino a 5s (intervallo polling) |
| **Complessità** | Richiede broker sempre acceso | Solo InfluxDB deve essere acceso |
| **Scalabilità** | Dipende dal broker | Dipende da InfluxDB |
| **Perdita dati offline** | Broker mette in coda | Query recupera i dati storici al ripristino |

---

## File nella cartella

```
nodered/
├── Dockerfile             ← Node-RED + plugin pre-installati
├── data/
│   ├── flows.json         ← I 3 flussi: Dashboard, Alerting, Chaos Events
│   ├── flows_cred.json    ← Token InfluxDB (credenziali)
│   └── settings.js        ← Configurazione Node-RED
└── README.md              ← Questo file
```

---

## `Dockerfile`

```dockerfile
FROM nodered/node-red:latest

USER root
RUN npm install --prefix /usr/src/node-red \
    node-red-contrib-influxdb \
    node-red-dashboard \
    node-red-node-email

USER node-red
```

Plugin installati:
- **node-red-contrib-influxdb**: nodi per leggere/scrivere su InfluxDB v2 con Flux
- **node-red-dashboard**: widget grafici (chart, gauge, button…)
- **node-red-node-email**: invio email via SMTP (Mailhog)

> La versione MQTT installa gli stessi plugin. L'unica differenza è che qui **non serve** `node-red-contrib-mqtt-broker` perché non c'è un broker.

---

## `data/settings.js`

```javascript
module.exports = {
    uiPort: 1880,
    userDir: '/data',
    flowFile: 'flows.json',
    credentialSecret: false,   // ← IMPORTANTE: non cifra le credenziali
};
```

`credentialSecret: false` è necessario per caricare il token InfluxDB da `flows_cred.json` senza cifratura. **Non usarlo in produzione.**

---

## `data/flows_cred.json`

```json
{"influxdb-cfg": {"token": "my-super-token"}}
```

Contiene il token di autenticazione InfluxDB. Node-RED lo abbina al nodo config `influxdb-cfg` nei flows.

---

## `data/flows.json` — I 3 flussi

### Tab 1: Dashboard (polling InfluxDB)

```
inject (ogni 5s)
    │
    ▼
influxdb-in (Flux query: ultimi 5 minuti)
    │  restituisce array di punti
    ▼
function "Formatta per grafico"
    │  invia msg.topic + msg.payload (numero) + msg.timestamp (ms)
    │  per ogni punto
    ▼
ui_chart (grafico lineare multi-serie)
```

**Regola critica per `ui_chart` con `node-red-dashboard@3.x`:**
```javascript
// ✅ FUNZIONA: payload numero puro + timestamp separato
node.send({
    topic:     "sensor-03",
    payload:   22.4,            // numero puro
    timestamp: 1748000000000    // millisecondi
});

// ❌ NON FUNZIONA: oggetto {x, y}
node.send({
    topic:   "sensor-03",
    payload: { x: 1748000000000, y: 22.4 }   // il chart ignora questo formato
});
```

**Query Flux usata:**
```flux
from(bucket: "iot")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "temperatura" and r._field == "value")
```

### Tab 2: Alerting

```
inject (ogni 30s)
    │
    ▼
influxdb-in (ultimo valore per sensore negli ultimi 35s)
    │
    ▼
function "Controlla soglie"
    │  > 30°C → alert ALTA
    │  < 16°C → alert BASSA
    ▼
e-mail (mailhog:1025)
```

### Tab 3: Chaos Events (HTTP invece di MQTT)

```
http in (POST /chaos/events)  ← riceve da chaos.sh via curl
    │
    ▼
function "Formatta email chaos"
    │  DROP → spiega cosa osservare
    │  RESTORE → spiega cosa verificare
    ▼
[e-mail]  [debug]  [http response 200]
```

Questa è la grande differenza dalla versione MQTT:
- **MQTT**: chaos inviava su topic `iot/chaos/events` con `mosquitto_pub`
- **CoAP**: chaos fa `curl -X POST http://node-red:1880/chaos/events`

---

## Come leggere i log

```bash
docker compose logs -f nodered

# Errori comuni
docker compose logs nodered | grep -i "error\|influx\|failed"
```

---

## Esempi pratici — Come modificare Node-RED

### 1. Cambiare l'intervallo di polling

Nel tab **Dashboard**, clicca sul nodo `inject` "Ogni 5s" → campo **Repeat** → cambia da `5` a `2` secondi.

In alternativa, modifica direttamente `flows.json`:
```json
{
    "id": "n-inject-poll",
    "type": "inject",
    "repeat": "2"   ← era "5"
}
```

Poi applica al container:
```bash
docker cp nodered/data/flows.json node-red:/data/flows.json
docker restart node-red
```

> **Tradeoff:** polling ogni 2s → più dati visualizzati ma più carico su InfluxDB (1 query HTTP ogni 2s invece di ogni 5s).

---

### 2. Cambiare le soglie di alerting

Apri l'editor Node-RED su http://localhost:1880, tab **Alerting**, doppio click sulla function **"Controlla soglie"**:

```javascript
var SOGLIA_ALTA  = 30;   // °C ← cambia qui
var SOGLIA_BASSA = 16;   // °C ← cambia qui
```

Clicca **Done** e poi il pulsante rosso **Deploy** in alto a destra.

---

### 3. Aggiungere un gauge per ogni sensore

Attualmente c'è un solo grafico multi-serie. Per aggiungere un gauge individuale:

1. Apri l'editor: http://localhost:1880
2. Tab **Dashboard**
3. Trascina un nodo **ui_gauge** dalla palette sinistra
4. Collegalo all'uscita della function "Formatta per grafico"
5. Nel nodo gauge: Group = "Temperature Sensori", min=10, max=40
6. Deploy

La function già invia un messaggio per ogni sensore — il gauge mostrerà l'ultimo sensore ricevuto (o puoi aggiungere un **switch** per filtrare per sensor_id).

---

### 4. Aggiungere una query storica (ultimi 30 minuti)

Aggiungi un secondo flusso nel tab Dashboard:

```
inject (ogni 60s) → influxdb-in (query) → function → ui_chart-storico
```

Con query Flux:
```flux
from(bucket: "iot")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "temperatura" and r._field == "value")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
```

Questo mostra la media per minuto degli ultimi 30 minuti — utile per vedere i trend.

---

### 5. Aggiungere un pulsante per resettare il grafico

1. Trascina un nodo **ui_button** nel tab Dashboard
2. Collegalo al nodo `ui_chart`
3. Configura il button: `msg.payload = []` e `msg.topic = ""`

Quando clicchi il pulsante, il chart si azzera. Il polling ricomincia a riempirlo dopo 5s.

---

### 6. Aggiungere un plugin Node-RED

Esempio: aggiungere `node-red-contrib-coap` per ricevere dati CoAP direttamente (senza polling InfluxDB):

Nel `Dockerfile`:
```dockerfile
RUN npm install --prefix /usr/src/node-red \
    node-red-contrib-influxdb \
    node-red-dashboard \
    node-red-node-email \
    node-red-contrib-coap          ← aggiunto
```

Ricostruisci:
```bash
docker compose build nodered --no-cache
docker compose up -d --force-recreate nodered
```

---

### 7. Applicare flows.json modificati al container in esecuzione

Dopo aver modificato `flows.json` manualmente:
```bash
# Copia il file nel container
docker cp nodered/data/flows.json node-red:/data/flows.json

# Riavvia Node-RED per caricare i nuovi flows
docker restart node-red
```

Oppure usa l'editor visuale (http://localhost:1880) — più sicuro perché valida il JSON.

---

### 8. Modificare il recipient delle email di alert

Nella function **"Controlla soglie"** (tab Alerting):
```javascript
msg.to   = "docente@scuola.it";        // ← cambia destinatario
msg.from = "iot-alert@lab.local";      // ← mittente
```

E/o nel nodo **e-mail** (doppio click):
- **To**: indirizzo default (può essere sovrascritto dalla function)
- **Server**: `mailhog` (container name, non cambiare)
- **Port**: `1025` (porta interna, non cambiare)
- **Auth**: None (Mailhog non richiede autenticazione)

---

## Troubleshooting

### Il grafico non mostra dati

1. Verifica che InfluxDB contenga dati: http://localhost:8086
2. Nel debug panel di Node-RED (icona bug in alto a destra), guarda cosa esce dalla function
3. Controlla che il nodo `influxdb-in` abbia il pallino **verde** (configurato correttamente)
4. Riavvia: `docker compose restart nodered`

### "Error: 401 Unauthorized" da InfluxDB

Il token è sbagliato. Verifica `flows_cred.json`:
```json
{"influxdb-cfg": {"token": "my-super-token"}}
```
E che corrisponda a `INFLUX_TOKEN` nel `.env`.

Applica:
```bash
docker cp nodered/data/flows_cred.json node-red:/data/flows_cred.json
docker restart node-red
```

### Le email non arrivano in Mailhog

Controlla che il nodo **e-mail** abbia:
- **Server**: `mailhog` (nome container Docker, non `localhost`)
- **Port**: `1025` (non 4025 che è la porta host)
- **Auth**: None / NONE

Poi verifica Mailhog: http://localhost:5025

### Il tab "Chaos Events" non riceve eventi

Verifica che il chaos monkey sia in esecuzione e raggiunga Node-RED:
```bash
docker compose logs chaos | tail -20

# Test manuale
docker exec network-chaos curl -s -X POST \
  -H "Content-Type: application/json" \
  -d '{"event_type":"test","container":"test","phase":"DROP","duration":5,"timestamp":0,"event_count":0}' \
  http://node-red:1880/chaos/events
```
