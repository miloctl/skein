ARG BACKEND_IMAGE
FROM ${BACKEND_IMAGE}
USER root
WORKDIR /app
# COPY merges directories. Remove the old trees first so deleted runtime modules
# and stock cards cannot survive a reused backend image.
RUN rm -rf /app/app /app/skein_stock
COPY backend/app /app/app
COPY backend/playbooks /app/skein_stock/playbooks
COPY backend/personas /app/skein_stock/personas
COPY backend/flocks /app/skein_stock/flocks
COPY backend/fieldguide /app/skein_stock/fieldguide
COPY backend/schemas /app/skein_stock/schemas
COPY scripts/fixtures/durability_app.py scripts/fixtures/durability_upstream.py /fixtures/
# config.STOCK_DIR prefers installed stock over /app/fieldguide. Validate the
# runtime-selected copy, or a reused base silently combines two source versions.
RUN mkdir -p /mirror && chgrp 0 /mirror && chmod g+w /mirror \
    && /usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp \
        PYTHON_DOTENV_DISABLED=1 SKEIN_DATA_DIR=/data PYTHONPATH=/app \
        python -c "from app.services.fieldguide import registry; registry()"
# Every run must clear these deployment-shaped defaults. The preboot guard in
# durability_app.py refuses the foreign URL before any database connection.
ENV PYTHONPATH=/fixtures:/app \
    SKEIN_DATABASE_URL=postgresql://unused.invalid:1/not-owned \
    SKEIN_MODEL_API_KEY=fixture-only-key \
    SKEIN_MODEL_BASE_URL=https://unused.invalid \
    SKEIN_MCP_SERVERS='[{"url":"https://unused.invalid"}]' \
    SLACK_WEBHOOK_URL=https://unused.invalid \
    OTEL_EXPORTER_OTLP_ENDPOINT=https://unused.invalid \
    OTEL_SDK_DISABLED=false \
    HTTPS_PROXY=http://unused.invalid:1
USER 1000710000:0
ENTRYPOINT []
CMD ["uvicorn", "durability_app:app", "--host", "0.0.0.0", "--port", "8000"]
