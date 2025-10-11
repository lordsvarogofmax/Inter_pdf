import os
import sys
import logging
import asyncio
import requests
import re
from io import BytesIO
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import PyPDF2
from pdf2image import convert_from_bytes
import pytesseract
from PIL import Image
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Пример: https://inter-pdf.onrender.com

if not BOT_TOKEN or not WEBHOOK_URL:
    logger.critical("❌ BOT_TOKEN or WEBHOOK_URL not set!")
    sys.exit(1)

def split_into_paragraphs(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    grouped = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) < 300:
            current += sentence + " "
        else:
            grouped.append(current.strip())
            current = sentence + " "
    if current:
        grouped.append(current.strip())
    return grouped

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'([а-яА-Яa-zA-Z])-\n([а-яА-Яa-zA-Z])', r'\1\2', text)
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    text = re.sub(r' +', ' ', text)
    text = '\n'.join(line.strip() for line in text.splitlines())
    return text.strip()

async def extract_text_from_pdf(file_bytes: bytes) -> str:
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
        return ""

async def improve_text_with_openrouter(text: str) -> str:
    if not OPENROUTER_API_KEY or not text.strip():
        return text

    prompt = f"""
Ты — профессиональный редактор и корректор художественной литературы.  
Перед тобой текст, извлечённый из PDF (возможно, отсканированной книги).  
Твоя задача — превратить его в **идеально отредактированную книгу**, готовую к публикации.

Следуй этим правилам:

1. **Исправь всё**:  
   - Орфографические и пунктуационные ошибки  
   - Случайные символы, артефакты распознавания  
   - Неправильные переносы слов  
   - Лишние или пропущенные пробелы  
   - Разорванные предложения из-за разрывов страниц

2. **Восстанови структуру**:  
   - Раздели текст на **логические абзацы**  
   - Сохрани диалоги, если они есть  
   - Не добавляй заголовков, если их не было  
   - Не сокращай и не расширяй содержание

3. **Стиль и язык**:  
   - Сохрани оригинальный стиль  
   - Используй литературный русский язык

4. **Формат вывода**:  
   - Верни **только итоговый текст**  
   - Без пояснений, комментариев  
   - Без markdown — только чистый текст с переносами строк между абзацами

Текст:
{text}
"""
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={"model": "nousresearch/hermes-3-llama-3.1-405b:free", "messages": [{"role": "user", "content": prompt}]},
            timeout=60
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            logger.error(f"OpenRouter error: {resp.status_code}")
            return text
    except Exception as e:
        logger.exception("OpenRouter недоступен")
        return text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Отправь PDF — я пришлю .docx с чистым текстом.")

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        if doc.mime_type != "application/pdf":
            await update.message.reply_text("Пожалуйста, отправьте PDF.")
            return

        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        raw_text = await extract_text_from_pdf(file_bytes)
        if not raw_text:
            await update.message.reply_text("Не удалось извлечь текст.")
            return

        final_text = await improve_text_with_openrouter(raw_text)

        docx_buffer = BytesIO()
        document = Document()
        style = document.styles['Normal']
        font = style.font
        font.name = 'Times New Roman'
        font.size = Pt(12)

        paragraphs = split_into_paragraphs(final_text)
        for para in paragraphs:
            if para.strip():
                p = document.add_paragraph(para.strip())
                p.paragraph_format.space_after = Pt(6)
                p.paragraph_format.space_before = Pt(6)
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT

        document.save(docx_buffer)
        docx_buffer.seek(0)
        docx_buffer.name = "output.docx"
        await update.message.reply_document(document=docx_buffer)

        reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("📄 Отправить новый PDF")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.message.reply_text("✅ Готово! Отправляй следующий PDF.", reply_markup=reply_markup)

    except Exception as e:
        logger.exception("💥 Ошибка в handle_pdf")
        await update.message.reply_text("Произошла ошибка.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Пожалуйста, отправьте PDF-файл.")

async def main():
    logger.info("🚀 Запуск бота...")

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    port = int(os.environ.get("PORT", 10000))
    webhook_path = "/webhook"
    full_webhook_url = WEBHOOK_URL.rstrip("/") + webhook_path

    await application.bot.set_webhook(url=full_webhook_url)
    logger.info(f"✅ Webhook установлен: {full_webhook_url}")

    await application.run_webhook(
        listen="0.0.0.0",
        port=port,
        webhook_url=full_webhook_url,
        secret_token=None
    )

if __name__ == "__main__":
    asyncio.run(main())
