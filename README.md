# Serverless REST API Template

Plantilla de una API REST con FastAPI, AWS Lambda, API Gateway REST API v1 y DynamoDB. La API se despliega en un endpoint regional público.

## Endpoints

- `GET /health` y `GET /healts`: health check, sin dependencia de DynamoDB.
- `POST /items`: crea un elemento y genera su `id`.
- `GET /items`: lista elementos; acepta `limit` (1-100) y `next_token`.
- `GET /items/{id}`: obtiene un elemento.
- `PUT /items/{id}`: reemplaza su documento completo.
- `PATCH /items/{id}`: combina los campos recibidos con el documento actual.
- `DELETE /items/{id}`: elimina el elemento.

El cuerpo de escritura es un objeto JSON arbitrario. Las respuestas individuales tienen la forma `{"id":"...","data":{...}}`; la lista responde `{"items":[...],"next_token":null}`. `PATCH` combina únicamente el primer nivel del objeto.

La tabla almacena cada documento como JSON en el atributo `data`, por lo que admite campos anidados y números JSON sin requerir un esquema de dominio.

## Despliegue

Requisitos: AWS CLI configurado, AWS SAM CLI y una cuenta con permisos para crear Lambda, API Gateway, DynamoDB e IAM.

```sh
sam build
sam deploy --guided
```

El stack muestra `ApiUrl` al terminar. Para probar localmente con SAM y Docker:

```sh
sam local start-api
```

La plantilla está en [template.yaml](template.yaml). Define una `AWS::Serverless::Api` con `EndpointConfiguration: REGIONAL` y una ruta proxy hacia Lambda. El gateway configura CORS para clientes web.

## Seguridad y costos

La API es pública y no requiere autenticación ni API key; cualquier persona que alcance el endpoint puede leer, crear, modificar y borrar datos. Antes de usarla con información real, agrega un authorizer y controles de acceso, además de límites de uso. DynamoDB usa facturación bajo demanda. La operación `GET /items` usa `Scan`, apropiada para una plantilla pequeña, pero puede ser costosa en tablas grandes.

## Desarrollo

```sh
pip install -r requirements-dev.txt
pytest
```

FastAPI publica OpenAPI y Swagger UI en `/openapi.json` y `/docs` cuando se ejecuta detrás de Mangum o localmente.