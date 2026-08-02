"""/api/ask: citations with row IDs, and the OR fallback when a phrase returns nothing."""


def test_ask_or_fallback(client):
    client.post(
        "/api/questions",
        json={"question": "what latency percentile does the partner team care about?"},
    )
    out = client.get("/api/ask", params={"q": "which latency number matters to the partner"}).json()
    assert out["citations"]
    assert "loosely related" in out["note"]


def test_ask_cites_rows(client, fresh_db):
    client.post("/api/decisions", json={"title": "Ship on Fridays", "decision": "we ship fridays"})
    out = client.get("/api/ask?q=fridays").json()
    assert out["citations"]
    assert out["citations"][0]["ref"].startswith("decision #")
    empty = client.get("/api/ask?q=zzzznothing").json()
    assert empty["citations"] == [] and "nothing indexed" in empty["note"]
