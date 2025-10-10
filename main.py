import os
import sys
import logging
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

# === ЗАГРУЗКА ПЕРЕМЕННЫХ ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN or not WEBHOOK_URL:
    logger.critical("❌ BOT_TOKEN or WEBHOOK_URL is missing! Set them in Render Environment Variables.")
    sys.exit(1)

# === FLASK ===
app = Flask(__name__)

# === TELEGRAM APPLICATION ===
application = Application.builder().token(BOT_TOKEN).build()

# === ОБРАБОТКА PDF ===
async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"📥 Received PDF from user {user_id}")

    try:
        doc = update.message.document
        if doc.mime_type != "application/pdf":
            await update.message.reply_text("Пожалуйста, отправьте PDF-файл.")
            return

        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        logger.info(f"💾 Downloaded {len(file_bytes)} bytes")

        # Извлечение текста
        pdf_reader = PyPDF2.PdfReader(BytesIO(file_bytes))
        raw_text = ""
        for i, page in enumerate(pdf_reader.pages):
            text = page.extract_text() or ""
            raw_text += text + "\n"
        logger.info(f"📄 Extracted {len(raw_text)} characters")

        if not raw_text.strip():
            await update.message.reply_text("Не удалось извлечь текст из PDF.")
            return

        # Структуризация через OpenRouter (если ключ есть)
        if OPENROUTER_API_KEY:
            logger.info("🧠 Sending to OpenRouter...")
            structured_text = await structure_with_openrouter(raw_text)
        else:
            logger.info("⏭️ Skipping OpenRouter")
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
            content = resp.json()["choices"][0]["message"]["content"].strip()
            logger.info("✅ OpenRouter returned structured text")
            return content
        else:
            logger.error(f"OpenRouter error: {resp.status_code} – {resp.text}")
            return text
    except Exception as e:
        logger.exception("OpenRouter request failed")
        return text


# === WEBHOOK ENDPOINT ===
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put(update)
    return "OK", 200


# === УСТАНОВКА WEBHOOK ЧЕРЕЗ HTTP (СИНХРОННО) ===
def set_webhook_sync():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    resp = requests.post(url, json={"url": WEBHOOK_URL})
    if resp.ok and resp.json().get("ok"):
        logger.info(f"✅ Webhook successfully set to: {WEBHOOK_URL}")
    else:
        logger.error(f"❌ Failed to set webhook: {resp.text}")


# === ЗАПУСК ===
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")

    # Устанавливаем webhook
    set_webhook_sync()

    # Регистрируем обработчик
    application.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    # Запуск Flask
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"📡 Starting Flask server on port {port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False)
