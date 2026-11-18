from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import app, get_store


class MemoryItemStore:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def create(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        item = {"id": item_id, "data": data.copy()}
        self.items[item_id] = item
        return item

    def get(self, item_id: str) -> dict[str, Any] | None:
        return self.items.get(item_id)

    def list(
        self,
        limit: int,
        _next_token: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        return list(self.items.values())[:limit], None

    def replace(self, item_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        if item_id not in self.items:
            return None
        self.items[item_id] = {"id": item_id, "data": data.copy()}
        return self.items[item_id]

    def patch(self, item_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        if item_id not in self.items:
            return None
        self.items[item_id]["data"].update(changes)
        return self.items[item_id]

    def delete(self, item_id: str) -> bool:
        return self.items.pop(item_id, None) is not None


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    store = MemoryItemStore()
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_generic_item_crud(client: TestClient) -> None:
    payload = {"name": "Example", "nested": {"enabled": True}, "quantity": 3}
    created = client.post("/items", json=payload)
    assert created.status_code == 201
    item_id = created.json()["id"]
    assert created.json() == {"id": item_id, "data": payload}

    found = client.get(f"/items/{item_id}")
    assert found.status_code == 200
    assert found.json()["data"] == payload

    listed = client.get("/items")
    assert [item["id"] for item in listed.json()["items"]] == [item_id]

    patched = client.patch(f"/items/{item_id}", json={"quantity": 2, "active": True})
    assert patched.status_code == 200
    assert patched.json()["data"] == {**payload, "quantity": 2, "active": True}

    replaced = client.put(f"/items/{item_id}", json={"name": "Replacement"})
    assert replaced.status_code == 200
    assert replaced.json()["data"] == {"name": "Replacement"}

    deleted = client.delete(f"/items/{item_id}")
    assert deleted.status_code == 204
    assert client.get(f"/items/{item_id}").status_code == 404


@pytest.mark.parametrize("path", ["/health", "/healts"])
def test_health_endpoint(path: str, client: TestClient) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_item_returns_not_found(client: TestClient) -> None:
    assert client.get("/items/missing").status_code == 404
    assert client.put("/items/missing", json={}).status_code == 404
    assert client.patch("/items/missing", json={}).status_code == 404
    assert client.delete("/items/missing").status_code == 404


def test_invalid_next_token_returns_bad_request(client: TestClient) -> None:
    response = client.get("/items", params={"next_token": "%%%"})
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid next_token"}