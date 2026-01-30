import os
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
        try:
            img = Image.open(image_path)
            img = ImageOps.exif_transpose(img)
            img.save(image_path)
        except: pass

    def _convert_pdf_to_jpg(self, pdf_path):
        try:
            images = convert_from_path(pdf_path)
            if not images: return None
            jpg_path = pdf_path.replace(".pdf", "_temp_analysis.jpg")
            images[0].save(jpg_path, "JPEG")
            return jpg_path
        except Exception as e:
            logger.error(f"PDF->JPG error: {e}")
            return None

    def _apply_clock_rotation(self, image_path, clock_pos):
        if clock_pos == "12_oclock": return
        angle_map = {"3_oclock": 90, "6_oclock": 180, "9_oclock": -90}
        angle = angle_map.get(clock_pos, 0)
        if angle:
            try:
                img = Image.open(image_path)
                img = img.rotate(angle, expand=True)
                img.save(image_path)
            except: pass

    def _convert_jpg_to_pdf(self, jpg_path):
        try:
            with open(jpg_path, "rb") as f: pdf_bytes = img2pdf.convert(f.read())
            pdf_path = os.path.splitext(jpg_path)[0] + ".pdf"
            with open(pdf_path, "wb") as f: f.write(pdf_bytes)
            return pdf_path
        except: return None

    def _encode_image(self, path):
        with open(path, "rb") as f: return base64.b64encode(f.read()).decode('utf-8')

    def process_and_upload(self, user_phone, local_path, original_filename):
        ai_input_path = None
        final_upload_path = None
        is_pdf_input = local_path.lower().endswith(".pdf")
        
        # 1. Сначала фиксим EXIF (если картинка), чтобы ИИ видел как есть
        if not is_pdf_input: self._fix_exif_orientation(local_path)

        try:
            # Подготовка для ИИ
            ai_input_path = self._convert_pdf_to_jpg(local_path) if is_pdf_input else local_path
            if not ai_input_path: raise Exception("File prep failed")

            # 2. Анализ ИИ (Часы + Список документов)
            doc_data = {"doc_type": "Document", "person_name": "Unknown", "top_position": "12_oclock"}
            try:
                base64_img = self._encode_image(ai_input_path)
                prompt = """
                Analyze for Israeli Ministry of Interior.
                TASK 1: Orientation (Clock Face). Where is the TOP of text? 
                (12_oclock, 3_oclock, 6_oclock, 9_oclock).
                TASK 2: Classify: ID_Document, Passport, Birth_Certificate, Marriage_Certificate, 
                Police_Clearance, Bank_Statement, Salary_Slip, Rental_Contract, Utility_Bill, etc.
                TASK 3: Extract Name (Latin).
                JSON: {"doc_type": "...", "person_name": "...", "top_position": "..."}
                """
                res = analyze_document(base64_img, prompt)
                if res: doc_data = res
            except Exception as e: logger.error(f"AI error: {e}")

            # 3. Поворот
            if not is_pdf_input: self._apply_clock_rotation(local_path, doc_data.get("top_position"))

            # 4. Конвертация в PDF (чистовик)
            final_upload_path = local_path
            if not is_pdf_input:
                pdf = self._convert_jpg_to_pdf(local_path)
                if pdf: final_upload_path = pdf

            # 5. Пути и Имена
            person = "".join(c for c in doc_data.get('person_name', 'Client') if c.isalnum() or c in ' _-').strip()
            folder = f"/Clients/{user_phone}/{person or 'Client'}"
            date_s = datetime.now().strftime("%Y-%m-%d")
            dtype = doc_data.get('doc_type', 'Doc')

            # Имя чистовика (PDF)
            remote_pdf = f"{folder}/{date_s}_{dtype}.pdf"
            
            # Имя оригинала (JPG или исходный PDF)
            orig_ext = os.path.splitext(local_path)[1] or ".jpg"
            remote_orig = f"{folder}/{date_s}_{dtype}_orig{orig_ext}"

            # 6. ЗАГРУЗКА (Сначала оригинал, потом чистовик)
            # Оригинал: загружаем local_path (он может быть повернут по EXIF, но это даже лучше)
            upload_file_to_disk(local_path, remote_orig)
            
            # Чистовик
            success = upload_file_to_disk(final_upload_path, remote_pdf)

            # Чистка
            for p in {local_path, ai_input_path, final_upload_path}:
                if p and os.path.exists(p) and "temp_files" in p: os.remove(p)

            if success:
                return {
                    "status": "success", "doc_type": dtype, "person": person, 
                    "filename": remote_pdf, "remote_path": remote_pdf
                }
            return {"status": "error", "message": "Upload failed"}

        except Exception as e:
            return {"status": "error", "message": str(e)}