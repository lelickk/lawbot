import os
import logging
import requests
from fastapi import FastAPI, Request, BackgroundTasks, Form
from twilio.rest import Client as TwilioClient
from services.doc_processor import DocumentProcessor
from services.yandex_disk import publish_file
from dotenv import load_dotenv
from sqlmodel import Session, select
from database import init_db, engine, Client, Document

# --- НОВЫЕ ИМПОРТЫ ДЛЯ АДМИНКИ ---
from sqladmin import Admin, ModelView

# --- НАСТРОЙКА ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
app = FastAPI()

# --- НАСТРОЙКА АДМИНКИ ---
# Доступна по адресу /admin
admin = Admin(app, engine)

class ClientAdmin(ModelView, model=Client):
    column_list = [Client.id, Client.phone_number, Client.full_name, Client.created_at]

class DocumentAdmin(ModelView, model=Document):
    column_list = [Document.id, Document.client_id, Document.doc_type, Document.file_path, Document.created_at]

admin.add_view(ClientAdmin)
admin.add_view(DocumentAdmin)
# -----------------------------

# Инициализация Twilio
twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_client = TwilioClient(twilio_sid, twilio_token)

@app.on_event("startup")
def on_startup():
    init_db()

processor = DocumentProcessor()

# СПИСОК ОБЯЗАТЕЛЬНЫХ ДОКУМЕНТОВ
REQUIRED_DOCS = {
    "Теудат_Зеут",
    "Водительские_Права",
    "Чек",
    "Справка",
    "Тлуш_Маскорет",
    "Паспорт",
    "Загранпаспорт",
    "Справка_об_отсутствии_судимости"
}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def send_whatsapp_message(to_number, body_text):
    """Отправляет сообщение пользователю через API"""
    try:
        # Для Sandbox номер фиксированный. Для продакшена замените на свой.
        from_number = 'whatsapp:+14155238886' 
        
        # Нормализация номера
        to = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
        
        message = twilio_client.messages.create(
            from_=from_number,
            body=body_text,
            to=to
        )
        logger.info(f"Message sent to {to_number}: {message.sid}")
    except Exception as e:
        logger.error(f"Failed to send message: {e}")

def process_file_task(user_phone, media_url, media_type):
    """Фоновая задача обработки файла"""
    logger.info(f"Starting background processing for {user_phone}")
    
    with Session(engine) as session:
        # Определяем расширение
        ext = ".jpg"
        if media_type == "application/pdf": ext = ".pdf"
        elif "image" in media_type: ext = ".jpg"
        
        filename = f"temp_{user_phone}_{os.urandom(4).hex()}{ext}"
        local_path = os.path.join("temp_files", filename)
        
        try:
            # 1. Скачиваем
            with open(local_path, 'wb') as f:
                f.write(requests.get(media_url).content)
            
            # 2. Обрабатываем (Поворот -> PDF -> AI -> Yandex)
            result = processor.process_and_upload(user_phone, local_path, filename)
            
            if result["status"] == "success":
                doc_type = result["doc_type"]
                person_name = result["person"]
                remote_path = result.get("remote_path")
                
                # 3. БД Клиент
                statement = select(Client).where(Client.phone_number == user_phone)
                client = session.exec(statement).first()
                
                if not client:
                    client = Client(phone_number=user_phone, full_name=person_name)
                    session.add(client)
                    session.commit()
                    session.refresh(client)
                elif client.full_name == "Unknown" and person_name != "Unknown":
                    client.full_name = person_name
                    session.add(client)
                    session.commit()

                # 4. БД Документ
                new_doc = Document(
                    client_id=client.id,
                    doc_type=doc_type,
                    file_path=result["filename"]
                )
                session.add(new_doc)
                session.commit()
                
                # 5. Публикуем ссылку
                public_link = publish_file(remote_path)
                
                # 6. Отчет о комплекте
                docs_stmt = select(Document).where(Document.client_id == client.id)
                existing_docs = session.exec(docs_stmt).all()
                uploaded_types = {d.doc_type for d in existing_docs}
                missing = REQUIRED_DOCS - uploaded_types
                
                msg = f"✅ Сохранено: {doc_type}\n"
                if doc_type == "Другое":
                     msg += "⚠️ (Тип не распознан)\n"
                
                msg += f"👤 Досье: {client.full_name}\n"
                
                if public_link:
                    msg += f"🔗 Ссылка: {public_link}\n"
                
                if missing:
                    msg += f"\n❌ Осталось сдать:\n- " + "\n- ".join(missing)
                else:
                    msg += "\n🎉 Полный комплект собран!"
                
                send_whatsapp_message(user_phone, msg)
                
            else:
                send_whatsapp_message(user_phone, f"⚠️ Ошибка обработки: {result.get('message')}")
                
        except Exception as e:
            logger.error(f"Background task failed: {e}")
            send_whatsapp_message(user_phone, "❌ Системная ошибка при обработке.")
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

# --- WEBHOOK ---

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Вебхук принимает запрос и сразу отвечает OK, работу шлет в фон"""
    form_data = await request.form()
    
    sender = form_data.get("From", "") 
    user_phone = sender.replace("whatsapp:", "")
    media_url = form_data.get("MediaUrl0")
    media_type = form_data.get("MediaContentType0")
    body_raw = form_data.get("Body", "")
    body_text = body_raw.strip().lower()
    
    logger.info(f"Incoming: {user_phone}, Media: {bool(media_url)}, Text: '{body_text}'")

    # СЦЕНАРИЙ 1: ФАЙЛ
    if media_url:
        background_tasks.add_task(process_file_task, user_phone, media_url, media_type)
        return "OK"

    # СЦЕНАРИЙ 2: КОМАНДА СТАТУС
    elif body_text in ["статус", "status", "отчет", "docs", "1"]:
        # Статус формируем тут же, но шлем через API для надежности
        with Session(engine) as session:
            statement = select(Client).where(Client.phone_number == user_phone)
            client = session.exec(statement).first()
            
            if not client:
                send_whatsapp_message(user_phone, "📂 Досье пусто. Пришлите первый документ.")
            else:
                docs_stmt = select(Document).where(Document.client_id == client.id)
                existing_docs = session.exec(docs_stmt).all()
                uploaded_types = {d.doc_type for d in existing_docs}
                missing = REQUIRED_DOCS - uploaded_types
                
                report = f"📂 Досье: {client.full_name}\n"
                report += f"📥 Всего файлов: {len(existing_docs)}\n"
                if uploaded_types:
                    report += "✅ Сдано: " + ", ".join(uploaded_types) + "\n"

                if missing:
                     report += "\n❌ Не хватает:\n- " + "\n- ".join(missing)
                else:
                    report += "\n🎉 Все документы собраны!"
                
                send_whatsapp_message(user_phone, report)
        return "OK"

    # СЦЕНАРИЙ 3: ДРУГОЕ
    else:
        msg = "🤖 LawBot слушает.\n\n📤 Отправьте файл для архива.\n📊 Напишите 'Статус' для проверки."
        send_whatsapp_message(user_phone, msg)
        return "OK"