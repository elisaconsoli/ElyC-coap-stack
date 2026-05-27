# FROM: prima istruzione obbligatoria di ogni Dockerfile
# Specifica l'immagine base da cui partire (scaricata da Docker Hub se non in cache)
# 'docker:cli' = immagine ufficiale minimale con SOLO il client Docker (nessun daemon)
# Serve perché chaos.sh usa 'docker network disconnect' per simulare guasti di rete
FROM docker:cli

# RUN: esegue un comando durante la BUILD dell'immagine (crea un nuovo layer nello strato filesystem)
# Tutto ciò che RUN fa viene "fotografato" nell'immagine: i pacchetti installati ci saranno a runtime
# 'apk' = Alpine Package Keeper, il gestore di pacchetti di Alpine Linux (su cui si basa docker:cli)
# '--no-cache' = non salva la cache del repository nell'immagine → immagine più piccola (best practice)
# 'bash' = shell avanzata, necessaria per chaos.sh che usa: array, funzioni, set -euo pipefail
# 'curl' = client HTTP da riga di comando, usato per inviare notifiche a Node-RED via HTTP POST
RUN apk add --no-cache bash curl

# COPY: copia un file/directory dal contesto di build (cartella dell'host) nel filesystem del container
# Argomento 1 (sorgente): 'chaos.sh' → relativo alla cartella dove si trova questo Dockerfile (./chaos/)
# Argomento 2 (destinazione): '/usr/local/bin/chaos.sh' → dentro il container
# '/usr/local/bin' è nella variabile PATH del container → lo script è invocabile senza percorso completo
COPY chaos.sh /usr/local/bin/chaos.sh

# RUN chmod: rende eseguibile lo script copiato
# I file copiati con COPY mantengono i permessi originali dell'host, ma non sempre hanno il bit +x
# 'chmod +x' = aggiunge il permesso di esecuzione (execute) per tutti gli utenti (owner, group, others)
RUN chmod +x /usr/local/bin/chaos.sh

# CMD: specifica il comando di DEFAULT eseguito quando il container viene AVVIATO (a runtime, non in build)
# Forma EXEC (array JSON ["eseguibile", "arg1", ...]): preferita rispetto alla forma shell "CMD script.sh"
# Motivo: con la forma EXEC il processo diventa PID 1 → riceve correttamente SIGTERM (es. docker stop)
# Con la forma shell, sarebbe: /bin/sh -c "/usr/local/bin/chaos.sh" → PID 1 è sh, non il nostro script
CMD ["/usr/local/bin/chaos.sh"]
