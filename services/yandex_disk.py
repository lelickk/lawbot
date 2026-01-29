import os
import requests
import logging

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

def get_upload_link(path, token):
    url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    headers = {"Authorization": f"OAuth {token}"}
    params = {"path": path, "overwrite": "true"}
    
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        return response.json().get("href")
    return None

def publish_file(path):
    """Делает файл публичным и возвращает ссылку"""
    token = os.getenv("YANDEX_DISK_TOKEN")
    if not token: return None

    url = "https://cloud-api.yandex.net/v1/disk/resources/publish"
    headers = {"Authorization": f"OAuth {token}"}
    params = {"path": path}
    
    try:
        response = requests.put(url, headers=headers, params=params)
        
        # Если 200 - все ок, ссылка в теле ответа
        if response.status_code == 200:
            link = response.json().get("href") # ВНИМАНИЕ: Тут API может вернуть 'href' или сам объект
            # Обычно для получения публичной ссылки нужно сделать отдельный GET запрос к ресурсу
            # Но метод publish возвращает link в ответе метода.
            # Если нет - делаем перестраховку ниже.
            pass
            
        # Запрашиваем мета-информацию, чтобы точно получить public_url
        meta_url = "https://cloud-api.yandex.net/v1/disk/resources"
        meta_response = requests.get(meta_url, headers=headers, params={"path": path})
        if meta_response.status_code == 200:
            return meta_response.json().get("public_url")
            
        return None

    except Exception as e:
        logger.error(f"Error publishing file: {e}")
        return None

def upload_file_to_disk(local_path, remote_path):
    token = os.getenv("YANDEX_DISK_TOKEN")
    if not token: return False

    try:
        ensure_folder_structure(remote_path, token)
        
        upload_link = get_upload_link(remote_path, token)
        if not upload_link: return False

        with open(local_path, "rb") as f:
            requests.put(upload_link, files={"file": f})
            
        logger.info(f"File uploaded: {remote_path}")
        return True
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False