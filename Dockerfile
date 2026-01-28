# Берем легкий Python 3.11
FROM python:3.11-slim

# Рабочая папка внутри контейнера
WORKDIR /app

# Чтобы Python не создавал файлы .pyc и выводил логи сразу
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Ставим системные зависимости (для PyMuPDF и работы с PDF иногда нужны библиотеки)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Копируем список зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Доустанавливаем Gunicorn для продакшена
RUN pip install gunicorn

# Копируем весь код проекта
COPY . .

# Открываем порт 8000
EXPOSE 8000

# Команда запуска (через Gunicorn для надежности)
CMD ["gunicorn", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "main:app"]