import os
import logging
import base64
import img2pdf
import cv2
import numpy as np
import pytesseract
import re
from datetime import datetime
from PIL import Image, ImageOps, ImageEnhance
from pdf2image import convert_from_path
from services.yandex_disk import upload_file_to_disk
from services.openai_client import analyze_document

logger = logging.getLogger(__name__)

class DocumentProcessor:
    def __init__(self):
        self.temp_dir = "temp_files"
        os.makedirs(self.temp_dir, exist_ok=True)

    def _fix_exif_orientation_pil(self, img):
        try: return ImageOps.exif_transpose(img)
        except: return img

    def _convert_pdf_to_jpg(self, pdf_path):
        try:
            # DPI=200 достаточно для OCR и быстрее работает
            images = convert_from_path(pdf_path, dpi=200)
            return images if images else None
        except Exception as e:
            logger.error(f"PDF->JPG error: {e}")
            return None

    def _determine_orientation_via_ocr(self, cv_image):
        """
        Самый надежный метод:
        Вращаем картинку на 0, 90, 180, 270.
        Читаем текст Tesseract'ом.
        Где больше слов (Иврит/Рус/Англ) - тот угол и правильный.
        """
        import pytesseract
        
        # Работаем с уменьшенной копией для скорости
        h, w = cv_image.shape[:2]
        scale = 1000 / max(h, w)
        small = cv2.resize(cv_image, None, fx=scale, fy=scale)
        
        # Превращаем в ЧБ для лучшего чтения
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        angles = [0, 90, 180, 270]
        results = {}

        logger.info("🕵️ OCR Orientation check started...")

        for angle in angles:
            if angle == 0: rotated = gray
            elif angle == 90: rotated = cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
            elif angle == 180: rotated = cv2.rotate(gray, cv2.ROTATE_180)
            elif angle == 270: rotated = cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)

            try:
                # OSD (Orientation Script Detection) часто ошибается на смешанных языках.
                # Поэтому читаем текст полностью.
                # lang='heb+rus+eng' - ищем знакомые буквы
                text = pytesseract.image_to_string(rotated, lang='heb+rus+eng')
                
                # Очистка и подсчет "значимых" символов (букв)
                # Убираем пробелы и мусор, считаем длину чистого текста
                clean_text = re.sub(r'[^а-яА-Яa-zA-Z\u0590-\u05FF]', '', text)
                score = len(clean_text)
                
                results[angle] = score
                # logger.info(f"Angle {angle}: score {score}") # Debug
            except Exception as e:
                logger.error(f"OCR Error at {angle}: {e}")
                results[angle] = 0

        # Выбираем угол с максимальным счетом
        best_angle = max(results, key=results.get)
        logger.info(f"✅ OCR Winner: {best_angle}° (Score: {results[best_angle]})")
        
        return best_angle

    def _enhance_image(self, pil_image):
        """Финальное улучшение читаемости"""
        # Увеличение контраста
        enhancer = ImageEnhance.Contrast(pil_image)
        pil_image = enhancer.enhance(1.3)
        # Увеличение резкости
        enhancer = ImageEnhance.Sharpness(pil_image)
        pil_image = enhancer.enhance(1.1)
        return pil_image

    def _smart_crop_v2(self, pil_image):
        """
        Умная обрезка с проверкой "А надо ли?".
        """
        try:
            full_img_cv = np.array(pil_image)
            if len(full_img_cv.shape) == 3: full_img_cv = full_img_cv[:, :, ::-1].copy()
            else: full_img_cv = cv2.cvtColor(full_img_cv, cv2.COLOR_GRAY2BGR)

            h_orig, w_orig = full_img_cv.shape[:2]
            area_orig = h_orig * w_orig

            # Масштабируем
            target_h = 800.0
            scale = target_h / float(h_orig)
            w_small = int(w_orig * scale)
            h_small = int(target_h)
            small_img = cv2.resize(full_img_cv, (w_small, h_small))

            gray = cv2.cvtColor(small_img, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            
            # Canny + Morph
            edged = cv2.Canny(blurred, 50, 200)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
            closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel) # Замыкаем контуры

            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours: return pil_image

            largest = max(contours, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(largest)
            
            area_rect = w * h
            area_total = w_small * h_small
            ratio = area_rect / area_total

            # ЛОГИКА РЕШЕНИЯ:
            # Если контур занимает > 75% кадра — значит это уже скан, НЕ РЕЖЕМ.
            if ratio > 0.75:
                logger.info(f"SmartCrop: Document fills {ratio:.0%} of image. Keeping original.")
                return pil_image

            # Если меньше — режем (но с запасом)
            logger.info(f"SmartCrop: Document found ({ratio:.0%}). Cropping...")
            
            x = int(x / scale)
            y = int(y / scale)
            w = int(w / scale)
            h = int(h / scale)

            pad = 30 # Безопасный отступ
            x = max(0, x - pad)
            y = max(0, y - pad)
            w = min(w_orig - x, w + 2*pad)
            h = min(h_orig - y, h + 2*pad)

            return pil_image.crop((x, y, x+w, y+h))

        except Exception as e:
            logger.error(f"Crop Error: {e}")
            return pil_image

    def _encode_image(self, path):
        with open(path, "rb") as f: return base64.b64encode(f.read()).decode('utf-8')

    def process_and_upload(self, user_phone, local_path, original_filename):
        is_pdf = local_path.lower().endswith(".pdf")
        processed_results = []
        pil_images = []
        
        # 1. Загрузка
        try:
            if is_pdf: pil_images = self._convert_pdf_to_jpg(local_path)
            else: pil_images = [self._fix_exif_orientation_pil(Image.open(local_path))]
        except Exception as e: return [{"status": "error", "message": f"Read error: {e}"}]

        if not pil_images: return [{"status": "error", "message": "No images"}]

        source_file_uploaded = False

        for i, img in enumerate(pil_images, start=1):
            page_suffix = f"_page{i}"
            temp_page_jpg = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}.jpg")
            
            # --- НОВАЯ ЛОГИКА ---
            try:
                # 1. OCR Rotation (Вращаем оригинал, пока не станет читаемым)
                # Конвертируем PIL -> CV2 для анализа
                cv_img = np.array(img)
                if len(cv_img.shape) == 3: cv_img = cv_img[:, :, ::-1].copy()
                
                best_angle = self._determine_orientation_via_ocr(cv_img)
                
                # Применяем поворот
                if best_angle == 90: img = img.rotate(-90, expand=True) # PIL крутит против часовой
                elif best_angle == 180: img = img.rotate(180, expand=True)
                elif best_angle == 270: img = img.rotate(-270, expand=True)

                # 2. Smart Crop (с проверкой на необходимость)
                img = self._smart_crop_v2(img)

                # 3. Enhance (Улучшаем читаемость для чиновника)
                img = self._enhance_image(img)

                # Сохраняем результат
                img.save(temp_page_jpg, "JPEG", quality=90)
                
                # 4. Классификация (OpenAI)
                doc_data = {"doc_type": "Document", "person_name": "Unknown"}
                try:
                    base64_img = self._encode_image(temp_page_jpg)
                    # Упрощенный промпт, так как мы уже повернули сами
                    prompt = """
                    Classify document for Israeli Ministry of Interior.
                    Types: ID_Document, Passport, Birth_Certificate, Marriage_Certificate, 
                    Police_Clearance, Bank_Statement, Salary_Slip, Rental_Contract, Utility_Bill.
                    Extract Name (Latin).
                    JSON: {"doc_type": "...", "person_name": "..."}
                    """
                    res = analyze_document(base64_img, prompt)
                    if res: doc_data = res
                except Exception as e: logger.error(f"AI Classify Error: {e}")

                # 5. Конвертация в PDF и Пути
                final_pdf_path = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}.pdf")
                with open(temp_page_jpg, "rb") as f: pdf_bytes = img2pdf.convert(f.read())
                with open(final_pdf_path, "wb") as f: f.write(pdf_bytes)

                person = "".join(c for c in doc_data.get('person_name', 'Client') if c.isalnum() or c in ' _-').strip()
                base_folder = f"/Clients/{user_phone}/{person or 'Client'}"
                date_s = datetime.now().strftime("%Y-%m-%d")
                dtype = doc_data.get('doc_type', 'Doc')
                remote_filename = f"{date_s}_{dtype}{page_suffix}.pdf"
                remote_path_pdf = f"{base_folder}/{remote_filename}"

                # 6. Загрузка Оригинала (Один раз)
                if not source_file_uploaded:
                    orig_ext = os.path.splitext(local_path)[1] or ".jpg"
                    remote_orig = f"{base_folder}/Originals/{date_s}_{dtype}_Source_orig{orig_ext}"
                    try:
                        upload_file_to_disk(local_path, remote_orig)
                        source_file_uploaded = True
                    except: pass

                # 7. Загрузка Результата
                if upload_file_to_disk(final_pdf_path, remote_path_pdf):
                    processed_results.append({
                        "status": "success", "doc_type": dtype, "person": person, 
                        "filename": remote_filename, "remote_path": remote_path_pdf
                    })
                else:
                    processed_results.append({"status": "error", "message": "Upload failed"})

            except Exception as e:
                logger.error(f"Process Page {i} Error: {e}")
                processed_results.append({"status": "error", "message": str(e)})
            finally:
                for p in {temp_page_jpg, final_pdf_path}:
                    if p and os.path.exists(p): os.remove(p)

        return processed_results