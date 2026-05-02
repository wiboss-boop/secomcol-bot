#!/usr/bin/env python3
"""
En Railway las credenciales de Google se pasan como variable de entorno
GOOGLE_CREDENTIALS_JSON (el contenido del JSON completo).
Este script las escribe en disco antes de arrancar el bot.
"""
import os
import sys
import json

creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
if creds_json:
    os.makedirs("config", exist_ok=True)
    with open("config/google_credentials.json", "w") as f:
        f.write(creds_json)

# Arrancar el bot
os.execlp("python", "python", "src/bot.py")
