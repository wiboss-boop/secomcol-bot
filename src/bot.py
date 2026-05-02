import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)
from dotenv import load_dotenv
from alarmas import procesar_screenshot_alarmas, confirmar_registro_alarmas

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Estados de conversación
ESPERANDO_TECNICO, ESPERANDO_SCREENSHOT = range(2)

TECNICOS_ALARMAS = ["JEAN", "JOEL", "DIANA"]
ALLOWED_USERS = list(map(int, os.getenv("ALLOWED_USERS", "").split(","))) if os.getenv("ALLOWED_USERS") else []


def check_auth(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True  # Sin restricción si no está configurado
    return user_id in ALLOWED_USERS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id):
        return
    await update.message.reply_text(
        "👋 *Bot Secomcol*\n\n"
        "Comandos disponibles:\n"
        "• /alarma — Registrar órdenes de alarmas\n"
        "• /ayuda — Ver ayuda",
        parse_mode="Markdown"
    )


async def cmd_alarma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id):
        return

    keyboard = [
        [InlineKeyboardButton(t, callback_data=f"tecnico_{t}")]
        for t in TECNICOS_ALARMAS
    ]
    await update.message.reply_text(
        "¿De qué técnico es el screenshot?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ESPERANDO_TECNICO


async def seleccionar_tecnico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tecnico = query.data.replace("tecnico_", "")
    context.user_data["tecnico"] = tecnico

    await query.edit_message_text(
        f"✅ Técnico: *{tecnico}*\n\nAhora envía el screenshot de ZENER.\n\n"
        f"Si hay notas (cámaras adicionales, inviables), "
        f"escríbelas en el mismo mensaje o en el siguiente.",
        parse_mode="Markdown"
    )
    return ESPERANDO_SCREENSHOT


async def recibir_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id):
        return

    tecnico = context.user_data.get("tecnico")
    if not tecnico:
        await update.message.reply_text("Usa /alarma para empezar.")
        return ConversationHandler.END

    # Puede llegar imagen, texto, o imagen+texto (caption)
    imagen = None
    notas_texto = ""

    if update.message.photo:
        imagen = update.message.photo[-1]  # Mayor resolución
        notas_texto = update.message.caption or ""
    elif update.message.text:
        # Si manda texto después de la imagen (notas adicionales)
        notas_texto = update.message.text
        imagen = context.user_data.get("ultima_imagen")

    if imagen:
        context.user_data["ultima_imagen"] = imagen

    if not imagen:
        await update.message.reply_text(
            "Por favor envía el screenshot de ZENER."
        )
        return ESPERANDO_SCREENSHOT

    await update.message.reply_text("🔍 Analizando screenshot...")

    try:
        ordenes = await procesar_screenshot_alarmas(
            imagen=imagen,
            notas_texto=notas_texto,
            tecnico=tecnico,
            bot=context.bot
        )

        if not ordenes:
            await update.message.reply_text(
                "No encontré órdenes en la imagen. ¿Es un screenshot de ZENER?"
            )
            return ESPERANDO_SCREENSHOT

        # Guardar para confirmación
        context.user_data["ordenes_pendientes"] = ordenes

        # Mostrar resumen para confirmar
        resumen = f"📋 *{tecnico} — {len(ordenes)} orden(es) encontradas:*\n\n"
        for o in ordenes:
            camara_str = f" 📷 {o['camaras']}" if o.get("camaras") else ""
            inviable_str = " ❌ INVIABLE" if o.get("inviable") else ""
            resumen += f"• `{o['orden']}` — {o['tipo']}{camara_str}{inviable_str}\n"
            if o.get("fecha"):
                resumen += f"  📅 {o['fecha']}\n"

        resumen += "\n¿Registrar estas órdenes?"

        keyboard = [
            [
                InlineKeyboardButton("✅ Confirmar", callback_data="confirmar_alarmas"),
                InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_alarmas")
            ]
        ]
        await update.message.reply_text(
            resumen,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ESPERANDO_TECNICO  # Reutilizamos estado para esperar confirmación

    except Exception as e:
        logger.error(f"Error procesando screenshot: {e}")
        await update.message.reply_text(
            f"❌ Error al procesar la imagen: {str(e)}\nIntenta de nuevo."
        )
        return ESPERANDO_SCREENSHOT


async def confirmar_alarmas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancelar_alarmas":
        await query.edit_message_text("❌ Cancelado. Usa /alarma para intentar de nuevo.")
        context.user_data.clear()
        return ConversationHandler.END

    ordenes = context.user_data.get("ordenes_pendientes", [])
    tecnico = context.user_data.get("tecnico")

    await query.edit_message_text("⏳ Guardando en Google Sheets...")

    try:
        n = await confirmar_registro_alarmas(tecnico=tecnico, ordenes=ordenes)
        await query.edit_message_text(
            f"✅ *{n} orden(es) registradas* para {tecnico} en Google Sheets.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error guardando alarmas: {e}")
        await query.edit_message_text(f"❌ Error al guardar: {str(e)}")

    context.user_data.clear()
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Operación cancelada.")
    return ConversationHandler.END


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
    app.add_handler(alarma_conv)

    logger.info("Bot iniciado")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
