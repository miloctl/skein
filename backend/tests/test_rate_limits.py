"""Rate caps and body caps on the write paths."""


def test_create_bodies_are_capped(client, fresh_db):
    r = client.post("/api/notes", json={"topic": "big", "content": "x" * 50_000})
    assert r.status_code == 422
    r = client.post("/api/chat", json={"message": "x" * 50_000})
    assert r.status_code == 422


def test_write_rate_cap_enforced_on_create_routes(client, fresh_db):
    for i in range(30):
        assert client.post("/api/tasks", json={"title": f"t{i}"}).status_code == 200
    r = client.post("/api/tasks", json={"title": "t31"})
    assert r.status_code == 400 and "slow down" in r.json()["detail"]


def test_rate_caps(client, fresh_db):
    from app import ratelimit

    ratelimit.reset()
    for i in range(30):
        client.post("/api/capture", json={"text": f"note: filler {i}"})
    r = client.post("/api/capture", json={"text": "note: one too many"})
    assert r.status_code == 400 and "slow down" in r.json()["detail"]
    ratelimit.reset()
    assert client.post("/api/capture", json={"text": "note: fine again"}).status_code == 200
