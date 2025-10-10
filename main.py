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


# === КОМАНДА /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Привет! Я бот для извлечения текста из PDF.\n\n"
        "📄 Просто отправь мне PDF-файл — я:\n"
        "1. Извлеку весь текст\n"
        "2. (Опционально) структурирую его с помощью ИИ\n"
        "3. Верну готовый `.txt` файл\n\n"
        "Отправляй PDF прямо сейчас!"
    )
    await update.message.reply_text(welcome_text)


# === ОБРАБОТКА PDF ===
async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"📥 PDF from user {user.id} ({user.username or 'no username'})")

    try:
        doc = update.message.document
        if doc.mime_type != "application/pdf":
            await update.message.reply_text("Пожалуйста, отправьте именно PDF-файл.")
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

        # Подсказка: можно прислать ещё
        reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("📄 Отправить новый PDF")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.message.reply_text(
            "✅ Готово! Если нужно обработать ещё один PDF — просто пришлите его.",
            reply_markup=reply_markup
        )
        logger.info("📤 TXT sent + hint message")

    except Exception as e:
        logger.exception("💥 Error in handle_pdf:")
        await update.message.reply_text("Произошла ошибка при обработке файла. Попробуйте снова.")


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


# === WEBHOOK (Flask + asyncio) ===
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

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    # Игнорируем кнопку — она просто для UX
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pdf))

    # Устанавливаем webhook
    set_webhook_sync()

    # Запуск Flask
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
