import os
import shutil
from pathlib import Path

# Папка, где будем хранить документы (локально)
BASE_STORAGE_DIR = "filestorage"

def save_document_locally(file_bytes, filename, analysis_data):
    """
    Принимает файл и данные от ИИ.
    Создает папку клиента и сохраняет файл с красивым именем.
    """
    
    # 1. Вытаскиваем данные из анализа
    # Если ИИ не нашел имя, назовем "Неизвестный Клиент"
    full_name = analysis_data.get("full_name", "Unknown_Client")
    doc_type = analysis_data.get("doc_type", "Document")
    doc_date = analysis_data.get("doc_date", "NoDate")
    
    # Очищаем имя от плохих символов (чтобы Windows не ругалась)
    safe_name = "".join([c for c in full_name if c.isalpha() or c.isspace()]).strip()
    
    # 2. Формируем путь к папке клиента
    # Пример: filestorage/Костюковский Леонид Михайлович
    client_folder = os.path.join(BASE_STORAGE_DIR, safe_name)
    
    # Создаем папку, если её нет
    os.makedirs(client_folder, exist_ok=True)
    
    # 3. Формируем новое имя файла
    # Определяем расширение (сохраним как PDF, если исходник был PDF)
    ext = ".pdf" if filename.lower().endswith(".pdf") else ".jpg"
    
    # Заменяем точки в дате на тире (17.07.2003 -> 17-07-2003)
    safe_date = doc_date.replace(".", "-").replace("/", "-")
    
    # Имя: Паспорт_17-07-2003.pdf
    new_filename = f"{doc_type}_{safe_date}{ext}"
    final_path = os.path.join(client_folder, new_filename)
    
    # 4. Сохраняем файл
    with open(final_path, "wb") as f:
        f.write(file_bytes)
        
    return final_path