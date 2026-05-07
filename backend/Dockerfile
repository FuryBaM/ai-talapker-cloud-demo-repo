FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=demo \
    APP_CONFIG_FILE=configs/app.cloud.yaml \
    APP_DEVICE=cpu \
    APP_LOAD_4BIT=0 \
    APP_HOST=0.0.0.0 \
    APP_PORT=10000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-kaz \
    tesseract-ocr-eng \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-cloud.txt ./requirements-cloud.txt
RUN pip install --upgrade pip && pip install -r requirements-cloud.txt

COPY app.py build_rag_chunks.py build_rag_documents.py download.py index_qdrant.py login.py manage.py normalize_input_data.py rebuild_rag_data.py test.py ./
COPY core ./core
COPY configs ./configs
COPY data ./data
COPY input_data ./input_data
COPY storage ./storage
COPY scripts ./scripts

RUN mkdir -p models storage data input_data

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT','10000'), timeout=3).read()"

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
