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
            # Берем первую страницу для анализа
            jpg_path = pdf_path.replace(".pdf", "_temp_analysis.jpg")
            images[0].save(jpg_path, "JPEG")
            return jpg_path
        except Exception as e:
            logger.error(f"Error converting PDF to JPG: {e}")
            return None

    def _rotate_image_by_angle(self, image_path, angle):
        """Поворот изображения. Отрицательный угол = по часовой стрелке."""
        if angle == 0: return
        try:
            img = Image.open(image_path)
            # Минус angle, чтобы крутить по часовой (как привычно людям)
            img = img.rotate(-angle, expand=True) 
            img.save(image_path)
            logger.info(f"Image rotated by {angle} degrees (Clockwise)")
        except Exception as e:
            logger.error(f"Rotation failed: {e}")

    def _convert_jpg_to_pdf(self, jpg_path):
        try:
            image = Image.open(jpg_path)
            image = ImageOps.exif_transpose(image) # Учитываем ориентацию EXIF
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
            # 1. Подготовка файла для ИИ (если PDF -> JPG)
            if is_pdf_input:
                ai_input_path = self._convert_pdf_to_jpg(local_path)
                final_upload_path = local_path 
            else:
                ai_input_path = local_path

            if not ai_input_path: raise Exception("Failed to prepare image for AI")

            # 2. АНАЛИЗ ИИ (СТУПРО / МВД)
            doc_data = {"doc_type": "Document", "person_name": "Unknown", "rotation": 0}
            try:
                base64_img = self._encode_image(ai_input_path)
                
                # Промпт: содержит все ключевые документы из твоего списка
                prompt = """
                Analyze this document for an Israeli Ministry of Interior (Misrad Hapnim) StuPro application.
                
                1. Classify the document type into ONE of these categories:
                   - ID_Document (Teudat Zehut / תעודת זהות)
                   - Passport (Foreign Passport / דרכון)
                   - Photo_ID (Passport photo / תמונה חזותית)
                   - Application_Form (Ash/6, Mar/6, Ash/1, Ash/3 / טפסים ובקשות)
                   - Marriage_Certificate (תעודת נישואין)
                   - Birth_Certificate (תעודת לידה)
                   - Name_Change_Cert (תעודת שינוי שם)
                   - Marital_Status_Doc (Tamzit Rishum / Certificate / Divorce Decree / תעודת מצב אישי או גירושין)
                   - Police_Clearance (Teudat Yosher / תעודת יושר)
                   - Relationship_Letter (Letter of explanation / מכתב הסבר)
                   - Chat_History (WhatsApp logs, calls / פירוט שיחות)
                   - Joint_Photos (Photos of couple / תמונות משותפות)
                   - Bank_Statement (3 months / תדפיס עובר ושב / בנק)
                   - Salary_Slip (Tlush Maskoret / תלוש שכר)
                   - Employment_Doc (Work confirmation / אישור מעסיק)
                   - National_Insurance (Bituach Leumi docs / ביטוח לאומי)
                   - Rental_Contract (חוזה שכירות)
                   - Utility_Bill (Arnona, Water, Electricity / חשבונות חשמל/מים/ארנונה)
                   - Recommendation_Letter (Letters from friends/family / מכתבי ממליצים)
                   - Power_of_Attorney (Yipuy Koach / ייפוי כוח)
                   - Lawyer_License (רישיון עו״ד)
                   - Minor_Document (Birth cert or custody of children / מסמכי קטינים)
                   - Other (Any other document)

                2. Extract First and Last Name (Latin/English transliteration).
                3. Rotation: needed angle (0, 90, 180, 270) CLOCKWISE to make text horizontal.
                
                Return JSON: {"doc_type": "...", "person_name": "...", "rotation": 0}
                """
                
                ai_result = analyze_document(base64_img, prompt)
                if ai_result: doc_data = ai_result
            except Exception as e:
                logger.error(f"AI Analysis failed: {e}")

            # 3. ПОВОРОТ (Если нужно)
            rotation_needed = doc_data.get("rotation", 0)
            # Если это картинка и нужен поворот
            if not is_pdf_input and rotation_needed in [90, 180, 270]:
                self._rotate_image_by_angle(local_path, rotation_needed)
            
            # Конвертируем в PDF для финального хранения (если была картинка)
            if not is_pdf_input:
                final_upload_path = self._convert_jpg_to_pdf(local_path)
                if not final_upload_path: final_upload_path = local_path

            # 4. ФОРМИРОВАНИЕ ПУТЕЙ И ИМЕН
            person_name = doc_data.get('person_name', 'Client').strip()
            # Убираем опасные символы из имени
            safe_person_name = "".join(c for c in person_name if c.isalnum() or c in (' ', '_', '-')).strip()
            if not safe_person_name: safe_person_name = "Client"
            
            doc_type = doc_data.get('doc_type', 'Doc').replace(" ", "_")
            date_str = datetime.now().strftime("%Y-%m-%d")
            
            # Папка клиента
            remote_folder = f"/Clients/{user_phone}/{safe_person_name}"
            # Итоговое имя файла
            final_filename = f"{date_str}_{doc_type}.pdf"
            remote_path = f"{remote_folder}/{final_filename}"
            
            # 5. ЗАГРУЗКА
            # Сначала пробуем загрузить, получаем результат
            success = upload_file_to_disk(final_upload_path, remote_path)

            # Чистим мусор
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
                return {"status": "error", "message": "Ошибка загрузки на Диск"}

        except Exception as e:
            logger.error(f"Critical error: {e}")
            return {"status": "error", "message": str(e)}