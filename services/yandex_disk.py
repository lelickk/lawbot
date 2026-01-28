import os
import requests
import logging

# Настройка логгера
logger = logging.getLogger(__name__)

YANDEX_DISK_TOKEN = os.getenv("YANDEX_DISK_TOKEN")

def get_upload_link(path):
    """Получает ссылку для загрузки файла"""
    url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    headers = {"Authorization": f"OAuth {YANDEX_DISK_TOKEN}"}
    params = {"path": path, "overwrite": "true"}
    
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        return response.json().get("href")
    else:
        logger.error(f"Error getting upload link: {response.text}")
        return None

def upload_file_to_disk(local_path, remote_path):
    """Основная функция загрузки"""
    if not YANDEX_DISK_TOKEN:
        logger.error("Yandex Disk Token is missing!")
        return False

    try:
        # 1. Получаем ссылку для загрузки
        # (Для MVP мы пока не создаем папки рекурсивно, надеемся что Яндекс умный или папка есть.
        # Если будет ошибка 'DiskNotFoundError', добавим функцию создания папок).
        upload_link = get_upload_link(remote_path)
        
        if not upload_link:
            logger.error(f"Failed to get upload link for {remote_path}")
            return False

        # 2. Загружаем файл
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