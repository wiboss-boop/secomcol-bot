import os
import re
import logging
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import gspread

logger = logging.getLogger(__name__)

TECNICOS = [
    "CRISTIAN", "MARTIN", "ALVARO", "YOHAN", "ERCS",
    "HANS", "DIEGO", "LUIS E", "JEAN", "JOEL", "DIANA"
]

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

def parsear_descuento(texto):
    """
    Parsea mensajes como:
    "descontar 50 euros a cristian de gasolina"
    "descontar 100 a martin adelanto"
    Devuelve dict con tecnico, concepto, monto o None si no se reconoce.
    """
    t = texto.lower().strip()
    if "descontar" not in t:
        return None

    monto_match = re.search(r"(\d+(?:[.,]\d+)?)", t)
    if not monto_match:
        return None
    monto = float(monto_match.group(1).replace(",", "."))

    tecnico = None
    for tec in TECNICOS:
        if tec.lower() in t:
            tecnico = tec
            break
    if not tecnico:
        return None

    partes = re.split(r"descontar|euros?|a\s+\w+|de\s+|\d+", t)
    concepto = " ".join(p.strip() for p in partes if p.strip() and len(p.strip()) > 2)
    concepto = concepto.upper() or "DESCUENTO"

    return {"tecnico": tecnico, "monto": monto, "concepto": concepto}

def registrar_descuento(tecnico, concepto, monto):
    wb = get_sheet()
    try:
        ws = wb.worksheet("Descuentos")
    except gspread.WorksheetNotFound:
        ws = wb.add_worksheet(title="Descuentos", rows=1000, cols=4)
        ws.append_row(["FECHA", "TECNICO", "CONCEPTO", "MONTO"])
    fecha = datetime.now().strftime("%d/%m/%Y")
    ws.append_row([fecha, tecnico, concepto, monto], value_input_option="USER_ENTERED")
