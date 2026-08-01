"""Optional OpenTelemetry export of Strands agent traces. Activates only when
SKEIN_OTEL_ENDPOINT is set (e.g. http://jaeger:4318); otherwise a no-op."""

import logging
import os

from . import config

log = logging.getLogger(__name__)


def setup_telemetry() -> bool:
    if not config.OTEL_ENDPOINT:
        return False
    try:
        os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", config.OTEL_ENDPOINT)
        os.environ.setdefault("OTEL_SERVICE_NAME", "skein")
        from strands.telemetry import StrandsTelemetry

        StrandsTelemetry().setup_otlp_exporter()
        log.info("OpenTelemetry export enabled -> %s", config.OTEL_ENDPOINT)
        return True
    except Exception as exc:
        log.warning("OpenTelemetry setup failed (continuing without): %s", exc)
        return False
