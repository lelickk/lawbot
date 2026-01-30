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

    def _apply_orientation_fix(self, image_path, orientation):
        """
        Поворачивает изображение на основе текстового описания ориентации.
        PIL rotate(90) = Против часовой стрелки.
        """
        if orientation == "normal": return
        
        try:
            img = Image.open(image_path)
            angle = 0
            
            # Логика поворота (Железобетонная)
            if orientation == "rotated_left":
                # Верхушка букв смотрит ВЛЕВО. 
                # Чтобы поставить ровно, нужно повернуть ПО ЧАСОВОЙ (CW).
                angle = -90 
            
            elif orientation == "rotated_right":
                # Верхушка букв смотрит ВПРАВО.
                # Чтобы поставить ровно, нужно повернуть ПРОТИВ ЧАСОВОЙ (CCW).
                angle = 90
            
            elif orientation == "upside_down":
                # Вверх ногами
                angle = 180
                
            if angle != 0:
                img = img.rotate(angle, expand=True)
                img.save(image_path)
                logger.info(f"Fixed orientation '{orientation}' by rotating {angle} degrees")
                
        except Exception as e:
            logger.error(f"Rotation failed: {e}")

    def _convert_jpg_to_pdf(self, jpg_path):
        try:
            image = Image.open(jpg_path)
            # Убираем exif_transpose, чтобы он не сбивал наш ручной поворот, 
            # или используем его ДО анализа. Но лучше довериться ИИ.
            # image = ImageOps.exif_transpose(image) 
            
            # Просто пересохраняем, чтобы сбросить EXIF ориентацию, если она была
            image.save(jpg_path)
            
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

            if not ai_input_path: raise Exception("Failed to prepare image for AI")

            # --- АНАЛИЗ ИИ (Словесная ориентация) ---
            doc_data = {
                "doc_type": "Document", 
                "person_name": "Unknown", 
                "text_orientation": "normal"
            }
            
            try:
                base64_img = self._encode_image(ai_input_path)
                
                prompt = """
                Analyze this document for Misrad Hapnim (Israeli Ministry of Interior).
                
                TASK 1: ORIENTATION (CRITICAL)
                Look at the text direction. Where is the TOP of the letters pointing?
                Return "text_orientation" as one of:
                - "normal" (Text is horizontal and readable)
                - "upside_down" (Text is upside down)
                - "rotated_left" (Top of text points to the LEFT side of image)
                - "rotated_right" (Top of text points to the RIGHT side of image)
                
                TASK 2: CLASSIFICATION
                Classify document type (e.g. ID_Document, Passport, Birth_Certificate, Marriage_Certificate, Bank_Statement, Salary_Slip, etc).
                
                TASK 3: EXTRACTION
                Extract First and Last Name (Latin). If text is rotated, read it mentally.
                
                OUTPUT JSON:
                {"doc_type": "...", "person_name": "...", "text_orientation": "..."}
                """
                
                ai_result = analyze_document(base64_img, prompt)
                if ai_result: doc_data = ai_result
            except Exception as e:
                logger.error(f"AI Analysis failed: {e}")

            # --- ПОВОРОТ ---
            orientation = doc_data.get("text_orientation", "normal")
            if not is_pdf_input and orientation != "normal":
                self._apply_orientation_fix(local_path, orientation)
            
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