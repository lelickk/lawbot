import os
from fastapi import FastAPI, Request
from twilio.twiml.messaging_response import MessagingResponse
from services.doc_processor import DocumentProcessor
from dotenv import load_dotenv
from sqlmodel import Session, select
from database import init_db, engine, Client, Document

# 1. Загрузка окружения и БД
load_dotenv()
app = FastAPI()

# Инициализируем базу данных при старте
@app.on_event("startup")
def on_startup():
    init_db()

processor = DocumentProcessor()

# 2. СПИСОК ОБЯЗАТЕЛЬНЫХ ДОКУМЕНТОВ
# (Названия должны совпадать с тем, что выдает GPT-4o)
REQUIRED_DOCS = {
    "Теудат_Зеут",       # GPT обычно возвращает на латинице или как мы настроили
    "Водительские_Права",
    "Чек",
    "Справка"
    "Тлуш_Маскорет"
    "Паспорт"
    "Загранпаспорт"
    "Справка об отсутствии судимости"
}
# Примечание: Лучше настроить промпт GPT выдавать именно эти типы, 
# сейчас мы будем сверять по тому, что придет.

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    form_data = await request.form()
    
    sender = form_data.get("From", "") 
    user_phone = sender.replace("whatsapp:", "")
    
    media_url = form_data.get("MediaUrl0")
    media_type = form_data.get("MediaContentType0")
    body_text = form_data.get("Body", "").strip().lower()
    
    resp = MessagingResponse()
    
    # Открываем сессию БД
    with Session(engine) as session:
        
        # --- СЦЕНАРИЙ 1: ПРИШЕЛ ФАЙЛ ---
        if media_url:
            print(f"--- Получен файл от {user_phone} ---")
            
            # Скачиваем (как раньше)
            import requests
            ext = ".jpg"
            if media_type == "application/pdf": ext = ".pdf"
            elif "image" in media_type: ext = ".jpg"
            
            filename = f"temp_{user_phone}{ext}"
            local_path = os.path.join("temp_files", filename)
            
            try:
                with open(local_path, 'wb') as f:
                    f.write(requests.get(media_url).content)
                
                # Обрабатываем
                result = processor.process_and_upload(user_phone, local_path, filename)
                
                if result["status"] == "success":
                    doc_type = result["doc_type"]
                    person_name = result["person"]
                    final_filename = result["filename"]
                    
                    # 1. Ищем или создаем клиента в БД
                    statement = select(Client).where(Client.phone_number == user_phone)
                    client = session.exec(statement).first()
                    
                    if not client:
                        client = Client(phone_number=user_phone, full_name=person_name)
                        session.add(client)
                        session.commit()
                        session.refresh(client)
                    else:
                        # Обновляем имя, если вдруг стало известно точнее
                        if client.full_name == "Unknown" and person_name != "Unknown":
                            client.full_name = person_name
                            session.add(client)
                            session.commit()

                    # 2. Записываем документ в БД
                    new_doc = Document(
                        client_id=client.id,
                        doc_type=doc_type,
                        file_path=final_filename
                    )
                    session.add(new_doc)
                    session.commit()
                    
                    # 3. Проверяем, чего не хватает
                    # Получаем все доки клиента
                    docs_stmt = select(Document).where(Document.client_id == client.id)
                    existing_docs = session.exec(docs_stmt).all()
                    
                    # Собираем типы, которые уже есть (убираем _ и приводим к регистру если надо)
                    uploaded_types = {d.doc_type for d in existing_docs}
                    
                    # Вычисляем разницу
                    # (Для простоты пока просто вычитаем множества, если названия совпадают)
                    missing = REQUIRED_DOCS - uploaded_types
                    
                    # Формируем ответ
                    msg = f"✅ Принято в Архив: {doc_type}\n"
                    msg += f"👤 Клиент: {client.full_name}\n"
                    
                    if missing:
                        msg += f"\n⚠️ Осталось сдать:\n- " + "\n- ".join(missing)
                    else:
                        msg += "\n🎉 Полный комплект собран! Спасибо."
                    
                    resp.message(msg)
                    
                else:
                    resp.message(f"⚠️ Ошибка обработки: {result.get('message')}")
                    
            except Exception as e:
                print(f"Error: {e}")
                resp.message("❌ Произошла ошибка при обработке.")

        # --- СЦЕНАРИЙ 2: КОМАНДА "СТАТУС" ---
        elif "статус" in body_text:
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
                report += f"📥 Всего документов: {len(existing_docs)}\n"
                
                if existing_docs:
                    report += "\n✅ Сдано:\n"
                    # Берем уникальные типы
                    for dtype in uploaded_types:
                        report += f"- {dtype}\n"
                
                if missing:
                    report += "\n❌ Не хватает:\n- " + "\n- ".join(missing)
                else:
                    report += "\n🎉 Все документы собраны!"
                
                resp.message(report)
        
        # --- СЦЕНАРИЙ 3: НЕПОНЯТНЫЙ ТЕКСТ ---
        else:
            resp.message("Привет! 👋\nПришли фото документа, и я сохраню его.\nНапиши 'Статус', чтобы узнать, чего не хватает.")

    return str(resp)