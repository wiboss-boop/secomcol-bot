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
    resultado = {"camaras": 0, "inviable": False}
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
- Un checkmark verde si está completada

Devuelve un JSON con esta estructura exacta, sin texto adicional:
{
  "ordenes": [
    {
      "orden": "SC2026185010",
      "tipo": "Instalaciones",
      "fec
