import logging
import os
import re
import unicodedata
from datetime import datetime

import gspread

from sheets import get_sheet

logger = logging.getLogger(__name__)

TECNICOS = [
    "CRISTIAN", "MARTIN", "ALVARO", "YOHAN", "ERCS",
    "HANS", "LUIS E", "JEAN", "JOEL", "DIANA", "AYMAN", "JAMES",
]


def _sin_acentos(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def parsear_descuento(texto: str) -> dict | None:
    t = _sin_acentos(texto.lower().strip())
    if "descontar" not in t:
        return None

    monto_match = re.search(r"(\d+(?:[.,]\d+)?)", t)
    if not monto_match:
        return None
    monto = float(monto_match.group(1).replace(",", "."))

    tecnico = next((tec for tec in TECNICOS if _sin_acentos(tec.lower()) in t), None)
    if not tecnico:
        return None

    concepto_match = re.search(r"\bde\s+(.+?)(?:\s+a\s+\w+)?$", t)
    concepto = concepto_match.group(1).strip().upper() if concepto_match else "DESCUENTO"

    return {"tecnico": tecnico, "monto": monto, "concepto": concepto}


def registrar_descuento(tecnico: str, concepto: str, monto: float) -> None:
    wb = get_sheet()
    try:
        ws = wb.worksheet("Descuentos")
    except gspread.WorksheetNotFound:
        ws = wb.add_worksheet(title="Descuentos", rows=1000, cols=4)
        ws.append_row(["FECHA", "TECNICO", "CONCEPTO", "MONTO"])
    ws.append_row(
        [datetime.now().strftime("%d/%m/%Y"), tecnico, concepto, monto],
        value_input_option="USER_ENTERED",
    )
