"""Optional OpenTelemetry export of Strands agent traces. Activates only when
SKEIN_OTEL_ENDPOINT is set (e.g. http://jaeger:4318); otherwise a no-op."""

import logging
import os

from . import config

log = logging.getLogger(__name__)


def _require_redaction_token() -> None:
    """Make the strands tracer redact conversation content by default.

    Without a `gen_ai_unredacted_attributes=` token the tracer emits full
    prompts, replies, and system instructions as span attributes — including
    private-classification content — into a collector that sits outside every
    Skein access control. The empty list means "redact everything sensitive";
    an operator who accepts the exposure appends attribute names (or a
    trailing-* glob) after the `=`.

    Token-prefix match, not a substring test: the tracer enables redaction
    only for a comma-separated token that starts with
    `gen_ai_unredacted_attributes=`, so a bare token without the `=` would
    pass a substring check while the tracer exports everything unredacted.

    Runs even with no SKEIN_OTEL_ENDPOINT: an operator can wire an exporter
    through plain OTel env autoconfig, and the tracer reads this variable
    either way. Strands memory spans emit content outside this policy; Skein
    loads no strands memory manager.
    """
    opt_in = os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN", "")
    tokens = [token.strip() for token in opt_in.split(",")]
    if any(token.startswith("gen_ai_unredacted_attributes=") for token in tokens):
        return
    redact = "gen_ai_unredacted_attributes="  # empty list: redact everything sensitive
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = f"{opt_in},{redact}" if opt_in else redact


def setup_telemetry() -> bool:
    _require_redaction_token()
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
