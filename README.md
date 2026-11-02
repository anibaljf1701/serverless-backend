# Serverless REST API Template

Estado: implementación inicial de la Lambda disponible; el despliegue y los recursos AWS quedan pendientes. La implementación AyPe se trasladó al repositorio `aype-cafeteria/backend` para mantener separadas la aplicación de negocio y esta plantilla.

## Objetivo

Este repositorio será una plantilla demostrativa de una API REST serverless con FastAPI, documentación OpenAPI/Swagger y persistencia en DynamoDB. Debe permitir configurar nombres de tablas, región, recursos y comportamiento por entorno sin valores ni reglas de negocio propias de una aplicación.

## Alcance actual

La Lambda está implementada en `app.py` con FastAPI y Mangum. Expone:

- `POST /products`: crea un producto con ID UUID.
- `GET /products/{product_id}`: obtiene un producto.
- `GET /products`: lista y filtra por prefijo de nombre y/o categoría exacta; pagina con `next_token`.
- `PUT /products/{product_id}`: reemplaza todos los campos editables.
- `PATCH /products/{product_id}`: actualiza solo los campos enviados.
- `DELETE /products/{product_id}`: elimina un producto.

Las escrituras requieren el grupo Cognito `admin`; las lecturas aceptan `admin` o `reader`. API Gateway REST API deberá validar el JWT de Cognito y exigir API Key en sus métodos protegidos. La API Key y los límites de uso son responsabilidad del Gateway, no de la Lambda.

La tabla DynamoDB usa `product_id` como clave de partición y requiere dos índices secundarios globales:

- `product-name-index`: `name_index_pk` (partition key) y `name_index_sk` (sort key); permite listar y buscar por prefijo de nombre.
- `product-category-name-index`: `category_index_pk` (partition key) y `category_name_key` (sort key); permite filtrar por categoría y combinar categoría con prefijo de nombre.

Configura `PRODUCTS_TABLE`, `PRODUCT_NAME_INDEX` y `PRODUCT_CATEGORY_INDEX` en Lambda. Para ejecutar localmente, instala `requirements-dev.txt` y ejecuta `pytest`. La documentación OpenAPI queda disponible en `/docs` cuando se ejecute la app fuera de Lambda; el despliegue de Gateway, Cognito, API Keys, tablas e índices se definirá en una etapa posterior.
