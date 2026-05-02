import os
import json
import logging
import httpx
from datetime import datetime
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

TECNICOS_ALARMAS = ["JEAN", "JOEL", "DIANA"]
HEADER_ROW = ["FECHA", "ORDEN", "CODIGO"]

MESES = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE"
}


def get_sheet(sheet_id: str = None):
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    creds.refresh(Request())
    gc = gspread.authorize(creds)
    sid = sheet_id or os.getenv("GOOGLE_SHEET_ID_ALARMAS")
    return gc, gc.open_by_key(sid)


async def crear_sheet_nuevo_mes(año: int, mes: int) -> str:
    """Duplica el Sheet actual, limpia datos de técnicos y devuelve el nuevo ID."""
    nombre_mes = MESES[mes]
    nuevo_nombre = f"{nombre_mes}_{año}"

    gc, wb = get_sheet()

    # Duplicar el spreadsheet via Drive API
    drive_service_url = "https://www.googleapis.com/drive/v3/files"
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(Request())

    sheet_id = os.getenv("GOOGLE_SHEET_ID_ALARMAS")
    copy_url = f"https://www.googleapis.com/drive/v3/files/{sheet_id}/copy"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            copy_url,
            headers={"Authorization": f"Bearer {creds.token}"},
            json={"name": nuevo_nombre},
        )
        resp.raise_for_status()
        nuevo_id = resp.json()["id"]

    # Abrir el nuevo Sheet y limpiar datos de técnicos (mantener encabezados y fórmulas D:E)
    _, nuevo_wb = get_sheet(nuevo_id)
    for tecnico in TECNICOS_ALARMAS:
        try:
            ws = nuevo_wb.worksheet(tecnico)
            # Borrar desde fila 3 en adelante (mantiene fila 1=nombre, fila 2=headers)
            all_values = ws.get_all_values()
            if len(all_values) > 2:
                # Limpiar columnas A, B, C desde fila 3
                n_rows = len(all_values)
                ws.batch_clear([f"A3:C{n_rows + 10}"])
        except Exception as e:
            logger.warning(f"No se pudo limpiar tab {tecnico}: {e}")

    return nuevo_id


async def actualizar_variable_railway(nuevo_sheet_id: str):
    """Actualiza GOOGLE_SHEET_ID_ALARMAS en Railway via API."""
    token = os.getenv("RAILWAY_TOKEN")
    service_id = os.getenv("RAILWAY_SERVICE_ID")
    environment_id = os.getenv("RAILWAY_ENVIRONMENT_ID", "")

    # Railway GraphQL API
    query = """
    mutation variableUpsert($input: VariableUpsertInput!) {
        variableUpsert(input: $input)
    }
    """
    variables = {
        "input": {
            "serviceId": service_id,
            "name": "GOOGLE_SHEET_ID_ALARMAS",
            "value": nuevo_sheet_id,
        }
    }
    if environment_id:
        variables["input"]["environmentId"] = environment_id

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://backboard.railway.app/graphql/v2",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables},
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise Exception(f"Railway API error: {data['errors']}")


async def ejecutar_nuevo_mes(año: int = None, mes: int = None) -> dict:
    """Función principal: crea nuevo Sheet y actualiza Railway."""
    now = datetime.now()
    if año is None:
        año = now.year
    if mes is None:
        mes = now.month

    nuevo_id = await crear_sheet_nuevo_mes(año, mes)
    await actualizar_variable_railway(nuevo_id)

    return {
        "nuevo_id": nuevo_id,
        "nombre": f"{MESES[mes]}_{año}",
        "url": f"https://docs.google.com/spreadsheets/d/{nuevo_id}",
    }
