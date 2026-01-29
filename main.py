import os
import logging
from fastapi import FastAPI, Request
from twilio.twiml.messaging_response import MessagingResponse
from services.doc_processor import DocumentProcessor
from dotenv import load_dotenv
from sqlmodel import Session, select
from database import init_db, engine, Client, Document

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
app = FastAPI()

@app.on_event("startup")
def on_startup():
    init_db()

processor = DocumentProcessor()

# СПИСОК ДОКУМЕНТОВ
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

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    form_data = await request.form()
    
    sender = form_data.get("From", "") 
    user_phone = sender.replace("whatsapp:", "")
    
    # Получаем данные
    media_url = form_data.get("MediaUrl0")
    media_type = form_data.get("MediaContentType0")
    body_raw = form_data.get("Body", "")
    body_text = body_raw.strip().lower()
    
    logger.info(f"👉 NEW MESSAGE from {user_phone}. Body: '{body_text}', Media: {media_type}")
    
    resp = MessagingResponse()
    
    with Session(engine) as session:
        
        # --- СЦЕНАРИЙ 1: ФАЙЛ (ФОТО или PDF) ---
        if media_url:
            logger.info("✅ Scenario: FILE UPLOAD triggered")
            import requests
            ext = ".jpg"
            if media_type == "application/pdf": ext = ".pdf"
            elif "image" in media_type: ext = ".jpg"
            
            filename = f"temp_{user_phone}_{os.urandom(4).hex()}{ext}"
            local_path = os.path.join("temp_files", filename)
            
            try:
                with open(local_path, 'wb') as f:
                    f.write(requests.get(media_url).content)
                
                # Сразу отвечаем, чтобы WhatsApp не таймаутил
                # resp.message("⏳ Принято, обрабатываю...") 
                # (Twilio поддерживает только 1 ответ, поэтому лучше сразу финальный)

                result = processor.process_and_upload(user_phone, local_path, filename)
                
                if result["status"] == "success":
                    doc_type = result["doc_type"]
                    person_name = result["person"]
                    
                    # Логика БД (Клиент)
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

                    # Логика БД (Документ)
                    new_doc = Document(
                        client_id=client.id,
                        doc_type=doc_type,
                        file_path=result["filename"]
                    )
                    session.add(new_doc)
                    session.commit()
                    
                    # Проверка списка
                    docs_stmt = select(Document).where(Document.client_id == client.id)
                    existing_docs = session.exec(docs_stmt).all()
                    uploaded_types = {d.doc_type for d in existing_docs}
                    missing = REQUIRED_DOCS - uploaded_types
                    
                    # Формируем ответ
                    msg = f"✅ Сохранено: {doc_type}\n"
                    if doc_type == "Другое":
                         msg += "⚠️ (Тип документа не распознан, он не учтен в списке)\n"
                    
                    msg += f"👤 Досье: {client.full_name}\n"
                    
                    if missing:
                        msg += f"\n❌ Осталось сдать:\n- " + "\n- ".join(missing)
                    else:
                        msg += "\n🎉 Полный комплект собран!"
                    
                    logger.info(f"Sending reply: {msg}")
                    resp.message(msg)
                    
                else:
                    resp.message(f"⚠️ Ошибка: {result.get('message')}")
                    
            except Exception as e:
                logger.error(f"Error in file handler: {e}")
                resp.message("❌ Сбой обработки файла.")

        # --- СЦЕНАРИЙ 2: КОМАНДА СТАТУС ---
        elif body_text in ["статус", "status", "отчет", "docs", "1"]:
            logger.info("✅ Scenario: STATUS triggered")
            
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
        
        # --- СЦЕНАРИЙ 3: ПРИВЕТСТВИЕ ---
        else:
            logger.info("✅ Scenario: DEFAULT triggered")
            resp.message("🤖 Привет! Пришли фото документа или напиши 'Статус'.")

    return str(resp)