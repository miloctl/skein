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
        # Without this token the strands tracer emits full prompts, replies,
        # and system instructions as span attributes — conversation content,
        # including private-classification documents, into a collector that
        # sits outside every Skein access control. The empty list means
        # "redact everything sensitive"; an operator who accepts the exposure
        # appends attribute names (or a trailing-* glob) after the `=`.
        opt_in = os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN", "")
        if "gen_ai_unredacted_attributes" not in opt_in:
            redact = "gen_ai_unredacted_attributes="  # empty list: redact everything sensitive
            os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = f"{opt_in},{redact}" if opt_in else redact
        from strands.telemetry import StrandsTelemetry

        StrandsTelemetry().setup_otlp_exporter()
        log.info("OpenTelemetry export enabled -> %s", config.OTEL_ENDPOINT)
        return True
    except Exception as exc:
        log.warning("OpenTelemetry setup failed (continuing without): %s", exc)
        return False
