import json
import os

import gspread
from google.auth.transport.requests import Request as AuthRequest
from google.oauth2.service_account import Credentials as SACredentials

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _sa_info() -> dict:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON no definida")
    return json.loads(raw)


def get_sheet(sheet_id: str | None = None) -> gspread.Spreadsheet:
    gc = gspread.service_account_from_dict(_sa_info())
    return gc.open_by_key(sheet_id or os.getenv("GOOGLE_SHEET_ID_ALARMAS"))


def get_access_token() -> str:
    """Devuelve un access token fresco para llamadas HTTP directas (Drive API)."""
    creds = SACredentials.from_service_account_info(_sa_info(), scopes=_SCOPES)
    creds.refresh(AuthRequest())
    return creds.token
