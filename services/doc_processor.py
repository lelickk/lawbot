import os
import logging
import base64
import img2pdf
import cv2  # <--- Добавили OpenCV
import numpy as np # <--- Добавили Numpy
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
        """Фикс EXIF для объекта PIL Image"""
        try:
            return ImageOps.exif_transpose(img)
        except:
            return img

    def _convert_pdf_to_jpg(self, pdf_path):
        try:
            # --- ИЗМЕНЕНИЕ: DPI=300 для высокого качества ---
            images = convert_from_path(pdf_path, dpi=300)
            if not images: return None
            # Возвращаем список PIL объектов (страниц)
            return images
        except Exception as e:
            logger.error(f"PDF->JPG error: {e}")
            return None

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

    def _smart_crop(self, pil_image):
        """
        Умная обрезка краев с помощью OpenCV.
        1. Конвертируем в формат OpenCV.
        2. Ищем контуры документа.
        3. Обрезаем лишний фон.
        """
        try:
            # Конвертация PIL -> OpenCV (RGB -> BGR)
            open_cv_image = np.array(pil_image) 
            # Если изображение черно-белое, shape будет (H, W), иначе (H, W, 3)
            if len(open_cv_image.shape) == 3:
                open_cv_image = open_cv_image[:, :, ::-1].copy()
            else:
                # Если вдруг пришло grayscale, конвертируем в BGR для унификации
                open_cv_image = cv2.cvtColor(open_cv_image, cv2.COLOR_GRAY2BGR)

            # Грейскейл для анализа
            gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)
            # Размытие (убираем шум)
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            
            # Пороговое преобразование (Threshold) - ищем белое на темном
            # Otsu отлично находит баланс между бумагой и столом
            _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # Морфология (закрываем дырки в буквах, чтобы получить сплошной контур листа)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
            morph = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

            # Ищем контуры
            contours, _ = cv2.findContours(morph, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if not contours:
                return pil_image # Если не нашли, возвращаем как есть

            # Берем самый большой контур (это и есть наш лист бумаги)
            largest_contour = max(contours, key=cv2.contourArea)

            # Получаем прямоугольник вокруг контура (x, y, w, h)
            x, y, w, h = cv2.boundingRect(largest_contour)

            # Проверка: если контур слишком маленький (мусор), не обрезаем
            img_h, img_w = open_cv_image.shape[:2]
            if w * h < (img_w * img_h) * 0.1: # Если меньше 10% площади, это ошибка
                return pil_image

            # Добавляем небольшой отступ (padding), чтобы не резать буквы по живому
            padding = 15 # пикселей
            x = max(0, x - padding)
            y = max(0, y - padding)
            w = min(img_w - x, w + 2 * padding)
            h = min(img_h - y, h + 2 * padding)

            # Обрезаем
            cropped = pil_image.crop((x, y, x + w, y + h))
            logger.info(f"Smart Cropped: x={x}, y={y}, w={w}, h={h}")
            return cropped

        except Exception as e:
            logger.error(f"Smart crop failed: {e}")
            return pil_image # При ошибке возвращаем оригинал

    def _encode_image(self, path):
        with open(path, "rb") as f: return base64.b64encode(f.read()).decode('utf-8')

    def process_and_upload(self, user_phone, local_path, original_filename):
        is_pdf = local_path.lower().endswith(".pdf")
        processed_results = []
        
        pil_images = []
        try:
            if is_pdf:
                # Теперь здесь DPI=300
                pil_images = self._convert_pdf_to_jpg(local_path)
            else:
                img = Image.open(local_path)
                img = self._fix_exif_orientation_pil(img)
                pil_images = [img]
        except Exception as e:
            return [{"status": "error", "message": f"File read error: {e}"}]

        if not pil_images:
            return [{"status": "error", "message": "No images found"}]

        source_file_uploaded = False

        for i, img in enumerate(pil_images, start=1):
            page_suffix = f"_page{i}"
            
            # Временный файл (уже высокого разрешения)
            temp_page_jpg = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}.jpg")
            img.save(temp_page_jpg, "JPEG", quality=95) # Высокое качество JPG
            
            final_pdf_path = None
            
            try:
                # --- АНАЛИЗ ИИ ---
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

                # --- 1. ПОВОРОТ (Rotate) ---
                # Сначала поворачиваем, чтобы документ встал ровно
                rotated_img = self._apply_clock_rotation(img, doc_data.get("top_position"))
                
                # --- 2. ОБРЕЗКА (Smart Crop) ---
                # Теперь, когда документ ровный, обрезаем поля стола
                final_img = self._smart_crop(rotated_img)

                # --- СОХРАНЕНИЕ ---
                final_pdf_path = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}_final.pdf")
                
                # Сохраняем финальную картинку перед PDF конвертацией
                temp_final_jpg = temp_page_jpg.replace(".jpg", "_final.jpg")
                final_img.save(temp_final_jpg, "JPEG", quality=95)
                
                with open(temp_final_jpg, "rb") as f:
                    pdf_bytes = img2pdf.convert(f.read())
                with open(final_pdf_path, "wb") as f:
                    f.write(pdf_bytes)
                
                if os.path.exists(temp_final_jpg): os.remove(temp_final_jpg)

                # --- ПУТИ ---
                person = "".join(c for c in doc_data.get('person_name', 'Client') if c.isalnum() or c in ' _-').strip()
                base_folder = f"/Clients/{user_phone}/{person or 'Client'}"
                date_s = datetime.now().strftime("%Y-%m-%d")
                dtype = doc_data.get('doc_type', 'Doc')

                remote_filename = f"{date_s}_{dtype}{page_suffix}.pdf"
                remote_path_pdf = f"{base_folder}/{remote_filename}"

                # --- ЗАГРУЗКА ИСХОДНИКА (Один раз) ---
                if not source_file_uploaded:
                    orig_ext = os.path.splitext(local_path)[1] or ".jpg"
                    remote_orig = f"{base_folder}/Originals/{date_s}_{dtype}_Source_orig{orig_ext}"
                    try:
                        upload_file_to_disk(local_path, remote_orig)
                        source_file_uploaded = True
                    except Exception as e:
                        logger.error(f"Failed source upload: {e}")

                # --- ЗАГРУЗКА ЧИСТОВИКА ---
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
                for p in {temp_page_jpg, final_pdf_path}:
                    if p and os.path.exists(p): os.remove(p)

        return processed_results