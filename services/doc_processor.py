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
        """Поворот изображения (против часовой стрелки, стандарт PIL)"""
        if angle == 0: return
        try:
            img = Image.open(image_path)
            img = img.rotate(angle, expand=True) 
            img.save(image_path)
            logger.info(f"Image rotated by {angle} degrees")
        except Exception as e:
            logger.error(f"Rotation failed: {e}")

    def _convert_jpg_to_pdf(self, jpg_path):
        try:
            image = Image.open(jpg_path)
            image = ImageOps.exif_transpose(image)
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

            # --- АНАЛИЗ ИИ (Обновленный жесткий промпт) ---
            doc_data = {"doc_type": "Document", "person_name": "Unknown", "rotation": 0}
            try:
                base64_img = self._encode_image(ai_input_path)
                
                prompt = """
                You are a strict document analysis system for the Israeli Ministry of Interior.
                Analyze the image carefully.

                TASK 1: ORIENTATION CHECK (CRITICAL)
                Look at the text direction (Hebrew or English). 
                - If the text is upside down, you MUST return 180.
                - If the text is vertical (pointing left), return 90.
                - If the text is vertical (pointing right), return 270.
                - Only return 0 if the text is perfectly horizontal and readable.
                
                TASK 2: CLASSIFICATION
                Classify into ONE category:
                   - ID_Document (Teudat Zehut)
                   - Passport (Foreign Passport)
                   - Photo_ID (Passport photo)
                   - Marriage_Certificate
                   - Birth_Certificate
                   - Police_Clearance (Teudat Yosher)
                   - Marital_Status_Doc
                   - Bank_Statement
                   - Salary_Slip (Tlush Maskoret)
                   - Rental_Contract
                   - Utility_Bill
                   - Relationship_Letter
                   - Other

                TASK 3: EXTRACTION
                Extract the First and Last Name (Latin characters). If the document is upside down, rotate it mentally first!

                OUTPUT JSON ONLY:
                {"doc_type": "...", "person_name": "...", "rotation": 0}
                """
                
                ai_result = analyze_document(base64_img, prompt)
                if ai_result: doc_data = ai_result
            except Exception as e:
                logger.error(f"AI Analysis failed: {e}")

            # --- ПОВОРОТ ---
            rotation_needed = doc_data.get("rotation", 0)
            if not is_pdf_input and rotation_needed in [90, 180, 270]:
                self._rotate_image_by_angle(local_path, rotation_needed)
            
            if not is_pdf_input:
                final_upload_path = self._convert_jpg_to_pdf(local_path)
                if not final_upload_path: final_upload_path = local_path

            # --- ПУТИ И ЗАГРУЗКА ---
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
                    "status": "success", "doc_type": doc_type, 
                    "person": person_name, "filename": final_filename,
                    "remote_path": remote_path
                }
            else:
                return {"status": "error", "message": "Ошибка загрузки"}

        except Exception as e:
            logger.error(f"Critical error: {e}")
            return {"status": "error", "message": str(e)}