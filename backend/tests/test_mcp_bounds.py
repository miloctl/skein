"""An MCP server's response cannot grow past a byte bound in this process.

The MCP SDK reads a JSON response whole (response.aread()) and buffers each
SSE event until its blank line, and discovery runs at registration - before
any chat turn. Without a bound, one strong-identity user who registers a
server that answers `initialize` with a compressed bomb exhausts the API
pod's memory. Both client factories carry the bound: the personal one and the
system (SKEIN_MCP_SERVERS) one.
"""

import asyncio

import httpx2
import pytest

from app.agents import mcp_tools

CAP = mcp_tools._RESPONSE_MAX_BYTES
_REAL_CLIENT = httpx2.AsyncClient


class _Body(httpx2.AsyncByteStream):
    """A body that arrives in chunks, as from the network."""

    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def _serve(monkeypatch, respond, sent=None):
    def handler(request):
        if sent is not None:
            sent.append(request)
        return respond(request)

    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: _REAL_CLIENT(transport=httpx2.MockTransport(handler), **kwargs),
    )
    # the personal factory validates each destination; a test host is public
    monkeypatch.setattr("app.services.mcp_servers.check_url", lambda url: url)


def _post(factory):
    async def go():
        async with factory() as client:
            return await client.post("https://mcp.example/mcp", json={})

    return asyncio.run(go())


FACTORIES = [mcp_tools._no_redirect_client, mcp_tools._system_client]


@pytest.mark.parametrize("factory", FACTORIES)
def test_a_json_body_past_the_bound_is_refused(monkeypatch, factory):
    chunk = b" " * (64 * 1024)
    chunks = [chunk] * (CAP // len(chunk) + 1)
    _serve(
        monkeypatch,
        lambda request: httpx2.Response(
            200, headers={"content-type": "application/json"}, stream=_Body(chunks)
        ),
    )
    with pytest.raises(ValueError, match="larger than"):
        _post(factory)


@pytest.mark.parametrize("factory", FACTORIES)
def test_a_compressed_body_is_refused_and_identity_is_asked_for(monkeypatch, factory):
    sent = []
    _serve(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            stream=_Body([b"\x1f\x8b"]),
        ),
        sent,
    )
    with pytest.raises(ValueError, match="compressed"):
        _post(factory)
    assert sent[0].headers["accept-encoding"] == "identity"


@pytest.mark.parametrize("factory", FACTORIES)
def test_an_event_stream_is_bounded_per_event_not_per_connection(monkeypatch, factory):
    """A long-lived SSE stream carries many events over its life. The bound
    is the one event the SDK buffers, so a stream of small events passes and
    one oversized event does not."""
    event = b"data: " + b"x" * (CAP // 4) + b"\n\n"
    _serve(
        monkeypatch,
        lambda request: httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_Body([event] * 6)
        ),
    )
    assert len(_post(factory).content) == len(event) * 6

    huge = [b"data: " + b"x" * (CAP // 4)] * 5 + [b"\n\n"]
    _serve(
        monkeypatch,
        lambda request: httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_Body(huge)
        ),
    )
    with pytest.raises(ValueError, match="larger than"):
        _post(factory)
