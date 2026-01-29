import os
import cv2
import logging
import base64
import img2pdf
from datetime import datetime
from PIL import Image, ImageOps
from pdf2image import convert_from_path
from services.yandex_disk import upload_file_to_disk
from services.openai_client import analyze_document

logger = logging.getLogger(__name__)

class DocumentProcessor:
    def __init__(self):
        self.temp_dir = "temp_files"
        os.makedirs(self.temp_dir, exist_ok=True)

    def _convert_pdf_to_jpg(self, pdf_path):
        try:
            images = convert_from_path(pdf_path)
            if not images: return None
            jpg_path = pdf_path.replace(".pdf", "_temp_analysis.jpg")
            images[0].save(jpg_path, "JPEG")
            return jpg_path
        except Exception as e:
            logger.error(f"Error converting PDF to JPG: {e}")
            return None

    def _rotate_image_by_angle(self, image_path, angle):
        """Физически поворачивает картинку"""
        if angle == 0: return
        try:
            img = Image.open(image_path)
            
            # УБРАЛИ МИНУС: Теперь крутим ПРОТИВ часовой стрелки (стандарт PIL)
            img = img.rotate(angle, expand=True)
            
            img.save(image_path)
            logger.info(f"Image rotated by {angle} degrees (Counter-Clockwise)")
        except Exception as e:
            logger.error(f"Rotation failed: {e}")

    def _convert_jpg_to_pdf(self, jpg_path):
        try:
            # 1. Сначала применяем EXIF (если есть)
            image = Image.open(jpg_path)
            image = ImageOps.exif_transpose(image)
            image.save(jpg_path)

            # 2. Конвертируем в PDF
            pdf_path = os.path.splitext(jpg_path)[0] + ".pdf"
            with open(pdf_path, "wb") as f:
                f.write(img2pdf.convert(jpg_path))
            return pdf_path
        except Exception as e:
            logger.error(f"Error converting JPG to PDF: {e}")
            return None

    def _encode_image(self, image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def process_and_upload(self, user_phone, local_path, original_filename):
        ai_input_path = None
        final_upload_path = None
        is_pdf_input = local_path.lower().endswith(".pdf")

        try:
            if is_pdf_input:
                ai_input_path = self._convert_pdf_to_jpg(local_path)
                final_upload_path = local_path 
            else:
                ai_input_path = local_path
                # PDF пока не делаем, ждем вердикта ИИ по углу

            if not ai_input_path: raise Exception("Failed to prepare image")

            # 2. АНАЛИЗ ИИ
            doc_data = {"doc_type": "Document", "person_name": "Unknown", "rotation": 0}
            try:
                base64_img = self._encode_image(ai_input_path)
                
                # ИЗМЕНИЛИ ПРОМПТ: Просим угол ПРОТИВ часовой стрелки
                prompt = """
                Проанализируй документ.
                1. Тип документа (Теудат_Зеут, Водительские_Права, Чек, Справка, Тлуш_Маскорет, Паспорт, Загранпаспорт, Справка_об_отсутствии_судимости, Другое).
                2. Имя и Фамилию (латиница).
                3. Если фото перевернуто, укажи угол поворота ПРОТИВ часовой стрелки (Counter-Clockwise), чтобы текст стал горизонтальным.
                   Варианты: 0, 90, 180, 270.
                
                Верни JSON: {"doc_type": "...", "person_name": "...", "rotation": 0}
                """
                
                ai_result = analyze_document(base64_img, prompt)
                if ai_result: doc_data = ai_result
            except Exception as e:
                logger.error(f"AI Analysis failed: {e}")

            # 3. ПОВОРОТ
            rotation_needed = doc_data.get("rotation", 0)
            
            if not is_pdf_input:
                # Поворачиваем картинку, если нужно
                if rotation_needed in [90, 180, 270]:
                    self._rotate_image_by_angle(local_path, rotation_needed)
                
                # Теперь делаем PDF из уже ровной картинки
                final_upload_path = self._convert_jpg_to_pdf(local_path)
                if not final_upload_path: final_upload_path = local_path

            # 4. ПУТИ
            person_name = doc_data.get('person_name', 'Client').strip()
            safe_person_name = "".join(c for c in person_name if c.isalnum() or c in (' ', '_', '-')).strip()
            if not safe_person_name: safe_person_name = "Client"
            
            doc_type = doc_data.get('doc_type', 'Doc').replace(" ", "_")
            date_str = datetime.now().strftime("%Y-%m-%d")
            
            remote_folder = f"/Clients/{user_phone}/{safe_person_name}"
            final_filename = f"{date_str}_{doc_type}.pdf"
            remote_path = f"{remote_folder}/{final_filename}"
            original_remote_path = f"/Clients/{user_phone}/_Originals_/{date_str}_{original_filename}"

            # 5. ЗАГРУЗКА
            upload_file_to_disk(local_path, original_remote_path)
            success = upload_file_to_disk(final_upload_path, remote_path)

            to_remove = [local_path, ai_input_path, final_upload_path]
            for path in set(to_remove):
                if path and os.path.exists(path) and "temp_files" in path: os.remove(path)

            if success:
                return {
                    "status": "success", "doc_type": doc_type, 
                    "person": person_name, "filename": final_filename,
                    "remote_path": remote_path
                }
            else:
                return {"status": "error", "message": "Ошибка загрузки"}

        except Exception as e:
            logger.error(f"Critical error: {e}")
            return {"status": "error", "message": str(e)}