"""Outbound request URLs stay out of the INFO log.

httpx and httpx2 log every request line at INFO, query string included. A
personal MCP server's URL and a provider URL reach the log that way, and the
log travels to whoever reads the pod output.
"""

import asyncio
import logging

import httpx2


def test_an_outbound_request_url_is_not_logged_at_info(monkeypatch, caplog):
    import app.main  # noqa: F401 - the log levels are set at import
    from app.agents import mcp_tools

    real = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: real(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={})),
            **kwargs,
        ),
    )
    caplog.set_level(logging.INFO)

    async def go():
        async with mcp_tools._system_client() as client:
            await client.get("https://mcp.internal.example/mcp?token=secret-value")

    asyncio.run(go())
    assert "secret-value" not in caplog.text


def test_a_plain_httpx_request_url_is_not_logged_at_info(caplog):
    """Provider calls (agents/team_agent.py) and embedding calls
    (services/search.py) go through httpx, not httpx2."""
    import httpx

    import app.main  # noqa: F401 - the log levels are set at import

    caplog.set_level(logging.INFO)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        client.get("https://provider.example/v1/embed?key=secret-value")
    assert "secret-value" not in caplog.text
