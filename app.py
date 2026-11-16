import base64
import json
import os
from functools import lru_cache
from typing import Any, Protocol
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from mangum import Mangum
from pydantic import BaseModel


class ItemRead(BaseModel):
    id: str
    data: dict[str, Any]


class ItemPage(BaseModel):
    items: list[ItemRead]
    next_token: str | None


class ItemStore(Protocol):
    def create(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]: ...

    def get(self, item_id: str) -> dict[str, Any] | None: ...

    def list(self, limit: int, next_token: str | None) -> tuple[list[dict[str, Any]], str | None]: ...

    def replace(self, item_id: str, data: dict[str, Any]) -> dict[str, Any] | None: ...

    def patch(self, item_id: str, changes: dict[str, Any]) -> dict[str, Any] | None: ...

    def delete(self, item_id: str) -> bool: ...


def encode_token(key: dict[str, Any] | None) -> str | None:
    if not key:
        return None
    raw = json.dumps(key, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_token(token: str | None) -> dict[str, Any] | None:
    if token is None:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        key = json.loads(decoded)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=400, detail="Invalid next_token") from error
    if not isinstance(key, dict) or not key:
        raise HTTPException(status_code=400, detail="Invalid next_token")
    return key


class DynamoItemStore:
    def __init__(self, table: Any) -> None:
        self.table = table

    @staticmethod
    def _record(item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"id": item_id, "data": json.dumps(data, separators=(",", ":"))}

    @staticmethod
    def _decode(record: dict[str, Any]) -> dict[str, Any]:
        return {"id": record["id"], "data": json.loads(record["data"])}

    def create(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            self.table.put_item(
                Item=self._record(item_id, data),
                ConditionExpression="attribute_not_exists(id)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise HTTPException(status_code=409, detail="Item already exists") from error
            raise
        return {"id": item_id, "data": data}

    def get(self, item_id: str) -> dict[str, Any] | None:
        record = self.table.get_item(Key={"id": item_id}).get("Item")
        return self._decode(record) if record else None

    def list(self, limit: int, next_token: str | None) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {"Limit": limit}
        start_key = decode_token(next_token)
        if start_key:
            query["ExclusiveStartKey"] = start_key
        result = self.table.scan(**query)
        return (
            [self._decode(record) for record in result.get("Items", [])],
            encode_token(result.get("LastEvaluatedKey")),
        )

    def replace(self, item_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        try:
            self.table.put_item(
                Item=self._record(item_id, data),
                ConditionExpression="attribute_exists(id)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return {"id": item_id, "data": data}

    def patch(self, item_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get(item_id)
        if current is None:
            return None
        data = {**current["data"], **changes}
        try:
            self.table.update_item(
                Key={"id": item_id},
                UpdateExpression="SET #data = :data",
                ConditionExpression="attribute_exists(id)",
                ExpressionAttributeNames={"#data": "data"},
                ExpressionAttributeValues={":data": json.dumps(data, separators=(",", ":"))},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return {"id": item_id, "data": data}

    def delete(self, item_id: str) -> bool:
        try:
            self.table.delete_item(
                Key={"id": item_id},
                ConditionExpression="attribute_exists(id)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True


@lru_cache
def get_store() -> ItemStore:
    table_name = os.environ.get("ITEMS_TABLE")
    if not table_name:
        raise RuntimeError("ITEMS_TABLE environment variable is required")
    dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION"))
    return DynamoItemStore(dynamodb.Table(table_name))


app = FastAPI(title="Serverless REST API", version="1.0.0")


@app.get("/health")
@app.get("/healts")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/items", status_code=status.HTTP_201_CREATED, response_model=ItemRead)
async def create_item(
    data: dict[str, Any],
    store: ItemStore = Depends(get_store),
) -> dict[str, Any]:
    return store.create(str(uuid4()), data)


@app.get("/items", response_model=ItemPage)
async def list_items(
    limit: int = Query(default=25, ge=1, le=100),
    next_token: str | None = None,
    store: ItemStore = Depends(get_store),
) -> ItemPage:
    items, cursor = store.list(limit, next_token)
    return ItemPage(items=items, next_token=cursor)


@app.get("/items/{item_id}", response_model=ItemRead)
async def get_item(item_id: str, store: ItemStore = Depends(get_store)) -> dict[str, Any]:
    item = store.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.put("/items/{item_id}", response_model=ItemRead)
async def replace_item(
    item_id: str,
    data: dict[str, Any],
    store: ItemStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.replace(item_id, data)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.patch("/items/{item_id}", response_model=ItemRead)
async def patch_item(
    item_id: str,
    changes: dict[str, Any],
    store: ItemStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.patch(item_id, changes)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: str, store: ItemStore = Depends(get_store)) -> Response:
    if not store.delete(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


handler = Mangum(app)