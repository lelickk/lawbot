import os
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

from fastapi import FastAPI, Request, Depends, Response
from sqlmodel import Session, select
from twilio.twiml.messaging_response import MessagingResponse

from database.models import create_db_and_tables, Client, Document, get_session
from services.ocr import analyze_document_with_ai 
# Подключаем наш новый модуль Яндекса
from services.yandex_drive import upload_to_yandex, init_yandex

load_dotenv()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

REQUIRED_DOCS = {
    "Паспорт",
    "Свидетельство о рождении",
    "Справка о несудимости",
    "Анкета"
}

app = FastAPI(title="LawBot AI")

@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    init_yandex() # Проверяем облако при старте

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, session: Session = Depends(get_session)):
    form_data = await request.form()
    
    sender_phone = form_data.get("From")
    media_url = form_data.get("MediaUrl0")
    body_text = form_data.get("Body", "").strip().lower()
    
    resp = MessagingResponse()
    
    # --- СЦЕНАРИЙ 1: ФАЙЛ ---
    if media_url:
        print(f"--- Получен файл от {sender_phone} ---")
        try:
            # Скачиваем файл (защита Twilio отключена)
            r = requests.get(media_url, timeout=15)
        except:
            resp.message("Ошибка сети при скачивании.")
            return Response(content=str(resp), media_type="application/xml")

        file_bytes = r.content
        content_type = r.headers.get('content-type', '')
        ext = ".pdf" if "pdf" in content_type else ".jpg"
        
        # Временное имя для анализа
        temp_filename = f"scan{ext}"
        
        # 1. АНАЛИЗ (AI)
        try:
            ai_response = analyze_document_with_ai(file_bytes, temp_filename)
            # Чистка JSON
            clean_json = ai_response.replace("```json", "").replace("```", "").strip()
            s = clean_json.find("{")
            e = clean_json.rfind("}") + 1
            if s != -1 and e != -1:
                data = json.loads(clean_json[s:e])
            else:
                data = json.loads(clean_json)
        except Exception as err:
            print(f"Ошибка AI: {err}")
            data = {"doc_type": "Документ", "full_name": "Неизвестно", "doc_date": ""}

        # 2. ПОДГОТОВКА ДАННЫХ
        doc_type = data.get("doc_type", "Документ")
        client_name = data.get("full_name")
        doc_date = data.get("doc_date", "").replace("/", "-").replace(".", "-")
        
        # Ищем клиента в базе
        statement = select(Client).where(Client.phone_number == sender_phone)
        client = session.exec(statement).first()
        
        # Если имя не распозналось, берем из базы или ставим заглушку
        if not client_name or client_name == "Unknown":
            if client:
                client_name = client.full_name
            else:
                client_name = f"Client_{sender_phone[-4:]}"

        # Формируем красивое имя файла для облака
        final_filename = f"{doc_type}_{doc_date}{ext}" if doc_date else f"{doc_type}{ext}"
        
        # 3. ЗАГРУЗКА В ЯНДЕКС.ДИСК
        yandex_link = upload_to_yandex(file_bytes, final_filename, client_name)
        
        if not yandex_link:
            yandex_link = "Ошибка загрузки (см. консоль)"

        # 4. ЗАПИСЬ В БАЗУ
        if not client:
            client = Client(phone_number=sender_phone, full_name=client_name)
            session.add(client)
            session.commit()
            session.refresh(client)
        
        new_doc = Document(
            client_id=client.id,
            doc_type=doc_type,
            status="approved",
            file_path=yandex_link, # Сохраняем ссылку на Яндекс
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        session.add(new_doc)
        session.commit()
        
        # 5. ОТЧЕТ О КОМПЛЕКТНОСТИ
        docs_stmt = select(Document).where(Document.client_id == client.id)
        existing_docs = session.exec(docs_stmt).all()
        uploaded_types = {d.doc_type for d in existing_docs}
        missing = REQUIRED_DOCS - uploaded_types
        
        msg = f"✅ Принято в Архив: {doc_type}\n"
        msg += f"🔗 Ссылка: {yandex_link}\n"
        if missing:
            msg += f"⚠️ Осталось сдать: {', '.join(missing)}"
        else:
            msg += "🎉 Полный комплект собран!"
            
        resp.message(msg)

    # --- СЦЕНАРИЙ 2: СТАТУС ---
    elif "статус" in body_text:
        statement = select(Client).where(Client.phone_number == sender_phone)
        client = session.exec(statement).first()
        
        if not client:
            resp.message("Архив пуст.")
        else:
            docs_stmt = select(Document).where(Document.client_id == client.id)
            existing_docs = session.exec(docs_stmt).all()
            uploaded_types = {d.doc_type for d in existing_docs}
            missing = REQUIRED_DOCS - uploaded_types
            
            report = f"📂 Досье: {client.full_name}\n"
            report += f"✅ Сдано ({len(existing_docs)} шт.):\n"
            # Выводим последние 5 документов с ссылками
            for d in existing_docs[-5:]:
                report += f"- {d.doc_type} (Ссылка: {d.file_path})\n"
                
            if missing:
                report += f"\n❌ Не хватает: {', '.join(missing)}"
            
            resp.message(report)
            
    else:
        resp.message("Пришлите фото документа или напишите 'Статус'.")

    return Response(content=str(resp), media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)