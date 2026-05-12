import logging
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
import os
import re
from datetime import datetime

import gspread

from sheets import get_sheet

logger = logging.getLogger(__name__)

TECNICOS = [
    "CRISTIAN", "MARTIN", "ALVARO", "YOHAN", "ERCS",
    "HANS", "DIEGO", "LUIS E", "JEAN", "JOEL", "DIANA",
]


def parsear_descuento(texto: str) -> dict | None:
    t = texto.lower().strip()
    if "descontar" not in t:
        return None

    monto_match = re.search(r"(\d+(?:[.,]\d+)?)", t)
    if not monto_match:
        return None
    monto = float(monto_match.group(1).replace(",", "."))

    tecnico = next((tec for tec in TECNICOS if tec.lower() in t), None)
    if not tecnico:
        return None

    concepto_match = re.search(r"\bde\s+(.+)$", t)
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
