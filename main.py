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

# === ЛОГИРОВАНИЕ ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# === ПЕРЕМЕННЫЕ ===
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
        _bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        _bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        asyncio.run(_bot_app.initialize())
        logger.info("✅ Application initialized")
    return _bot_app

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'([а-яА-Яa-zA-Z])-\n([а-яА-Яa-zA-Z])', r'\1\2', text)
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    text = re.sub(r' +', ' ', text)
    text = '\n'.join(line.strip() for line in text.splitlines())
    return text.strip()

async def extract_text_from_pdf(file_bytes: bytes, is_ocr_needed: bool = False) -> str:
    if not is_ocr_needed:
        try:
            reader = PyPDF2.PdfReader(BytesIO(file_bytes))
            raw = "\n".join(page.extract_text() or "" for page in reader.pages)
            if raw.strip():
                logger.info("📄 Текст извлечён напрямую")
                return clean_text(raw)
        except Exception as e:
            logger.warning(f"Прямое извлечение не удалось: {e}")

    # OCR
    logger.info("🖼️ Запуск OCR...")
    try:
        images = convert_from_bytes(file_bytes, dpi=200)
        ocr_text = ""
        for i, img in enumerate(images):
            logger.info(f"🖼️ OCR страница {i+1}...")
            text = pytesseract.image_to_string(img, lang='rus+eng')
            ocr_text += text + "\n"
        logger.info("✅ OCR завершён")
        return clean_text(ocr_text)
    except Exception as e:
        logger.exception("💥 OCR полностью провален")
        raise

# === ОБРАБОТЧИКИ ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_markup = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить PDF на конвертацию")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    await update.message.reply_text(
        "👋 Привет! Я бот для конвертации PDF в текст.\n\n"
        "Нажмите кнопку ниже, чтобы начать.",
        reply_markup=reply_markup
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📎 Я работаю только с PDF-файлами.\n\n"
        "Пожалуйста, отправьте PDF или нажмите кнопку «📤 Отправить PDF на конвертацию»."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document

    if doc.mime_type != "application/pdf":
        await update.message.reply_text("❌ Я принимаю только PDF-файлы. Пожалуйста, отправьте PDF.")
        return

    await update.message.reply_text("⏳ Принял PDF. Начинаю обработку...")

    try:
        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        logger.info(f"📥 Получен PDF: {doc.file_name or 'без имени'}, {len(file_bytes)} байт")

        # Проверим, текстовый ли PDF
        try:
            reader = PyPDF2.PdfReader(BytesIO(file_bytes))
            raw = "\n".join(page.extract_text() or "" for page in reader.pages)
            is_ocr_needed = not raw.strip()
        except:
            is_ocr_needed = True

        if is_ocr_needed:
            await update.message.reply_text(
                "🔍 Обнаружен скан или изображение. Использую OCR (распознавание текста с картинок).\n"
                "Это может занять 30–60 секунд. Пожалуйста, подождите..."
            )

        text = await extract_text_from_pdf(file_bytes, is_ocr_needed=is_ocr_needed)

        if not text.strip():
            await update.message.reply_text("❌ Не удалось извлечь текст из PDF. Возможно, файл повреждён или пуст.")
            return

        # Имя файла: исходное имя PDF → .txt
        base_name = doc.file_name
        if base_name:
            txt_name = os.path.splitext(base_name)[0] + ".txt"
        else:
            txt_name = "converted.txt"

        txt_buffer = BytesIO(text.encode("utf-8"))
        txt_buffer.name = txt_name
        await update.message.reply_document(document=txt_buffer)

        reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("📤 Отправить PDF на конвертацию")]],
            resize_keyboard=True,
            one_time_keyboard=False
        )
        await update.message.reply_text(
            "✅ Готово! Текст успешно извлечён.\n\n"
            "Отправляйте следующий PDF!",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.exception("💥 Ошибка при обработке PDF")
        await update.message.reply_text(
            "❌ Произошла ошибка при конвертации. Попробуйте снова или отправьте другой PDF."
        )

# === WEBHOOK ===

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    json_data = request.get_json(force=True)
    if not json_data:
        return "Bad Request", 400

    import asyncio
    from telegram import Update

    async def handle():
        application = get_application()
        update = Update.de_json(json_data, application.bot)
        await application.process_update(update)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(handle())
        return "OK", 200
    finally:
        loop.close()

# === УСТАНОВКА WEBHOOK ===

def set_webhook_sync():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    full_url = WEBHOOK_URL.rstrip("/") + "/webhook"
    resp = requests.post(url, json={"url": full_url})
    if resp.ok and resp.json().get("ok"):
        logger.info(f"✅ Webhook установлен: {full_url}")
    else:
        logger.error(f"❌ Ошибка webhook: {resp.text}")

# === ЗАПУСК ===

if __name__ == "__main__":
    logger.info("🚀 Запуск бота...")
    set_webhook_sync()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
