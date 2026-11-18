# Serverless REST API Template

Plantilla de una API REST con FastAPI, AWS Lambda, API Gateway REST API v1 y DynamoDB. La API se despliega en un endpoint regional público.

## Endpoints

- `GET /health` y `GET /healts`: health check, sin dependencia de DynamoDB.
- `POST /items`: crea un elemento y genera su `id`; requiere API Key.
- `GET /items`: lista elementos; acepta `limit` (1-100) y `next_token`.
- `GET /items/{id}`: obtiene un elemento.
- `PUT /items/{id}`: reemplaza su documento completo; requiere API Key.
- `PATCH /items/{id}`: combina los campos recibidos con el documento actual; requiere API Key.
- `DELETE /items/{id}`: elimina el elemento; requiere API Key.

Las escrituras protegidas deben enviar el API Key en el header `x-api-key`. SAM crea una clave y un Usage Plan por API; obtén el valor desde API Gateway y distribúyelo como secreto, nunca lo guardes en el repositorio. Las lecturas y los health checks siguen siendo públicos.

El cuerpo de escritura es un objeto JSON arbitrario. Las respuestas individuales tienen la forma `{"id":"...","data":{...}}`; la lista responde `{"items":[...],"next_token":null}`. `PATCH` combina únicamente el primer nivel del objeto.

La tabla almacena cada documento como JSON en el atributo `data`, por lo que admite campos anidados y números JSON sin requerir un esquema de dominio.

## Despliegue

Requisitos: AWS CLI configurado, AWS SAM CLI y una cuenta con permisos para crear Lambda, API Gateway, DynamoDB e IAM.

```sh
sam build
sam deploy --guided
```

Durante el despliegue, configura `AllowedOrigin` con el origen exacto del cliente web, por ejemplo `https://app.example.com`. Es un único origen y no tiene valor por defecto, por lo que debes indicarlo en cada despliegue. El stack muestra `ApiUrl` al terminar. Para probar localmente con SAM y Docker:

```sh
sam local start-api --parameter-overrides AllowedOrigin=http://localhost:3000
```

La plantilla está en [template.yaml](template.yaml). Define una `AWS::Serverless::Api` con endpoint regional y rutas explícitas para aplicar API Key solo a las operaciones de escritura. CORS permite el origen configurado y el header `x-api-key`.

## Seguridad y costos

- `POST`, `PUT`, `PATCH` y `DELETE` requieren `x-api-key`; API Gateway rechaza solicitudes sin clave antes de invocar Lambda.
- `GET /items`, `GET /items/{id}`, `/health` y `/healts` no requieren clave y son públicos. No los uses para exponer datos privados sin añadir autorización.
- El API Key identifica una aplicación y habilita el Usage Plan, pero **no es autenticación de usuario ni autorización fuerte**. Una clave incluida en JavaScript o una app distribuida puede extraerse y reutilizarse. No la publiques en el frontend ni la almacenes en Git; rótala si se filtra.
- CORS permite al navegador leer respuestas solo desde el origen configurado en `AllowedOrigin`. CORS **no bloquea clientes que no sean navegadores ni sustituye autenticación**; la URL sigue siendo accesible desde Internet.
- Para datos sensibles o clientes públicos, añade Cognito/JWT o un authorizer y verifica permisos por usuario. El Usage Plan ayuda con cuotas y throttling, pero no es una garantía contra abuso.
- DynamoDB usa facturación bajo demanda. `GET /items` usa `Scan`, apropiada para una plantilla pequeña, pero puede ser costosa en tablas grandes.

## Desarrollo

```sh
pip install -r requirements-dev.txt
ruff check .
pytest
sam validate --lint
sam build
```

## Integración continua

El workflow `.github/workflows/ci.yml` se ejecuta en cada push y pull request. GitHub Actions configura Python 3.12, instala las dependencias de desarrollo y verifica:

- estilo y errores comunes con Ruff;
- la suite de pruebas con Pytest;
- la validez del template SAM;
- que la aplicación pueda construirse con `sam build`.

FastAPI genera OpenAPI y Swagger UI en `/openapi.json` y `/docs` al ejecutar la aplicación localmente. El Gateway desplegado solo expone las rutas y métodos declarados en `template.yaml`; la documentación no se publica allí.