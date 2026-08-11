ARG SKEIN_IMAGE=skein:0.2.0
FROM ${SKEIN_IMAGE}

USER root
COPY dist/atlas_skein_extension-*.whl /tmp/
RUN pip install --no-cache-dir --no-deps /tmp/atlas_skein_extension-*.whl \
    && rm /tmp/atlas_skein_extension-*.whl

ENV SKEIN_PLAYBOOKS_DIR=/opt/atlas/content/playbooks
ENV SKEIN_PERSONAS_DIR=/opt/atlas/content/personas
ENV SKEIN_FLOCKS_DIR=/opt/atlas/content/flocks
COPY content /opt/atlas/content

USER appuser
CMD ["uvicorn", "atlas_skein.app:app", "--host", "0.0.0.0", "--port", "8000"]
