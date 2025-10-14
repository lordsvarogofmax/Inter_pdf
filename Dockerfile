FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-rus \
        tesseract-ocr-eng \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        && rm -rf /var/lib/apt/lists/*

# Увеличиваем лимиты памяти для tesseract
ENV OMP_THREAD_LIMIT=2
ENV OMP_NUM_THREADS=2

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]