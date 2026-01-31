import os
import logging
import yadisk
import dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError

logger = logging.getLogger(__name__)

# Читаем конфиг
PROVIDER = os.getenv("STORAGE_PROVIDER", "yandex").lower()
YANDEX_TOKEN = os.getenv("YANDEX_DISK_TOKEN")
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")

def _get_yandex_client():
    if not YANDEX_TOKEN:
        logger.error("❌ Yandex Token is missing!")
        return None
    return yadisk.YaDisk(token=YANDEX_TOKEN)

def _get_dropbox_client():
    if not DROPBOX_TOKEN:
        logger.error("❌ Dropbox Token is missing!")
        return None
    return dropbox.Dropbox(DROPBOX_TOKEN)

def upload_file_to_cloud(local_path, remote_path):
    """
    Универсальная функция загрузки.
    remote_path должен быть полным: /Clients/+7999/Name/file.pdf
    """
    if PROVIDER == "dropbox":
        return _upload_to_dropbox(local_path, remote_path)
    else:
        return _upload_to_yandex(local_path, remote_path)

# --- YANDEX LOGIC ---
def _upload_to_yandex(local_path, remote_path):
    y = _get_yandex_client()
    if not y: return False

    try:
        # Создаем папки рекурсивно (если их нет)
        folder_path = os.path.dirname(remote_path)
        # Яндекс требует создавать каждую папку по очереди, но yadisk умеет это делать? 
        # Проще пройтись циклом, но yadisk.mkdir не рекурсивен.
        # Упрощенная логика создания папок:
        parts = folder_path.strip("/").split("/")
        current_path = ""
        for part in parts:
            current_path += f"/{part}"
            if not y.exists(current_path):
                try: y.mkdir(current_path)
                except: pass

        if y.exists(remote_path):
            logger.info(f"File {remote_path} exists, overwriting...")
            y.remove(remote_path)

        y.upload(local_path, remote_path)
        logger.info(f"✅ Uploaded to Yandex: {remote_path}")
        return True
    except Exception as e:
        logger.error(f"Yandex Upload Error: {e}")
        return False

# --- DROPBOX LOGIC ---
def _upload_to_dropbox(local_path, remote_path):
    dbx = _get_dropbox_client()
    if not dbx: return False

    try:
        # Dropbox требует слэш в начале
        if not remote_path.startswith('/'):
            remote_path = '/' + remote_path

        with open(local_path, 'rb') as f:
            # WriteMode.overwrite перезапишет файл, если он есть
            dbx.files_upload(
                f.read(), 
                remote_path, 
                mode=WriteMode('overwrite')
            )
        
        logger.info(f"✅ Uploaded to Dropbox: {remote_path}")
        return True
    except ApiError as e:
        logger.error(f"Dropbox API Error: {e}")
        return False
    except Exception as e:
        logger.error(f"Dropbox Generic Error: {e}")
        return False