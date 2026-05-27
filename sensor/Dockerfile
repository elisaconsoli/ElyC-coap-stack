# Stessa struttura del Dockerfile del gateway (entrambi sono app Python con aiocoap)
# 'python:3.11-slim' = Debian minimale con Python 3.11 preinstallato
# Versione fissa (3.11, non 'latest') → build riproducibili: non cambia tra un build e l'altro
FROM python:3.11-slim

# Imposta /app come directory di lavoro dentro il container
# Tutti i COPY, RUN, CMD successivi operano da qui
WORKDIR /app

# Prima copia solo requirements.txt — ottimizzazione cache Docker:
# Se sensor.py cambia ma requirements.txt no → pip install non viene rieseguito (usa il layer in cache)
# Questo riduce significativamente il tempo di build durante lo sviluppo
COPY requirements.txt .

# Installa le librerie Python dall'elenco in requirements.txt
# Libreria principale: aiocoap → implementa il protocollo CoAP su UDP in modo asincrono
# '--no-cache-dir' → non salva la cache di pip nell'immagine → immagine finale più piccola
RUN pip install --no-cache-dir -r requirements.txt

# Ora copia il codice sorgente (DOPO pip install per sfruttare la cache)
# sensor.py viene copiato in /app/sensor.py (WORKDIR)
COPY sensor.py .

# Avvia sensor.py all'avvio del container
# Forma EXEC (array): il processo Python è PID 1 → riceve correttamente SIGTERM (docker stop)
# '-u' = unbuffered: i print() di sensor.py (TX, OFFLINE, BUFFER...) appaiono subito in 'docker logs'
CMD ["python", "-u", "sensor.py"]
