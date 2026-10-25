# Built by the Home Assistant Supervisor. The source is the commit VK_REF
# from build.yaml, set by scripts/release-ha-app.sh.
FROM python:3.13-slim

ARG VK_REF
ENV TZ=Europe/Helsinki \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
ADD https://github.com/Vkuparin/villa-kuppis/archive/${VK_REF}.tar.gz /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 --wildcards \
        '*/requirements.txt' '*/villa_kuppis/*' \
    && rm /tmp/source.tar.gz \
    && pip install --no-cache-dir -r requirements.txt

CMD ["python", "-m", "villa_kuppis.ha_app"]
