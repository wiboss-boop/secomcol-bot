import base64
import json
import logging
import os
import re
import unicodedata
from datetime import datetime

import anthropic
import gspread

from sheets import get_sheet, llamar_con_reintento

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
    "Extrae TODAS las ordenes visibles, con cualquier estado (check verde, icono de lapiz, circulo naranja, etc.). "
    "Cada orden tiene un codigo (por ejemplo SC..., OT..., LS...), un tipo entre parentesis "
    "(Instalaciones, Incidencias, Mantenimiento, Desmontaje, Traslado) y una fecha u hora. "
    "El campo 'completada' debe ser true SOLO si la orden muestra un checkmark VERDE a la derecha; "
    "si en su lugar muestra un icono de lapiz/edicion o cualquier otro estado sin check verde, debe ser false. "
    "Devuelve SOLO un JSON sin texto adicional con esta estructura: "
    '{"ordenes": [{"orden": "SC2026185010", "tipo": "Instalaciones", '
    '"fecha": "30/04/2026", "completada": true}]}. '
    "Si hay fecha en el calendario usala para las ordenes sin fecha explicita."
)


def _anio_desde_orden(orden: str) -> int | None:
    """El codigo de orden ZENER embebe el anio: SC<AAAA><secuencia> (p.ej. SC2026185010)."""
    match = re.match(r"SC(\d{4})", orden.upper())
    if match:
        anio = int(match.group(1))
        if 2020 <= anio <= 2100:
            return anio
    return None


def _corregir_anio_fecha(fecha_raw: str, orden: str) -> str:
    """El LLM lee bien dia/mes del screenshot pero inventa el anio (sesgo -> 2025).
    Conserva dia/mes y fuerza el anio real: primero el del codigo de orden
    (SC<AAAA>...), si no, el anio actual. Sin dia/mes usable -> fecha de hoy.
    """
    anio = _anio_desde_orden(orden) or datetime.now().year
    match = re.search(r"(\d{1,2})[/\-](\d{1,2})", fecha_raw or "")
    if match:
        dia, mes = int(match.group(1)), int(match.group(2))
        try:
            return datetime(anio, mes, dia).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return datetime.now().strftime("%d/%m/%Y")


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
    omitidas = 0
    for o in ordenes_raw:
        # Solo se registran las ordenes con check verde (completadas). Las que muestran
        # icono de lapiz u otro estado se ignoran.
        if not o.get("completada"):
            omitidas += 1
            continue
        codigo = o["orden"].upper()
        nota = notas_por_orden.get(codigo, {"camaras": 0, "inviable": False})
        ordenes.append({
            "orden": codigo,
            "tipo": _normalizar_tipo(o.get("tipo", "")),
            "fecha": _corregir_anio_fecha(o.get("fecha", ""), codigo),
            "camaras": nota["camaras"] or camaras_global,
            "inviable": nota.get("inviable", False) or inviable_global,
        })
    if omitidas:
        logger.info("Alarmas: %d orden(es) omitidas por no tener check verde", omitidas)
    return ordenes


def _limpiar_precio(v: str) -> float:
    return float(v.replace("€", "").replace("$", "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip() or "0")


def _leer_precios_base(wb: gspread.Spreadsheet) -> dict:
    precios = {}
    filas_base = llamar_con_reintento(lambda: wb.worksheet("Base").get_all_values())
    for row in filas_base[1:]:
        if len(row) >= 3 and row[0]:
            codigo = row[0].strip().replace("\xa0", "")
            try:
                precios[codigo] = {"precio": _limpiar_precio(row[1]), "tecnico": _limpiar_precio(row[2])}
            except Exception as e:
                logger.warning(f"BASE error en {codigo}: {e}")
    return precios


async def confirmar_registro_alarmas(tecnico: str, ordenes: list[dict]) -> int:
    wb = llamar_con_reintento(get_sheet)
    precios_base = _leer_precios_base(wb)

    try:
        ws = llamar_con_reintento(lambda: wb.worksheet(tecnico))
    except gspread.WorksheetNotFound:
        ws = llamar_con_reintento(lambda: wb.add_worksheet(title=tecnico, rows=1000, cols=5))
        llamar_con_reintento(lambda: ws.append_row(["FECHA", "ORDEN", "CODIGO", "PRECIO", "TECNICO"]))

    existing = llamar_con_reintento(lambda: ws.get_all_values())
    registradas = {(r[1].strip().upper(), r[2].strip().upper()) for r in existing[1:] if len(r) >= 3 and r[1]}

    filas = []
    for o in ordenes:
        codigo_base = CODIGO_POR_TIPO.get(o["tipo"], f"ZA_{o['tipo']}")
        p = precios_base.get(codigo_base, {"precio": 0, "tecnico": 0})
        if (o["orden"], codigo_base) not in registradas:
            filas.append([o.get("fecha", ""), o["orden"], codigo_base, p["precio"], p["tecnico"]])
            registradas.add((o["orden"], codigo_base))

    if filas:
        llamar_con_reintento(lambda: ws.append_rows(filas, value_input_option="USER_ENTERED"))
    return len(filas)
