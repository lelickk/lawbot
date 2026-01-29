import os
import logging
import requests
import hashlib
import hmac
from fastapi import FastAPI, Request, BackgroundTasks
from twilio.rest import Client as TwilioClient
from services.doc_processor import DocumentProcessor
from services.yandex_disk import publish_file
from dotenv import load_dotenv
from sqlmodel import Session, select
from database import init_db, engine, Client, Document

# --- ИМПОРТЫ АДМИНКИ ---
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request as StarletteRequest
from starlette.responses import RedirectResponse

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
app = FastAPI()

# --- 1. НАСТРОЙКА БЕЗОПАСНОСТИ АДМИНКИ (DEBUG MODE) ---
class AdminAuth(AuthenticationBackend):
    async def login(self, request: StarletteRequest) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        # Получаем настройки из .env
        stored_user = os.getenv("ADMIN_USERNAME", "admin")
        stored_hash = os.getenv("ADMIN_PASSWORD_HASH")

        # ЛОГИРОВАНИЕ ДЛЯ ОТЛАДКИ (УДАЛИТЬ ПОТОМ)
        logger.info(f"--- LOGIN ATTEMPT ---")
        logger.info(f"Input Username: '{username}'")
        logger.info(f"Stored Username: '{stored_user}'")
        
        if not stored_hash:
            logger.error("CRITICAL: ADMIN_PASSWORD_HASH is empty in .env!")
            return False

        # Хешируем введенный пароль
        input_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
        
        logger.info(f"Input Password Hash: {input_hash}")
        logger.info(f"Stored Password Hash: {stored_hash}")

        # Сравниваем
        user_match = (username == stored_user)
        pass_match = hmac.compare_digest(input_hash, stored_hash)
        
        logger.info(f"Username Match: {user_match}")
        logger.info(f"Password Match: {pass_match}")

        if user_match and pass_match:
            logger.info("LOGIN SUCCESS")
            request.session.update({"token": "valid_token"})
            return True
            
        logger.warning("LOGIN FAILED")
        return False

    async def logout(self, request: StarletteRequest) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: StarletteRequest) -> bool:
        token = request.session.get("token")
        return bool(token)

# Инициализация защиты
authentication_backend = AdminAuth(secret_key=os.getenv("SECRET_KEY", "change_me_please"))

# --- 2. НАСТРОЙКА АДМИНКИ (VIEWS) ---
admin = Admin(app, engine, authentication_backend=authentication_backend)

class ClientAdmin(ModelView, model=Client):
    column_list = [Client.id, Client.phone_number, Client.full_name, Client.created_at]
    icon = "fa-solid fa-user"
    name_plural = "Clients"

class DocumentAdmin(ModelView, model=Document):
    column_list = [Document.id, Document.client_id, Document.doc_type, Document.file_path, Document.created_at]
    icon = "fa-solid fa-file"
    name_plural = "Documents"

admin.add_view(ClientAdmin)
admin.add_view(DocumentAdmin)

# --- 3. ИНИЦИАЛИЗАЦИЯ СЕРВИСОВ ---
twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_client = TwilioClient(twilio_sid, twilio_token)

processor = DocumentProcessor()

@app.on_event("startup")
def on_startup():
    init_db()

# Список обязательных документов (должен совпадать с выводом AI)
REQUIRED_DOCS = {
    "Теудат_Зеут", "Водительские_Права", "Чек", "Справка",
    "Тлуш_Маскорет", "Паспорт", "Загранпаспорт", "Справка_об_отсутствии_судимости"
}

# --- 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def send_whatsapp_message(to_number, body_text):
    """Отправка сообщения через Twilio API"""
    try:
        # Для Sandbox номер фиксированный. В проде замените на свой купленный Sender ID.
        from_number = 'whatsapp:+14155238886' 
        
        # Форматирование номера получателя
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
    """Фоновая задача: Скачать -> Обработать -> Сохранить -> Ответить"""
    logger.info(f"Starting background processing for {user_phone}")
    
    # Создаем временный файл
    import requests
    ext = ".jpg"
    if media_type == "application/pdf": ext = ".pdf"
    elif "image" in media_type: ext = ".jpg"
    
    filename = f"temp_{user_phone}_{os.urandom(4).hex()}{ext}"
    local_path = os.path.join("temp_files", filename)
    
    try:
        # 1. Скачивание
        with open(local_path, 'wb') as f:
            f.write(requests.get(media_url).content)
        
        # 2. Обработка (AI поворот, конвертация, загрузка)
        result = processor.process_and_upload(user_phone, local_path, filename)
        
        if result["status"] == "success":
            doc_type = result["doc_type"]
            person_name = result["person"]
            remote_path = result.get("remote_path")
            
            with Session(engine) as session:
                # 3. Работа с БД (Клиент)
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

                # 4. Работа с БД (Документ)
                new_doc = Document(
                    client_id=client.id,
                    doc_type=doc_type,
                    file_path=result["filename"]
                )
                session.add(new_doc)
                session.commit()
                
                # 5. Получение публичной ссылки
                public_link = publish_file(remote_path)
                
                # 6. Проверка комплектности
                docs_stmt = select(Document).where(Document.client_id == client.id)
                existing_docs = session.exec(docs_stmt).all()
                uploaded_types = {d.doc_type for d in existing_docs}
                missing = REQUIRED_DOCS - uploaded_types
                
                # Формирование отчета
                msg = f"✅ Сохранено: {doc_type}\n"
                if doc_type == "Другое":
                     msg += "⚠️ (Тип не распознан, но сохранен)\n"
                
                msg += f"👤 Досье: {client.full_name}\n"
                
                if public_link:
                    msg += f"🔗 Ссылка: {public_link}\n"
                else:
                    msg += "🔗 (Ссылка создается...)\n"
                
                if missing:
                    msg += f"\n❌ Осталось сдать:\n- " + "\n- ".join(missing)
                else:
                    msg += "\n🎉 Полный комплект собран!"
                
                send_whatsapp_message(user_phone, msg)
        else:
            send_whatsapp_message(user_phone, f"⚠️ Ошибка обработки: {result.get('message')}")
            
    except Exception as e:
        logger.error(f"Background task failed: {e}")
        send_whatsapp_message(user_phone, "❌ Произошла ошибка при обработке файла.")
    finally:
        # Удаляем временный файл скачивания
        if os.path.exists(local_path):
            os.remove(local_path)


# --- 5. WEBHOOK (ТОЧКА ВХОДА) ---
@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Принимает запрос от Twilio, отвечает 200 OK, запускает логику в фоне"""
    form_data = await request.form()
    
    sender = form_data.get("From", "") 
    user_phone = sender.replace("whatsapp:", "")
    media_url = form_data.get("MediaUrl0")
    media_type = form_data.get("MediaContentType0")
    body_text = form_data.get("Body", "").strip().lower()
    
    logger.info(f"Incoming: {user_phone}, Media: {bool(media_url)}, Text: '{body_text}'")

    # СЦЕНАРИЙ A: ВХОДЯЩИЙ ФАЙЛ
    if media_url:
        background_tasks.add_task(process_file_task, user_phone, media_url, media_type)
        return "OK"

    # СЦЕНАРИЙ B: КОМАНДА СТАТУС
    elif body_text in ["статус", "status", "отчет", "docs", "1"]:
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

    # СЦЕНАРИЙ C: ЛЮБОЙ ДРУГОЙ ТЕКСТ
    else:
        msg = "🤖 Привет! Я LawBot.\n\n📤 Отправь фото/PDF для архива.\n📊 Напиши 'Статус' для проверки."
        send_whatsapp_message(user_phone, msg)
        return "OK"