import base64
import json
import os
import re
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Any, Protocol
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from mangum import Mangum
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    stock: int = Field(ge=0)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class ProductPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    stock: int | None = Field(default=None, ge=0)
    price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None

    @model_validator(mode="after")
    def require_changes(self) -> "ProductPatch":
        if not self.model_fields_set:
            raise ValueError("PATCH requires at least one field")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("PATCH fields cannot be null")
        return self


class ProductRead(BaseModel):
    product_id: str
    name: str
    category: str
    stock: int
    price: Decimal


class ProductPage(BaseModel):
    items: list[ProductRead]
    next_token: str | None


class ProductStore(Protocol):
    def create(self, product: dict[str, Any]) -> dict[str, Any]: ...

    def get(self, product_id: str) -> dict[str, Any] | None: ...

    def list(
        self,
        name_prefix: str | None,
        category: str | None,
        limit: int,
        next_token: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]: ...

    def replace(self, product_id: str, product: dict[str, Any]) -> dict[str, Any] | None: ...

    def patch(self, product_id: str, changes: dict[str, Any]) -> dict[str, Any] | None: ...

    def delete(self, product_id: str) -> bool: ...


def normalize_name(value: str) -> str:
    return value.casefold()


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


class DynamoProductStore:
    def __init__(self, table: Any, name_index: str, category_index: str) -> None:
        self.table = table
        self.name_index = name_index
        self.category_index = category_index

    @staticmethod
    def _index_fields(product: dict[str, Any]) -> dict[str, str]:
        name_key = f"{normalize_name(product['name'])}#{product['product_id']}"
        return {
            "name_index_pk": "PRODUCTS",
            "name_index_sk": name_key,
            "category_index_pk": product["category"],
            "category_name_key": name_key,
        }

    def create(self, product: dict[str, Any]) -> dict[str, Any]:
        item = {**product, **self._index_fields(product)}
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(product_id)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise HTTPException(status_code=409, detail="Product already exists") from error
            raise
        return item

    def get(self, product_id: str) -> dict[str, Any] | None:
        return self.table.get_item(Key={"product_id": product_id}).get("Item")

    def list(
        self,
        name_prefix: str | None,
        category: str | None,
        limit: int,
        next_token: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if category:
            index_name = self.category_index
            partition = Key("category_index_pk").eq(category)
            if name_prefix:
                partition &= Key("category_name_key").begins_with(normalize_name(name_prefix))
        else:
            index_name = self.name_index
            partition = Key("name_index_pk").eq("PRODUCTS")
            if name_prefix:
                partition &= Key("name_index_sk").begins_with(normalize_name(name_prefix))

        query: dict[str, Any] = {
            "IndexName": index_name,
            "KeyConditionExpression": partition,
            "Limit": limit,
            "ScanIndexForward": True,
        }
        start_key = decode_token(next_token)
        if start_key:
            query["ExclusiveStartKey"] = start_key
        result = self.table.query(**query)
        return result.get("Items", []), encode_token(result.get("LastEvaluatedKey"))

    def replace(self, product_id: str, product: dict[str, Any]) -> dict[str, Any] | None:
        item = {**product, "product_id": product_id}
        item.update(self._index_fields(item))
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_exists(product_id)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return item

    def patch(self, product_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        assignments: list[str] = []
        for field, value in changes.items():
            names[f"#{field}"] = field
            values[f":{field}"] = value
            assignments.append(f"#{field} = :{field}")

        if "name" in changes:
            normalized = normalize_name(changes["name"])
            names["#name_index_sk"] = "name_index_sk"
            names["#category_name_key"] = "category_name_key"
            values[":name_index_sk"] = f"{normalized}#{product_id}"
            values[":category_name_key"] = f"{normalized}#{product_id}"
            assignments.extend(
                ["#name_index_sk = :name_index_sk", "#category_name_key = :category_name_key"]
            )
        if "category" in changes:
            names["#category_index_pk"] = "category_index_pk"
            values[":category_index_pk"] = changes["category"]
            assignments.append("#category_index_pk = :category_index_pk")

        try:
            result = self.table.update_item(
                Key={"product_id": product_id},
                UpdateExpression="SET " + ", ".join(assignments),
                ConditionExpression="attribute_exists(product_id)",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return result["Attributes"]

    def delete(self, product_id: str) -> bool:
        try:
            self.table.delete_item(
                Key={"product_id": product_id},
                ConditionExpression="attribute_exists(product_id)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True


@lru_cache
def get_store() -> ProductStore:
    table_name = os.environ.get("PRODUCTS_TABLE")
    if not table_name:
        raise RuntimeError("PRODUCTS_TABLE environment variable is required")
    dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION"))
    table = dynamodb.Table(table_name)
    return DynamoProductStore(
        table,
        name_index=os.environ.get("PRODUCT_NAME_INDEX", "product-name-index"),
        category_index=os.environ.get("PRODUCT_CATEGORY_INDEX", "product-category-name-index"),
    )


async def current_groups(request: Request) -> set[str]:
    event = request.scope.get("aws.event", {})
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    raw_groups = claims.get("cognito:groups", [])
    if isinstance(raw_groups, str):
        try:
            parsed = json.loads(raw_groups)
            raw_groups = parsed if isinstance(parsed, list) else [raw_groups]
        except json.JSONDecodeError:
            raw_groups = re.split(r"[,\s]+", raw_groups.strip("[] "))
    if not isinstance(raw_groups, list):
        raw_groups = []
    groups = {group.strip(" '\"") for group in raw_groups if isinstance(group, str)}
    if not groups.intersection({"admin", "reader"}):
        raise HTTPException(status_code=401, detail="Authenticated Cognito group required")
    return groups


async def require_reader(groups: set[str] = Depends(current_groups)) -> set[str]:
    if not groups.intersection({"admin", "reader"}):
        raise HTTPException(status_code=403, detail="Reader role required")
    return groups


async def require_admin(groups: set[str] = Depends(current_groups)) -> None:
    if "admin" not in groups:
        raise HTTPException(status_code=403, detail="Admin role required")


app = FastAPI(title="Products API", version="1.0.0")


@app.post("/products", status_code=status.HTTP_201_CREATED, response_model=ProductRead)
async def create_product(
    product: ProductCreate,
    _: None = Depends(require_admin),
    store: ProductStore = Depends(get_store),
) -> dict[str, Any]:
    item = product.model_dump()
    item["product_id"] = str(uuid4())
    return store.create(item)


@app.get("/products", response_model=ProductPage)
async def list_products(
    name_prefix: str | None = Query(default=None, min_length=1),
    category: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=25, ge=1, le=100),
    next_token: str | None = None,
    _: set[str] = Depends(require_reader),
    store: ProductStore = Depends(get_store),
) -> ProductPage:
    items, cursor = store.list(name_prefix, category, limit, next_token)
    return ProductPage(items=items, next_token=cursor)


@app.get("/products/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: str,
    _: set[str] = Depends(require_reader),
    store: ProductStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.get(product_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return item


@app.put("/products/{product_id}", response_model=ProductRead)
async def replace_product(
    product_id: str,
    product: ProductCreate,
    _: None = Depends(require_admin),
    store: ProductStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.replace(product_id, product.model_dump())
    if item is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return item


@app.patch("/products/{product_id}", response_model=ProductRead)
async def patch_product(
    product_id: str,
    product: ProductPatch,
    _: None = Depends(require_admin),
    store: ProductStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.patch(product_id, product.model_dump(exclude_unset=True))
    if item is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return item


@app.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: str,
    _: None = Depends(require_admin),
    store: ProductStore = Depends(get_store),
) -> Response:
    if not store.delete(product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


handler = Mangum(app)