import os
import requests
import logging
import time

logger = logging.getLogger(__name__)

class YandexDiskClient:
    def __init__(self):
        self.token = os.getenv("YANDEX_DISK_TOKEN")
        self.headers = {
            "Authorization": f"OAuth {self.token}",
            "Accept": "application/json"
        }
        self.base_url = "https://cloud-api.yandex.net/v1/disk/resources"

    def create_folder(self, path):
        """Создает папку рекурсивно, корректно обрабатывая спецсимволы (+)"""
        if not path: return
        
        parts = path.strip("/").split("/")
        current_path = ""
        
        for part in parts:
            current_path += "/" + part
            # ВАЖНО: Передаем path через params, чтобы requests закодировал '+' как '%2B'
            params = {"path": current_path}
            resp = requests.put(self.base_url, headers=self.headers, params=params)
            
            # 201 = Created, 409 = Already exists (это нормально)
            if resp.status_code not in [201, 409]:
                logger.error(f"Failed to create folder {current_path}: {resp.status_code} {resp.text}")

    def get_upload_link(self, remote_path):
        url = f"{self.base_url}/upload"
        params = {"path": remote_path, "overwrite": "true"}
        
        resp = requests.get(url, headers=self.headers, params=params)
        if resp.status_code == 200:
            return resp.json().get("href")
        else:
            logger.error(f"Get upload link failed for {remote_path}: {resp.text}")
            return None

    def publish_resource(self, remote_path):
        publish_url = f"{self.base_url}/publish"
        params = {"path": remote_path}
        
        requests.put(publish_url, headers=self.headers, params=params)

        meta_url = self.base_url
        meta_params = {"path": remote_path, "fields": "public_url"}
        
        for _ in range(5): # Пробуем 5 раз с задержкой
            meta_resp = requests.get(meta_url, headers=self.headers, params=meta_params)
            if meta_resp.status_code == 200:
                link = meta_resp.json().get("public_url")
                if link: return link
            time.sleep(1.0) # Ждем секунду
            
        logger.warning(f"File {remote_path} published, but public_url missing.")
        return None

_client = None

def _get_client():
    global _client
    if not _client:
        _client = YandexDiskClient()
    return _client

def upload_file_to_disk(local_path, remote_path):
    client = _get_client()
    try:
        # 1. Создаем папку
        folder = os.path.dirname(remote_path)
        client.create_folder(folder)
        
        # 2. Получаем ссылку
        upload_href = client.get_upload_link(remote_path)
        if not upload_href: return False
        
        # 3. Грузим
        with open(local_path, "rb") as f:
            files = {"file": f}
            resp = requests.put(upload_href, files=files)
            
        if resp.status_code in [201, 200]:
            logger.info(f"File uploaded: {remote_path}")
            return True
        else:
            logger.error(f"Upload failed: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Yandex Disk Error: {e}")
        return False

def publish_file(remote_path):
    client = _get_client()
    try:
        return client.publish_resource(remote_path)
    except Exception as e:
        logger.error(f"Publish Error: {e}")
        return None