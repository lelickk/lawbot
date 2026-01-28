import os
import io
import yadisk
from dotenv import load_dotenv

load_dotenv()

# Подключаемся к Яндексу
y = yadisk.YaDisk(token=os.getenv("YANDEX_TOKEN"))

# Папка в корне Диска, куда будем всё складывать
ROOT_FOLDER = "/LAW_BOT_DOCS"

def init_yandex():
    """Проверяет токен и создает корневую папку"""
    try:
        if y.check_token():
            if not y.exists(ROOT_FOLDER):
                y.mkdir(ROOT_FOLDER)
            print("--- Яндекс.Диск подключен успешно ---")
            return True
        else:
            print("--- ОШИБКА: Токен Яндекс.Диска неверный ---")
            return False
    except Exception as e:
        print(f"Ошибка подключения к Яндексу: {e}")
        return False

def upload_to_yandex(file_bytes, filename, client_name):
    """
    1. Создает папку клиента.
    2. Загружает файл.
    3. Делает файл публичным и возвращает ссылку.
    """
    try:
        # 1. Готовим пути
        safe_client_name = "".join([c for c in client_name if c.isalnum() or c.isspace()]).strip()
        client_folder = f"{ROOT_FOLDER}/{safe_client_name}"
        
        # Создаем папку клиента, если нет
        if not y.exists(client_folder):
            y.mkdir(client_folder)
            
        # Полный путь к файлу в облаке
        remote_path = f"{client_folder}/{filename}"
        
        # Если файл с таким именем есть - добавим цифру (чтобы не перезатереть)
        if y.exists(remote_path):
            import time
            timestamp = int(time.time())
            remote_path = f"{client_folder}/{timestamp}_{filename}"

        # 2. Загружаем (из памяти, не создавая временный файл на диске)
        y.upload(io.BytesIO(file_bytes), remote_path)
        
        # 3. Публикуем (создаем красивую ссылку)
        # Если нужна приватность - закомментируй этот блок, но тогда бот не пришлет ссылку
        y.publish(remote_path)
        meta = y.get_meta(remote_path)
        public_link = meta.public_url
        
        print(f"Загружено на Яндекс: {public_link}")
        return public_link

    except Exception as e:
        print(f"Ошибка Яндекс.Диска: {e}")
        return None

# Запускаем проверку при старте файла (для теста)
if __name__ == "__main__":
    init_yandex()