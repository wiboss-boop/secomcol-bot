import logging
import os
from datetime import datetime
import httpx
from sheets import get_sheet

logger = logging.getLogger(__name__)

TECNICOS_ALARMAS = ["JEAN", "JOEL", "DIANA"]

MESES = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}


def _crear_sheet_vacio(nombre: str, token: str) -> str:
    """Crea un nuevo spreadsheet vacío via Sheets API y devuelve el ID."""
    import requests
    resp = requests.post(
        "https://sheets.googleapis.com/v4/spreadsheets",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"properties": {"title": nombre}},
    )
    resp.raise_for_status()
    return resp.json()["spreadsheetId"]


def _get_token() -> str:
    from sheets import get_access_token
    return get_access_token()


async def _actualizar_variable_railway(nuevo_sheet_id: str) -> None:
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

    # Leer sheet origen
    sheet_id = os.getenv("GOOGLE_SHEET_ID_ALARMAS")
    wb_origen = get_sheet(sheet_id)

    # Crear nuevo spreadsheet vacío
    token = _get_token()
    nuevo_id = _crear_sheet_vacio(nuevo_nombre, token)
    wb_nuevo = get_sheet(nuevo_id)

    # Copiar cada tab del origen al nuevo, limpiando datos de técnicos
    tabs_origen = wb_origen.worksheets()
    primera = True
    for ws_origen in tabs_origen:
        nombre_tab = ws_origen.title
        todos = ws_origen.get_all_values()

        if primera:
            # La primera hoja ya existe en el nuevo sheet (Sheet1), renombrarla
            ws_nuevo = wb_nuevo.get_worksheet(0)
            ws_nuevo.update_title(nombre_tab)
            primera = False
        else:
            ws_nuevo = wb_nuevo.add_worksheet(title=nombre_tab, rows=200, cols=20)

        if not todos:
            continue

        # Si es tab de técnico, solo copiar encabezados (fila 1)
        if nombre_tab in TECNICOS_ALARMAS:
            ws_nuevo.update([todos[0]], "A1")
        else:
            # Tab Base u otras: copiar todo
            ws_nuevo.update(todos, "A1")

    await _actualizar_variable_railway(nuevo_id)

    return {
        "nuevo_id": nuevo_id,
        "nombre": nuevo_nombre,
        "url": f"https://docs.google.com/spreadsheets/d/{nuevo_id}",
    }
