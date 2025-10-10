import os
import sys
import logging
import asyncio
import requests
from io import BytesIO
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import PyPDF2
from dotenv import load_dotenv

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# === ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN or not WEBHOOK_URL:
    logger.critical("❌ BOT_TOKEN or WEBHOOK_URL not set in environment variables!")
    sys.exit(1)

# === ИНИЦИАЛИЗАЦИЯ ===
app = Flask(__name__)
application = Application.builder().token(BOT_TOKEN).build()

# === ОБРАБОТКА PDF ===
async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"📥 PDF from user {user.id} ({user.username or 'no username'})")

    try:
        doc = update.message.document
        if doc.mime_type != "application/pdf":
            await update.message.reply_text("Пожалуйста, отправьте PDF-файл.")
            return

        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        logger.info(f"💾 File size: {len(file_bytes)} bytes")

        # Извлечение текста
        pdf = PyPDF2.PdfReader(BytesIO(file_bytes))
        raw_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        logger.info(f"📄 Extracted {len(raw_text)} characters")

        if not raw_text.strip():
            await update.message.reply_text("Не удалось извлечь текст из PDF.")
            return

        # Структуризация (если есть ключ OpenRouter)
        if OPENROUTER_API_KEY:
            logger.info("🧠 Structuring with OpenRouter...")
            structured = await structure_with_openrouter(raw_text)
        else:
            structured = raw_text

        # Отправка TXT
        txt = BytesIO(structured.encode("utf-8"))
        txt.name = "output.txt"
        await update.message.reply_document(document=txt)
        logger.info("📤 TXT sent")

    except Exception as e:
        logger.exception("💥 Error in handle_pdf:")
        await update.message.reply_text("Произошла ошибка при обработке файла.")


async def structure_with_openrouter(text: str) -> str:
    prompt = f"""
Разбей следующий текст на логически завершённые блоки.
Сохрани исходный смысл, но сделай структуру читаемой.
Верни только текст, без пояснений.

Текст:
{text}
"""
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": "meta-llama/llama-3-8b-instruct:free",
                "messages": [{"role": "user", "content": prompt}]
            },
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


# === WEBHOOK (СИНХРОННЫЙ FLASK + АСИНХРОННЫЙ TELEGRAM) ===
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    json_data = request.get_json(force=True)
    if not json_
        return "Bad Request", 400
    update = Update.de_json(json_data, application.bot)
    asyncio.run(process_update(update))
    return "OK", 200

async def process_update(update: Update):
    await application.process_update(update)


# === УСТАНОВКА WEBHOOK ЧЕРЕЗ HTTP ===
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

    # Устанавливаем webhook
    set_webhook_sync()

    # Регистрируем обработчик
    application.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    # Запуск Flask на порту из Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
