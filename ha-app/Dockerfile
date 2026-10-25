# Built by the Home Assistant Supervisor. It passes BUILD_VERSION (the
# version in config.yaml); the source is the matching release tag.
FROM python:3.13-slim

ARG BUILD_VERSION
ENV TZ=Europe/Helsinki \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
ADD https://github.com/Vkuparin/villa-kuppis/archive/refs/tags/ha-app-${BUILD_VERSION}.tar.gz /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 --wildcards \
        '*/requirements.txt' '*/villa_kuppis/*' \
    && rm /tmp/source.tar.gz \
    && pip install --no-cache-dir -r requirements.txt

CMD ["python", "-m", "villa_kuppis.ha_app"]
