# Secomcol Bot

Bot de Telegram para gestión de órdenes de alarmas y fibra óptica.

## Variables de entorno (Railway)

| Variable | Descripción |
|----------|-------------|
| `TELEGRAM_TOKEN` | Token del bot de BotFather |
| `ALLOWED_USERS` | IDs de Telegram autorizados, separados por coma |
| `ANTHROPIC_API_KEY` | API key de Anthropic para visión IA |
| `GOOGLE_SHEET_ID_ALARMAS` | ID del Google Sheet de alarmas |
| `GOOGLE_CREDENTIALS_JSON` | Contenido del JSON de credenciales de Google (en Railway como variable) |

## Comandos

- `/start` — Bienvenida
- `/alarma` — Registrar órdenes de un técnico de alarmas
- `/cancelar` — Cancelar operación en curso

## Flujo de alarmas

1. `/alarma` → seleccionar técnico (JEAN / JOEL / DIANA)
2. Enviar screenshot de ZENER (con notas opcionales en el caption)
3. Bot muestra las órdenes extraídas para confirmar
4. Confirmar → se guardan en Google Sheets
