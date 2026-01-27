import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
from dotenv import load_dotenv

load_dotenv()

# Настройки
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'google_credentials.json'
PARENT_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

def authenticate():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

def find_or_create_folder(service, folder_name, parent_id):
    """Ищет папку по имени. Если нет - создает."""
    query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and '{parent_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get('files', [])

    if items:
        return items[0]['id'] # Папка уже есть
    else:
        # Создаем новую
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        folder = service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')

def upload_to_drive(file_bytes, filename, client_name):
    """
    Главная функция: 
    1. Находит папку клиента (или создает).
    2. Загружает туда файл.
    3. Возвращает ссылку на файл.
    """
    try:
        service = authenticate()
        
        # 1. Готовим папку клиента
        # Очищаем имя от плохих символов
        safe_name = "".join([c for c in client_name if c.isalnum() or c.isspace()]).strip()
        client_folder_id = find_or_create_folder(service, safe_name, PARENT_FOLDER_ID)
        
        # 2. Загружаем файл
        file_metadata = {
            'name': filename,
            'parents': [client_folder_id]
        }
        
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype='application/octet-stream', resumable=True)
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        print(f"Файл загружен в Google Drive: {file.get('webViewLink')}")
        return file.get('webViewLink')
        
    except Exception as e:
        print(f"Ошибка Google Drive: {e}")
        return None