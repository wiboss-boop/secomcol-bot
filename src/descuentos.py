import logging
import os
import re
import unicodedata
from datetime import datetime

import gspread

from sheets import get_sheet, llamar_con_reintento

logger = logging.getLogger(__name__)

TECNICOS = [
    "CRISTIAN", "MARTIN", "ALVARO", "YOHAN", "ERCS",
    "HANS", "LUIS E", "JEAN", "JOEL", "DIANA", "AYMAN", "JAMES",
]


def _sin_acentos(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _mensaje_menciona_tecnico(texto_normalizado: str, tecnico: str) -> bool:
    """Matchea por el primer nombre con limite de palabra.

    El canonico puede llevar apellido/inicial (p.ej. "LUIS E"), pero en el
    mensaje se suele escribir solo el nombre ("Luis"). Se compara el primer
    token con \\b para no matchear substrings dentro de otra palabra.
    """
    primer_nombre = _sin_acentos(tecnico.lower()).split()[0]
    return re.search(rf"\b{re.escape(primer_nombre)}\b", texto_normalizado) is not None


def parsear_descuento(texto: str) -> dict | None:
    t = _sin_acentos(texto.lower().strip())
    if "descontar" not in t:
        return None

    monto_match = re.search(r"(\d+(?:[.,]\d+)?)", t)
    if not monto_match:
        return None
    monto = float(monto_match.group(1).replace(",", "."))

    tecnico = next((tec for tec in TECNICOS if _mensaje_menciona_tecnico(t, tec)), None)
    if not tecnico:
        return None

    # El concepto es lo que va tras "de ...", recortando el destinatario final
    # ("a Luis", "a Luis E"): se ancla al primer nombre del tecnico para no
    # tragarse un concepto que legitimamente contenga " a " (p.ej. "a la 80").
    primer_nombre = _sin_acentos(tecnico.lower()).split()[0]
    concepto_match = re.search(rf"\bde\s+(.+?)(?:\s+a\s+{re.escape(primer_nombre)}.*)?$", t)
    concepto = concepto_match.group(1).strip().upper() if concepto_match else "DESCUENTO"

    return {"tecnico": tecnico, "monto": monto, "concepto": concepto}


def registrar_descuento(tecnico: str, concepto: str, monto: float) -> None:
    wb = llamar_con_reintento(get_sheet)
    try:
        ws = llamar_con_reintento(lambda: wb.worksheet("Descuentos"))
    except gspread.WorksheetNotFound:
        ws = llamar_con_reintento(lambda: wb.add_worksheet(title="Descuentos", rows=1000, cols=4))
        llamar_con_reintento(lambda: ws.append_row(["FECHA", "TECNICO", "CONCEPTO", "MONTO"]))
    llamar_con_reintento(
        lambda: ws.append_row(
            [datetime.now().strftime("%d/%m/%Y"), tecnico, concepto, monto],
            value_input_option="USER_ENTERED",
        )
    )
