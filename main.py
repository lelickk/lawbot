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

# СПИСОК ОБЯЗАТЕЛЬНЫХ ДОКУМЕНТОВ
# Должен совпадать с тем, что возвращает AI (включая подчеркивания)
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
    media_url = form_data.get("MediaUrl0")
    media_type = form_data.get("MediaContentType0")
    
    body_raw = form_data.get("Body", "")
    body_text = body_raw.strip().lower()
    
    logger.info(f"Message from {user_phone}. Text: '{body_text}', Media: {media_type}")
    
    resp = MessagingResponse()
    
    with Session(engine) as session:
        
        # --- СЦЕНАРИЙ 1: ПРИШЕЛ ФАЙЛ ---
        if media_url:
            import requests
            ext = ".jpg"
            if media_type == "application/pdf": ext = ".pdf"
            elif "image" in media_type: ext = ".jpg"
            
            # Уникальное имя временного файла
            filename = f"temp_{user_phone}_{os.urandom(4).hex()}{ext}"
            local_path = os.path.join("temp_files", filename)
            
            try:
                # Скачиваем файл
                with open(local_path, 'wb') as f:
                    f.write(requests.get(media_url).content)
                
                # Сообщаем, что начали работу
                resp.message("⏳ Документ принят, обрабатываю...")
                
                # Обработка
                result = processor.process_and_upload(user_phone, local_path, filename)
                
                if result["status"] == "success":
                    doc_type = result["doc_type"]
                    person_name = result["person"]
                    
                    # Логика клиента (создать или обновить имя)
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

                    # Сохраняем документ в БД
                    new_doc = Document(
                        client_id=client.id,
                        doc_type=doc_type,
                        file_path=result["filename"]
                    )
                    session.add(new_doc)
                    session.commit()
                    
                    # Проверка комплектности
                    docs_stmt = select(Document).where(Document.client_id == client.id)
                    existing_docs = session.exec(docs_stmt).all()
                    uploaded_types = {d.doc_type for d in existing_docs}
                    
                    missing = REQUIRED_DOCS - uploaded_types
                    
                    msg = f"✅ Архив обновлен: {doc_type}\n"
                    msg += f"👤 Клиент: {client.full_name}\n"
                    if missing:
                        msg += f"\n⚠️ Осталось сдать:\n- " + "\n- ".join(missing)
                    else:
                        msg += "\n🎉 Полный комплект собран! Спасибо."
                    
                    resp.message(msg)
                    
                else:
                    resp.message(f"⚠️ Ошибка обработки: {result.get('message')}")
                    
            except Exception as e:
                logger.error(f"Error processing file: {e}")
                resp.message("❌ Произошла ошибка при обработке файла.")

        # --- СЦЕНАРИЙ 2: КОМАНДА "СТАТУС" ---
        elif body_text in ["статус", "status", "отчет", "документы", "docs"]:
            statement = select(Client).where(Client.phone_number == user_phone)
            client = session.exec(statement).first()
            
            if not client:
                resp.message("📂 Ваше досье пока пусто. Пришлите первый документ.")
            else:
                docs_stmt = select(Document).where(Document.client_id == client.id)
                existing_docs = session.exec(docs_stmt).all()
                uploaded_types = {d.doc_type for d in existing_docs}
                missing = REQUIRED_DOCS - uploaded_types
                
                report = f"📂 Досье: {client.full_name}\n"
                report += f"📥 Принято документов: {len(existing_docs)}\n"
                
                if missing:
                     report += "\n❌ Не хватает:\n- " + "\n- ".join(missing)
                else:
                    report += "\n🎉 Все необходимые документы собраны!"
                
                resp.message(report)
        
        # --- СЦЕНАРИЙ 3: ДРУГОЙ ТЕКСТ ---
        else:
            resp.message("🤖 Привет! Отправьте мне фото/PDF документа.\nНапишите 'Статус' для проверки комплекта.")

    return str(resp)