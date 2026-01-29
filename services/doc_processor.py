import os
import cv2
import shutil
import logging
import base64
from pdf2image import convert_from_path
from services.yandex_disk import upload_file_to_disk
from services.openai_client import analyze_document

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
            # 1. Подготовка файла для ИИ (PDF -> JPG)
            ai_input_path = local_path
            if local_path.lower().endswith(".pdf"):
                converted_jpg = self._convert_pdf_to_jpg(local_path)
                if converted_jpg:
                    ai_input_path = converted_jpg
                else:
                    logger.warning("Could not convert PDF, skipping AI analysis")
                    ai_input_path = None

            # 2. Спрашиваем ИИ (только если есть картинка)
            doc_data = {"doc_type": "Нераспознанный", "person_name": "Неизвестный"}
            
            if ai_input_path:
                try:
                    # Можно попробовать улучшить картинку перед отправкой
                    # enhanced_path = self._enhance_image(ai_input_path) 
                    # Но для PDF конвертации часто достаточно
                    
                    base64_img = self._encode_image(ai_input_path)
                    
                    prompt = """
                    Проанализируй документ. 
                    1. Определи тип документа (Теудат Зеут, Водительские права, Справка, Счет, Неизвестно).
                    2. Найди имя и фамилию человека (на иврите или английском). Если нет - напиши 'Неизвестный'.
                    Верни JSON: {"doc_type": "...", "person_name": "..."}
                    """
                    
                    ai_result = analyze_document(base64_img, prompt)
                    if ai_result:
                        doc_data = ai_result
                        
                except Exception as e:
                    logger.error(f"AI Analysis failed: {e}")

            # 3. Формируем красивое имя
            # Очищаем имя от лишних символов для файла
            safe_name = "".join(c for c in doc_data['person_name'] if c.isalnum() or c in (' ', '_', '-')).strip()
            if not safe_name: safe_name = "Client"
            
            doc_type = doc_data.get('doc_type', 'Doc')
            
            # Имя файла: 2026-01-29_TeudatZehut.jpg (дата добавляется в yandex_disk.py или тут)
            # В MVP мы делали это при загрузке.
            
            # Структура папок: /Clients/PHONE/NAME/
            remote_folder = f"/Clients/{user_phone}/{safe_name}"
            
            # Имя файла для сохранения
            ext = os.path.splitext(original_filename)[1]
            final_filename = f"{doc_type}{ext}"
            
            remote_path = f"{remote_folder}/{final_filename}"
            original_path = f"/Clients/{user_phone}/_Originals_/{original_filename}"

            # 4. Загружаем (сначала оригинал, потом красиво названный)
            # Загружаем оригинал
            upload_file_to_disk(local_path, original_path)
            
            # Загружаем переименованный (основной)
            success = upload_file_to_disk(local_path, remote_path)

            # Чистим мусор
            if os.path.exists(local_path): os.remove(local_path)
            if ai_input_path and ai_input_path != local_path: os.remove(ai_input_path)

            if success:
                return {
                    "status": "success", 
                    "doc_type": doc_data['doc_type'], 
                    "person": doc_data['person_name']
                }
            else:
                return {"status": "error", "message": "Ошибка загрузки на Диск"}

        except Exception as e:
            logger.error(f"Critical error in processor: {e}")
            return {"status": "error", "message": str(e)}