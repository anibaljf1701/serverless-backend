import asyncio
from collections.abc import Generator
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import app, current_groups, get_store


class MemoryProductStore:
    def __init__(self) -> None:
        self.products: dict[str, dict[str, Any]] = {}

    def create(self, product: dict[str, Any]) -> dict[str, Any]:
        self.products[product["product_id"]] = product.copy()
        return self.products[product["product_id"]]

    def get(self, product_id: str) -> dict[str, Any] | None:
        return self.products.get(product_id)

    def list(
        self,
        name_prefix: str | None,
        category: str | None,
        limit: int,
        _next_token: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        products = list(self.products.values())
        if name_prefix:
            products = [item for item in products if item["name"].casefold().startswith(name_prefix.casefold())]
        if category:
            products = [item for item in products if item["category"] == category]
        return products[:limit], None

    def replace(self, product_id: str, product: dict[str, Any]) -> dict[str, Any] | None:
        if product_id not in self.products:
            return None
        self.products[product_id] = {**product, "product_id": product_id}
        return self.products[product_id]

    def patch(self, product_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        if product_id not in self.products:
            return None
        self.products[product_id].update(changes)
        return self.products[product_id]

    def delete(self, product_id: str) -> bool:
        return self.products.pop(product_id, None) is not None


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    store = MemoryProductStore()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_groups] = lambda: {"admin"}
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_product_crud_and_prefix_category_filters(client: TestClient) -> None:
    created = client.post(
        "/products",
        json={"name": "Laptop Pro", "category": "Computers", "stock": 4, "price": "1299.99"},
    )
    assert created.status_code == 201
    product_id = created.json()["product_id"]
    assert set(created.json()) == {"product_id", "name", "category", "stock", "price"}

    found = client.get(f"/products/{product_id}")
    assert found.status_code == 200
    assert found.json()["name"] == "Laptop Pro"

    filtered = client.get("/products?name_prefix=LAP&category=Computers")
    assert [item["product_id"] for item in filtered.json()["items"]] == [product_id]

    patched = client.patch(f"/products/{product_id}", json={"stock": 2})
    assert patched.status_code == 200
    assert patched.json()["stock"] == 2
    assert patched.json()["name"] == "Laptop Pro"

    replaced = client.put(
        f"/products/{product_id}",
        json={"name": "Laptop Air", "category": "Computers", "stock": 3, "price": "999.00"},
    )
    assert replaced.status_code == 200
    assert replaced.json()["name"] == "Laptop Air"

    deleted = client.delete(f"/products/{product_id}")
    assert deleted.status_code == 204
    assert client.get(f"/products/{product_id}").status_code == 404


def test_reader_can_read_but_cannot_write(client: TestClient) -> None:
    app.dependency_overrides[current_groups] = lambda: {"reader"}
    assert client.get("/products").status_code == 200
    response = client.post(
        "/products",
        json={"name": "Keyboard", "category": "Accessories", "stock": 2, "price": "49.99"},
    )
    assert response.status_code == 403


def test_cognito_groups_are_read_from_api_gateway_event() -> None:
    request = Request(
        {
            "type": "http",
            "aws.event": {
                "requestContext": {
                    "authorizer": {"claims": {"cognito:groups": '["admin", "reader"]'}}
                }
            },
        }
    )
    assert asyncio.run(current_groups(request)) == {"admin", "reader"}


def test_missing_cognito_groups_are_rejected() -> None:
    request = Request({"type": "http", "aws.event": {"requestContext": {}}})
    with pytest.raises(HTTPException) as error:
        asyncio.run(current_groups(request))
    assert error.value.status_code == 401


@pytest.mark.parametrize("body", [{}, {"stock": None}, {"unknown": "value"}])
def test_patch_requires_valid_non_null_fields(client: TestClient, body: dict[str, Any]) -> None:
    response = client.patch("/products/missing", json=body)
    assert response.status_code == 422