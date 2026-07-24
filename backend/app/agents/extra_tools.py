"""Opt-in prebuilt tools from the strands-agents-tools package, loaded via
STRANDS_EXTRA_TOOLS (comma-separated names).

Only allowlisted tools can load. Anything with arbitrary code/filesystem/shell
access (shell, python_repl, file_write, editor, mcp_client, use_computer, …)
is deliberately not loadable on the shared server — platform writes go through
the service layer, with provenance, or not at all."""

import logging
from functools import lru_cache

log = logging.getLogger(__name__)

# name -> (module under strands_tools, attribute)
ALLOWED = {
    # keyless, no side effects
    "calculator": ("calculator", "calculator"),
    "current_time": ("current_time", "current_time"),
    "think": ("think", "think"),
    "batch": ("batch", "batch"),
    "sleep": ("sleep", "sleep"),
    "diagram": ("diagram", "diagram"),
    # network readers (enable deliberately)
    "http_request": ("http_request", "http_request"),
    "rss": ("rss", "rss"),
    # key-gated research (TAVILY_API_KEY / EXA_API_KEY)
    "tavily_search": ("tavily", "tavily_search"),
    "tavily_extract": ("tavily", "tavily_extract"),
    "exa_search": ("exa", "exa_search"),
    "exa_get_contents": ("exa", "exa_get_contents"),
    # nested-agent orchestration (uses the configured model provider)
    "use_agent": ("use_agent", "use_agent"),
    "use_llm": ("use_llm", "use_llm"),
    "workflow": ("workflow", "workflow"),
}


@lru_cache(maxsize=1)
def extra_tools() -> tuple:
    from .. import config

    tools = []
    for name in config.EXTRA_TOOLS:
        spec = ALLOWED.get(name)
        if not spec:
            log.warning("extra tool %r refused: not in the allowlist %s",
                        name, sorted(ALLOWED))
            continue
        module_name, attr = spec
        try:
            module = __import__(f"strands_tools.{module_name}", fromlist=[attr])
            tools.append(getattr(module, attr))
            log.info("extra tool loaded: %s", name)
        except Exception as exc:
            log.warning("extra tool %s failed to load (%s) — skipped", name, exc)
    return tuple(tools)
