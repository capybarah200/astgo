import pytest
from fastapi.testclient import TestClient
from helpers import LANDMARKS, POIS

from app.engine import Catalog
from app.server import create_app
from app.service import Assistant


@pytest.fixture()
def client():
    return TestClient(create_app(Assistant(Catalog(POIS), LANDMARKS)))


def test_index_and_static_are_revalidated(client):
    resp = client.get("/")
    assert resp.status_code == 200 and "Куда пойдём?" in resp.text
    assert resp.headers["cache-control"] == "no-cache"
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_status(client):
    assert client.get("/api/status").json() == {"places": len(POIS), "llm": False, "model": None}


def test_chat_round_trip_keeps_context_on_client(client):
    first = client.post("/api/chat", json={"message": "я у Байтерека, погулять", "at": "15:00"}).json()
    assert 1 <= len(first["items"]) <= 3
    second = client.post("/api/chat", json={"message": "ещё", "state": first["state"], "at": "15:00"}).json()
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


def test_chat_with_map_point(client):
    res = client.post("/api/chat", json={"lat": 51.1, "lon": 71.4, "source": "map", "at": "15:00"}).json()
    assert res["start"]["label"] == "точка на карте" and res["items"]


def test_garbage_state_is_ignored(client):
    resp = client.post("/api/chat", json={"message": "погулять", "state": {"start": "Москва", "wants": 5}})
    assert resp.status_code == 200 and "Где вы сейчас" in resp.json()["reply"]


def test_huge_body_rejected_before_parsing(client):
    resp = client.post("/api/chat", content=b'{"message": "' + b"x" * 40_000 + b'"}',
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 413


@pytest.mark.parametrize("body, status", [
    ({"lat": 43.24, "lon": 76.95}, 400),             # не Астана
    ({"message": "кофе", "at": "утром"}, 422),
    ({"message": "x" * 501}, 422),
    ({"lat": 91, "lon": 71.4}, 422),
    ({"lat": 51.1, "lon": 71.4, "source": "gps"}, 422),
])
def test_bad_requests(client, body, status):
    resp = client.post("/api/chat", json=body)
    assert resp.status_code == status
    if status == 400:
        assert "только по Астане" in resp.json()["detail"]
