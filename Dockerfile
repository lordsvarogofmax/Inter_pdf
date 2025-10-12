FROM python:3.12-slim

# Установка системных зависимостей
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-rus \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        && rm -rf /var/lib/apt/lists/*

# Установка Python-зависимостей
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Запуск
CMD ["python", "main.py"]
