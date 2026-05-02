import os
import json
import base64
import anthropic
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from datetime import datetime
import logging
import re

logger = logging.getLogger(__name__)

PRECIOS = {
    "INSTALACION":   {"empresa": 66.50, "tecnico": 30.0},
    "INC/MTO/AMP":   {"empresa": 27.30, "tecnico": 14.0},
    "DESMONTAJE":    {"empresa": 21.84, "tecnico": 10.0},
    "TRASLADO":      {"empresa": 85.80, "tecnico": 40.0},
    "INVIABLE":      {"empresa": 14.00, "tecnico": 4.0},
    "CAMARA":        {"empresa": 8.00,  "tecnico": 4.0},
}

TIPO_MAPPING = {
    "instalacion": "INSTALACION",
    "instalaciones": "INSTALACION",
    "instalación": "INSTALACION",
    "instalaciones ok": "INSTALACION",
    "mantenimiento": "INC/MTO/AMP",
    "inc/mto/amp": "INC/MTO/AMP",
    "inc/mtto/ampl": "INC/MTO/AMP",
    "mantenimiento ok": "INC/MTO/AMP",
    "ampliación": "INC/MTO/AMP",
    "ampliacion": "INC/MTO/AMP",
    "reconexión": "INC/MTO/AMP",
    "reconexion": "INC/MTO/AMP",
    "desmontaje": "DESMONTAJE",
    "desmontaje ok": "DESMONTAJE",
    "traslado": "TRASLADO",
    "traslado ok": "TRASLADO",
    "inviable": "INVIABLE",
    "cliente rechaza": "INVIABLE",
    "cliente ausente": "INVIABLE",
    "cancela": "INVIABLE",
    "técnico no llega": "INVIABLE",
    "tecnico no llega": "INVIABLE",
    "cliente solicita cambio": "INVIABLE",
}


def normalizar_tipo(tipo_raw: str) -> str:
    t = tipo_raw.lower().strip()
    for key, val in TIPO_MAPPING.items():
        if key in t:
            return val
    return tipo_raw.upper()


def extraer_notas_texto(notas: str) -> dict:
    resultado = {"camaras": 0, "inviable": False, "texto": notas}
    if not notas:
        return resultado
    n = notas.lower()
    if "inviable" in n:
        resultado["inviable"] = True
    match = re.search(r"(\d+|una|dos|tres|cuatro|cinco)\s*c[áa]mara", n)
    if match:
        word_map = {"una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
        val = match.group(1)
        resultado["camaras"] = word_map.get(val, int(val) if val.isdigit() else 1)
    elif "cámara" in n or "camara" in n:
        resultado["camaras"] = 1
    return resultado


async def procesar_screenshot_alarmas(imagen, notas_texto: str, tecnico: str, bot) -> list:
    file = await bot.get_file(imagen.file_id)
    img_bytes = await file.download_as_bytearray()
    img_b64 = base64.standard_b64encode(img_bytes).decode("utf-8")

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    prompt = """Analiza este screenshot de la app ZENER de instalaciones de alarmas.
Extrae TODAS las órdenes que aparecen. Cada orden tiene:
- Un código que empieza por "SC" seguido de números (ej: SC2026185010)
- Un tipo entre paréntesis (Instalaciones, Mantenimiento, Desmontaje, Traslado, etc.)
- Una fecha o hora
- Un checkmark verde ✓ si está completada

Devuelve un JSON con esta estructura exacta, sin texto adicional:
{
  "ordenes": [
    {
      "orden": "SC2026185010",
      "tipo": "Instalaciones",
      "fecha": "30/04/2026",
      "completada": true
    }
  ]
}

Solo incluye órdenes con checkmark verde (completadas).
Si hay fecha en el calendario visible en la pantalla úsala para las órdenes sin fecha explícita.
"""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": img_b64
                    }
                },
                {"type": "text", "text": prompt}
            ]
        }]
    )

    raw = response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    ordenes_raw = data.get("ordenes", [])

    notas_por_orden = {}
    if notas_texto:
        for linea in notas_texto.strip().split("\n"):
            linea = linea.strip()
            if not linea:
                continue
            match = re.match(r"(SC\d+)\s*(.*)", linea, re.IGNORECASE)
            if match:
                cod = match.group(1).upper()
                nota = match.group(2).strip()
                notas_por_orden[cod] = extraer_notas_texto(nota)

    ordenes = []
    for o in ordenes_raw:
        codigo = o["orden"].upper()
        tipo_norm = normalizar_tipo(o.get("tipo", ""))
        nota = notas_por_orden.get(codigo, {"camaras": 0, "inviable": False})

        ordenes.append({
            "orden": codigo,
            "tipo": tipo_norm,
            "fecha": o.get("fecha", ""),
            "camaras": nota["camaras"],
            "inviable": nota.get("inviable", False),
        })

    return ordenes


def get_sheet():
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    creds.refresh(Request())
    gc = gspread.authorize(creds)
    sheet_id = os.getenv("GOOGLE_SHEET_ID_ALARMAS")
    return gc.open_by_key(sheet_id)


async def confirmar_registro_alarmas(tecnico: str, ordenes: list) -> int:
    wb = get_sheet()

    try:
        ws = wb.worksheet(tecnico)
    except gspread.WorksheetNotFound:
        ws = wb.add_worksheet(title=tecnico, rows=1000, cols=6)
        ws.append_row(["FECHA", "ORDEN", "CODIGO", "CAMARAS", "PRECIO", "TECNICO"])

    registradas = set()
    try:
        existing = ws.get_all_values()
        for row in existing[1:]:
            if len(row) > 1 and row[1]:
                registradas.add(row[1].strip().upper())
    except Exception:
        pass

    nuevas = 0
    filas = []
    for o in ordenes:
        if o["orden"] in registradas:
            continue

        tipo = o["tipo"]
        if o.get("inviable"):
            codigo = "ZA_INVIABLE"
        else:
            tipo_map = {
                "INSTALACION": "ZA_INSTALACION",
                "INC/MTO/AMP": "ZA_INC/MTO/AMP",
                "DESMONTAJE":  "ZA_DESMONTAJE",
                "TRASLADO":    "ZA_TRASLADO",
                "INVIABLE":    "ZA_INVIABLE",
            }
            codigo = tipo_map.get(tipo, f"ZA_{tipo}")

        n_camaras = o.get("camaras", 0) or 0

        precio_formula = (
            f'=IFERROR(BUSCARV("{codigo}",BASE!A:C,2,0),0)'
            f'+{n_camaras}*IFERROR(BUSCARV("ZA_CAMARA",BASE!A:C,2,0),0)'
        )
        tecnico_formula = (
            f'=IFERROR(BUSCARV("{codigo}",BASE!A:C,3,0),0)'
            f'+{n_camaras}*IFERROR(BUSCARV("ZA_CAMARA",BASE!A:C,3,0),0)'
        )

        filas.append([
            o.get("fecha", ""),
            o["orden"],
            codigo,
            n_camaras if n_camaras else "",
            precio_formula,
            tecnico_formula,
        ])
        nuevas += 1

    if filas:
        ws.append_rows(filas, value_input_option="USER_ENTERED")

    return nuevas
