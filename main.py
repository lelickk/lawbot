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
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request as StarletteRequest

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()
app = FastAPI()

# --- 1. СПИСОК ОБЯЗАТЕЛЬНЫХ ДОКУМЕНТОВ (СТУПРО) ---
# Эти документы бот будет требовать.
REQUIRED_DOCS = {
    "ID_Document",          # ТЗ / ID
    "Passport",             # Загранпаспорт
    "Marriage_Certificate", # Свидетельство о браке
    "Birth_Certificate",    # Свидетельство о рождении
    "Police_Clearance",     # Справка о несудимости (Теудат Йошер)
    "Marital_Status_Doc",   # Справка о семейном положении / Развод
    "Relationship_Letter",  # Письмо о знакомстве
    "Bank_Statement",       # Распечатка из банка
    "Salary_Slip",          # Тлуши (зарплата)
    "Rental_Contract",      # Договор аренды
    "Utility_Bill",         # Счета (вода/свет/арнона)
    "Recommendation_Letter" # Письма от друзей/семьи
}

# --- 2. НАСТРОЙКА АДМИНКИ (SQLAdmin) ---
class AdminAuth(AuthenticationBackend):
    async def login(self, request: StarletteRequest) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        stored_user = os.getenv("ADMIN_USERNAME", "admin")
        stored_hash = os.getenv("ADMIN_PASSWORD_HASH")

        if not stored_hash:
            logger.error("CRITICAL: ADMIN_PASSWORD_HASH is empty in .env!")
            return False

        input_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
        
        user_match = (username == stored_user)
        pass_match = hmac.compare_digest(input_hash, stored_hash)

        if user_match and pass_match:
            request.session.update({"token": "valid_token"})
            return True
        return False

    async def logout(self, request: StarletteRequest) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: StarletteRequest) -> bool:
        return bool(request.session.get("token"))

authentication_backend = AdminAuth(secret_key=os.getenv("SECRET_KEY", "change_me_please"))
admin = Admin(app, engine, authentication_backend=authentication_backend)

class ClientAdmin(ModelView, model=Client):
    column_list = [Client.id, Client.phone_number, Client.full_name, Client.created_at]
    icon = "fa-solid fa-user"

class DocumentAdmin(ModelView, model=Document):
    column_list = [Document.id, Document.client_id, Document.doc_type, Document.file_path, Document.created_at]
    icon = "fa-solid fa-file"

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

# --- 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def send_whatsapp_message(to_number, body_text):
    """Отправка сообщения через Twilio"""
    try:
        from_number = 'whatsapp:+14155238886' 
        to = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
        
        twilio_client.messages.create(
            from_=from_number,
            body=body_text,
            to=to
        )
    except Exception as e:
        logger.error(f"Failed to send message to {to_number}: {e}")

def process_file_task(user_phone, media_url, media_type):
    """Фоновая задача: Скачать -> Распознать -> Загрузить (Ориг+PDF) -> Ответить"""
    with Session(engine) as session:
        ext = ".jpg"
        if media_type == "application/pdf":
            ext = ".pdf"
        elif "image" in media_type:
            ext = ".jpg"
        
        filename = f"temp_{user_phone}_{os.urandom(4).hex()}{ext}"
        local_path = os.path.join("temp_files", filename)
        
        try:
            # 1. Скачиваем
            response = requests.get(media_url)
            with open(local_path, 'wb') as f:
                f.write(response.content)
            
            # 2. Обрабатываем
            result = processor.process_and_upload(user_phone, local_path, filename)
            
            if result["status"] == "success":
                doc_type = result["doc_type"]
                person_name = result["person"]
                remote_path = result.get("remote_path")
                
                # 3. Обновляем БД (Клиент)
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

                # 4. Обновляем БД (Документ)
                new_doc = Document(client_id=client.id, doc_type=doc_type, file_path=result["filename"])
                session.add(new_doc)
                session.commit()
                
                # 5. Проверяем, чего не хватает (Полный список)
                docs_stmt = select(Document).where(Document.client_id == client.id)
                existing_docs = session.exec(docs_stmt).all()
                uploaded_types = {d.doc_type for d in existing_docs}
                
                missing = REQUIRED_DOCS - uploaded_types
                
                # 6. Формируем ответ
                msg = f"✅ Принято: {doc_type}\n"
                msg += f"👤 Досье: {client.full_name}\n"
                
                if missing:
                    msg += f"\n⏳ Осталось собрать ({len(missing)} шт):\n- "
                    msg += "\n- ".join(missing)
                else:
                    msg += "\n🎉 Полный комплект собран! Ожидайте проверки."
                
                send_whatsapp_message(user_phone, msg)
                
            else:
                send_whatsapp_message(user_phone, f"⚠️ Ошибка: {result.get('message')}")
                
        except Exception as e:
            logger.error(f"Task failed: {e}")
            send_whatsapp_message(user_phone, "❌ Произошла внутренняя ошибка сервера.")
            
        finally:
            if os.path.exists(local_path):
                try: os.remove(local_path)
                except: pass

# --- 5. WEBHOOK ДЛЯ WHATSAPP ---
@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    form_data = await request.form()
    
    user_phone = form_data.get("From", "").replace("whatsapp:", "")
    media_url = form_data.get("MediaUrl0")
    media_type = form_data.get("MediaContentType0")
    body_text = form_data.get("Body", "").strip().lower()
    
    # 1. Пришел файл
    if media_url:
        background_tasks.add_task(process_file_task, user_phone, media_url, media_type)
        return "OK"
    
    # 2. Команда "Статус"
    elif body_text in ["статус", "status", "отчет", "1", "check"]:
        with Session(engine) as session:
            statement = select(Client).where(Client.phone_number == user_phone)
            client = session.exec(statement).first()
            
            if not client:
                send_whatsapp_message(user_phone, "📂 Ваше досье пусто. Пришлите документы.")
            else:
                docs_stmt = select(Document).where(Document.client_id == client.id)
                existing_docs = session.exec(docs_stmt).all()
                uploaded_types = {d.doc_type for d in existing_docs}
                
                missing = REQUIRED_DOCS - uploaded_types
                
                report = f"📂 Досье: {client.full_name}\n"
                report += f"📥 Всего файлов: {len(existing_docs)}\n"
                
                if uploaded_types:
                    # ВЫВОДИМ ПОЛНЫЙ СПИСОК (убрали [:3])
                    report += f"✅ Есть:\n- " + "\n- ".join(uploaded_types)
                    report += "\n"
                
                if missing:
                    report += "\n❌ НУЖНО ДОСЛАТЬ:\n- " + "\n- ".join(missing)
                else:
                    report += "\n🎉 Всё отлично! Базовый комплект готов."
                
                send_whatsapp_message(user_phone, report)
        return "OK"
    
    # 3. Просто текст
    else:
        send_whatsapp_message(user_phone, "🤖 Привет! Я LawBot.\n📸 Пришли мне фото или PDF, и я сохраню их.")
        return "OK"