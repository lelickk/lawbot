import os
import logging
import base64
import img2pdf
import cv2
import numpy as np
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
            # DPI=300 для высокого качества
            images = convert_from_path(pdf_path, dpi=300)
            if not images: return None
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

    def _order_points(self, pts):
        """Упорядочивает координаты 4 точек (TL, TR, BR, BL)"""
        rect = np.zeros((4, 2), dtype="float32")
        
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        
        return rect

    def _four_point_transform(self, image, pts):
        """Перспективная трансформация (выравнивание документа)"""
        rect = self._order_points(pts)
        (tl, tr, br, bl) = rect

        widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        maxWidth = max(int(widthA), int(widthB))

        heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        maxHeight = max(int(heightA), int(heightB))

        dst = np.array([
            [0, 0],
            [maxWidth - 1, 0],
            [maxWidth - 1, maxHeight - 1],
            [0, maxHeight - 1]], dtype="float32")

        M = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))

        return warped

    def _smart_crop(self, pil_image):
        """
        Продвинутая обрезка (Scan-like behavior).
        """
        try:
            # 1. Конвертация PIL -> OpenCV
            full_img_cv = np.array(pil_image)
            if len(full_img_cv.shape) == 3:
                full_img_cv = full_img_cv[:, :, ::-1].copy()
            else:
                full_img_cv = cv2.cvtColor(full_img_cv, cv2.COLOR_GRAY2BGR)

            h_orig, w_orig = full_img_cv.shape[:2]

            # 2. Масштабирование (работаем с высотой 800px)
            target_h = 800.0
            scale = target_h / float(h_orig)
            w_small = int(w_orig * scale)
            h_small = int(target_h)
            
            small_img = cv2.resize(full_img_cv, (w_small, h_small), interpolation=cv2.INTER_AREA)

            # 3. Препроцессинг
            gray = cv2.cvtColor(small_img, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            edged = cv2.Canny(blurred, 75, 200)
            
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)

            # 4. Поиск контуров
            contours, _ = cv2.findContours(closed.copy(), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

            screenCnt = None

            # 5. Ищем полигон с 4 углами
            for c in contours:
                peri = cv2.arcLength(c, True)
                approx = cv2.approxPolyDP(c, 0.02 * peri, True)
                if len(approx) == 4:
                    screenCnt = approx
                    break

            # Фолбэк: Если 4 угла не нашли, берем MinAreaRect
            if screenCnt is None and len(contours) > 0:
                rect = cv2.minAreaRect(contours[0])
                box = cv2.boxPoints(rect)
                # !!! ИСПРАВЛЕНИЕ: np.int32 вместо устаревшего np.int0 !!!
                screenCnt = np.int32(box)
                # --------------------------------------------------------

            if screenCnt is None:
                logger.warning("SmartCrop: No document contour found.")
                return pil_image

            # 6. Масштабируем точки обратно к оригиналу
            screenCnt = screenCnt.reshape(4, 2)
            original_pts = screenCnt.astype("float32") / scale

            # 7. Трансформация
            warped_cv = self._four_point_transform(full_img_cv, original_pts)

            # 8. Обратно в PIL
            warped_pil = Image.fromarray(cv2.cvtColor(warped_cv, cv2.COLOR_BGR2RGB))
            
            logger.info("SmartCrop: Perspective Transform applied successfully.")
            return warped_pil

        except Exception as e:
            logger.error(f"Smart crop failed: {e}")
            return pil_image 

    def _encode_image(self, path):
        with open(path, "rb") as f: return base64.b64encode(f.read()).decode('utf-8')

    def process_and_upload(self, user_phone, local_path, original_filename):
        is_pdf = local_path.lower().endswith(".pdf")
        processed_results = []
        
        pil_images = []
        try:
            if is_pdf:
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
            
            temp_page_jpg = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}.jpg")
            img.save(temp_page_jpg, "JPEG", quality=95)
            
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

                # --- 1. ПОВОРОТ ---
                rotated_img = self._apply_clock_rotation(img, doc_data.get("top_position"))
                
                # --- 2. ОБРЕЗКА (Smart Crop) ---
                final_img = self._smart_crop(rotated_img)

                # --- СОХРАНЕНИЕ ---
                final_pdf_path = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}_final.pdf")
                
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

                # --- ЗАГРУЗКА ИСХОДНИКА ---
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