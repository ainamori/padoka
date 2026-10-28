# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PADOKA_CONFIG=/app/config/padoka.yml

WORKDIR /app

# Install dependencies from vendored wheel (no PyPI access needed at build time).
COPY vendor/ ./vendor/
RUN pip install --no-cache-dir ./vendor/*.whl \
    && useradd --create-home --uid 10001 padoka

# Application code (src layout).
COPY src/ ./src/
COPY plugins/ ./plugins/
COPY config/ ./config/

# TLS cert for the client port.  A self-signed sample pair is bundled in the
# repo (config/certs, copied above) and used as-is; it is only regenerated
# at build time if missing.  Replace with a real CA-issued cert/key before
# exposing the port publicly.
RUN if [ ! -f /app/config/certs/padoka.crt ] || [ ! -f /app/config/certs/padoka.key ]; then \
        mkdir -p /app/config/certs && \
        openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
            -keyout /app/config/certs/padoka.key \
            -out /app/config/certs/padoka.crt \
            -subj "/CN=padoka" \
            -addext "subjectAltName=DNS:localhost,DNS:padoka,IP:127.0.0.1" && \
        chmod 600 /app/config/certs/padoka.key; \
    fi

# Entry point script (exec form compatible, receives SIGTERM directly).
RUN printf '#!/bin/sh\nexec python -m padoka -c "${PADOKA_CONFIG:-/app/config/padoka.yml}" "$@"\n' \
    > /usr/local/bin/padoka-entry \
    && chmod +x /usr/local/bin/padoka-entry

# Writable log directory (volume in compose).
RUN mkdir -p /app/log && chown -R padoka:padoka /app

USER padoka

# Client IRC port (published in compose).
EXPOSE 6667

ENTRYPOINT ["padoka-entry"]
