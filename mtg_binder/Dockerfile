# Schlankes Abbild: Python 3.12 mit Starlette und Uvicorn, sonst nur der Server-Teil von binderapp (der Code bleibt
# 3.11-tauglich). 3.12, weil Silhouette Card Maker ab v3.0.0 mindestens 3.12 verlangt; seine Pakete kommen nicht ins
# Abbild, sondern je Stand in ein eigenes venv unter /data/proxy (binderapp/proxy/staende.py).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY requirements.txt /app/requirements.txt
# Vor ARG BUILD_VERSION, damit die Pakete im Zwischenspeicher bleiben: Ein ARG davor zwingt jeden RUN nach einem
# Versionssprung neu zu laufen (Update 0.32.0 am 08.11.2026 scheiterte an einer Zeitüberschreitung bei PyPI).
# Wiederholungen und längere Wartezeit fangen ein langsames Netz ab.
RUN pip install --no-cache-dir --retries 10 --timeout 60 -r /app/requirements.txt

ARG BUILD_VERSION=0.0.0
ENV BINDER_VERSION=${BUILD_VERSION}
COPY binderapp /app/binderapp

CMD ["python", "-m", "binderapp.addon"]
