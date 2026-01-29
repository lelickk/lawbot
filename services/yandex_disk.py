import os
import requests
import logging

logger = logging.getLogger(__name__)

def create_folder(path, token):
    """Создает одну папку. Если она есть (409) - не ругается."""
    url = "https://cloud-api.yandex.net/v1/disk/resources"
    headers = {"Authorization": f"OAuth {token}"}
    params = {"path": path}
    
    response = requests.put(url, headers=headers, params=params)
    
    if response.status_code == 201:
        logger.info(f"Created folder: {path}")
        return True
    elif response.status_code == 409:
        # Папка уже есть - это нормально
        return True
    else:
        logger.error(f"Error creating folder {path}: {response.text}")
        return False

def ensure_folder_structure(full_path, token):
    """
    Рекурсивно создает структуру папок.
    Принимает полный путь к файлу (например /Clients/Phone/Doc.jpg)
    И создает /Clients -> /Clients/Phone
    """
    # Отсекаем имя файла, оставляем только путь к папке
    folder_path = os.path.dirname(full_path)
    
    if not folder_path or folder_path == "/":
        return True

    # Разбиваем путь на кусочки (['Clients', '+972...', '_Originals_'])
    parts = folder_path.strip("/").split("/")
    
    current_path = ""
    for part in parts:
        current_path += "/" + part
        # Создаем текущий уровень
        if not create_folder(current_path, token):
            return False
            
    return True

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
    token = os.getenv("YANDEX_DISK_TOKEN")
    
    if not token:
        logger.error("Yandex Disk Token is missing! Check .env file.")
        return False

    try:
        # 1. ГАРАНТИРУЕМ, ЧТО ПАПКИ СУЩЕСТВУЮТ
        if not ensure_folder_structure(remote_path, token):
            logger.error("Failed to create folder structure")
            return False

        # 2. Получаем ссылку
        upload_link = get_upload_link(remote_path, token)
        
        if not upload_link:
            logger.error(f"Failed to get upload link for {remote_path}")
            return False

        # 3. Загружаем файл
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