# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

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
COPY skein-agents.lock /tmp/skein-agents.lock
COPY dist/skein_agents-0.6.7-py3-none-any.whl /tmp/skein/skein_agents-0.6.7-py3-none-any.whl
COPY dist/atlas_skein_extension-2.0.0-py3-none-any.whl /tmp/atlas_skein_extension-2.0.0-py3-none-any.whl
# The Skein wheel comes from outside this repository, so it installs only
# through its pinned digest. The Atlas wheel is built from this tree.
RUN pip install --no-cache-dir --no-deps --no-index --find-links /tmp/skein \
        --require-hashes -r /tmp/skein-agents.lock \
    && pip install --no-cache-dir --no-deps \
        /tmp/atlas_skein_extension-2.0.0-py3-none-any.whl \
    && pip check \
    && rm -r /tmp/skein /tmp/skein-agents.lock /tmp/*.whl

COPY content /opt/atlas/content
# Only /data is writable. Code and content stay root's, so a compromised
# process cannot rewrite them. g=u gives OpenShift's arbitrary UID, which runs
# in group 0, the owner's access to /data.
RUN useradd --create-home appuser \
    && mkdir -p /data \
    && chgrp -R 0 /data \
    && chmod -R g=u /data

USER appuser:0
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1
CMD ["uvicorn", "atlas_skein.app:app", "--host", "0.0.0.0", "--port", "8000"]
