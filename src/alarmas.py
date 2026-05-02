import os
import json
import base64
import anthropic
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import logging
import re

logger = logging.getLogger(__name__)

TIPO_MAPPING = {
    "instalacion": "INSTALACION",
    "instalaciones": "INSTALACION",
    "instalacion ok": "INSTALACION",
    "mantenimiento": "INC/MTO/AMP",
    "mantenimiento ok": "INC/MTO/AMP",
    "ampliacion": "INC/MTO/AMP",
    "reconexion": "INC/MTO/AMP",
    "desmontaje": "DESMONTAJE",
    "desmontaje ok": "DESMONTAJE",
    "traslado": "TRASLADO",
    "traslado ok": "TRASLADO",
    "inviable": "INVIABLE",
    "cliente rechaza": "INVIABLE",
    "cliente ausente": "INVIABLE",
    "cancela": "INVIABLE",
    "tecnico no llega": "INVIABLE",
    "cliente solicita cambio": "INVIABLE",
}

def normalizar_tipo(tipo_raw):
    t = tipo_raw.lower().strip()
    for key, val in TIPO_MAPPING.items():
        if key in t:
            return val
    return tipo_raw.upper()

def extraer_notas_texto(notas):
    resultado = {"camaras": 0, "inviable": False}
    if not notas:
        return resultado
    n = notas.lower()
    if "inviable" in n:
        resultado["inviable"] = True
    match = re.search(r"(\d+|una|dos|tres|cuatro|cinco)\s*c[aa]mara", n)
    if match:
        word_map = {"una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
        val = match.group(1)
        resultado["camaras"] = word_map.get(val, int(val) if val.isdigit() else 1)
    elif "camara" in n:
        resultado["camaras"] = 1
    return resultado

async def procesar_screenshot_alarmas(imagen, notas_texto, tecnico, bot):
    file = await bot.get_file(imagen.file_id)
    img_bytes = await file.download_as_bytearray()
    img_b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = (
        "Analiza este screenshot de la app ZENER de instalaciones de alarmas. "
        "Extrae TODAS las ordenes completadas (con checkmark verde). "
        "Cada orden tiene un codigo SC seguido de numeros, un tipo entre parentesis "
        "(Instalaciones, Mantenimiento, Desmontaje, Traslado) y una fecha o hora. "
        "Devuelve SOLO un JSON sin texto adicional con esta estructura: "
        '{"ordenes": [{"orden": "SC2026185010", "tipo": "Instalaciones", '
        '"fecha": "30/04/2026", "completada": true}]}. '
        "Si hay fecha en el calendario usala para las ordenes sin fecha explicita."
    )
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
            {"type": "text", "text": prompt}
        ]}]
    )
    raw = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
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
                notas_por_orden[cod] = extraer_notas_texto(match.group(2).strip())
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
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(Request())
    gc = gspread.authorize(creds)
    return gc.open_by_key(os.getenv("GOOGLE_SHEET_ID_ALARMAS"))

async def confirmar_registro_alarmas(tecnico, ordenes):
    wb = get_sheet()
    try:
        ws = wb.worksheet(tecnico)
    except gspread.WorksheetNotFound:
        ws = wb.add_worksheet(title=tecnico, rows=1000, cols=3)
        ws.append_row(["FECHA", "ORDEN", "CODIGO"])
    registradas = set()
    try:
        existing = ws.get_all_values()
        for row in existing[1:]:
            if len(row) >= 3 and row[1]:
                registradas.add((row[1].strip().upper(), row[2].strip().upper()))
    except Exception:
        pass
    filas = []
    nuevas = 0
    for o in ordenes:
        tipo = o["tipo"]
        if o.get("inviable"):
            codigo_base = "ZA_INVIABLE"
        else:
            tipo_map = {
                "INSTALACION": "ZA_INSTALACION",
                "INC/MTO/AMP": "ZA_INC/MTO/AMP",
                "DESMONTAJE": "ZA_DESMONTAJE",
                "TRASLADO": "ZA_TRASLADO",
                "INVIABLE": "ZA_INVIABLE",
            }
            codigo_base = tipo_map.get(tipo, "ZA_" + tipo)
        fecha = o.get("fecha", "")
        orden = o["orden"]
        n_camaras = o.get("camaras", 0) or 0
        if (orden, codigo_base) not in registradas:
            filas.append([fecha, orden, codigo_base])
            nuevas += 1
        for _ in range(n_camaras):
            if (orden, "ZA_CAMARA") not in registradas:
                filas.append([fecha, orden, "ZA_CAMARA"])
                nuevas += 1
    if filas:
        ws.append_rows(filas, value_input_option="USER_ENTERED")
    return nuevas
