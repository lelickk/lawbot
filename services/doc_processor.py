import os
import cv2
import shutil
import logging
import base64
from pdf2image import convert_from_path
from services.yandex_disk import upload_file_to_disk
from services.openai_client import analyze_document
from datetime import datetime

logger = logging.getLogger(__name__)

class DocumentProcessor:
    def __init__(self):
        self.temp_dir = "temp_files"
        os.makedirs(self.temp_dir, exist_ok=True)

    def _convert_pdf_to_jpg(self, pdf_path):
        """Превращает первую страницу PDF в JPG для анализа ИИ"""
        try:
            images = convert_from_path(pdf_path)
            if not images:
                return None
            
            # Сохраняем первую страницу как временный jpg
            jpg_path = pdf_path.replace(".pdf", ".jpg")
            images[0].save(jpg_path, "JPEG")
            return jpg_path
        except Exception as e:
            logger.error(f"Error converting PDF: {e}")
            return None

    def _enhance_image(self, file_path):
        """Улучшает фото, но аккуратно. Если не вышло - возвращает оригинал."""
        try:
            # Если это PDF, его нельзя улучшать OpenCV напрямую
            if file_path.endswith(".pdf"):
                return file_path

            img = cv2.imread(file_path)
            if img is None:
                return file_path 

            # Конвертируем в ч/б для улучшения текста, но возвращаем RGB
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            processed = cv2.adaptiveThreshold(
                blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
            )
            # Возвращаем цвет, чтобы OpenAI не ругался
            final_img = cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR)

            enhanced_path = file_path.replace(".", "_enhanced.")
            cv2.imwrite(enhanced_path, final_img)
            return enhanced_path
        except Exception as e:
            logger.warning(f"Enhancement failed, using original: {e}")
            return file_path

    def _encode_image(self, image_path):
        """Кодирует картинку в base64"""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def process_and_upload(self, user_phone, local_path, original_filename):
        try:
            # 1. Подготовка (PDF -> JPG для ИИ)
            ai_input_path = local_path
            if local_path.lower().endswith(".pdf"):
                converted_jpg = self._convert_pdf_to_jpg(local_path)
                if converted_jpg:
                    ai_input_path = converted_jpg
                else:
                    logger.warning("Could not convert PDF, skipping AI analysis")
                    ai_input_path = None

            # 2. Анализ ИИ
            doc_data = {"doc_type": "Document", "person_name": "Unknown"}
            
            if ai_input_path:
                try:
                    base64_img = self._encode_image(ai_input_path)
                    
                    # Промпт уже настроен на JSON
                    prompt = """
                    Проанализируй документ. 
                    1. Тип документа. ВЫБЕРИ СТРОГО ИЗ СПИСКА: 
                       [Теудат_Зеут, Водительские_Права, Чек, Справка, Тлуш_Маскорет, Другое].
                       Если не уверен - пиши 'Другое'.
                    2. Найди Имя и Фамилию (на латинице, транслитерация).
                    Верни JSON: {"doc_type": "...", "person_name": "..."}
                    """
                    ai_result = analyze_document(base64_img, prompt)
                    if ai_result:
                        doc_data = ai_result
                except Exception as e:
                    logger.error(f"AI Analysis failed: {e}")

            # 3. ФОРМИРОВАНИЕ ИМЕНИ (ИСПРАВЛЕНО)
            # Очистка имени человека для папки
            person_name = doc_data.get('person_name', 'Client').strip()
            safe_person_name = "".join(c for c in person_name if c.isalnum() or c in (' ', '_', '-')).strip()
            if not safe_person_name: safe_person_name = "Client"
            
            # Тип документа для файла
            doc_type = doc_data.get('doc_type', 'Doc').replace(" ", "_")
            
            # Дата для файла (YYYY-MM-DD)
            date_str = datetime.now().strftime("%Y-%m-%d")
            
            # Папка: /Clients/+97250.../Ivan_Ivanov/
            remote_folder = f"/Clients/{user_phone}/{safe_person_name}"
            
            # Имя файла: 2026-01-29_Teudat_Zehut.pdf
            ext = os.path.splitext(original_filename)[1] # .pdf или .jpg
            final_filename = f"{date_str}_{doc_type}{ext}"
            
            remote_path = f"{remote_folder}/{final_filename}"
            
            # Оригиналы кладем отдельно, чтобы не потерять исходное имя
            original_path = f"/Clients/{user_phone}/_Originals_/{date_str}_{original_filename}"

            # 4. Загрузка
            # Сначала оригинал (резервная копия)
            upload_file_to_disk(local_path, original_path)
            
            # Потом красивый файл в папку клиента
            success = upload_file_to_disk(local_path, remote_path)

            # Чистка
            if os.path.exists(local_path): os.remove(local_path)
            if ai_input_path and ai_input_path != local_path: os.remove(ai_input_path)

            if success:
                return {
                    "status": "success", 
                    "doc_type": doc_type, 
                    "person": person_name,
                    "filename": final_filename
                }
            else:
                return {"status": "error", "message": "Ошибка загрузки на Диск"}

        except Exception as e:
            logger.error(f"Critical error: {e}")
            return {"status": "error", "message": str(e)}