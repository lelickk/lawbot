import os
import shutil
from datetime import datetime
import base64
import cv2
import numpy as np
from thefuzz import process  # Для исправления опечаток в именах
from services.yandex_disk import upload_file_to_disk
from services.openai_client import analyze_document

# Локальная папка для временного хранения (внутри Docker)
TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

class DocumentProcessor:
    def __init__(self):
        pass

    def _enhance_image(self, file_path):
        """
        Улучшает качество изображения для OCR:
        - Переводит в Ч/Б
        - Убирает шум
        - Делает 'бинаризацию' (как скан)
        """
        try:
            # Читаем изображение
            img = cv2.imread(file_path)
            if img is None:
                return file_path # Если не картинка (pdf), возвращаем как есть

            # 1. Перевод в оттенки серого
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # 2. Убираем шум (Gaussian Blur)
            blur = cv2.GaussianBlur(gray, (5, 5), 0)

            # 3. Адаптивная бинаризация (делает текст черным, фон белым, убирает тени)
            processed = cv2.adaptiveThreshold(
                blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
            )

            # Сохраняем улучшенную версию во временный файл
            enhanced_path = file_path.replace(".", "_enhanced.")
            cv2.imwrite(enhanced_path, processed)
            return enhanced_path
        except Exception as e:
            print(f"Error enhancing image: {e}")
            return file_path # Если ошибка, возвращаем оригинал

    def _find_existing_folder(self, base_path, target_name):
        """
        Ищет папку с похожим названием (защита от опечаток Иванов/Йванов).
        Использует расстояние Левенштейна.
        """
        # Получаем список папок, которые уже есть в облаке (или локально, если бы мы кэшировали)
        # В MVP мы пока не можем сканировать всё облако быстро, 
        # поэтому будем проверять точное совпадение или полагаться на структуру.
        # В данной версии мы пока просто возвращаем target_name, 
        # но сюда можно подключить кэш имен клиентов из БД.
        return target_name

    def process_and_upload(self, user_phone, file_path, original_filename):
        try:
            # 1. Формируем путь для оригиналов
            # Структура: /Clients/PHONE/_Originals_/DATE_FILENAME
            date_str = datetime.now().strftime("%Y-%m-%d")
            remote_original_path = f"/Clients/{user_phone}/_Originals_/{date_str}_{original_filename}"
            
            # Загружаем ОРИГИНАЛ сразу (Backup)
            upload_file_to_disk(file_path, remote_original_path)

            # 2. Улучшаем качество фото перед отправкой в ИИ
            enhanced_file_path = self._enhance_image(file_path)

            # 3. Спрашиваем ИИ (что это и чье это?)
            # Кодируем улучшенное изображение
            with open(enhanced_file_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')

            # --- ОБНОВЛЕННЫЙ ПРОМПТ ДЛЯ ИЗРАИЛЯ ---
            prompt = """
            Ты - профессиональный юридический помощник в Израиле.
            Твоя задача:
            1. Определить тип документа (на Иврите, Английском или Русском).
               Примеры: Teudat Zehut, Tlush Maskoret, Bank Statement (Osh), Rental Agreement.
            2. Найти ФИО человека, которому принадлежит документ.
               - Если имя на Иврите/Английском, транслитерируй его на Русский (или оставь на Английском, как удобнее заказчику).
               - Исправь явные опечатки (OCR errors). Пример: "Йванов" -> "Иванов".
            3. Вернуть ответ строго в формате JSON:
               {"doc_type": "Тип документа", "person_name": "Имя Фамилия"}
            
            Если имя не найдено, используй "Неизвестный".
            Если тип не понятен, используй "Прочее".
            """
            
            ai_result = analyze_document(base64_image, prompt)
            
            # Удаляем временный улучшенный файл
            if enhanced_file_path != file_path:
                os.remove(enhanced_file_path)

            # Парсим ответ ИИ
            doc_type = ai_result.get("doc_type", "Документ")
            person_name = ai_result.get("person_name", "Клиент").replace("/", "-") # Убираем слэши из имени

            # 4. Логика защиты от дублей папок (TheFuzz)
            # В идеале здесь мы должны знать список существующих папок этого клиента.
            # Для MVP сделаем простую нормализацию: Первая буква Заглавная, остальные строчные.
            person_name = person_name.title() 

            # 5. Формируем финальное имя файла
            # Пример: 2024-01-28_Teudat-Zehut.jpg
            file_ext = os.path.splitext(original_filename)[1]
            new_filename = f"{date_str}_{doc_type}{file_ext}"

            # 6. Финальный путь загрузки
            # Структура: /Clients/PHONE/Имя Фамилия/Документ
            remote_final_path = f"/Clients/{user_phone}/{person_name}/{new_filename}"

            # Загружаем обработанный файл (или копируем оригинал, если качество устраивает)
            upload_file_to_disk(file_path, remote_final_path)

            # Удаляем локальный файл
            os.remove(file_path)

            return {
                "status": "success",
                "original_path": remote_original_path,
                "final_path": remote_final_path,
                "doc_type": doc_type,
                "person": person_name
            }

        except Exception as e:
            print(f"Error processing doc: {e}")
            return {"status": "error", "message": str(e)}