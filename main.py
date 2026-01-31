import os
import logging
import requests
import hashlib
import hmac
from fastapi import FastAPI, Request, BackgroundTasks
from twilio.rest import Client as TwilioClient
from services.doc_processor import DocumentProcessor
from services.storage import publish_file
from dotenv import load_dotenv
from sqlmodel import Session, select
from database import init_db, engine, Client, Document
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request as StarletteRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
app = FastAPI()

REQUIRED_DOCS = {
    "ID_Document", "Passport", "Marriage_Certificate", "Birth_Certificate",
    "Police_Clearance", "Marital_Status_Doc", "Relationship_Letter",
    "Bank_Statement", "Salary_Slip", "Rental_Contract", "Utility_Bill",
    "Recommendation_Letter"
}

# --- ADMIN SETUP ---
class AdminAuth(AuthenticationBackend):
    async def login(self, request: StarletteRequest) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")
        stored_user = os.getenv("ADMIN_USERNAME", "admin")
        stored_hash = os.getenv("ADMIN_PASSWORD_HASH")
        if not stored_hash: return False
        input_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
        if username == stored_user and hmac.compare_digest(input_hash, stored_hash):
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

# --- SERVICES ---
twilio_client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
processor = DocumentProcessor()

@app.on_event("startup")
def on_startup():
    init_db()

def send_whatsapp_message(to_number, body_text):
    try:
        from_number = 'whatsapp:+14155238886'
        to = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
        twilio_client.messages.create(from_=from_number, body=body_text, to=to)
    except Exception as e:
        logger.error(f"Twilio error: {e}")

# --- ГЛАВНАЯ ЛОГИКА ОБРАБОТКИ ---
def process_file_task(user_phone, media_url, media_type):
    with Session(engine) as session:
        ext = ".pdf" if media_type == "application/pdf" else ".jpg"
        filename = f"temp_{user_phone}_{os.urandom(4).hex()}{ext}"
        local_path = os.path.join("temp_files", filename)
        
        try:
            response = requests.get(media_url)
            with open(local_path, 'wb') as f: f.write(response.content)
            
            # ТЕПЕРЬ ПОЛУЧАЕМ СПИСОК РЕЗУЛЬТАТОВ (Page 1, Page 2...)
            results_list = processor.process_and_upload(user_phone, local_path, filename)
            
            # Если вернулась фатальная ошибка списка (например, файл битый)
            if not results_list or (len(results_list) == 1 and results_list[0].get("status") == "error"):
                 send_whatsapp_message(user_phone, f"⚠️ Ошибка: {results_list[0].get('message')}")
                 return

            # Перебираем успешные страницы
            success_pages = [r for r in results_list if r["status"] == "success"]
            
            if not success_pages:
                send_whatsapp_message(user_phone, "⚠️ Не удалось обработать страницы документа.")
                return

            # Берем имя и тип из первой успешной страницы для клиента
            first_res = success_pages[0]
            person_name = first_res["person"]
            
            # Обновляем Клиента
            client = session.exec(select(Client).where(Client.phone_number == user_phone)).first()
            if not client:
                client = Client(phone_number=user_phone, full_name=person_name)
                session.add(client)
            elif client.full_name == "Unknown" and person_name != "Unknown":
                client.full_name = person_name
                session.add(client)
            session.commit()
            session.refresh(client)

            # Сохраняем КАЖДУЮ страницу в БД Documents
            added_types = set()
            last_link = None
            
            for page in success_pages:
                new_doc = Document(client_id=client.id, doc_type=page["doc_type"], file_path=page["filename"])
                session.add(new_doc)
                added_types.add(page["doc_type"])
                last_link = page["remote_path"] # Запомним последнюю ссылку
            
            session.commit()
            
            # Генерируем ссылку (на последнюю страницу или папку - пока на файл)
            # В идеале публиковать папку, но API Диска проще публикует файл.
            public_link = publish_file(last_link)
            
            # Считаем остаток
            existing = {d.doc_type for d in session.exec(select(Document).where(Document.client_id == client.id)).all()}
            missing = REQUIRED_DOCS - existing
            
            # Формируем отчет
            msg = f"✅ Принято страниц: {len(success_pages)}\n"
            msg += f"📄 Тип: {', '.join(added_types)}\n"
            msg += f"👤 Досье: {client.full_name}\n"
            
            if public_link:
                msg += f"🔗 Ссылка (пример): {public_link}\n"
            
            if missing:
                msg += f"\n⏳ Осталось сдать ({len(missing)}):\n- " + "\n- ".join(missing)
            else:
                msg += "\n🎉 Полный комплект собран!"
            
            send_whatsapp_message(user_phone, msg)

        except Exception as e:
            logger.error(f"Task error: {e}")
            send_whatsapp_message(user_phone, "❌ Сбой обработки.")
        finally:
            if os.path.exists(local_path): os.remove(local_path)

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    user_phone = form.get("From", "").replace("whatsapp:", "")
    media_url = form.get("MediaUrl0")
    
    if media_url:
        background_tasks.add_task(process_file_task, user_phone, media_url, form.get("MediaContentType0"))
        return "OK"
    
    body = form.get("Body", "").strip().lower()
    if body in ["статус", "status", "1", "check"]:
        with Session(engine) as session:
            client = session.exec(select(Client).where(Client.phone_number == user_phone)).first()
            if not client:
                send_whatsapp_message(user_phone, "📂 Досье пусто.")
            else:
                existing = {d.doc_type for d in session.exec(select(Document).where(Document.client_id == client.id)).all()}
                missing = REQUIRED_DOCS - existing
                
                report = f"📂 Досье: {client.full_name}\n✅ Сдано: {len(existing)}\n"
                if existing: report += f"- " + "\n- ".join(existing) + "\n"
                if missing: report += f"\n❌ НУЖНО ДОСЛАТЬ ({len(missing)}):\n- " + "\n- ".join(missing)
                else: report += "\n🎉 Всё готово!"
                send_whatsapp_message(user_phone, report)
        return "OK"
    
    send_whatsapp_message(user_phone, "🤖 Пришлите фото или PDF.")
    return "OK"