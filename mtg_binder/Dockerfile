# Schlankes Abbild: Python 3.11 mit Starlette und Uvicorn, sonst nur der Server-Teil von binderapp.
FROM python:3.11-slim-bookworm

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
