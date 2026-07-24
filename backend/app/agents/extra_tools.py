"""Opt-in prebuilt tools from the strands-agents-tools package, loaded via
STRANDS_EXTRA_TOOLS (comma-separated names).

Only allowlisted tools can load, and the allowlist is deliberately small.
Excluded on security review, not oversight:
- shell/python_repl/file_read/file_write/editor/use_computer/mcp_client —
  arbitrary code/filesystem access; platform writes go through the service
  layer, with provenance, or not at all.
- http_request — a write-capable HTTP client is a third write path: it can
  POST to this platform's own REST API with a forged X-User, bypassing the
  authority matrix, the review gate, and agent provenance (and it has no
  SSRF egress filter).
- use_agent/use_llm — the model chooses provider + client_args (base_url),
  which allows exfiltrating context to an attacker-controlled endpoint.
- workflow/diagram — model-controlled file paths (traversal) / subprocess.
"""

import logging
from functools import lru_cache

log = logging.getLogger(__name__)

# name -> (module under strands_tools, attribute)
ALLOWED = {
    # keyless; verified side-effect-free (calculator sandboxes its evaluator,
    # batch can only call tools already in the registry — still gated)
    "calculator": ("calculator", "calculator"),
    "current_time": ("current_time", "current_time"),
    "think": ("think", "think"),
    "batch": ("batch", "batch"),
    "sleep": ("sleep", "sleep"),
    # external feed reader (has its own storage traversal guard)
    "rss": ("rss", "rss"),
    # key-gated research (TAVILY_API_KEY / EXA_API_KEY; each reads only its key)
    "tavily_search": ("tavily", "tavily_search"),
    "tavily_extract": ("tavily", "tavily_extract"),
    "exa_search": ("exa", "exa_search"),
    "exa_get_contents": ("exa", "exa_get_contents"),
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
            obj = getattr(module, attr)
            # legacy TOOL_SPEC-style tools (e.g. batch) register as the module;
            # @tool-decorated ones (everything else) register as the function
            if hasattr(module, "TOOL_SPEC") and not hasattr(obj, "tool_spec"):
                obj = module
            tools.append(obj)
            log.info("extra tool loaded: %s", name)
        except Exception as exc:
            log.warning("extra tool %s failed to load (%s) — skipped", name, exc)
    return tuple(tools)
