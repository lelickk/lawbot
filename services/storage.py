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
    """
    if PROVIDER == "dropbox":
        return _upload_to_dropbox(local_path, remote_path)
    else:
        return _upload_to_yandex(local_path, remote_path)

def publish_file(remote_path):
    """
    Создает публичную ссылку на файл.
    """
    if PROVIDER == "dropbox":
        return _publish_dropbox(remote_path)
    else:
        return _publish_yandex(remote_path)

# --- YANDEX LOGIC ---
def _upload_to_yandex(local_path, remote_path):
    y = _get_yandex_client()
    if not y: return False
    try:
        folder_path = os.path.dirname(remote_path)
        parts = folder_path.strip("/").split("/")
        current_path = ""
        for part in parts:
            current_path += f"/{part}"
            if not y.exists(current_path):
                try: y.mkdir(current_path)
                except: pass

        if y.exists(remote_path):
            y.remove(remote_path)

        y.upload(local_path, remote_path)
        logger.info(f"✅ Uploaded to Yandex: {remote_path}")
        return True
    except Exception as e:
        logger.error(f"Yandex Upload Error: {e}")
        return False

def _publish_yandex(remote_path):
    y = _get_yandex_client()
    if not y: return None
    try:
        if not y.exists(remote_path): return None
        # Проверяем, может уже опубликовано
        meta = y.get_meta(remote_path)
        if meta.public_url:
            return meta.public_url
        
        y.publish(remote_path)
        meta = y.get_meta(remote_path)
        return meta.public_url
    except Exception as e:
        logger.error(f"Yandex Publish Error: {e}")
        return None

# --- DROPBOX LOGIC ---
def _upload_to_dropbox(local_path, remote_path):
    dbx = _get_dropbox_client()
    if not dbx: return False
    try:
        if not remote_path.startswith('/'): remote_path = '/' + remote_path
        with open(local_path, 'rb') as f:
            dbx.files_upload(f.read(), remote_path, mode=WriteMode('overwrite'))
        logger.info(f"✅ Uploaded to Dropbox: {remote_path}")
        return True
    except Exception as e:
        logger.error(f"Dropbox Upload Error: {e}")
        return False

def _publish_dropbox(remote_path):
    dbx = _get_dropbox_client()
    if not dbx: return None
    try:
        if not remote_path.startswith('/'): remote_path = '/' + remote_path
        # Создаем публичную ссылку
        link_metadata = dbx.sharing_create_shared_link_with_settings(remote_path)
        return link_metadata.url
    except ApiError as e:
        # Если ссылка уже существует
        if e.error.is_shared_link_already_exists():
            links = dbx.sharing_list_shared_links(path=remote_path, direct_only=True).links
            if links: return links[0].url
        logger.error(f"Dropbox Publish Error: {e}")
        return None
    except Exception as e:
        logger.error(f"Dropbox Generic Error: {e}")
        return None