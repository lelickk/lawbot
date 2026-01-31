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

    def _fix_exif_orientation_pil(self, img):
        """Фикс EXIF для объекта PIL Image (не файла)"""
        try:
            return ImageOps.exif_transpose(img)
        except:
            return img

    def _apply_clock_rotation(self, img, clock_pos):
        """Поворот PIL Image объекта в памяти"""
        if clock_pos == "12_oclock": return img
        
        angle_map = {"3_oclock": 90, "6_oclock": 180, "9_oclock": -90}
        angle = angle_map.get(clock_pos, 0)
        
        if angle:
            try:
                return img.rotate(angle, expand=True)
            except Exception as e:
                logger.error(f"Rotation error: {e}")
        return img

    def _encode_image(self, path):
        with open(path, "rb") as f: return base64.b64encode(f.read()).decode('utf-8')

    def process_and_upload(self, user_phone, local_path, original_filename):
        """
        Возвращает СПИСОК результатов, так как страниц может быть много.
        Пример: [{'status': 'success', 'doc_type':...}, {...}]
        """
        is_pdf = local_path.lower().endswith(".pdf")
        processed_results = []
        
        # 1. Получаем список изображений (страниц)
        pil_images = []
        try:
            if is_pdf:
                # Конвертируем все страницы PDF в картинки
                pil_images = convert_from_path(local_path)
            else:
                # Если это картинка, открываем и сразу фиксим EXIF
                img = Image.open(local_path)
                img = self._fix_exif_orientation_pil(img)
                pil_images = [img]
        except Exception as e:
            return [{"status": "error", "message": f"File read error: {e}"}]

        if not pil_images:
            return [{"status": "error", "message": "No images found"}]

        # Флаг, чтобы загрузить исходный файл (Оригинал) только 1 раз
        source_file_uploaded = False

        # 2. Цикл по страницам
        for i, img in enumerate(pil_images, start=1):
            page_suffix = f"_page{i}" # _page1, _page2
            
            # Временный файл для анализа текущей страницы
            temp_page_jpg = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}.jpg")
            img.save(temp_page_jpg, "JPEG")
            
            final_pdf_path = None
            
            try:
                # --- АНАЛИЗ ИИ (Для каждой страницы отдельно!) ---
                # Ведь страница 2 может быть перевернута, даже если страница 1 нормальная
                doc_data = {"doc_type": "Document", "person_name": "Unknown", "top_position": "12_oclock"}
                try:
                    base64_img = self._encode_image(temp_page_jpg)
                    prompt = """
                    Analyze document page for Israeli Ministry of Interior.
                    TASK 1: Orientation. Where is the TOP of text? (12_oclock, 3_oclock, 6_oclock, 9_oclock).
                    TASK 2: Classify. ID_Document, Passport, Birth_Certificate, Marriage_Certificate, 
                    Police_Clearance, Bank_Statement, Salary_Slip, Rental_Contract, Utility_Bill, etc.
                    TASK 3: Extract Name (Latin).
                    JSON: {"doc_type": "...", "person_name": "...", "top_position": "..."}
                    """
                    res = analyze_document(base64_img, prompt)
                    if res: doc_data = res
                except Exception as e:
                    logger.error(f"AI error on page {i}: {e}")

                # --- ПОВОРОТ (В памяти) ---
                rotated_img = self._apply_clock_rotation(img, doc_data.get("top_position"))
                
                # --- СОХРАНЕНИЕ СТРАНИЦЫ В PDF (Чистовик) ---
                final_pdf_path = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}_final.pdf")
                
                # Сохраняем повернутую картинку во временный буфер, чтобы сделать PDF
                temp_rot_jpg = temp_page_jpg.replace(".jpg", "_rot.jpg")
                rotated_img.save(temp_rot_jpg, "JPEG")
                
                with open(temp_rot_jpg, "rb") as f:
                    pdf_bytes = img2pdf.convert(f.read())
                with open(final_pdf_path, "wb") as f:
                    f.write(pdf_bytes)
                
                # Удаляем промежуточный rot_jpg
                if os.path.exists(temp_rot_jpg): os.remove(temp_rot_jpg)

                # --- ФОРМИРОВАНИЕ ПУТЕЙ ---
                person = "".join(c for c in doc_data.get('person_name', 'Client') if c.isalnum() or c in ' _-').strip()
                base_folder = f"/Clients/{user_phone}/{person or 'Client'}"
                date_s = datetime.now().strftime("%Y-%m-%d")
                dtype = doc_data.get('doc_type', 'Doc')

                # Имя файла: Date_Type_pageN.pdf
                remote_filename = f"{date_s}_{dtype}{page_suffix}.pdf"
                remote_path_pdf = f"{base_folder}/{remote_filename}"

                # --- ЗАГРУЗКА ИСХОДНИКА (Только 1 раз) ---
                # Мы загружаем весь исходный файл (даже если он многостраничный) в Originals
                if not source_file_uploaded:
                    orig_ext = os.path.splitext(local_path)[1] or ".jpg"
                    # Имя оригинала: Date_Type_Source_orig.pdf
                    remote_orig = f"{base_folder}/Originals/{date_s}_{dtype}_Source_orig{orig_ext}"
                    try:
                        upload_file_to_disk(local_path, remote_orig)
                        source_file_uploaded = True
                    except Exception as e:
                        logger.error(f"Failed source upload: {e}")

                # --- ЗАГРУЗКА СТРАНИЦЫ ---
                if upload_file_to_disk(final_pdf_path, remote_path_pdf):
                    processed_results.append({
                        "status": "success",
                        "doc_type": dtype,
                        "person": person,
                        "filename": remote_filename,
                        "remote_path": remote_path_pdf
                    })
                else:
                    processed_results.append({"status": "error", "message": f"Upload failed page {i}"})

            finally:
                # Чистка временных файлов страницы
                for p in {temp_page_jpg, final_pdf_path}:
                    if p and os.path.exists(p): os.remove(p)

        return processed_results