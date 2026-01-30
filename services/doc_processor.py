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

    def _fix_exif_orientation(self, image_path):
        """
        Убирает скрытые теги поворота (EXIF), которые ставят телефоны.
        Делаем это ДО того, как отдавать ИИ, чтобы мы и ИИ видели одно и то же.
        """
        try:
            img = Image.open(image_path)
            # Если есть EXIF ориентация, применяем её физически к пикселям
            img = ImageOps.exif_transpose(img)
            img.save(image_path)
        except Exception as e:
            logger.warning(f"EXIF fix failed (maybe no exif data): {e}")

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

    def _apply_clock_rotation(self, image_path, clock_position):
        """
        Поворот на основе циферблата.
        clock_position: где сейчас находится ВЕРХ страницы (12, 3, 6, 9).
        PIL rotate(90) крутит ПРОТИВ часовой стрелки (влево).
        """
        if clock_position == "12_oclock": return

        try:
            img = Image.open(image_path)
            angle = 0
            
            if clock_position == "3_oclock":
                # Верх смотрит направо (3 часа).
                # Чтобы стало ровно (12), нужно крутить ВЛЕВО (против часовой) на 90.
                angle = 90
            
            elif clock_position == "6_oclock":
                # Верх смотрит вниз (6 часов).
                # Крутим на 180.
                angle = 180
            
            elif clock_position == "9_oclock":
                # Верх смотрит налево (9 часов).
                # Чтобы стало ровно (12), нужно крутить ВПРАВО (по часовой).
                # rotate(-90) или rotate(270)
                angle = -90
            
            if angle != 0:
                img = img.rotate(angle, expand=True)
                img.save(image_path)
                logger.info(f"Clock Rotation: Top was at {clock_position}, rotated by {angle} degrees")
                
        except Exception as e:
            logger.error(f"Rotation failed: {e}")

    def _convert_jpg_to_pdf(self, jpg_path):
        try:
            # Просто конвертируем, так как EXIF и поворот уже решены
            with open(jpg_path, "rb") as f:
                pdf_bytes = img2pdf.convert(f.read())
            
            pdf_path = os.path.splitext(jpg_path)[0] + ".pdf"
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)
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
                # ВАЖНО: Сначала фиксим EXIF телефонов
                self._fix_exif_orientation(local_path)
                ai_input_path = local_path

            if not ai_input_path: raise Exception("Failed to prepare image for AI")

            # --- АНАЛИЗ ИИ (МЕТОД ЧАСОВ) ---
            doc_data = {
                "doc_type": "Document", 
                "person_name": "Unknown", 
                "top_position": "12_oclock"
            }
            
            try:
                base64_img = self._encode_image(ai_input_path)
                
                prompt = """
                Analyze this document for Israeli Ministry of Interior.
                
                TASK 1: ORIENTATION (CLOCK FACE METHOD)
                Imagine the image is a clock face. Where is the TOP HEADER of the text pointing?
                - If text is upright, return "12_oclock".
                - If top of text points Right, return "3_oclock".
                - If top of text points Down (upside down), return "6_oclock".
                - If top of text points Left, return "9_oclock".
                
                TASK 2: CLASSIFICATION
                Classify document type (e.g. ID_Document, Passport, Birth_Certificate, Marriage_Certificate, Bank_Statement, Salary_Slip, etc).
                
                TASK 3: EXTRACTION
                Extract First and Last Name (Latin). Read mentally even if rotated.
                
                OUTPUT JSON:
                {"doc_type": "...", "person_name": "...", "top_position": "..."}
                """
                
                ai_result = analyze_document(base64_img, prompt)
                if ai_result: doc_data = ai_result
            except Exception as e:
                logger.error(f"AI Analysis failed: {e}")

            # --- ПОВОРОТ ---
            top_pos = doc_data.get("top_position", "12_oclock")
            if not is_pdf_input and top_pos != "12_oclock":
                self._apply_clock_rotation(local_path, top_pos)
            
            # Конвертация в PDF
            if not is_pdf_input:
                final_upload_path = self._convert_jpg_to_pdf(local_path)
                if not final_upload_path: final_upload_path = local_path

            # --- ЗАГРУЗКА ---
            person_name = doc_data.get('person_name', 'Client').strip()
            safe_person_name = "".join(c for c in person_name if c.isalnum() or c in (' ', '_', '-')).strip()
            if not safe_person_name: safe_person_name = "Client"
            
            doc_type = doc_data.get('doc_type', 'Doc').replace(" ", "_")
            date_str = datetime.now().strftime("%Y-%m-%d")
            
            remote_folder = f"/Clients/{user_phone}/{safe_person_name}"
            final_filename = f"{date_str}_{doc_type}.pdf"
            remote_path = f"{remote_folder}/{final_filename}"
            
            success = upload_file_to_disk(final_upload_path, remote_path)

            to_remove = [local_path, ai_input_path, final_upload_path]
            for path in set(to_remove):
                if path and os.path.exists(path) and "temp_files" in path: 
                    os.remove(path)

            if success:
                return {
                    "status": "success", 
                    "doc_type": doc_type, 
                    "person": person_name, 
                    "filename": final_filename,
                    "remote_path": remote_path
                }
            else:
                return {"status": "error", "message": "Ошибка загрузки"}

        except Exception as e:
            logger.error(f"Critical error: {e}")
            return {"status": "error", "message": str(e)}