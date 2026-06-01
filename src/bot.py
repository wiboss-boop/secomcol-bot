import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)

from alarmas import confirmar_registro_alarmas, procesar_screenshot_alarmas
from descuentos import parsear_descuento, registrar_descuento
from nomina import generar_excel
from nuevo_mes import MESES, ejecutar_nuevo_mes

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ESPERANDO_TECNICO, ESPERANDO_SCREENSHOT = range(2)
TECNICOS_ALARMAS = ["JEAN", "JOEL", "DIANA"]
ALLOWED_USERS = (
    list(map(int, os.getenv("ALLOWED_USERS", "").split(",")))
    if os.getenv("ALLOWED_USERS") else []
)


def check_auth(user_id: int) -> bool:
    return not ALLOWED_USERS or user_id in ALLOWED_USERS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not check_auth(update.effective_user.id):
        return
    await update.message.reply_text(
        "Comandos disponibles:\n"
        "- /alarma — Registrar ordenes de alarmas\n"
        "- /nuevo_mes — Crear sheet del mes siguiente\n"
        "- /nomina [MES] [AÑO] — Generar Excel de nomina"
    )


async def cmd_alarma(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not check_auth(update.effective_user.id):
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(t, callback_data=f"tecnico_{t}")] for t in TECNICOS_ALARMAS]
    await update.message.reply_text(
        "De que tecnico es el screenshot?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ESPERANDO_TECNICO


async def seleccionar_tecnico(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    tecnico = query.data.removeprefix("tecnico_")
    context.user_data["tecnico"] = tecnico
    await query.edit_message_text(
        f"Tecnico: {tecnico}\n\nAhora envia el screenshot de ZENER.\n"
        "Si hay notas (camaras, inviables) escribelas en el mismo mensaje."
    )
    return ESPERANDO_SCREENSHOT


async def recibir_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not check_auth(update.effective_user.id):
        return ConversationHandler.END
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
            imagen=imagen, notas_texto=notas_texto, tecnico=tecnico, bot=context.bot
        )
        if not ordenes:
            await update.message.reply_text("No encontre ordenes en la imagen.")
            return ESPERANDO_SCREENSHOT

        context.user_data["ordenes_pendientes"] = ordenes
        lineas = [f"- {o['orden']} | {o['tipo']}" +
                  (f" CAM+{o['camaras']}" if o.get("camaras") else "") +
                  (" INVIABLE" if o.get("inviable") else "") +
                  (f"\n  Fecha: {o['fecha']}" if o.get("fecha") else "")
                  for o in ordenes]
        resumen = f"{tecnico} - {len(ordenes)} orden(es):\n\n" + "\n".join(lineas) + "\n\nRegistrar estas ordenes?"
        keyboard = [[
            InlineKeyboardButton("Confirmar", callback_data="confirmar_alarmas"),
            InlineKeyboardButton("Cancelar", callback_data="cancelar_alarmas"),
        ]]
        await update.message.reply_text(resumen, reply_markup=InlineKeyboardMarkup(keyboard))
        return ESPERANDO_TECNICO
    except Exception as e:
        logger.error(f"Error procesando screenshot: {e}")
        await update.message.reply_text(f"Error al procesar la imagen: {e}")
        return ESPERANDO_SCREENSHOT


async def confirmar_alarmas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        await query.edit_message_text(f"{n} orden(es) registradas para {tecnico}")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Error guardando alarmas: {e}\n{tb}")
        await query.edit_message_text(f"Error al guardar: {e!r}\n{tb[-500:]}")
    context.user_data.clear()
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Operacion cancelada.")
    return ConversationHandler.END


async def timeout_alarma(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Sesion expirada por inactividad. Usa /alarma para empezar de nuevo.",
        )
    return ConversationHandler.END


async def cmd_nuevo_mes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not check_auth(update.effective_user.id):
        return
    now = datetime.now()
    mes = now.month
    ano = now.year
    context.user_data["nuevo_mes"] = mes
    context.user_data["nuevo_ano"] = ano
    keyboard = [[
        InlineKeyboardButton("Confirmar", callback_data="confirmar_nuevo_mes"),
        InlineKeyboardButton("Cancelar", callback_data="cancelar_nuevo_mes"),
    ]]
    await update.message.reply_text(
        f"Vas a crear el Sheet de {MESES[mes]} {ano}.\n\n"
        "Esto duplicara el Sheet actual, limpiara los datos de JEAN, JOEL y DIANA "
        "y actualizara el bot para usar el nuevo archivo.\n\nConfirmas?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def callback_nuevo_mes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "cancelar_nuevo_mes":
        await query.edit_message_text("Cancelado.")
        return
    mes = context.user_data.get("nuevo_mes")
    ano = context.user_data.get("nuevo_ano")
    await query.edit_message_text(f"Creando Sheet {MESES[mes]} {ano}...")
    try:
        resultado = await ejecutar_nuevo_mes(ano, mes)
        await query.edit_message_text(
            f"Sheets {resultado['nombre']} creados.\n\n"
            f"Fibra: {resultado['fibra_url']}\n"
            f"Alarmas: {resultado['alarmas_url']}"
        )
    except Exception as e:
        logger.error(f"Error creando nuevo mes: {e}")
        await query.edit_message_text(f"Error: {e}")


async def cmd_nomina(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not check_auth(update.effective_user.id):
        return
    now = datetime.now()
    mes = context.args[0] if context.args else MESES[now.month]
    ano = context.args[1] if len(context.args) > 1 else str(now.year)
    await update.message.reply_text(f"Generando nomina {mes} {ano}...")
    try:
        nombre = generar_excel(mes, ano)
        with open(nombre, "rb") as f:
            await update.message.reply_document(document=f, filename=nombre)
        os.remove(nombre)
    except Exception as e:
        logger.error(f"Error generando nomina: {e}")
        await update.message.reply_text(f"Error: {e}")


async def manejar_descuento(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not check_auth(update.effective_user.id):
        return
    resultado = parsear_descuento(update.message.text or "")
    if not resultado:
        await update.message.reply_text("No entendí el descuento. Formato: descontar 50 de gasolina a Cristian")
        return
    try:
        registrar_descuento(resultado["tecnico"], resultado["concepto"], resultado["monto"])
        await update.message.reply_text(
            f"Descuento registrado:\n"
            f"Técnico: {resultado['tecnico']}\n"
            f"Concepto: {resultado['concepto']}\n"
            f"Monto: {resultado['monto']} €"
        )
    except Exception as e:
        logger.error(f"Error registrando descuento: {e}")
        await update.message.reply_text(f"Error al registrar descuento: {e}")


def main() -> None:
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
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, timeout_alarma),
            ],
        },
        fallbacks=[
            CommandHandler("cancelar", cancelar),
            CommandHandler("start", start),
            CommandHandler("nomina", cmd_nomina),
            CommandHandler("nuevo_mes", cmd_nuevo_mes),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex("(?i)descontar"),
                manejar_descuento,
            ),
        ],
        conversation_timeout=300,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("nuevo_mes", cmd_nuevo_mes))
    app.add_handler(CallbackQueryHandler(callback_nuevo_mes, pattern="^(confirmar|cancelar)_nuevo_mes$"))
    app.add_handler(alarma_conv)
    app.add_handler(CommandHandler("nomina", cmd_nomina))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex("(?i)descontar"),
        manejar_descuento,
    ))

    logger.info("Bot iniciado")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import time
    from telegram.error import Conflict
    for attempt in range(10):
        try:
            main()
            break
        except Conflict:
            wait = 10 * (attempt + 1)
            logger.warning(f"Conflict con otra instancia, reintentando en {wait}s (intento {attempt + 1}/10)")
            time.sleep(wait)
