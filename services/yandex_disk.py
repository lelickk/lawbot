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
        """Создает папку (и все родительские, если нужно)"""
        if not path: return
        
        # Разбиваем путь на части и создаем по очереди
        parts = path.strip("/").split("/")
        current_path = ""
        
        for part in parts:
            current_path += "/" + part
            url = f"{self.base_url}?path={current_path}"
            requests.put(url, headers=self.headers)

    def get_upload_link(self, remote_path):
        """Получает URL для загрузки файла"""
        url = f"{self.base_url}/upload"
        params = {"path": remote_path, "overwrite": "true"}
        
        resp = requests.get(url, headers=self.headers, params=params)
        if resp.status_code == 200:
            return resp.json().get("href")
        else:
            logger.error(f"Get upload link failed: {resp.text}")
            return None

    def publish_resource(self, remote_path):
        """
        Публикует файл и возвращает публичную ссылку.
        Делает это в два этапа:
        1. PUT publish (открыть доступ)
        2. GET meta (получить ссылку)
        """
        publish_url = f"{self.base_url}/publish"
        params = {"path": remote_path}
        
        # 1. Публикуем
        resp = requests.put(publish_url, headers=self.headers, params=params)
        if resp.status_code not in [200, 201]:
            # Если уже опубликован (409) - это ок, идем дальше
            if resp.status_code != 409:
                logger.error(f"Publish failed: {resp.text}")
                return None

        # 2. Запрашиваем метаданные, чтобы найти ссылку
        meta_url = self.base_url
        meta_params = {"path": remote_path, "fields": "public_url"}
        
        # Иногда Яндекс не отдает ссылку мгновенно, делаем простую повторную попытку
        for _ in range(3):
            meta_resp = requests.get(meta_url, headers=self.headers, params=meta_params)
            if meta_resp.status_code == 200:
                link = meta_resp.json().get("public_url")
                if link:
                    return link
            time.sleep(0.5)
            
        logger.warning(f"File {remote_path} published, but public_url not returned.")
        return None

# --- Глобальные функции для использования в других модулях ---

_client = None

def _get_client():
    global _client
    if not _client:
        _client = YandexDiskClient()
    return _client

def upload_file_to_disk(local_path, remote_path):
    """Основная функция загрузки"""
    client = _get_client()
    
    try:
        # 1. Создаем папку (на всякий случай)
        folder = os.path.dirname(remote_path)
        client.create_folder(folder)
        
        # 2. Получаем ссылку
        upload_href = client.get_upload_link(remote_path)
        if not upload_href: return False
        
        # 3. Загружаем
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
    """Публикация файла и возврат ссылки"""
    client = _get_client()
    try:
        return client.publish_resource(remote_path)
    except Exception as e:
        logger.error(f"Publish Error: {e}")
        return None