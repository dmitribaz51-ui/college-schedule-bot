# Кастомный образ для BotHost: Python + нативный Tesseract с русским языком.
# В панели BotHost нужно включить «Использовать собственный Dockerfile».
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tesseract-ocr \
        tesseract-ocr-rus \
        tesseract-ocr-osd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ на BotHost монтируется отдельным Volume и не должен затираться образом.
CMD ["python", "-m", "app.bot.main"]
