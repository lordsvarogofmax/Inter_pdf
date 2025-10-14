import os
import sys
import logging
import requests
import re
import hashlib
import time
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

# Кэш для отслеживания обработанных сообщений
processed_messages = set()

def get_message_hash(message):
    """Создает уникальный хеш для сообщения"""
    message_str = str(message.get('message_id', '')) + str(message.get('date', ''))
    return hashlib.md5(message_str.encode()).hexdigest()

def is_message_processed(message_hash):
    """Проверяет, было ли сообщение уже обработано"""
    return message_hash in processed_messages

def mark_message_processed(message_hash):
    """Отмечает сообщение как обработанное"""
    processed_messages.add(message_hash)
    # Очищаем старые записи (старше 1 часа) если их слишком много
    if len(processed_messages) > 1000:
        logger.info("🧹 Очищаем кэш обработанных сообщений")
        processed_messages.clear()

def send_message(chat_id, text, reply_markup=None):
    """Отправляет сообщение в Telegram с улучшенной обработкой ошибок"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {"chat_id": chat_id, "text": text}
        if reply_markup:
            data["reply_markup"] = reply_markup
        
        response = requests.post(url, json=data, timeout=10)
        if not response.ok:
            logger.error(f"❌ Ошибка отправки сообщения: {response.status_code} - {response.text}")
        else:
            logger.info(f"✅ Сообщение отправлено в чат {chat_id}")
    except requests.exceptions.Timeout:
        logger.error("⏰ Таймаут при отправке сообщения")
    except Exception as e:
        logger.exception("💥 Ошибка при отправке сообщения")

def send_document(chat_id, file_buffer, filename):
    """Отправляет документ в Telegram с улучшенной обработкой ошибок"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
        files = {"document": (filename, file_buffer, "text/plain")}
        data = {"chat_id": chat_id}
        
        response = requests.post(url, files=files, data=data, timeout=60)
        if not response.ok:
            logger.error(f"❌ Ошибка отправки документа: {response.status_code} - {response.text}")
        else:
            logger.info(f"✅ Документ {filename} отправлен в чат {chat_id}")
    except requests.exceptions.Timeout:
        logger.error("⏰ Таймаут при отправке документа")
    except Exception as e:
        logger.exception("💥 Ошибка при отправке документа")

def clean_text(text):
    """Очищает извлеченный текст"""
    if not text:
        return ""
    text = re.sub(r'([а-яА-Яa-zA-Z])-\n([а-яА-Яa-zA-Z])', r'\1\2', text)
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    text = re.sub(r' +', ' ', text)
    text = '\n'.join(line.strip() for line in text.splitlines())
    return text.strip()

def extract_text_from_pdf(file_bytes, is_ocr_needed=False):
    """Извлекает текст из PDF с улучшенной обработкой ошибок"""
    if not is_ocr_needed:
        try:
            reader = PyPDF2.PdfReader(BytesIO(file_bytes))
            raw = "\n".join(page.extract_text() or "" for page in reader.pages)
            if raw.strip():
                logger.info("📄 Текст извлечен напрямую из PDF")
                return clean_text(raw)
        except Exception as e:
            logger.warning(f"⚠️ Не удалось извлечь текст напрямую: {e}")

    logger.info("🖼️ Запуск OCR...")
    try:
        images = convert_from_bytes(file_bytes, dpi=200)
        ocr_text = ""
        for i, img in enumerate(images):
            logger.info(f"🔍 Обрабатываю страницу {i+1}/{len(images)}")
            text = pytesseract.image_to_string(img, lang='rus+eng')
            ocr_text += text + "\n"
        logger.info("✅ OCR завершен успешно")
        return clean_text(ocr_text)
    except Exception as e:
        logger.exception("💥 OCR провален")
        raise

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    """Обрабатывает webhook от Telegram с защитой от дублирования"""
    try:
        data = request.get_json()
        logger.info(f"📨 Получен webhook: {data}")
        
        if not data or "message" not in data:
            logger.info("❌ Нет сообщения в данных")
            return "OK", 200

        message = data["message"]
        message_id = message.get("message_id", "unknown")
        chat_id = message["chat"]["id"]
        
        # Проверяем, не обрабатывали ли мы это сообщение уже
        message_hash = get_message_hash(message)
        if is_message_processed(message_hash):
            logger.info(f"🔄 Пропускаем дублирующееся сообщение ID: {message_id}")
            return "OK", 200
        
        # Отмечаем сообщение как обрабатываемое
        mark_message_processed(message_hash)
        logger.info(f"📝 Обрабатываем новое сообщение ID: {message_id}")

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
                logger.info(f"📁 Загружаю файл ID: {file_id}")
                
                resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}", timeout=10)
                if not resp.ok:
                    logger.error(f"❌ Ошибка получения файла: {resp.status_code}")
                    send_message(chat_id, "❌ Ошибка загрузки файла.")
                    return "OK", 200
                
                file_path = resp.json()["result"]["file_path"]
                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                
                file_resp = requests.get(file_url, timeout=30)
                if not file_resp.ok:
                    logger.error(f"❌ Ошибка скачивания файла: {file_resp.status_code}")
                    send_message(chat_id, "❌ Ошибка скачивания файла.")
                    return "OK", 200
                
                file_bytes = file_resp.content
                logger.info(f"📄 Файл загружен, размер: {len(file_bytes)} байт")

                # Проверка: текстовый PDF или скан?
                try:
                    reader = PyPDF2.PdfReader(BytesIO(file_bytes))
                    raw = "\n".join(page.extract_text() or "" for page in reader.pages)
                    is_ocr_needed = not raw.strip()
                    logger.info(f"🔍 PDF тип: {'скан (требует OCR)' if is_ocr_needed else 'текстовый'}")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка анализа PDF: {e}")
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

    except Exception as e:
        logger.exception("💥 Критическая ошибка в webhook")
        # Всегда возвращаем 200, чтобы Telegram не повторял запрос
        return "OK", 200

    return "OK", 200

def set_webhook():
    """Устанавливает webhook с улучшенной обработкой ошибок"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        webhook_url = WEBHOOK_URL.rstrip("/") + "/webhook"
        resp = requests.post(url, json={"url": webhook_url}, timeout=10)
        if resp.ok:
            logger.info(f"✅ Webhook установлен: {webhook_url}")
        else:
            logger.error(f"❌ Ошибка webhook: {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.exception("💥 Ошибка установки webhook")

if __name__ == "__main__":
    logger.info("🚀 Запуск бота...")
    set_webhook()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)