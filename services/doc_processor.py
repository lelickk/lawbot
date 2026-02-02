import os
import io
import logging
import base64
import img2pdf
import cv2
import numpy as np
from datetime import datetime
from PIL import Image, ImageOps, ImageEnhance
from google.cloud import vision
from pdf2image import convert_from_path
from services.storage import upload_file_to_cloud
from services.openai_client import analyze_document

logger = logging.getLogger(__name__)

if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "google_credentials.json"

class DocumentProcessor:
    def __init__(self):
        self.temp_dir = "temp_files"
        os.makedirs(self.temp_dir, exist_ok=True)
        self.vision_client = vision.ImageAnnotatorClient()

    def _fix_exif_orientation_pil(self, img):
        try: return ImageOps.exif_transpose(img)
        except: return img

    def _convert_pdf_to_jpg(self, pdf_path):
        try:
            return convert_from_path(pdf_path, dpi=200)
        except Exception as e:
            logger.error(f"PDF->JPG error: {e}")
            return None

    def _google_vision_process(self, pil_image, is_retry=False):
        """
        Финальная версия для Демо:
        1. Поворот (Rotation).
        2. Безопасная обрезка (Safe Crop): если документ занимает < 20% кадра, отдаем оригинал.
        """
        extracted_text = ""
        try:
            img_byte_arr = io.BytesIO()
            pil_image.save(img_byte_arr, format='JPEG')
            content = img_byte_arr.getvalue()
            image = vision.Image(content=content)
            
            # --- 1. GOOGLE VISION ---
            response = self.vision_client.document_text_detection(image=image)
            
            if response.error.message:
                logger.error(f"Google Error: {response.error.message}")
                return pil_image, ""

            if response.full_text_annotation:
                extracted_text = response.full_text_annotation.text

            # --- 2. ROTATION ---
            if not is_retry and response.full_text_annotation.pages:
                page = response.full_text_annotation.pages[0]
                if page.blocks:
                    word = page.blocks[0].paragraphs[0].words[0]
                    v = word.bounding_box.vertices
                    dx = v[1].x - v[0].x
                    dy = v[1].y - v[0].y
                    import math
                    rotation_angle = math.degrees(math.atan2(dy, dx))
                    
                    final_rotation = 0
                    if 45 <= rotation_angle < 135: final_rotation = 90
                    elif -135 < rotation_angle <= -45: final_rotation = -90
                    elif rotation_angle >= 135 or rotation_angle <= -135: final_rotation = 180
                    
                    if final_rotation != 0:
                        logger.info(f"🔄 Rotation needed: {final_rotation}")
                        if final_rotation == 90: pil_image = pil_image.rotate(90, expand=True)
                        elif final_rotation == -90: pil_image = pil_image.rotate(-90, expand=True)
                        elif final_rotation == 180: pil_image = pil_image.rotate(180, expand=True)
                        return self._google_vision_process(pil_image, is_retry=True)

            # --- 3. SAFE CROP (Aggressive Union) ---
            if response.full_text_annotation:
                blocks = []
                for page in response.full_text_annotation.pages:
                    for block in page.blocks:
                        v = block.bounding_box.vertices
                        min_x = min(p.x for p in v)
                        min_y = min(p.y for p in v)
                        max_x = max(p.x for p in v)
                        max_y = max(p.y for p in v)
                        blocks.append({'box': (min_x, min_y, max_x, max_y)})

                if not blocks:
                    return pil_image, extracted_text

                # Вместо кластеров просто ищем крайние точки ВСЕГО текста на странице
                w_orig, h_orig = pil_image.size
                
                final_min_x = w_orig
                final_min_y = h_orig
                final_max_x = 0
                final_max_y = 0

                for b in blocks:
                    box = b['box']
                    final_min_x = min(final_min_x, box[0])
                    final_min_y = min(final_min_y, box[1])
                    final_max_x = max(final_max_x, box[2])
                    final_max_y = max(final_max_y, box[3])

                # Добавляем отступы (Padding)
                pad = 30
                final_min_x = max(0, final_min_x - pad)
                final_min_y = max(0, final_min_y - pad)
                final_max_x = min(w_orig, final_max_x + pad)
                final_max_y = min(h_orig, final_max_y + pad)

                area_crop = (final_max_x - final_min_x) * (final_max_y - final_min_y)
                ratio = area_crop / (w_orig * h_orig)

                # --- ГЛАВНАЯ ЗАЩИТА ДЛЯ ДЕМО ---
                # Если обрезанная часть составляет менее 15% от всего фото,
                # значит мы скорее всего вырезали только кусок текста, а не документ.
                # В таком случае ОТМЕНЯЕМ кроп и отдаем полный кадр.
                if ratio < 0.15:
                    logger.warning(f"🛡️ SAFE CROP: Detected small area ({ratio:.1%}). Returning FULL IMAGE to be safe.")
                    return pil_image, extracted_text
                
                logger.info(f"✂️ SAFE CROP: Applied. Ratio {ratio:.1%}")
                pil_image = pil_image.crop((final_min_x, final_min_y, final_max_x, final_max_y))

            return pil_image, extracted_text

        except Exception as e:
            logger.error(f"Google Vision Error: {e}")
            return pil_image, extracted_text

    def _enhance_image(self, pil_image):
        enhancer = ImageEnhance.Contrast(pil_image)
        pil_image = enhancer.enhance(1.2)
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
                pil_images = [self._fix_exif_orientation_pil(Image.open(local_path))]
        except Exception as e: return [{"status": "error", "message": f"Read error: {e}"}]

        if not pil_images: return [{"status": "error", "message": "No images"}]
        source_file_uploaded = False

        for i, img in enumerate(pil_images, start=1):
            page_suffix = f"_page{i}"
            temp_page_jpg = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}.jpg")
            
            try:
                # 1. Processing
                img, ocr_text = self._google_vision_process(img)

                # 2. Enhance & Save
                img = self._enhance_image(img)
                img.save(temp_page_jpg, "JPEG", quality=90)
                
                # 3. AI Classification
                doc_data = {"doc_type": "Document", "person_name": "Unknown"}
                prompt = ""
                image_arg = None
                
                if ocr_text and len(ocr_text) > 50:
                    prompt = f"""
                    Analyze text (Document Page):
                    '''{ocr_text[:3000]}''' 
                    1. Type (Passport, ID, Marriage, Birth, etc.)
                    2. Name (Latin)
                    JSON: {{"doc_type": "...", "person_name": "..."}}
                    """
                else:
                    image_arg = self._encode_image(temp_page_jpg)
                    prompt = """Classify & Extract Name. JSON: {{"doc_type": "...", "person_name": "..."}}"""

                try:
                    res = analyze_document(image_arg, prompt)
                    if res: doc_data = res
                except Exception as e: logger.error(f"AI Error: {e}")

                # 4. Save PDF
                final_pdf_path = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}.pdf")
                with open(temp_page_jpg, "rb") as f: pdf_bytes = img2pdf.convert(f.read())
                with open(final_pdf_path, "wb") as f: f.write(pdf_bytes)

                person = "".join(c for c in doc_data.get('person_name', 'Client') if c.isalnum() or c in ' _-').strip()
                base_folder = f"/Clients/{user_phone}/{person or 'Client'}"
                date_s = datetime.now().strftime("%Y-%m-%d")
                dtype = doc_data.get('doc_type', 'Doc')
                remote_filename = f"{date_s}_{dtype}{page_suffix}.pdf"
                remote_path_pdf = f"{base_folder}/{remote_filename}"

                # Original Upload (once per file)
                if not source_file_uploaded:
                    orig_ext = os.path.splitext(local_path)[1] or ".jpg"
                    remote_orig = f"{base_folder}/Originals/{date_s}_{dtype}_Source_orig{orig_ext}"
                    try:
                        upload_file_to_cloud(local_path, remote_orig)
                        source_file_uploaded = True
                    except: pass

                if upload_file_to_cloud(final_pdf_path, remote_path_pdf):
                    processed_results.append({
                        "status": "success", "doc_type": dtype, "person": person, 
                        "filename": remote_filename, "remote_path": remote_path_pdf
                    })
                else:
                    processed_results.append({"status": "error", "message": "Upload failed"})

            except Exception as e:
                logger.error(f"Page {i} Error: {e}")
                processed_results.append({"status": "error", "message": str(e)})
            finally:
                for p in {temp_page_jpg, final_pdf_path}:
                    if p and os.path.exists(p): os.remove(p)

        return processed_results