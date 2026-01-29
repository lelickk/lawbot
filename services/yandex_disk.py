import os
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def create_folder(path, token):
    url = "https://cloud-api.yandex.net/v1/disk/resources"
    headers = {"Authorization": f"OAuth {token}"}
    requests.put(url, headers=headers, params={"path": path})

def ensure_folder_structure(full_path, token):
    folder_path = os.path.dirname(full_path)
    if not folder_path or folder_path == "/": return True
    parts = folder_path.strip("/").split("/")
    current_path = ""
    for part in parts:
        current_path += "/" + part
        create_folder(current_path, token)
    return True

def check_file_exists(path, token):
    """Проверяет, существует ли файл"""
    url = "https://cloud-api.yandex.net/v1/disk/resources"
    headers = {"Authorization": f"OAuth {token}"}
    params = {"path": path}
    response = requests.get(url, headers=headers, params=params)
    return response.status_code == 200

def get_unique_path(path, token):
    """
    Если файл 'Doc.jpg' существует, превращает путь в 'Doc_1.jpg', 'Doc_2.jpg' и т.д.
    """
    if not check_file_exists(path, token):
        return path
    
    # Файл существует, начинаем подбирать имя
    folder = os.path.dirname(path)
    filename = os.path.basename(path)
    name, ext = os.path.splitext(filename)
    
    counter = 1
    while True:
        # Добавляем дату и счетчик, чтобы точно не совпало
        # Или просто счетчик
        date_str = datetime.now().strftime("%Y%m%d")
        new_filename = f"{name}_{date_str}_{counter}{ext}"
        new_path = f"{folder}/{new_filename}"
        
        if not check_file_exists(new_path, token):
            return new_path
        counter += 1
        if counter > 10: # Защита от бесконечного цикла
            return f"{folder}/{name}_{datetime.now().timestamp()}{ext}"

def get_upload_link(path, token):
    url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    headers = {"Authorization": f"OAuth {token}"}
    params = {"path": path, "overwrite": "false"} # Важно: false, мы сами контролируем имена
    
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        return response.json().get("href")
    return None

def upload_file_to_disk(local_path, remote_path):
    token = os.getenv("YANDEX_DISK_TOKEN")
    if not token: return False

    try:
        ensure_folder_structure(remote_path, token)
        
        # Генерируем уникальное имя, чтобы не затереть старое
        final_path = get_unique_path(remote_path, token)

        upload_link = get_upload_link(final_path, token)
        if not upload_link: return False

        with open(local_path, "rb") as f:
            requests.put(upload_link, files={"file": f})
            
        logger.info(f"File uploaded: {final_path}")
        return True
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False