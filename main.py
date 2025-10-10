import os
import sys
import logging
from io import BytesIO
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import PyPDF2
import requests
from dotenv import load_dotenv

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# === ЗАГРУЗКА ПЕРЕМЕННЫХ ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN:
    logger.critical("❌ BOT_TOKEN is not set! Add it in Render Environment Variables.")
    sys.exit(1)

# === FLASK И TELEGRAM ===
app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

# === ОБРАБОТКА PDF ===
async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"📥 Received PDF from user {user_id}")

    try:
        document = update.message.document
        if not document.mime_type == "application/pdf":
            await update.message.reply_text("Пожалуйста, отправьте PDF-файл.")
            return

        file = await document.get_file()
        file_bytes = await file.download_as_bytearray()
        logger.info(f"💾 Downloaded {len(file_bytes)} bytes")

        # Извлечение текста
        pdf_reader = PyPDF2.PdfReader(BytesIO(file_bytes))
        raw_text = ""
        for i, page in enumerate(pdf_reader.pages):
            text = page.extract_text() or ""
            raw_text += text + "\n"
            logger.debug(f"📄 Page {i+1}: {len(text)} chars")

        if not raw_text.strip():
            await update.message.reply_text("Не удалось извлечь текст из PDF.")
            return

        # Структуризация через OpenRouter (если ключ есть)
        if OPENROUTER_API_KEY:
            logger.info("🧠 Sending to OpenRouter for structuring...")
            structured_text = await structure_with_openrouter(raw_text)
        else:
            logger.info("⏭️ Skipping OpenRouter (no API key)")
            structured_text = raw_text

        # Отправка TXT
        txt_file = BytesIO(structured_text.encode("utf-8"))
        txt_file.name = "output.txt"
        await update.message.reply_document(document=txt_file)
        logger.info("📤 Sent TXT file")

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
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "meta-llama/llama-3-8b-instruct:free",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            logger.info("✅ OpenRouter returned structured text")
            return content
        else:
            logger.error(f"OpenRouter error: {response.status_code} – {response.text}")
            return text
    except Exception as e:
        logger.exception("OpenRouter request failed")
        return text


# === WEBHOOK ДЛЯ TELEGRAM ===
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application.update_queue.put(update)
    return "OK", 200


# === ЗАПУСК ===
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")

    # Создаём Application
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    # Устанавливаем webhook
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"✅ Webhook set to: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"❌ Failed to set webhook: {e}")

    # Запуск Flask
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"📡 Listening on port {port}")
    app.run(host="0.0.0.0", port=port)
