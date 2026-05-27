# FROM: immagine base ufficiale Node-RED (Node.js + Node-RED pre-installati e configurati)
# 'latest' = ultima versione stabile disponibile su Docker Hub
# In produzione è preferibile una versione fissa (es. nodered/node-red:3.1.9) per build riproducibili
FROM nodered/node-red:latest

# RUN npm install: installa moduli Node-RED aggiuntivi nell'immagine durante la build
# 'npm' = Node Package Manager (equivalente di 'pip' per Python, 'apk' per Alpine)
# Il backslash '\' a fine riga continua il comando sulla riga successiva (solo per leggibilità)
# Docker esegue tutto come un unico comando: npm install pacchetto1 pacchetto2 pacchetto3
#
# Moduli installati:
#   node-red-contrib-influxdb → nodo per leggere/scrivere su InfluxDB v2 con query Flux
#   node-red-dashboard        → aggiunge nodi UI al browser: grafici (ui_chart), gauge, testo, ecc.
#   node-red-node-email       → nodo per inviare email via SMTP (configurato su Mailhog in sviluppo)
RUN npm install \
    node-red-contrib-influxdb \
    node-red-dashboard \
    node-red-node-email

# Nessun WORKDIR, COPY o CMD espliciti perché l'immagine base li gestisce già:
#   WORKDIR = /data (dove Node-RED cerca i flussi e la configurazione)
#   CMD     = ["node-red"] (avvia Node-RED automaticamente)
#
# I flussi e la configurazione vengono montati dall'host tramite il volume in docker-compose.yml:
#   ./nodered/data:/data   (bind mount → flows.json, flows_cred.json, settings.js)
# Questo significa che i flussi creati nell'editor web persistono anche se il container viene ricreato
