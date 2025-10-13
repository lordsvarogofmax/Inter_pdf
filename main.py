import os
import sys
import logging
import requests
import re
from io import BytesIO
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN or not WEBHOOK_URL:
    logger.critical("❌ BOT_TOKEN or WEBHOOK_URL not set!")
    sys.exit(1)

app = Flask(__name__)

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def send_message(chat_id: int, text: str, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup.to_dict()
    requests.post(url, json=data, timeout=10)

def send_document(chat_id: int, file_buffer: BytesIO, filename: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    files = {"document": (filename, file_buffer, "text/plain")}
    data = {"chat_id": chat_id}
    requests.post(url, files=files, data=data, timeout=60)

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'([а-яА-Яa-zA-Z])-\n([а-яА-Яa-zA-Z])', r'\1\2', text)
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    text = re.sub(r' +', ' ', text)
    text = '\n'.join(line.strip() for line in text.splitlines())
    return text.strip()

def extract_text_from_pdf(file_bytes: bytes, is_ocr_needed: bool = False) -> str:
    if not is_ocr_needed:
        try:
            reader = PyPDF2.PdfReader(BytesIO(file_bytes))
            raw = "\n".join(page.extract_text() or "" for page in reader.pages)
            if raw.strip():
                logger.info("📄 Текст извлечён напрямую")
                return clean_text(raw)
        except Exception as e:
            logger.warning(f"Прямое извлечение не удалось: {e}")

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

def handle_update(json_data):
    update = json_data
    if "message" not in update:
        return

    message = update["message"]
    chat_id = message["chat"]["id"]

    if "text" in message:
        text = message["text"]
        if text == "/start":
            reply_markup = ReplyKeyboardMarkup(
                [[KeyboardButton("📤 Отправить PDF на конвертацию")]],
                resize_keyboard=True,
                one_time_keyboard=False
            )
            send_message(
                chat_id,
                "👋 Привет! Я бот для конвертации PDF в текст.\n\nНажмите кнопку ниже, чтобы начать.",
                reply_markup
            )
        else:
            send_message(
                chat_id,
                "📎 Я работаю только с PDF-файлами.\n\nПожалуйста, отправьте PDF или нажмите кнопку «📤 Отправить PDF на конвертацию»."
            )
    elif "document" in message:
        doc = message["document"]
        if doc.get("mime_type") != "application/pdf":
            send_message(chat_id, "❌ Я принимаю только PDF-файлы. Пожалуйста, отправьте PDF.")
            return

        send_message(chat_id, "⏳ Принял PDF. Начинаю обработку...")

        try:
            file_id = doc["file_id"]
            # Получаем file_path
            resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}")
            file_path = resp.json()["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            file_bytes = requests.get(file_url).content

            logger.info(f"📥 Получен PDF: {doc.get('file_name', 'без имени')}, {len(file_bytes)} байт")

            # Проверим, текстовый ли PDF
            try:
                reader = PyPDF2.PdfReader(BytesIO(file_bytes))
                raw = "\n".join(page.extract_text() or "" for page in reader.pages)
                is_ocr_needed = not raw.strip()
            except:
                is_ocr_needed = True

            if is_ocr_needed:
                send_message(
                    chat_id,
                    "🔍 Обнаружен скан или изображение. Использую OCR (распознавание текста с картинок).\n"
                    "Это может занять 30–60 секунд. Пожалуйста, подождите..."
                )

            text = extract_text_from_pdf(file_bytes, is_ocr_needed=is_ocr_needed)

            if not text.strip():
                send_message(chat_id, "❌ Не удалось извлечь текст из PDF. Возможно, файл повреждён или пуст.")
                return

            base_name = doc.get("file_name")
            if base_name:
                txt_name = os.path.splitext(base_name)[0] + ".txt"
            else:
                txt_name = "converted.txt"

            txt_buffer = BytesIO(text.encode("utf-8"))
            send_document(chat_id, txt_buffer, txt_name)

            reply_markup = ReplyKeyboardMarkup(
                [[KeyboardButton("📤 Отправить PDF на конвертацию")]],
                resize_keyboard=True,
                one_time_keyboard=False
            )
            send_message(
                chat_id,
                "✅ Готово! Текст успешно извлечён.\n\nОтправляйте следующий PDF!",
                reply_markup
            )

        except Exception as e:
            logger.exception("💥 Ошибка при обработке PDF")
            send_message(
                chat_id,
                "❌ Произошла ошибка при конвертации. Попробуйте снова или отправьте другой PDF."
            )

# === WEBHOOK ===

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    json_data = request.get_json(force=True)
    if not json_data:
        return "Bad Request", 400
    handle_update(json_data)
    return "OK", 200

# === УСТАНОВКА WEBHOOK ===

def set_webhook_sync():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    full_url = WEBHOOK_URL.rstrip("/") + "/webhook"
    resp = requests.post(url, json={"url": full_url})
    if resp.ok and resp.json().get("ok"):
        logger.info(f"✅ Webhook установлен: {full_url}")
    else:
        logger.error(f"❌ Ошибка webhook: {resp.text}")

if __name__ == "__main__":
    logger.info("🚀 Запуск бота...")
    set_webhook_sync()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
