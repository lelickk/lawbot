import os
import logging
from services.yandex_disk import upload_file_to_disk as yandex_upload, publish_file as yandex_publish
import dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError

logger = logging.getLogger(__name__)

# Читаем настройки
STORAGE_PROVIDER = os.getenv("STORAGE_PROVIDER", "yandex") # 'yandex' или 'dropbox'
DROPBOX_TOKEN = os.getenv("DROPBOX_TOKEN")

def _upload_to_dropbox(local_path, remote_path):
    """Загрузка в Dropbox"""
    try:
        if not DROPBOX_TOKEN:
            logger.error("Dropbox Token is missing!")
            return False, None

        dbx = dropbox.Dropbox(DROPBOX_TOKEN)
        
        # Dropbox требует слэш в начале пути
        if not remote_path.startswith('/'):
            remote_path = '/' + remote_path
            
        with open(local_path, "rb") as f:
            # mode=WriteMode('overwrite') перезапишет файл, если такой есть
            dbx.files_upload(f.read(), remote_path, mode=WriteMode('overwrite'))
        
        logger.info(f"Uploaded to Dropbox: {remote_path}")

        # Создаем публичную ссылку (Shared Link)
        try:
            shared_link_metadata = dbx.sharing_create_shared_link_with_settings(remote_path)
            return True, shared_link_metadata.url
        except ApiError as e:
            # Если ссылка уже существует, просто получаем её
            if e.error.is_shared_link_already_exists():
                links = dbx.sharing_get_shared_links(path=remote_path)
                if links.links:
                    return True, links.links[0].url
            logger.error(f"Dropbox link error: {e}")
            return True, "No Link"

    except Exception as e:
        logger.error(f"Dropbox upload error: {e}")
        return False, None

def upload_file(local_path, remote_path):
    """Универсальная точка входа"""
    if STORAGE_PROVIDER == "dropbox":
        return _upload_to_dropbox(local_path, remote_path)
    else:
        # Yandex Disk Logic
        success = yandex_upload(local_path, remote_path)
        link = None
        if success:
            link = yandex_publish(remote_path)
        return success, link