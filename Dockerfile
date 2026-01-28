# 1. Базовый образ
FROM python:3.11-slim

# 2. Метаданные
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 3. УСТАНОВКА СИСТЕМНЫХ БИБЛИОТЕК
# FIX: Заменили libgl1-mesa-glx на libgl1
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1 \
    libxcb1 \
    libx11-xcb1 \
    libxi6 \
    && rm -rf /var/lib/apt/lists/*

# 4. Python зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn

# 5. Код приложения
COPY . .

# 6. Порт и запуск
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/docs || exit 1

# Запуск
CMD ["gunicorn", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "main:app"]