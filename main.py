import os
import sys
import logging
import asyncio
import requests
import re
from io import BytesIO
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import PyPDF2
from pdf2image import convert_from_bytes
import pytesseract
from PIL import Image
from dotenv import load_dotenv

# === ЛОГИРОВАНИЕ ===
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
_bot_app = None


def get_application():
    global _bot_app
    if _bot_app is None:
        _bot_app = Application.builder().token(BOT_TOKEN).build()
        _bot_app.add_handler(CommandHandler("start", start))
        _bot_app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
        _bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        asyncio.run(_bot_app.initialize())
        logger.info("✅ Application initialized")
    return _bot_app


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def clean_text(text: str) -> str:
    """Техническая очистка текста от артефактов OCR."""
    if not text:
        return ""
    # Убираем разрывы слов в середине строки (например: "сло-\nво" → "слово")
    text = re.sub(r'([а-яА-Яa-zA-Z])-\n([а-яА-Яa-zA-Z])', r'\1\2', text)
    # Заменяем одиночные \n на пробел (внутри абзацев)
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    # Убираем множественные пробелы
    text = re.sub(r' +', ' ', text)
    # Убираем пробелы в начале/конце строк
    text = '\n'.join(line.strip() for line in text.splitlines())
    # Убираем пустые строки в начале и конце
    return text.strip()


async def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Извлекает текст: сначала как есть, потом через OCR."""
    # Попытка 1: обычное извлечение
    try:
        reader = PyPDF2.PdfReader(BytesIO(file_bytes))
        raw = "\n".join(page.extract_text() or "" for page in reader.pages)
        if raw.strip():
            logger.info("📄 Текст извлечён напрямую")
            return clean_text(raw)
    except Exception as e:
        logger.warning(f"Прямое извлечение не удалось: {e}")

    # Попытка 2: OCR
    logger.info("🖼️ Запуск OCR...")
    try:
        images = convert_from_bytes(file_bytes, dpi=200, thread_count=1)
        ocr_text = ""
        for i, img in enumerate(images):
            logger.info(f"🖼️ OCR страница {i+1}...")
            # rus+eng — поддержка обоих языков
            text = pytesseract.image_to_string(img, lang='rus+eng')
            ocr_text += text + "\n"
        logger.info("✅ OCR завершён")
        return clean_text(ocr_text)
    except Exception as e:
        logger.exception("💥 OCR полностью провален")
        return ""


async def improve_text_with_openrouter(text: str) -> str:
    """Улучшает текст через OpenRouter (если ключ есть)."""
    if not OPENROUTER_API_KEY or not text.strip():
        return text

    prompt = f"""
Ты — профессиональный редактор. Исправь следующий текст:
- Исправь орфографию и пунктуацию.
- Удали артефакты распознавания (случайные символы, обрывки).
- Восстанови логические абзацы.
- Убедись, что нет лишних пробелов или переносов.
- Сохрани смысл и стиль.
- Верни ТОЛЬКО текст, без пояснений.

Текст:
{text}
"""
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": "nousresearch/hermes-3-llama-3.1-405b:free",  # мощная бесплатная модель
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"].strip()
            logger.info("✨ Текст улучшен ИИ")
            return content
        else:
            logger.error(f"OpenRouter error: {resp.status_code}")
            return text
    except Exception as e:
        logger.exception("OpenRouter недоступен")
        return text


# === ОБРАБОТЧИКИ ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Отправь мне PDF (даже скан!) — я извлеку и отредактирую текст, и пришлю .txt файл."
    )

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"📥 PDF от {user.id}")

    try:
        doc = update.message.document
        if doc.mime_type != "application/pdf":
            await update.message.reply_text("Пожалуйста, отправьте PDF.")
            return

        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        logger.info(f"💾 Размер: {len(file_bytes)} байт")

        # Извлечение текста (с OCR при необходимости)
        raw_text = await extract_text_from_pdf(file_bytes)
        if not raw_text:
            await update.message.reply_text("Не удалось извлечь текст из PDF.")
            return

        # Улучшение через ИИ (если ключ есть)
        final_text = await improve_text_with_openrouter(raw_text)

        # Отправка TXT
        txt = BytesIO(final_text.encode("utf-8"))
        txt.name = "output.txt"
        await update.message.reply_document(document=txt)

        # Кнопка
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
        logger.exception("💥 Ошибка в handle_pdf")
        await update.message.reply_text("Произошла ошибка. Попробуйте позже.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Пожалуйста, отправьте PDF-файл.")


# === WEBHOOK ===

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    json_data = request.get_json(force=True)
    if not json_data:
        return "Bad Request", 400
    application = get_application()
    update = Update.de_json(json_data, application.bot)
    asyncio.run(application.process_update(update))
    return "OK", 200


# === УСТАНОВКА WEBHOOK ===

def set_webhook_sync():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    resp = requests.post(url, json={"url": WEBHOOK_URL})
    if resp.ok and resp.json().get("ok"):
        logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}")
    else:
        logger.error(f"❌ Ошибка webhook: {resp.text}")


# === ЗАПУСК ===

if __name__ == "__main__":
    logger.info("🚀 Запуск бота...")
    set_webhook_sync()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
