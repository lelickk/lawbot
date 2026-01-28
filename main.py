import os
import requests
from fastapi import FastAPI, Request, Form
from twilio.twiml.messaging_response import MessagingResponse
from services.doc_processor import DocumentProcessor
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

app = FastAPI()
processor = DocumentProcessor()

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Основной вебхук для приема сообщений от Twilio WhatsApp
    """
    # Получаем данные формы (Twilio шлет их как form-data)
    form_data = await request.form()
    
    sender = form_data.get("From", "") # format: whatsapp:+97250...
    media_url = form_data.get("MediaUrl0") # Ссылка на файл (если есть)
    media_type = form_data.get("MediaContentType0") # Тип файла
    body_text = form_data.get("Body", "").strip() # Текст сообщения
    
    # Очищаем номер телефона от "whatsapp:"
    user_phone = sender.replace("whatsapp:", "")
    
    # Готовим ответ для Twilio
    resp = MessagingResponse()
    
    print(f"--- Получен файл от {sender} ---")

    # СЦЕНАРИЙ 1: Пользователь прислал ФАЙЛ
    if media_url:
        try:
            # 1. Скачиваем файл локально
            ext = ".jpg" # Дефолт
            if media_type == "application/pdf":
                ext = ".pdf"
            elif "image" in media_type:
                ext = ".jpg"
            
            # Временное имя файла
            filename = f"temp_{user_phone}{ext}"
            local_path = os.path.join("temp_files", filename)
            
            # Скачиваем
            with open(local_path, 'wb') as f:
                f.write(requests.get(media_url).content)
            
            # 2. Отправляем в наш новый умный Процессор
            # Он сам улучшит фото, найдет имя, создаст папку и загрузит в Яндекс
            result = processor.process_and_upload(user_phone, local_path, filename)
            
            if result["status"] == "success":
                # Формируем красивый ответ
                doc_type = result.get("doc_type", "Документ")
                person = result.get("person", "Неизвестный")
                
                msg_body = (
                    f"✅ *Принято в архив!*\n"
                    f"📄 *Документ:* {doc_type}\n"
                    f"👤 *Клиент:* {person}\n"
                    f"📂 *Папка:* {person}"
                )
            else:
                msg_body = f"⚠️ Ошибка обработки: {result.get('message')}"
                
        except Exception as e:
            print(f"Error in main loop: {e}")
            msg_body = "❌ Произошла ошибка при скачивании или обработке файла."
            
        # Добавляем сообщение в ответ
        resp.message(msg_body)

    # СЦЕНАРИЙ 2: Пользователь прислал ТЕКСТ
    else:
        if body_text.lower() == "статус":
            # Тут можно будет прикрутить проверку базы данных
            resp.message("📂 Архив работает. Жду фото документов.")
        else:
            resp.message("Привет! Отправь мне фото документа (Теудат Зеут, Тлуш и т.д.), и я разложу его по папкам.")

    # Возвращаем XML для Twilio
    return str(resp)