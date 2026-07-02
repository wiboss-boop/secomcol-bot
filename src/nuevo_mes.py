import logging
import os
from datetime import datetime
import httpx
from sheets import get_sheet, get_access_token

logger = logging.getLogger(__name__)

TECNICOS_ALARMAS = ["JEAN", "JOEL", "DIANA", "MARTIN"]

MESES = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}

TABS_TECNICOS = [
    "CRISTIAN", "MARTIN", "JAMES", "JEAN", "YOHAN",
    "ERCS", "HANS", "JOEL", "DIANA", "AYMAN", "LUIS E"
]
TABS_INTEGRAS = ["Base", "Hoja6"]
TABS_SOLO_ENCABEZADO = TABS_TECNICOS + ["Descuentos"]
TABS_OMITIR = ["Hoja1"]

# Hasta que fila se siembran las formulas de PRECIO/TECNICO en cada hoja de tecnico.
FORMULA_MAXROW = 500


def _sembrar_formulas_precio(ws, primera_fila_datos: int, hasta: int = FORMULA_MAXROW) -> None:
    """Siembra en PRECIO (col D) y TECNICO (col E) la formula VLOOKUP contra la
    pestana Base, para que cualquier CODIGO que escriban los bots se tarifique solo.
    El bot de fibra solo escribe FECHA/ORDEN/CODIGO; sin esta formula esas columnas
    quedan vacias (el bot de alarmas escribe su precio literal, que la sobreescribe).
    """
    if ws.row_count < hasta:
        ws.resize(rows=hasta + 20)
    grid = [
        [
            f'=IF($C{r}="","",IFERROR(VLOOKUP($C{r},Base!$A:$C,2,FALSE),""))',
            f'=IF($C{r}="","",IFERROR(VLOOKUP($C{r},Base!$A:$C,3,FALSE),""))',
        ]
        for r in range(primera_fila_datos, hasta + 1)
    ]
    ws.update(grid, f"D{primera_fila_datos}:E{hasta}", value_input_option="USER_ENTERED")


def _get_token() -> str:
    return get_access_token()


def _crear_sheet_vacio(nombre: str, token: str) -> str:
    import requests
    resp = requests.post(
        "https://sheets.googleapis.com/v4/spreadsheets",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"properties": {"title": nombre}},
    )
    resp.raise_for_status()
    return resp.json()["spreadsheetId"]


async def _actualizar_variable_railway(nombre_var: str, valor: str) -> None:
    token = os.getenv("RAILWAY_TOKEN")
    service_id = os.getenv("RAILWAY_SERVICE_ID")
    environment_id = os.getenv("RAILWAY_ENVIRONMENT_ID", "")

    variables: dict = {
        "input": {
            "serviceId": service_id,
            "name": nombre_var,
            "value": valor,
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


async def _duplicar_sheet(sheet_id_origen: str, nuevo_nombre: str) -> str:
    token = _get_token()
    wb_origen = get_sheet(sheet_id_origen)
    nuevo_id = _crear_sheet_vacio(nuevo_nombre, token)
    wb_nuevo = get_sheet(nuevo_id)

    tabs_origen = wb_origen.worksheets()
    primera = True

    for ws_origen in tabs_origen:
        nombre_tab = ws_origen.title

        if nombre_tab in TABS_OMITIR:
            continue

        todos = ws_origen.get_all_values()

        if primera:
            ws_nuevo = wb_nuevo.get_worksheet(0)
            ws_nuevo.update_title(nombre_tab)
            primera = False
        else:
            ws_nuevo = wb_nuevo.add_worksheet(title=nombre_tab, rows=max(200, len(todos) + 10), cols=20)

        if not todos:
            continue

        if nombre_tab in TABS_SOLO_ENCABEZADO:
            # Copiar las filas de encabezado hasta la de titulos de columna (la que trae
            # "CODIGO"), descartando los datos del mes anterior.
            fila_codigo = next(
                (i for i, r in enumerate(todos) if len(r) > 2 and r[2].strip().upper() == "CODIGO"),
                0,
            )
            ws_nuevo.update(todos[: fila_codigo + 1], "A1")
            # Hojas de tecnico: sembrar las formulas VLOOKUP de PRECIO/TECNICO.
            if nombre_tab in TABS_TECNICOS:
                _sembrar_formulas_precio(ws_nuevo, primera_fila_datos=fila_codigo + 2)
        else:
            ws_nuevo.update(todos, "A1")

    return nuevo_id


async def ejecutar_nuevo_mes(año: int | None = None, mes: int | None = None) -> dict:
    now = datetime.now()
    año = año or now.year
    mes = mes or now.month
    nombre_mes = MESES[mes]
    nuevo_nombre = f"{nombre_mes}_{año}"

    sheet_id_alarmas = os.getenv("GOOGLE_SHEET_ID_ALARMAS")
    nuevo_id_alarmas = await _duplicar_sheet(sheet_id_alarmas, f"ALARMAS_{nuevo_nombre}")
    await _actualizar_variable_railway("GOOGLE_SHEET_ID_ALARMAS", nuevo_id_alarmas)

    sheet_id_fibra = os.getenv("ACTIVE_SHEET_ID")
    nuevo_id_fibra = await _duplicar_sheet(sheet_id_fibra, nuevo_nombre)
    await _actualizar_variable_railway("ACTIVE_SHEET_ID", nuevo_id_fibra)

    return {
        "nombre": nuevo_nombre,
        "alarmas_id": nuevo_id_alarmas,
        "fibra_id": nuevo_id_fibra,
        "alarmas_url": f"https://docs.google.com/spreadsheets/d/{nuevo_id_alarmas}",
        "fibra_url": f"https://docs.google.com/spreadsheets/d/{nuevo_id_fibra}",
    }
