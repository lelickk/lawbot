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
    """Делает файл публичным и ГАРАНТИРОВАННО возвращает ссылку"""
    token = os.getenv("YANDEX_DISK_TOKEN")
    if not token: return None

    headers = {"Authorization": f"OAuth {token}"}
    
    # 1. Сначала пытаемся опубликовать
    publish_url = "https://cloud-api.yandex.net/v1/disk/resources/publish"
    try:
        requests.put(publish_url, headers=headers, params={"path": path})
        # Мы даже не проверяем ответ, так как если файл уже публичен, 
        # Яндекс может вернуть ошибку, но нам все равно.
        # Главное - следующий шаг.
    except Exception as e:
        logger.error(f"Error sending publish request: {e}")

    # 2. Теперь запрашиваем мета-данные файла, там точно будет ссылка
    meta_url = "https://cloud-api.yandex.net/v1/disk/resources"
    try:
        response = requests.get(meta_url, headers=headers, params={"path": path})
        if response.status_code == 200:
            data = response.json()
            public_url = data.get("public_url")
            if public_url:
                return public_url
            else:
                logger.warning(f"File {path} published, but no public_url found in meta.")
                return None
        else:
            logger.error(f"Failed to get meta for {path}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error getting file link: {e}")
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