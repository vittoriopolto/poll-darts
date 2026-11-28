# Immagine per Render: Python + Tesseract (OCR gratuito per leggere gli screenshot di diretta.it).
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PYTHONUNBUFFERED=1
# Render imposta PORT da solo; il bot la legge in config.py
CMD ["python", "-m", "pdc"]
