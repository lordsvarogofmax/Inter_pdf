import os
import sys
import logging
import asyncio
import requests
from io import BytesIO
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import PyPDF2
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN or not WEBHOOK_URL:
    logger.critical("❌ BOT_TOKEN or WEBHOOK_URL not set!")
    sys.exit(1)

app = Flask(__name__)

# Создаём Application один раз (глобально)
_bot_app = None

def get_application():
    global _bot_app
    if _bot_app is None:
        _bot_app = Application.builder().token(BOT_TOKEN).build()
        _bot_app.add_handler(CommandHandler("start", start))
        _bot_app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
        _bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        # Инициализируем один раз
        asyncio.run(_bot_app.initialize())
        logger.info("✅ Application initialized")
    return _bot_app


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Отправь мне PDF — я извлеку текст и пришлю .txt файл."
    )

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        if doc.mime_type != "application/pdf":
            await update.message.reply_text("Пожалуйста, отправьте PDF.")
            return

        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        pdf = PyPDF2.PdfReader(BytesIO(file_bytes))
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

        if not text.strip():
            await update.message.reply_text("Не удалось извлечь текст.")
            return

        if OPENROUTER_API_KEY:
            logger.info("🧠 Structuring with OpenRouter...")
            text = await structure_with_openrouter(text)

        txt = BytesIO(text.encode("utf-8"))
        txt.name = "output.txt"
        await update.message.reply_document(document=txt)

        reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("📄 Отправить новый PDF")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.message.reply_text(
            "✅ Готово! Отправляй следующий PDF.",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.exception("💥 Error in handle_pdf")
        await update.message.reply_text("Ошибка при обработке файла.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Пожалуйста, отправьте PDF-файл.")

async def structure_with_openrouter(text: str) -> str:
    prompt = f"Разбей на логические блоки и верни только текст:\n\n{text}"
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={"model": "meta-llama/llama-3-8b-instruct:free", "messages": [{"role": "user", "content": prompt}]},
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            logger.error(f"OpenRouter error: {resp.status_code}")
            return text
    except Exception as e:
        logger.exception("OpenRouter failed")
        return text

# === ВАЖНО: СИНХРОННЫЙ WEBHOOK ===
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    json_data = request.get_json(force=True)
    if not json_data:
        return "Bad Request", 400

    # Получаем Application (инициализируется один раз)
    application = get_application()
    update = Update.de_json(json_data, application.bot)

    # Запускаем обработку в новом event loop'е
    asyncio.run(application.process_update(update))

    return "OK", 200

# === УСТАНОВКА WEBHOOK ===
def set_webhook_sync():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    resp = requests.post(url, json={"url": WEBHOOK_URL})
    if resp.ok and resp.json().get("ok"):
        logger.info(f"✅ Webhook set to: {WEBHOOK_URL}")
    else:
        logger.error(f"❌ Webhook failed: {resp.text}")

# === ЗАПУСК ===
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")
    set_webhook_sync()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
