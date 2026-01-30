import os
import logging
import base64
import img2pdf
import numpy as np
import cv2
from datetime import datetime
from PIL import Image, ImageOps, ImageEnhance # <--- Добавили ImageEnhance
from pdf2image import convert_from_path
from services.storage_manager import upload_file # <--- Ссылка на новый менеджер хранилища
from services.openai_client import analyze_document

logger = logging.getLogger(__name__)

class DocumentProcessor:
    def __init__(self):
        self.temp_dir = "temp_files"
        os.makedirs(self.temp_dir, exist_ok=True)

    # ... (методы _convert_pdf_to_jpg и _encode_image без изменений) ...
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

    def _encode_image(self, image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def _enhance_image(self, image_path):
        """Улучшает качество: поворот, контраст, резкость"""
        try:
            img = Image.open(image_path)
            
            # 1. Применяем EXIF (на всякий случай)
            img = ImageOps.exif_transpose(img)

            # 2. Увеличиваем контраст (делаем текст чернее, фон белее)
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.5) # +50% контраста

            # 3. Увеличиваем резкость
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(2.0) # +100% резкости

            # Пересохраняем
            img.save(image_path, quality=95)
            logger.info("Image enhanced (Contrast+Sharpness)")
        except Exception as e:
            logger.error(f"Enhancement failed: {e}")

    def _rotate_image_smart(self, image_path, orientation):
        """
        Поворачивает на основе того, где находится ВЕРХ документа.
        orientation: 'UP', 'RIGHT', 'DOWN', 'LEFT'
        """
        if orientation == 'UP': return
        
        angle = 0
        if orientation == 'RIGHT': angle = 90    # Верх справа -> крутим на 90 влево (CCW)
        elif orientation == 'DOWN': angle = 180  # Верх внизу -> крутим на 180
        elif orientation == 'LEFT': angle = -90  # Верх слева -> крутим на 90 вправо (CW) или 270 CCW
        
        if angle != 0:
            try:
                img = Image.open(image_path)
                img = img.rotate(angle, expand=True)
                img.save(image_path)
                logger.info(f"Image rotated by {angle} (Orientation was {orientation})")
            except Exception as e:
                logger.error(f"Rotation failed: {e}")

    def _convert_jpg_to_pdf(self, jpg_path):
        try:
            # Конвертация в PDF
            pdf_path = os.path.splitext(jpg_path)[0] + ".pdf"
            with open(pdf_path, "wb") as f:
                f.write(img2pdf.convert(jpg_path))
            return pdf_path
        except Exception as e:
            logger.error(f"Error converting JPG to PDF: {e}")
            return None

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
            
            if not ai_input_path: raise Exception("Failed to prepare image")

            # 1. Сначала улучшаем качество (до отправки в ИИ, чтобы он лучше читал)
            if not is_pdf_input:
                self._enhance_image(ai_input_path)

            # 2. АНАЛИЗ ИИ
            doc_data = {"doc_type": "Document", "person_name": "Unknown", "orientation": "UP"}
            try:
                base64_img = self._encode_image(ai_input_path)
                
                # НОВЫЙ ПРОМПТ
                prompt = """
                Analyze this document for Israeli Ministry of Interior.
                1. Classify document type (Passport, Teudat_Zehut, etc.).
                2. Extract Person Name (Latin).
                3. Determine ORIENTATION. Where is the TOP of the text currently facing?
                   - UP (Text is upright, readable)
                   - RIGHT (Text is rotated 90 deg clockwise, top is on the right)
                   - DOWN (Text is upside down)
                   - LEFT (Text is rotated 90 deg counter-clockwise, top is on the left)
                
                Return JSON: {"doc_type": "...", "person_name": "...", "orientation": "UP/RIGHT/DOWN/LEFT"}
                """
                
                ai_result = analyze_document(base64_img, prompt)
                if ai_result: doc_data = ai_result
            except Exception as e:
                logger.error(f"AI Analysis failed: {e}")

            # 3. ПОВОРОТ
            orientation = doc_data.get("orientation", "UP")
            if not is_pdf_input:
                self._rotate_image_smart(local_path, orientation)
                # После поворота и улучшений делаем финальный PDF
                final_upload_path = self._convert_jpg_to_pdf(local_path)
                if not final_upload_path: final_upload_path = local_path

            # 4. ПУТИ И ЗАГРУЗКА
            person_name = doc_data.get('person_name', 'Client').strip()
            safe_person_name = "".join(c for c in person_name if c.isalnum() or c in (' ', '_', '-')).strip() or "Client"
            doc_type = doc_data.get('doc_type', 'Doc').replace(" ", "_")
            date_str = datetime.now().strftime("%Y-%m-%d")
            
            remote_folder = f"/Clients/{user_phone}/{safe_person_name}"
            final_filename = f"{date_str}_{doc_type}.pdf"
            remote_path = f"{remote_folder}/{final_filename}"
            
            # --- ИСПОЛЬЗУЕМ УНИВЕРСАЛЬНЫЙ ЗАГРУЗЧИК ---
            success, public_link = upload_file(final_upload_path, remote_path)

            # Чистка
            to_remove = [local_path, ai_input_path, final_upload_path]
            for path in set(to_remove):
                if path and os.path.exists(path) and "temp_files" in path: os.remove(path)

            if success:
                return {
                    "status": "success", "doc_type": doc_type, 
                    "person": person_name, "filename": final_filename,
                    "remote_path": remote_path,
                    "public_link": public_link
                }
            else:
                return {"status": "error", "message": "Ошибка загрузки"}

        except Exception as e:
            logger.error(f"Critical error: {e}")
            return {"status": "error", "message": str(e)}