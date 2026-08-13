ARG SKEIN_IMAGE=skein:0.2.1
FROM ${SKEIN_IMAGE}

USER root
COPY dist/atlas_skein_extension-*.whl /tmp/
# Ordinary dependency resolution, on purpose: --no-deps happened to work
# while the only dependency was the already-installed core, and silently
# omitted the first real private dependency an extension adds.
RUN pip install --no-cache-dir /tmp/atlas_skein_extension-*.whl \
    && pip check \
    && rm /tmp/atlas_skein_extension-*.whl

ENV SKEIN_PLAYBOOKS_DIR=/opt/atlas/content/playbooks
ENV SKEIN_PERSONAS_DIR=/opt/atlas/content/personas
ENV SKEIN_FLOCKS_DIR=/opt/atlas/content/flocks
COPY content /opt/atlas/content
RUN mkdir -p /atlas-data \
    && chown -R appuser:appuser /atlas-data /opt/atlas/content

USER appuser
CMD ["uvicorn", "atlas_skein.app:app", "--host", "0.0.0.0", "--port", "8000"]
