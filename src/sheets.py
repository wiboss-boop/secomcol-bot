import json
import logging
import os
import time
from typing import Callable, TypeVar

import gspread
from google.auth.transport.requests import Request as AuthRequest
from google.oauth2.service_account import Credentials as SACredentials
from gspread.exceptions import APIError

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Google Sheets devuelve estos codigos de forma intermitente (no son culpa del
# input): 429 = rate limit, 5xx = backend caido momentaneamente. Se reintentan.
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}

_ResultadoLlamada = TypeVar("_ResultadoLlamada")


def llamar_con_reintento(
    operacion: Callable[[], _ResultadoLlamada],
    *,
    intentos_maximos: int = 4,
    espera_base_segundos: float = 1.0,
) -> _ResultadoLlamada:
    """Ejecuta una operacion de Google Sheets reintentando ante errores transitorios.

    Sin reintento, un solo hipo de Google (503/500/429) le muestra error al
    usuario aunque el dato sea valido. Backoff exponencial: 1s, 2s, 4s.
    """
    for numero_intento in range(1, intentos_maximos + 1):
        try:
            return operacion()
        except APIError as error:
            status_code = getattr(error.response, "status_code", None)
            es_ultimo_intento = numero_intento == intentos_maximos
            if status_code not in _TRANSIENT_STATUS_CODES or es_ultimo_intento:
                raise
            espera_segundos = espera_base_segundos * (2 ** (numero_intento - 1))
            logger.warning(
                "Google Sheets %s en intento %d/%d; reintentando en %.0fs",
                status_code,
                numero_intento,
                intentos_maximos,
                espera_segundos,
            )
            time.sleep(espera_segundos)
    raise RuntimeError("llamar_con_reintento agoto los intentos sin resultado")


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
