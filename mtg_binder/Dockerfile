# Schlankes Abbild: Python 3.12 mit Starlette und Uvicorn, sonst nur der Server-Teil von binderapp (der Code bleibt
# 3.11-tauglich). 3.12, weil Silhouette Card Maker ab v3.0.0 mindestens 3.12 verlangt; seine Pakete kommen nicht ins
# Abbild, sondern je Stand in ein eigenes venv unter /data/proxy (binderapp/proxy/staende.py).
FROM python:3.12-slim-bookworm

ARG BUILD_VERSION=0.0.0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    BINDER_VERSION=${BUILD_VERSION}

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY binderapp /app/binderapp

CMD ["python", "-m", "binderapp.addon"]
