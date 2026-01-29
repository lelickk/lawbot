import os
import logging
from fastapi import FastAPI, Request, BackgroundTasks, Form
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from services.doc_processor import DocumentProcessor
from dotenv import load_dotenv
from sqlmodel import Session, select
from database import init_db, engine, Client, Document

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
app = FastAPI()

# Инициализация Twilio API для отправки сообщений
twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_client = TwilioClient(twilio_sid, twilio_token)

@app.on_event("startup")
def on_startup():
    init_db()

processor = DocumentProcessor()

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

def send_whatsapp_message(to_number, body_text):
    """Отправляет сообщение пользователю через API (не зависит от тайм-аута)"""
    try:
        # Обычно номер бота это 'whatsapp:+14155238886' (Sandbox) или твой купленный
        # Лучше брать его из .env, но пока захардкодим стандартный Sandbox или возьмем динамически
        # Если ты в Sandbox, убедись, что это тот номер.
        from_number = 'whatsapp:+14155238886' 
        
        message = twilio_client.messages.create(
            from_=from_number,
            body=body_text,
            to=to_number
        )
        logger.info(f"Message sent to {to_number}: {message.sid}")
    except Exception as e:
        logger.error(f"Failed to send message: {e}")

def process_file_task(user_phone, media_url, media_type):
    """Эта функция работает в фоне, долго и упорно"""
    logger.info(f"Starting background processing for {user_phone}")
    
    with Session(engine) as session:
        import requests
        ext = ".jpg"
        if media_type == "application/pdf": ext = ".pdf"
        elif "image" in media_type: ext = ".jpg"
        
        filename = f"temp_{user_phone}_{os.urandom(4).hex()}{ext}"
        local_path = os.path.join("temp_files", filename)
        
        try:
            # 1. Скачиваем
            with open(local_path, 'wb') as f:
                f.write(requests.get(media_url).content)
            
            # 2. Обрабатываем (PDF/AI/Yandex)
            result = processor.process_and_upload(user_phone, local_path, filename)
            
            if result["status"] == "success":
                doc_type = result["doc_type"]
                person_name = result["person"]
                
                # 3. База данных
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

                new_doc = Document(
                    client_id=client.id,
                    doc_type=doc_type,
                    file_path=result["filename"]
                )
                session.add(new_doc)
                session.commit()
                
                # 4. Проверка комплекта
                docs_stmt = select(Document).where(Document.client_id == client.id)
                existing_docs = session.exec(docs_stmt).all()
                uploaded_types = {d.doc_type for d in existing_docs}
                missing = REQUIRED_DOCS - uploaded_types
                
                msg = f"✅ Сохранено: {doc_type}\n"
                if doc_type == "Другое":
                     msg += "⚠️ (Тип не распознан, не учтен в списке)\n"
                msg += f"👤 Досье: {client.full_name}\n"
                
                if missing:
                    msg += f"\n❌ Осталось сдать:\n- " + "\n- ".join(missing)
                else:
                    msg += "\n🎉 Полный комплект собран!"
                
                # ОТПРАВЛЯЕМ ОТВЕТ
                send_whatsapp_message(f"whatsapp:{user_phone}", msg)
                
            else:
                error_msg = f"⚠️ Ошибка обработки: {result.get('message')}"
                send_whatsapp_message(f"whatsapp:{user_phone}", error_msg)
                
        except Exception as e:
            logger.error(f"Background task failed: {e}")
            send_whatsapp_message(f"whatsapp:{user_phone}", "❌ Произошла системная ошибка при обработке.")

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Вебхук теперь отвечает МГНОВЕННО, а работу скидывает в фон.
    """
    form_data = await request.form()
    
    sender = form_data.get("From", "") 
    user_phone = sender.replace("whatsapp:", "")
    media_url = form_data.get("MediaUrl0")
    media_type = form_data.get("MediaContentType0")
    body_raw = form_data.get("Body", "")
    body_text = body_raw.strip().lower()
    
    logger.info(f"Incoming: {user_phone}, Media: {bool(media_url)}, Text: {body_text}")

    resp = MessagingResponse()

    # --- СЦЕНАРИЙ 1: ФАЙЛ ---
    if media_url:
        # Сразу говорим пользователю "Жди"
        # resp.message("⏳ Принято. Обрабатываю...") 
        # (Можно не отвечать ничего, тогда пользователь просто увидит, что сообщение доставлено,
        # а потом придет ответ. Но лучше дать фидбек).
        
        # Добавляем задачу в фон
        background_tasks.add_task(process_file_task, user_phone, media_url, media_type)
        
        return "OK" # Возвращаем пустой 200 OK, Twilio доволен. 
                    # Ответ придет отдельным сообщением из функции выше.

    # --- СЦЕНАРИЙ 2: СТАТУС (Это быстро, можно синхронно) ---
    elif body_text in ["статус", "status", "отчет", "docs", "1"]:
        with Session(engine) as session:
            statement = select(Client).where(Client.phone_number == user_phone)
            client = session.exec(statement).first()
            
            if not client:
                resp.message("📂 Досье пусто. Пришлите документ.")
            else:
                docs_stmt = select(Document).where(Document.client_id == client.id)
                existing_docs = session.exec(docs_stmt).all()
                uploaded_types = {d.doc_type for d in existing_docs}
                missing = REQUIRED_DOCS - uploaded_types
                
                report = f"📂 Досье: {client.full_name}\n"
                report += f"📥 Всего файлов: {len(existing_docs)}\n"
                if missing:
                     report += "\n❌ Не хватает:\n- " + "\n- ".join(missing)
                else:
                    report += "\n🎉 Комплект собран!"
                
                resp.message(report)
        return str(resp)

    # --- СЦЕНАРИЙ 3: ПРИВЕТСТВИЕ ---
    else:
        resp.message("🤖 Привет! Пришли фото/PDF документа. Напиши 'Статус' для проверки.")
        return str(resp)