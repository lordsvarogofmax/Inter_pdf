import os
import sys
import logging
import requests
import re
from io import BytesIO
from flask import Flask, request
import PyPDF2
from pdf2image import convert_from_bytes
import pytesseract
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN or not WEBHOOK_URL:
    logger.critical("❌ BOT_TOKEN or WEBHOOK_URL not set!")
    sys.exit(1)

app = Flask(__name__)

def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    requests.post(url, json=data, timeout=10)

def send_document(chat_id, file_buffer, filename):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    files = {"document": (filename, file_buffer, "text/plain")}
    data = {"chat_id": chat_id}
    requests.post(url, files=files, data=data, timeout=60)

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'([а-яА-Яa-zA-Z])-\n([а-яА-Яa-zA-Z])', r'\1\2', text)
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    text = re.sub(r' +', ' ', text)
    text = '\n'.join(line.strip() for line in text.splitlines())
    return text.strip()

def extract_text_from_pdf(file_bytes, is_ocr_needed=False):
    if not is_ocr_needed:
        try:
            reader = PyPDF2.PdfReader(BytesIO(file_bytes))
            raw = "\n".join(page.extract_text() or "" for page in reader.pages)
            if raw.strip():
                return clean_text(raw)
        except:
            pass

    logger.info("🖼️ Запуск OCR...")
    try:
        images = convert_from_bytes(file_bytes, dpi=200)
        ocr_text = ""
        for img in images:
            text = pytesseract.image_to_string(img, lang='rus+eng')
            ocr_text += text + "\n"
        return clean_text(ocr_text)
    except Exception as e:
        logger.exception("💥 OCR провален")
        raise

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    if not data or "message" not in data:
        return "OK", 200

    message = data["message"]
    chat_id = message["chat"]["id"]

    if "text" in message:
        text = message["text"]
        if text == "/start":
            reply_markup = {
                "keyboard": [[{"text": "📤 Отправить PDF на конвертацию"}]],
                "resize_keyboard": True,
                "one_time_keyboard": False
            }
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
            send_message(chat_id, "❌ Я принимаю только PDF-файлы.")
            return "OK", 200

        send_message(chat_id, "⏳ Принял PDF. Начинаю обработку...")

        try:
            file_id = doc["file_id"]
            resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}")
            file_path = resp.json()["result"]["file_path"]
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            file_bytes = requests.get(file_url).content

            # Проверка: текстовый PDF или скан?
            try:
                reader = PyPDF2.PdfReader(BytesIO(file_bytes))
                raw = "\n".join(page.extract_text() or "" for page in reader.pages)
                is_ocr_needed = not raw.strip()
            except:
                is_ocr_needed = True

            if is_ocr_needed:
                send_message(
                    chat_id,
                    "🔍 Обнаружен скан. Использую OCR. Это займёт 30–60 секунд..."
                )

            text = extract_text_from_pdf(file_bytes, is_ocr_needed=is_ocr_needed)
            if not text.strip():
                send_message(chat_id, "❌ Не удалось извлечь текст.")
                return "OK", 200

            base_name = doc.get("file_name", "converted")
            txt_name = os.path.splitext(base_name)[0] + ".txt"
            txt_buffer = BytesIO(text.encode("utf-8"))
            send_document(chat_id, txt_buffer, txt_name)

            reply_markup = {
                "keyboard": [[{"text": "📤 Отправить PDF на конвертацию"}]],
                "resize_keyboard": True,
                "one_time_keyboard": False
            }
            send_message(
                chat_id,
                "✅ Готово! Текст успешно извлечён.\n\nОтправляйте следующий PDF!",
                reply_markup
            )

        except Exception as e:
            logger.exception("💥 Ошибка при обработке PDF")
            send_message(chat_id, "❌ Произошла ошибка. Попробуйте снова.")

    return "OK", 200

def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    webhook_url = WEBHOOK_URL.rstrip("/") + "/webhook"
    resp = requests.post(url, json={"url": webhook_url})
    if resp.ok:
        logger.info(f"✅ Webhook установлен: {webhook_url}")
    else:
        logger.error(f"❌ Ошибка webhook: {resp.text}")

if __name__ == "__main__":
    logger.info("🚀 Запуск бота...")
    set_webhook()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
