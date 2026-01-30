import sys
import os
import logging
# Добавляем корневую папку в путь, чтобы видеть services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.doc_processor import DocumentProcessor
from dotenv import load_dotenv

# Настройка простого вывода в консоль
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

def run_test(file_path):
    print(f"🚀 ЗАПУСК ТЕСТА ДЛЯ ФАЙЛА: {file_path}")
    
    if not os.path.exists(file_path):
        print(f"❌ Файл не найден: {file_path}")
        return

    processor = DocumentProcessor()
    
    # Используем тестовый номер телефона, чтобы не мусорить в папках клиентов
    test_phone = "TEST_BOT_USER"
    
    # Эмулируем работу
    print("⏳ Обработка...")
    try:
        # Мы передаем file_path как local_path. 
        # Важно: process_and_upload удаляет файл в конце, поэтому для теста
        # лучше скопировать его во временное имя, если хочешь сохранить оригинал.
        # Но для простоты передадим как есть.
        
        result = processor.process_and_upload(test_phone, file_path, os.path.basename(file_path))
        
        print("\n" + "="*30)
        print("📊 РЕЗУЛЬТАТ:")
        print("="*30)
        
        if result["status"] == "success":
            print(f"✅ Статус:      УСПЕХ")
            print(f"📄 Тип:         {result['doc_type']}")
            print(f"👤 Имя:         {result['person']}")
            print(f"📁 Файл:        {result['filename']}")
            print(f"🔗 Путь (Disk): {result.get('remote_path')}")
            print("-" * 30)
            print("Теперь проверь папку '/Clients/TEST_BOT_USER' на Яндекс.Диске")
        else:
            print(f"❌ Ошибка: {result.get('message')}")
            
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")

if __name__ == "__main__":
    # Если передали аргумент, берем его, иначе ищем дефолтный файл
    if len(sys.argv) > 1:
        target_file = sys.argv[1]
    else:
        # Можно положить файл test.jpg в папку temp_files для проверки
        target_file = "temp_files/test.jpg"
        
    run_test(target_file)