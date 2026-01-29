import os
import requests
import logging

logger = logging.getLogger(__name__)

# УБИРАЕМ YANDEX_DISK_TOKEN = ... из глобальной области

def get_upload_link(path, token):
    """Получает ссылку для загрузки файла"""
    url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    headers = {"Authorization": f"OAuth {token}"}
    params = {"path": path, "overwrite": "true"}
    
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        return response.json().get("href")
    else:
        logger.error(f"Error getting upload link: {response.text}")
        return None

def upload_file_to_disk(local_path, remote_path):
    """Основная функция загрузки"""
    # Читаем токен прямо сейчас
    token = os.getenv("YANDEX_DISK_TOKEN")
    
    if not token:
        logger.error("Yandex Disk Token is missing! Check .env file.")
        return False

    try:
        upload_link = get_upload_link(remote_path, token) # Передаем токен
        
        if not upload_link:
            logger.error(f"Failed to get upload link for {remote_path}")
            return False

        with open(local_path, "rb") as f:
            response = requests.put(upload_link, files={"file": f})
            
        if response.status_code == 201:
            logger.info(f"File uploaded successfully to {remote_path}")
            return True
        else:
            logger.error(f"Upload failed with status: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Exception during upload: {e}")
        return False