#!/usr/bin/env bash
# =============================================================
# chaos.sh — Simulatore di guasti di rete per CoAP Stack
# Progetto didattico ITS 3° anno
#
# Differenza rispetto alla versione MQTT:
#   - Non usa mosquitto_pub (niente broker MQTT)
#   - Invia eventi chaos via HTTP POST a Node-RED
#   - Disconnette/riconnette container Docker dalla rete UDP (CoAP)
#
# Reti gestite:
#   coap_sensor_net  → tra sensori e gateway CoAP
#   coap_core_net    → tra gateway, InfluxDB, Node-RED, Mailhog
#
# Variabili d'ambiente:
#   CHAOS_ENABLED     = true|false
#   DROP_INTERVAL     = secondi tra un evento e l'altro (default: 90)
#   DROP_DURATION     = secondi di interruzione (default: 20)
#   SENSOR_NET        = nome rete sensori (default: coap_sensor_net)
#   CORE_NET          = nome rete core (default: coap_core_net)
# =============================================================

set -euo pipefail

# ── Configurazione ────────────────────────────────────────────
CHAOS_ENABLED="${CHAOS_ENABLED:-true}"
DROP_INTERVAL="${DROP_INTERVAL:-90}"
DROP_DURATION="${DROP_DURATION:-20}"
SENSOR_NET="${SENSOR_NET:-coap_sensor_net}"
CORE_NET="${CORE_NET:-coap_core_net}"
NODERED_URL="http://node-red:1880/chaos/events"
WARMUP=45  # secondi di attesa iniziale prima del primo evento

# Sensori disponibili (container 01..10)
SENSORS=(
    "sensor-01" "sensor-02" "sensor-03" "sensor-04" "sensor-05"
    "sensor-06" "sensor-07" "sensor-08" "sensor-09" "sensor-10"
)

log() {
    echo "[CHAOS] $(date '+%Y-%m-%d %H:%M:%S') $*"
}

# ── Attendi che Docker daemon sia disponibile ─────────────────
wait_for_docker() {
    log "Attendo Docker daemon..."
    local max=30
    local count=0
    while ! docker info >/dev/null 2>&1; do
        count=$((count + 1))
        if [ $count -ge $max ]; then
            log "ERRORE: Docker non disponibile dopo ${max} tentativi. Uscita."
            exit 1
        fi
        log "Docker non pronto, riprovo tra 3s... ($count/$max)"
        sleep 3
    done
    log "Docker daemon disponibile."
}

# ── Disconnetti un container da una rete ─────────────────────
network_disconnect() {
    local container="$1"
    local network="$2"
    log "DISCONNECT: $container dalla rete $network"
    docker network disconnect "$network" "$container" 2>/dev/null || {
        log "WARN: impossibile disconnettere $container da $network (già disconnesso?)"
    }
}

# ── Riconnetti un container a una rete ───────────────────────
network_connect() {
    local container="$1"
    local network="$2"
    log "CONNECT: $container alla rete $network"
    docker network connect "$network" "$container" 2>/dev/null || {
        log "WARN: impossibile riconnettere $container a $network (già connesso?)"
    }
}

# ── Pubblica evento chaos su Node-RED via HTTP POST ───────────
publish_chaos_event() {
    local event_type="$1"
    local container="$2"
    local phase="$3"  # DROP o RESTORE

    local payload
    payload=$(printf '{"event_type":"%s","container":"%s","phase":"%s","duration":%s,"ts":"%s"}' \
        "$event_type" "$container" "$phase" "$DROP_DURATION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)")

    log "Invio evento chaos → Node-RED: $phase / $event_type / $container"
    curl -s -X POST \
         -H "Content-Type: application/json" \
         -d "$payload" \
         "$NODERED_URL" \
         --max-time 5 \
         >/dev/null 2>&1 || {
        log "WARN: Node-RED non raggiungibile, evento non notificato"
    }
}

# ── Evento: disconnetti un singolo sensore ────────────────────
event_drop_single_sensor() {
    local idx=$(( RANDOM % ${#SENSORS[@]} ))
    local sensor="${SENSORS[$idx]}"

    log "=== EVENTO: drop_single_sensor → $sensor ==="
    publish_chaos_event "drop_single_sensor" "$sensor" "DROP"
    network_disconnect "$sensor" "$SENSOR_NET"

    log "Sensore $sensor disconnesso per ${DROP_DURATION}s"
    log "  → Osserva [OFFLINE] nei log: docker compose logs -f $sensor"
    sleep "$DROP_DURATION"

    network_connect "$sensor" "$SENSOR_NET"
    publish_chaos_event "drop_single_sensor" "$sensor" "RESTORE"
    log "=== RIPRISTINO: $sensor riconnesso ==="
    log "  → Osserva [BUFFER] nei log: docker compose logs -f $sensor"
}

# ── Evento: disconnetti 3 sensori casuali ─────────────────────
event_drop_multi_sensor() {
    # Seleziona 3 sensori casuali (senza ripetizioni)
    local shuffled=( $(printf '%s\n' "${SENSORS[@]}" | shuf | head -3) )

    log "=== EVENTO: drop_multi_sensor → ${shuffled[*]} ==="

    for sensor in "${shuffled[@]}"; do
        publish_chaos_event "drop_multi_sensor" "$sensor" "DROP"
        network_disconnect "$sensor" "$SENSOR_NET"
    done

    log "${#shuffled[@]} sensori disconnessi per ${DROP_DURATION}s"
    sleep "$DROP_DURATION"

    for sensor in "${shuffled[@]}"; do
        network_connect "$sensor" "$SENSOR_NET"
        publish_chaos_event "drop_multi_sensor" "$sensor" "RESTORE"
    done
    log "=== RIPRISTINO: sensori multipli riconnessi ==="
}

# ── Evento: disconnetti il gateway dalla rete sensori ─────────
# Simula il gateway irraggiungibile: TUTTI i sensori vanno in buffer
event_drop_gateway() {
    log "=== EVENTO: drop_gateway → coap-gateway da $SENSOR_NET ==="
    publish_chaos_event "drop_gateway" "coap-gateway" "DROP"
    network_disconnect "coap-gateway" "$SENSOR_NET"

    log "Gateway disconnesso dalla sensor_net per ${DROP_DURATION}s"
    log "  → TUTTI i sensori vanno in [OFFLINE] e accumulano nel buffer locale"
    log "  → Il gateway NON riceve i POST CoAP"
    sleep "$DROP_DURATION"

    network_connect "coap-gateway" "$SENSOR_NET"
    publish_chaos_event "drop_gateway" "coap-gateway" "RESTORE"
    log "=== RIPRISTINO: gateway riconnesso alla sensor_net ==="
    log "  → Osserva il burst di messaggi [BUFFER] su TUTTI i sensori"
}

# ── Evento: disconnetti Node-RED dalla core_net ───────────────
event_drop_nodered() {
    log "=== EVENTO: drop_nodered → node-red da $CORE_NET ==="
    publish_chaos_event "drop_nodered" "node-red" "DROP"

    # Nota: Node-RED viene disconnesso DOPO aver inviato l'evento
    # altrimenti l'evento non arriverebbe!
    sleep 1
    network_disconnect "node-red" "$CORE_NET"

    log "Node-RED disconnesso per ${DROP_DURATION}s"
    log "  → I dati continuano ad arrivare in InfluxDB tramite il gateway"
    log "  → La dashboard non si aggiorna"
    sleep "$DROP_DURATION"

    network_connect "node-red" "$CORE_NET"
    sleep 2  # aspetta che Node-RED si riconnetta prima di notificare
    publish_chaos_event "drop_nodered" "node-red" "RESTORE"
    log "=== RIPRISTINO: Node-RED riconnesso ==="
}

# ── Loop principale ───────────────────────────────────────────
main() {
    log "Chaos engine avviato"
    log "  CHAOS_ENABLED = $CHAOS_ENABLED"
    log "  DROP_INTERVAL = ${DROP_INTERVAL}s"
    log "  DROP_DURATION = ${DROP_DURATION}s"
    log "  SENSOR_NET    = $SENSOR_NET"
    log "  CORE_NET      = $CORE_NET"
    log "  NODERED_URL   = $NODERED_URL"

    if [ "$CHAOS_ENABLED" != "true" ]; then
        log "Chaos DISABILITATO (CHAOS_ENABLED!=true). Sleep infinito."
        while true; do sleep 3600; done
    fi

    wait_for_docker

    log "Warmup: attendo ${WARMUP}s prima del primo evento..."
    log "  (Dà tempo ai container di avviarsi e stabilizzarsi)"
    sleep "$WARMUP"

    # Elenco degli eventi disponibili
    EVENTS=(
        "event_drop_single_sensor"
        "event_drop_single_sensor"   # più probabilità = evento più comune
        "event_drop_multi_sensor"
        "event_drop_gateway"
        "event_drop_nodered"
    )

    local cycle=0
    while true; do
        cycle=$((cycle + 1))
        log "--- Ciclo chaos #${cycle} ---"

        # Seleziona evento casuale
        local idx=$(( RANDOM % ${#EVENTS[@]} ))
        local event_fn="${EVENTS[$idx]}"

        log "Esecuzione evento: $event_fn"
        $event_fn || log "WARN: evento $event_fn fallito (container non trovato?)"

        log "Prossimo evento tra ${DROP_INTERVAL}s"
        sleep "$DROP_INTERVAL"
    done
}

main "$@"
