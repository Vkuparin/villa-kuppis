# Villa Kuppis service image. For reproducible builds pin the base by digest
# (python:3.13-slim@sha256:...) when you update it.
FROM python:3.13-slim

ENV TZ=Europe/Helsinki \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin kuppis

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY villa_kuppis/ villa_kuppis/

USER kuppis

# Cheap GET that passes the loopback Host check; exits non-zero on failure.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8377/api/identity', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "villa_kuppis.runtime", "--config", "/config/villa-kuppis.json"]
