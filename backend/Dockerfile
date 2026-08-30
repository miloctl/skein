FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    SKEIN_DATA_DIR=/data

WORKDIR /app

# pg_dump for services/admin.py::backup. The MAJOR VERSION MUST MATCH OR
# EXCEED the server's: pg_dump refuses to dump a server newer than itself
# ("server version 17.x; pg_dump version 16.x"), and the failure lands in the
# nightly backup job, not at boot — the daily copy stops being written and
# job_health only notices at 48h. Debian's own postgresql-client trails the
# current server release, so this takes the PGDG repo to pin the major
# deliberately. Bump it together with docker-compose.yml, ci.yml, and the
# StatefulSet image in deploy/k8s/base.
ARG PG_MAJOR=17
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-client-${PG_MAJOR}" \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer first (pinned, reproducible) so source edits don't
# reinstall everything. Regenerate with:
#   uv pip freeze --python .venv/bin/python > requirements.lock  (minus dev deps)
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml ./
COPY app ./app
# Content directories, resolved from the package root by services/playbooks.py,
# personas.py, flocks.py and fieldguide.py. Each one MUST be copied: a missing
# personas/ or flocks/ degrades silently to an empty bench (no @ picker
# specialists, no flock fan-out), and a missing fieldguide/ makes
# fieldguide.registry() raise FileNotFoundError on every field-guide request.
COPY playbooks ./playbooks
COPY personas ./personas
COPY flocks ./flocks
COPY fieldguide ./fieldguide
COPY schemas ./schemas
COPY seed.py ./

RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home appuser \
    && mkdir -p /data \
    && chown -R appuser:0 /data /app \
    && chmod -R g=u /data /app

USER appuser:0
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
