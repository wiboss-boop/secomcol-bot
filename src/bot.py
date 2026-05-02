import logging
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)
from dotenv import load_dotenv
from alarmas import procesar_screenshot_alarmas, confirmar_registro_alarmas
from nuevo_mes import ejecutar_nuevo_mes, MESES

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ESPERANDO_TECNICO, ESPERANDO_SCREENSHOT = range(2)
TECNICOS_ALARMAS = ["JEAN", "JOEL", "DIANA"]
ALLOWED_USERS = list(map(int, os.getenv("ALLOWED_USERS", "").split(","))) if os.getenv("ALLOWED_USERS") else []


def check_auth(user_id):
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


async def start(update, context):
    if not check_auth(update.effective_user.id):
        return
    await update.message.reply_text(
        "Comandos disponibles:\n"
        "- /alarma — Registrar ordenes de alarmas\n"
        "- /nuevo_mes — Crear sheet del mes siguiente"
    )


async def cmd_alarma(update, context):
    if not check_auth(update.effective_user.id):
        return
    keyboard = [[InlineKeyboardButton(t, callback_data="tecnico_" + t)] for t in TECNICOS_ALARMAS]
    await update.message.reply_text(
        "De que tecnico es el screenshot?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ESPERANDO_TECNICO


async def seleccionar_tecnico(update, context):
    query = update.callback_query
    await query.answer()
    tecnico = query.data.replace("tecnico_", "")
    context.user_data["tecnico"] = tecnico
    await query.edit_message_text(
        "Tecnico: " + tecnico + "\n\nAhora envia el screenshot de ZENER.\n"
        "Si hay notas (camaras, inviables) escribelas en el mismo mensaje."
    )
    return ESPERANDO_SCREENSHOT


async def recibir_screenshot(update, context):
    if not check_auth(update.effective_user.id):
        return
    tecnico = context.user_data.get("tecnico")
    if not tecnico:
        await update.message.reply_text("Usa /alarma para empezar.")
        return ConversationHandler.END
    imagen = None
    notas_texto = ""
    if update.message.photo:
        imagen = update.message.photo[-1]
        notas_texto = update.message.caption or ""
    elif update.message.text:
        notas_texto = update.message.text
        imagen = context.user_data.get("ultima_imagen")
    if imagen:
        context.user_data["ultima_imagen"] = imagen
    if not imagen:
        await update.message.reply_text("Por favor envia el screenshot de ZENER.")
        return ESPERANDO_SCREENSHOT
    await update.message.reply_text("Analizando screenshot...")
    try:
        ordenes = await procesar_screenshot_alarmas(
            imagen=imagen, notas_texto=notas_texto,
            tecnico=tecnico, bot=context.bot
        )
        if not ordenes:
            await update.message.reply_text("No encontre ordenes en la imagen.")
            return ESPERANDO_SCREENSHOT
        context.user_data["ordenes_pendientes"] = ordenes
        resumen = tecnico + " - " + str(len(ordenes)) + " orden(es):\n\n"
        for o in ordenes:
            cam = " CAM+" + str(o["camaras"]) if o.get("camaras") else ""
            inv = " INVIABLE" if o.get("inviable") else ""
            resumen += "- " + o["orden"] + " | " + o["tipo"] + cam + inv + "\n"
            if o.get("fecha"):
                resumen += "  Fecha: " + o["fecha"] + "\n"
        resumen += "\nRegistrar estas ordenes?"
        keyboard = [[
            InlineKeyboardButton("Confirmar", callback_data="confirmar_alarmas"),
            InlineKeyboardButton("Cancelar", callback_data="cancelar_alarmas")
        ]]
        await update.message.reply_text(resumen, reply_markup=InlineKeyboardMarkup(keyboard))
        return ESPERANDO_TECNICO
    except Exception as e:
        logger.error("Error procesando screenshot: " + str(e))
        await update.message.reply_text("Error al procesar la imagen: " + str(e))
        return ESPERANDO_SCREENSHOT


async def confirmar_alarmas(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cancelar_alarmas":
        await query.edit_message_text("Cancelado.")
        context.user_data.clear()
        return ConversationHandler.END
    ordenes = context.user_data.get("ordenes_pendientes", [])
    tecnico = context.user_data.get("tecnico")
    await query.edit_message_text("Guardando en Google Sheets...")
    try:
        n = await confirmar_registro_alarmas(tecnico=tecnico, ordenes=ordenes)
        await query.edit_message_text(str(n) + " orden(es) registradas para " + tecnico)
    except Exception as e:
        logger.error("Error guardando alarmas: " + str(e))
        await query.edit_message_text("Error al guardar: " + str(e))
    context.user_data.clear()
    return ConversationHandler.END


async def cancelar(update, context):
    context.user_data.clear()
    await update.message.reply_text("Operacion cancelada.")
    return ConversationHandler.END


async def cmd_nuevo_mes(update, context):
    if not check_auth(update.effective_user.id):
        return
    now = datetime.now()
    if now.month == 12:
        mes, ano = 1, now.year + 1
    else:
        mes, ano = now.month + 1, now.year
    context.user_data["nuevo_mes"] = mes
    context.user_data["nuevo_ano"] = ano
    keyboard = [[
        InlineKeyboardButton("Confirmar", callback_data="confirmar_nuevo_mes"),
        InlineKeyboardButton("Cancelar", callback_data="cancelar_nuevo_mes"),
    ]]
    await update.message.reply_text(
        "Vas a crear el Sheet de " + MESES[mes] + " " + str(ano) + ".\n\n"
        "Esto duplicara el Sheet actual, limpiara los datos de JEAN, JOEL y DIANA "
        "y actualizara el bot para usar el nuevo archivo.\n\nConfirmas?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def callback_nuevo_mes(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cancelar_nuevo_mes":
        await query.edit_message_text("Cancelado.")
        return
    mes = context.user_data.get("nuevo_mes")
    ano = context.user_data.get("nuevo_ano")
    await query.edit_message_text("Creando Sheet " + MESES[mes] + " " + str(ano) + "...")
    try:
        resultado = await ejecutar_nuevo_mes(ano, mes)
        await query.edit_message_text(
            "Sheet " + resultado["nombre"] + " creado.\n\n"
            "URL: " + resultado["url"]
        )
    except Exception as e:
        logger.error("Error creando nuevo mes: " + str(e))
        await query.edit_message_text("Error: " + str(e))


def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_TOKEN no configurado")
    app = Application.builder().token(token).build()

    alarma_conv = ConversationHandler(
        entry_points=[CommandHandler("alarma", cmd_alarma)],
        states={
            ESPERANDO_TECNICO: [
                CallbackQueryHandler(seleccionar_tecnico, pattern="^tecnico_"),
                CallbackQueryHandler(confirmar_alarmas, pattern="^(confirmar|cancelar)_alarmas$"),
            ],
            ESPERANDO_SCREENSHOT: [
                MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, recibir_screenshot),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("nuevo_mes", cmd_nuevo_mes))
    app.add_handler(CallbackQueryHandler(callback_nuevo_mes, pattern="^(confirmar|cancelar)_nuevo_mes$"))
    app.add_handler(alarma_conv)

    logger.info("Bot iniciado")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
