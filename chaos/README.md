# Cartella `chaos/` — Il Chaos Monkey (versione CoAP)

## Cos'è questa cartella?

Qui vive il **Chaos Monkey**: lo strumento che simula guasti di rete in modo controllato e automatico.

Il principio è lo stesso della versione MQTT — disconnettere container dalle reti Docker per simulare interruzioni realistiche — ma con due differenze chiave:

| Aspetto | MQTT Chaos | CoAP Chaos |
|---|---|---|
| **Notifiche eventi** | `mosquitto_pub` → topic MQTT | `curl` → HTTP POST a Node-RED |
| **Drop gateway** | Disconnette `mqtt-gateway` da `core_net` | Disconnette `coap-gateway` da `coap_sensor_net` |
| **Dipendenze** | mosquitto-clients (per `mosquitto_pub`) | curl (pre-installato in Alpine) |

---

## File nella cartella

```
chaos/
├── chaos.sh       ← Script bash con tutta la logica di simulazione
├── Dockerfile     ← docker:cli + bash + curl
└── README.md      ← Questo file
```

---

## `Dockerfile`

```dockerfile
FROM docker:cli                    # contiene Docker CLI per network disconnect/connect
RUN apk add --no-cache bash curl   # bash per lo script, curl per le notifiche HTTP
COPY chaos.sh /usr/local/bin/chaos.sh
RUN chmod +x /usr/local/bin/chaos.sh
CMD ["/usr/local/bin/chaos.sh"]
```

**Perché `curl` invece di `mosquitto_pub`?**  
In CoAP non c'è un broker MQTT. Le notifiche degli eventi di chaos vengono inviate direttamente a Node-RED tramite HTTP POST sull'endpoint `/chaos/events` (un nodo `http in` configurato nel flow "Chaos Events").

---

## `chaos.sh` — Come funziona

### Il loop principale

```
Attesa iniziale 45s (stack si stabilizza)
│
└─ Loop infinito:
      │
      ├─ Aspetta tempo casuale (~45–135s)
      │
      ├─ Sceglie evento casuale:
      │     60% → caduta sensore singolo
      │     20% → caduta due sensori simultanei
      │     20% → caduta coap-gateway (lato sensori)
      │     12% → caduta Node-RED (rara)
      │
      ├─ curl POST → node-red:1880/chaos/events (fase DROP)
      │
      ├─ docker network disconnect
      │
      ├─ Aspetta DROP_DURATION secondi
      │
      ├─ docker network connect
      │
      └─ curl POST → node-red:1880/chaos/events (fase RESTORE)
```

### Notifiche HTTP

Il payload inviato a Node-RED:
```json
{
    "event_type":  "sensor_single",
    "container":   "sensor-05",
    "phase":       "DROP",
    "duration":    20,
    "timestamp":   1748000000,
    "event_count": 7
}
```

Node-RED riceve questo JSON, lo formatta in un'email e la invia a Mailhog.

### I quattro tipi di evento

| Evento | Container | Rete | Effetto |
|---|---|---|---|
| `drop_single_sensor` | un sensore | `coap_sensor_net` | Un sensore non raggiunge il gateway; attiva il buffer locale |
| `drop_multi_sensor` | due sensori | `coap_sensor_net` | Due sensori offline simultanei |
| `drop_gateway` | `coap-gateway` | `coap_sensor_net` | Tutti i sensori vedono timeout CON; buffer locale attivo su tutti |
| `drop_nodered` | `node-red` | `coap_core_net` | Dashboard non si aggiorna (polling fallisce) |

> **Nota:** il drop del gateway in CoAP è più "drammatico" che in MQTT. In MQTT, se Telegraf cadeva, Mosquitto continuava a raccogliere i dati (broker sempre acceso). In CoAP, se il gateway cade dalla `coap_sensor_net`, **nessun sensore può inviare dati** finché non torna online.

---

## Configurazione tramite variabili d'ambiente

| Variabile | Default | Descrizione |
|---|---|---|
| `CHAOS_ENABLED` | `true` | `false` = container attivo ma nessun evento |
| `DROP_INTERVAL` | `90` | Secondi di riferimento tra eventi |
| `DROP_DURATION` | `20` | Durata della disconnessione in secondi |
| `SENSOR_NET` | `coap_sensor_net` | Rete Docker dei sensori |
| `CORE_NET` | `coap_core_net` | Rete Docker dei servizi |

---

## Come leggere i log

```bash
docker compose logs -f chaos
```

Output tipico:
```
[CHAOS] 2026-05-21 10:30:00 Prossimo evento #3 tra 112s
[CHAOS] 2026-05-21 10:31:52 ════════════════════════════════
[CHAOS] 2026-05-21 10:31:52 EVENTO #3: drop_gateway — coap-gateway da coap_sensor_net
[CHAOS] 2026-05-21 10:31:52   Durata: 20s
[CHAOS] 2026-05-21 10:31:52   [HTTP] Notifica DROP inviata a Node-RED
[CHAOS] 2026-05-21 10:31:52   ✗ coap-gateway disconnesso da coap_sensor_net
[CHAOS] 2026-05-21 10:32:12   ✓ coap-gateway riconnesso a coap_sensor_net
[CHAOS] 2026-05-21 10:32:12   [HTTP] Notifica RESTORE inviata a Node-RED
[CHAOS] 2026-05-21 10:32:12 ════════════════════════════════
```

---

## Esempi pratici — Come modificare il Chaos Monkey

### 1. Disabilitare il chaos temporaneamente

Nel file `.env`:
```env
CHAOS_ENABLED=false
```

Applica:
```bash
docker compose up -d --force-recreate chaos
```

Il container rimane in esecuzione (stampa un messaggio ogni ora) ma non genera eventi.

---

### 2. Rendere gli eventi più frequenti e più brevi (demo intensiva)

```env
CHAOS_DROP_INTERVAL=20    # evento ogni ~10–30s invece di 45–135s
CHAOS_DROP_DURATION=5     # disconnessione 5s invece di 20s
```

Riavvia:
```bash
docker compose up -d --force-recreate chaos
```

---

### 3. Rendere i drop più lunghi (test di resilienza estesa)

```env
CHAOS_DROP_INTERVAL=300    # evento ogni ~150–450s
CHAOS_DROP_DURATION=60     # 60s di disconnessione
```

Con 60s di drop del gateway: tutti i sensori accumuleranno nel buffer locale `60/5 = 12` messaggi ciascuno. Al ripristino, il gateway riceverà un burst di `10 × 12 = 120` messaggi in pochi secondi.

---

### 4. Simulare manualmente un evento (senza aspettare il chaos)

```bash
# Disconnetti manualmente un sensore
docker network disconnect coap_sensor_net sensor-05

# Osserva i log del sensore
docker compose logs -f sensor05   # vedi [OFFLINE] dopo ~15s

# Riconnetti
docker network connect coap_sensor_net sensor-05

# Verifica il flush del buffer
docker compose logs sensor05 | tail -10   # vedi [BUFFER]
```

Drop del gateway (tutti i sensori vedono timeout):
```bash
docker network disconnect coap_sensor_net coap-gateway
# ... tutti i sensori vanno in [OFFLINE] ...
docker network connect coap_sensor_net coap-gateway
# ... burst di dati dal buffer di ogni sensore ...
```

---

### 5. Aggiungere un nuovo tipo di evento: riavvio InfluxDB

Modifica `chaos.sh` aggiungendo una nuova funzione:

```bash
# Aggiungi in chaos.sh dopo le funzioni esistenti:
drop_influxdb() {
    log "═══════════════════════════════════════"
    log "EVENTO #${event_count}: Riavvio InfluxDB"
    log "  Durata: ${DROP_DURATION}s"
    log "  Il gateway accumula nel buffer interno"

    publish_chaos_event "influxdb_restart" "influxdb" "DROP"

    docker stop influxdb 2>/dev/null && log "  ✗ InfluxDB fermato"
    log "  [Osserva] Gateway log: batch write fallisce, dati in buffer"

    sleep "$DROP_DURATION"

    docker start influxdb 2>/dev/null && log "  ✓ InfluxDB riavviato"
    log "  [Osserva] Gateway log: batch write riprende, buffer svuotato"

    publish_chaos_event "influxdb_restart" "influxdb" "RESTORE"
    log "═══════════════════════════════════════"
}
```

Aggiorna il `case` nel loop principale:
```bash
case "$event_type" in
    0|1|2) drop_single_sensor ;;
    3|4)   drop_multi_sensor ;;
    5|6)   drop_gateway ;;
    7)     drop_nodered ;;
    # Decommenta per aggiungere:
    # 8)   drop_influxdb ;;
esac
```

E cambia `$((RANDOM % 8))` in `$((RANDOM % 9))`.

Ricostruisci l'immagine:
```bash
docker compose build chaos --no-cache
docker compose up -d --force-recreate chaos
```

---

### 6. Cambiare l'endpoint di notifica

Se volessi notificare un sistema esterno invece di Node-RED:

In `chaos.sh`, la funzione `publish_chaos_event`:
```bash
publish_chaos_event() {
    local payload=$(printf \
        '{"event_type":"%s","container":"%s","phase":"%s","duration":%s,"timestamp":%s,"event_count":%s}' \
        "$1" "$2" "$3" "$DROP_DURATION" "$(date +%s)" "$event_count")

    # Notifica Node-RED (attuale)
    curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "http://node-red:1880/chaos/events" 2>/dev/null

    # Puoi aggiungere altri endpoint:
    # curl -s -X POST http://mio-webhook.example.com/alert -d "$payload"
    # curl -s -X POST http://slack-webhook-url -d "{\"text\":\"$payload\"}"
}
```

---

### 7. Escludere sensori specifici dagli eventi chaos

Modifica l'array `SENSORS` in `chaos.sh`:

```bash
SENSORS=(
    # "sensor-01"   ← commentato: non verrà mai toccato
    "sensor-02" "sensor-03" "sensor-04" "sensor-05"
    "sensor-06" "sensor-07" "sensor-08" "sensor-09" "sensor-10"
)
```

---

### 8. Limitare il chaos a orari specifici (es. solo durante le lezioni)

Aggiungi in `chaos.sh` all'inizio del loop:

```bash
while true; do
    # Chaos attivo solo tra le 09:00 e le 17:00
    ora=$(date +%H)
    if [ "$ora" -lt 9 ] || [ "$ora" -ge 17 ]; then
        log "Fuori orario (${ora}:xx). Chaos in pausa. Riprendo tra 5 minuti..."
        sleep 300
        continue
    fi

    event_count=$((event_count + 1))
    # ... resto del loop
done
```

---

## Differenza rispetto alla versione MQTT

### Come vengono inviate le notifiche

**MQTT:**
```bash
mosquitto_pub \
  -h mosquitto -p 1883 \
  -t "iot/chaos/events" \
  -m "$payload" \
  -q 1
```

**CoAP (questo progetto):**
```bash
curl -s -X POST \
  -H "Content-Type: application/json" \
  -d "$payload" \
  "http://node-red:1880/chaos/events"
```

In CoAP non c'è broker, quindi le notifiche usano HTTP diretto verso Node-RED. Se Node-RED è offline, la notifica viene persa (ma il chaos event avviene comunque).

### Effetto del drop gateway

**MQTT:** Se Telegraf (`mqtt-gateway`) cade, i sensori continuano a pubblicare su Mosquitto che accoda. I dati non si perdono.

**CoAP:** Se `coap-gateway` cade dalla `coap_sensor_net`, **nessun sensore può consegnare i dati**. Tutti attivano il buffer locale. Al ripristino, il gateway riceve un burst massiccio da tutti i sensori simultaneamente.

---

## Domande frequenti

**Q: Il chaos ha accesso al Docker socket. È sicuro?**  
A: Nel contesto di un laboratorio locale è accettabile. In produzione, dare accesso al socket Docker è come dare i permessi di root sull'host. Strumenti come **LitmusChaos** o **Chaos Toolkit** offrono alternative più sicure per ambienti Kubernetes.

**Q: Cosa succede se il chaos container si riavvia durante un DROP?**  
A: Il container che era stato disconnesso rimane disconnesso (nessuno lo riconnette). Bisogna farlo manualmente:
```bash
docker network connect coap_sensor_net sensor-07   # o il container bloccato
```

**Q: Posso usare questo chaos in produzione?**  
A: Solo con molta cautela. La **Chaos Engineering** disciplinata richiede: definire uno "steady state" da monitorare, limitare il "blast radius" (quanti sistemi colpire), avere un rollback immediato. Per iniziare, studia i principi su **principlesofchaos.org**.
