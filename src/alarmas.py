import base64
import json
import logging
import os
import re
import unicodedata

import anthropic
import gspread

from sheets import get_sheet

logger = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

TIPO_MAPPING = {
    "instalacion": "INSTALACION",
    "instalaciones": "INSTALACION",
    "instalacion ok": "INSTALACION",
    "incidencia": "INCIDENCIAS",
    "incidencias": "INCIDENCIAS",
    "incidencia ok": "INCIDENCIAS",
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

CODIGO_POR_TIPO = {
    "INSTALACION": "ZA_INSTALACION",
    "INCIDENCIAS": "ZA_INCIDENCIAS",
    "INCIDENCIA": "ZA_INCIDENCIAS",
    "INC/MTO/AMP": "ZA_INC/MTO/AMP",
    "MANTENIMIENTO": "ZA_INC/MTO/AMP",
    "AMPLIACION": "ZA_INC/MTO/AMP",
    "DESMONTAJE": "ZA_DESMONTAJE",
    "TRASLADO": "ZA_TRASLADO",
    "INVIABLE": "ZA_INVIABLE",
}

_PROMPT_ZENER = (
    "Analiza este screenshot de la app ZENER de instalaciones de alarmas. "
    "Extrae TODAS las ordenes visibles, tengan o no checkmark verde. Incluye ordenes con circulo naranja, icono de lapiz, o cualquier estado. "
    "Cada orden tiene un codigo SC seguido de numeros, un tipo entre parentesis "
    "(Instalaciones, Incidencias, Mantenimiento, Desmontaje, Traslado) y una fecha o hora. "
    "Devuelve SOLO un JSON sin texto adicional con esta estructura: "
    '{"ordenes": [{"orden": "SC2026185010", "tipo": "Instalaciones", '
    '"fecha": "30/04/2026", "completada": true}]}. '
    "Si hay fecha en el calendario usala para las ordenes sin fecha explicita."
)


def _normalizar_tipo(tipo_raw: str) -> str:
    t = tipo_raw.lower().strip()
    for key, val in TIPO_MAPPING.items():
        if key in t:
            return val
    return tipo_raw.upper()


def _normalizar_texto(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def _extraer_notas(notas: str) -> dict:
    resultado = {"camaras": 0, "inviable": False}
    if not notas:
        return resultado
    n = _normalizar_texto(notas.lower())
    if "inviable" in n:
        resultado["inviable"] = True
    match = re.search(r"(\d+|una|dos|tres|cuatro|cinco)\s*c[aa]mara", n)
    if match:
        word_map = {"una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
        val = match.group(1)
        resultado["camaras"] = min(word_map.get(val, int(val) if val.isdigit() else 1), 10)
    elif "camara" in n:
        resultado["camaras"] = 1
    return resultado


async def procesar_screenshot_alarmas(imagen, notas_texto: str, tecnico: str, bot) -> list[dict]:
    img_bytes = await (await bot.get_file(imagen.file_id)).download_as_bytearray()
    img_b64 = base64.standard_b64encode(img_bytes).decode()

    response = await _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
            {"type": "text", "text": _PROMPT_ZENER},
        ]}],
    )
    raw = response.content[0].text.strip().removeprefix("```json").removesuffix("```").strip()
    ordenes_raw = json.loads(raw).get("ordenes", [])

    notas_norm = _normalizar_texto((notas_texto or "").lower())
    tiene_sc = bool(re.search(r"SC\d+", notas_texto or "", re.IGNORECASE))
    inviable_global = "inviable" in notas_norm and not tiene_sc
    camaras_global = _extraer_notas(notas_texto)["camaras"] if notas_texto and not tiene_sc else 0

    notas_por_orden: dict[str, dict] = {}
    if notas_texto and tiene_sc:
        for linea in notas_texto.strip().splitlines():
            linea = linea.strip()
            m = re.match(r"(SC\d+)\s*(.*)", linea, re.IGNORECASE)
            if m:
                notas_por_orden[m.group(1).upper()] = _extraer_notas(m.group(2).strip())

    ordenes = []
    for o in ordenes_raw:
        codigo = o["orden"].upper()
        nota = notas_por_orden.get(codigo, {"camaras": 0, "inviable": False})
        ordenes.append({
            "orden": codigo,
            "tipo": _normalizar_tipo(o.get("tipo", "")),
            "fecha": o.get("fecha", ""),
            "camaras": nota["camaras"] or camaras_global,
            "inviable": nota.get("inviable", False) or inviable_global,
        })
    return ordenes


def _limpiar_precio(v: str) -> float:
    return float(v.replace("€", "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip() or "0")


def _leer_precios_base(wb: gspread.Spreadsheet) -> dict:
    precios = {}
    for row in wb.worksheet("Base").get_all_values()[1:]:
        if len(row) >= 3 and row[0]:
            codigo = row[0].strip().replace("\xa0", "")
            try:
                precios[codigo] = {"precio": _limpiar_precio(row[1]), "tecnico": _limpiar_precio(row[2])}
            except Exception as e:
                logger.warning(f"BASE error en {codigo}: {e}")
    return precios


async def confirmar_registro_alarmas(tecnico: str, ordenes: list[dict]) -> int:
    wb = get_sheet()
    precios_base = _leer_precios_base(wb)

    try:
        ws = wb.worksheet(tecnico)
    except gspread.WorksheetNotFound:
        ws = wb.add_worksheet(title=tecnico, rows=1000, cols=5)
        ws.append_row(["FECHA", "ORDEN", "CODIGO", "PRECIO", "TECNICO"])

    existing = ws.get_all_values()
    registradas = {(r[1].strip().upper(), r[2].strip().upper()) for r in existing[1:] if len(r) >= 3 and r[1]}

    filas = []
    for o in ordenes:
        codigo_base = "ZA_INVIABLE" if o.get("inviable") else CODIGO_POR_TIPO.get(o["tipo"], f"ZA_{o['tipo']}")
        p = precios_base.get(codigo_base, {"precio": 0, "tecnico": 0})
        if (o["orden"], codigo_base) not in registradas:
            filas.append([o.get("fecha", ""), o["orden"], codigo_base, p["precio"], p["tecnico"]])
        p_cam = precios_base.get("ZA_CAMARA", {"precio": 0, "tecnico": 0})
        camaras_existentes = sum(1 for (ord_, cod_) in registradas if ord_ == o["orden"] and cod_ == "ZA_CAMARA")
        camaras_a_agregar = max(0, (o.get("camaras", 0) or 0) - camaras_existentes)
        for _ in range(camaras_a_agregar):
            filas.append([o.get("fecha", ""), o["orden"], "ZA_CAMARA", p_cam["precio"], p_cam["tecnico"]])
            registradas.add((o["orden"], "ZA_CAMARA"))

    if filas:
        primera_vacia = len(existing) + 1
        ws.insert_rows(filas, row=primera_vacia, value_input_option="USER_ENTERED")
    return len(filas)
