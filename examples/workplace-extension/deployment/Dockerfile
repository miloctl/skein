# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    SKEIN_DATA_DIR=/data \
    SKEIN_PLAYBOOKS_DIR=/opt/atlas/content/playbooks \
    SKEIN_PERSONAS_DIR=/opt/atlas/content/personas \
    SKEIN_FLOCKS_DIR=/opt/atlas/content/flocks
WORKDIR /app

ARG PG_MAJOR=17
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-client-${PG_MAJOR}" \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN --mount=type=secret,id=pip-config,target=/etc/pip.conf,required=true \
    PIP_CONFIG_FILE=/etc/pip.conf \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY dist/skein_agents-0.3.2-py3-none-any.whl /tmp/skein_agents-0.3.2-py3-none-any.whl
COPY dist/atlas_skein_extension-2.0.0-py3-none-any.whl /tmp/atlas_skein_extension-2.0.0-py3-none-any.whl
RUN pip install --no-cache-dir --no-deps \
        /tmp/skein_agents-0.3.2-py3-none-any.whl \
        /tmp/atlas_skein_extension-2.0.0-py3-none-any.whl \
    && pip check \
    && rm /tmp/*.whl

COPY content /opt/atlas/content
RUN useradd --create-home appuser \
    && mkdir -p /data \
    && chgrp -R 0 /app /data /opt/atlas/content \
    && chmod -R g=u /app /data /opt/atlas/content

USER appuser:0
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1
CMD ["uvicorn", "atlas_skein.app:app", "--host", "0.0.0.0", "--port", "8000"]
