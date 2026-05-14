import logging
import os
from datetime import datetime

import httpx

from sheets import get_access_token, get_sheet

logger = logging.getLogger(__name__)

TECNICOS_ALARMAS = ["JEAN", "JOEL", "DIANA"]

MESES = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}


async def _copiar_sheet(sheet_id: str, nombre: str) -> str:
    """Duplica un spreadsheet en Drive y devuelve el nuevo ID."""
    token = get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://www.googleapis.com/drive/v3/files/{sheet_id}/copy",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": nombre},
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def _actualizar_variable_railway(nuevo_sheet_id: str) -> None:
    """Actualiza GOOGLE_SHEET_ID_ALARMAS en Railway via GraphQL API."""
    token = os.getenv("RAILWAY_TOKEN")
    service_id = os.getenv("RAILWAY_SERVICE_ID")
    environment_id = os.getenv("RAILWAY_ENVIRONMENT_ID", "")

    variables: dict = {
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
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "query": "mutation variableUpsert($input: VariableUpsertInput!) { variableUpsert(input: $input) }",
                "variables": variables,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Railway API error: {data['errors']}")


async def ejecutar_nuevo_mes(año: int | None = None, mes: int | None = None) -> dict:
    now = datetime.now()
    año = año or now.year
    mes = mes or now.month
    nombre_mes = MESES[mes]
    nuevo_nombre = f"{nombre_mes}_{año}"

    sheet_id = os.getenv("GOOGLE_SHEET_ID_ALARMAS")
    nuevo_id = await _copiar_sheet(sheet_id, nuevo_nombre)

    nuevo_wb = get_sheet(nuevo_id)
    for tecnico in TECNICOS_ALARMAS:
        try:
            ws = nuevo_wb.worksheet(tecnico)
            all_values = ws.get_all_values()
            if len(all_values) > 2:
                ws.batch_clear([f"A3:E{len(all_values) + 10}"])
        except Exception as e:
            logger.warning(f"No se pudo limpiar tab {tecnico}: {e}")

    await _actualizar_variable_railway(nuevo_id)

    return {
        "nuevo_id": nuevo_id,
        "nombre": nuevo_nombre,
        "url": f"https://docs.google.com/spreadsheets/d/{nuevo_id}",
    }
