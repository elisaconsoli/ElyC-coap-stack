# FROM: specifica l'immagine base per questo container
# 'python:3.11-slim' = immagine ufficiale Python 3.11 nella variante 'slim'
# 'slim' = Debian minimale: rimuove compilatori, documentazione, locale data inutili
# Più pesante di 'alpine' ma più compatibile (alpine usa musl libc invece di glibc)
# Alternativa: python:3.11-alpine (più piccola ma a volte problemi con librerie C come aiocoap)
FROM python:3.11-slim

# WORKDIR: imposta la directory di lavoro corrente dentro il container
# Tutti i comandi successivi (COPY, RUN, CMD) vengono eseguiti DA questa directory
# Se la directory non esiste, Docker la crea automaticamente
# Buona pratica: usare /app invece della root / per evitare conflitti con file di sistema
WORKDIR /app

# COPY prima solo requirements.txt, poi il codice — tecnica di ottimizzazione cache Docker:
# Docker salva ogni istruzione come un "layer". Se requirements.txt non cambia,
# il layer successivo (RUN pip install) viene riutilizzato dalla cache → build molto più veloci.
# Se copiassimo tutto insieme (COPY . .) e cambiassimo gateway.py, pip install girerebbe di nuovo.
# '.' come destinazione = WORKDIR corrente (= /app/requirements.txt)
COPY requirements.txt .

# RUN pip install: installa le dipendenze Python elencate in requirements.txt
# 'pip' = Python package installer (gestore pacchetti Python, come npm per Node.js)
# '--no-cache-dir' = non salva la cache di pip nell'immagine → riduce la dimensione finale
# '-r requirements.txt' = legge la lista di pacchetti dal file (uno per riga)
# Pacchetti installati: aiocoap (server/client CoAP async), influxdb-client (InfluxDB v2 API)
RUN pip install --no-cache-dir -r requirements.txt

# COPY: copia il codice sorgente DOPO aver installato le dipendenze
# Così se modifichi solo gateway.py, il layer RUN pip install viene preso dalla cache (nessuna reinstallazione)
# Questo è il principale pattern di ottimizzazione nei Dockerfile Python
COPY gateway.py .

# CMD: comando eseguito all'avvio del container (sovrascrivibile con 'docker run ... <comando>')
# Forma EXEC con array JSON → il processo Python diventa PID 1 (riceve SIGTERM correttamente)
# 'python' = interprete Python (aggiunto al PATH dall'immagine base)
# '-u' = unbuffered output: disabilita il buffer di stdout/stderr
#        Senza -u, i print() in Python vengono bufferizzati → i log Docker appaiono in ritardo
#        Con -u, ogni print() appare immediatamente in 'docker logs coap-gateway'
# 'gateway.py' = file da eseguire (cercato in WORKDIR = /app)
CMD ["python", "-u", "gateway.py"]
