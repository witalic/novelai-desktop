async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]


async def test_openapi_lists_health(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert "/health" in resp.json()["paths"]
