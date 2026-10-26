import os
from datetime import datetime
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr
from mangum import Mangum
import boto3
from boto3.dynamodb.conditions import Key

# Configuración de la API
app = FastAPI(
    title="AyPe Coffee API",
    description="Backend Serverless para gestión de menú y reservas limitadas (Máximo 50 tazas/día).",
    version="1.0.0"
)

# Inicialización de DynamoDB (Asume que los nombres de las tablas vienen por variables de entorno)
dynamodb = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION', 'us-east-1'))
RESERVAS_TABLE = os.getenv('RESERVAS_TABLE_NAME', 'aype-reservas')
MENU_TABLE = os.getenv('MENU_TABLE_NAME', 'aype-menu')

# --- Modelos de Datos (Pydantic) ---
class ReservaRequest(BaseModel):
    nombre_cliente: str
    email: EmailStr
    variedad_cafe: str
    cantidad_tazas: int
    
class ReservaResponse(BaseModel):
    id_reserva: str
    fecha: str
    estado: str
    mensaje: str

# --- Endpoints ---

@app.get("/api/v1/menu", tags=["Menú"])
def obtener_menu():
    """Obtiene la lista de cafés de especialidad disponibles hoy."""
    table = dynamodb.Table(MENU_TABLE)
    try:
        response = table.scan()
        return {"menu": response.get('Items', [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/reservas", response_model=ReservaResponse, status_code=status.HTTP_201_CREATED, tags=["Reservas"])
def crear_reserva(reserva: ReservaRequest):
    """
    Crea una nueva reserva. 
    Regla de negocio: Máximo 2 tazas por persona y límite estricto de 50 tazas diarias totales.
    """
    if reserva.cantidad_tazas > 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Política estricta: Máximo 2 tazas por persona para garantizar la experiencia."
        )

    table = dynamodb.Table(RESERVAS_TABLE)
    hoy = datetime.utcnow().strftime('%Y-%m-%d')
    
    # 1. Consultar cuántas tazas ya se han reservado hoy
    response = table.query(
        IndexName='FechaIndex', # Asumimos un GSI (Global Secondary Index) por fecha
        KeyConditionExpression=Key('fecha').eq(hoy)
    )
    
    tazas_reservadas_hoy = sum(item.get('cantidad_tazas', 0) for item in response.get('Items', []))
    
    # 2. Validar límite diario (50 tazas)
    if tazas_reservadas_hoy + reserva.cantidad_tazas > 50:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail=f"Reserva rechazada. Solo quedan {50 - tazas_reservadas_hoy} tazas disponibles para el ritual de hoy."
        )
        
    # 3. Guardar la reserva
    id_reserva = f"RES-{int(datetime.utcnow().timestamp())}-{reserva.email[:3].upper()}"
    
    nueva_reserva = {
        'id': id_reserva,
        'fecha': hoy,
        'nombre_cliente': reserva.nombre_cliente,
        'email': reserva.email,
        'variedad_cafe': reserva.variedad_cafe,
        'cantidad_tazas': reserva.cantidad_tazas,
        'timestamp': datetime.utcnow().isoformat()
    }
    
    table.put_item(Item=nueva_reserva)
    
    return ReservaResponse(
        id_reserva=id_reserva,
        fecha=hoy,
        estado="CONFIRMADA",
        mensaje="Te esperamos en Lago Puelo para tu ritual."
    )

# --- Handler para AWS Lambda ---
# Mangum envuelve la aplicación FastAPI para que pueda ser invocada por AWS API Gateway / Lambda
handler = Mangum(app)