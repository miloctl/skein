FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SKEIN_DATA_DIR=/data

WORKDIR /app

# Dependency layer first (pinned, reproducible) so source edits don't
# reinstall everything. Regenerate with:
#   uv pip freeze --python .venv/bin/python > requirements.lock  (minus dev deps)
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY playbooks ./playbooks
COPY seed.py ./

RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app

USER appuser
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
