import os
import logging
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.ext import Updater
import PyPDF2
import requests
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)
application = Application.builder().token(BOT_TOKEN).build()

# --- Обработка PDF ---
async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    file = await update.message.document.get_file()
    file_bytes = await file.download_as_bytearray()

    # Извлекаем текст из PDF
    pdf_reader = PyPDF2.PdfReader(BytesIO(file_bytes))
    raw_text = ""
    for page in pdf_reader.pages:
        raw_text += page.extract_text() + "\n"

    # (Опционально) отправляем в OpenRouter для структурирования
    structured_text = await structure_with_openrouter(raw_text)

    # Отправляем обратно как .txt
    txt_file = BytesIO(structured_text.encode('utf-8'))
    txt_file.name = "output.txt"
    await update.message.reply_document(document=txt_file)

async def structure_with_openrouter(text: str) -> str:
    if not OPENROUTER_API_KEY:
        return text  # если ключа нет — просто возвращаем сырой текст

    prompt = f"""
    Разбей следующий текст на логически завершённые блоки.
    Сохрани исходный смысл, но сделай структуру читаемой.
    Верни только текст, без пояснений.

    Текст:
    {text}
    """

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "meta-llama/llama-3-8b-instruct:free",  # бесплатная модель
            "messages": [{"role": "user", "content": prompt}]
        }
    )

    if response.status_code == 200:
        data = response.json()
        return data['choices'][0]['message']['content'].strip()
    else:
        print("OpenRouter error:", response.text)
        return text  # fallback

# --- Webhook endpoint для Flask ---
@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application.update_queue.put(update)
    return 'OK'

# --- Запуск ---
if __name__ == '__main__':
    # Устанавливаем webhook при старте
    bot.set_webhook(url=WEBHOOK_URL)

    # Добавляем обработчик
    application.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))

    # Запускаем Flask
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
