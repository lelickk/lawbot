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

    def _google_vision_process(self, pil_image):
        """
        Возвращает: (processed_image, extracted_text)
        """
        extracted_text = ""
        try:
            img_byte_arr = io.BytesIO()
            pil_image.save(img_byte_arr, format='JPEG')
            content = img_byte_arr.getvalue()

            image = vision.Image(content=content)
            
            # ЗАПРОС К GOOGLE
            response = self.vision_client.document_text_detection(image=image)
            
            if response.error.message:
                logger.error(f"Google Error: {response.error.message}")
                return pil_image, ""

            # 1. Забираем полный текст
            if response.full_text_annotation:
                extracted_text = response.full_text_annotation.text

            # 2. Логика поворота (Text Orientation)
            if response.full_text_annotation.pages:
                page = response.full_text_annotation.pages[0]
                if page.blocks:
                    # Считаем угол по первому слову
                    word = page.blocks[0].paragraphs[0].words[0]
                    v = word.bounding_box.vertices
                    dx = v[1].x - v[0].x
                    dy = v[1].y - v[0].y
                    import math
                    rotation_angle = math.degrees(math.atan2(dy, dx))
                    
                    # Нормализуем угол
                    final_rotation = 0
                    if 45 <= rotation_angle < 135: final_rotation = 90
                    elif -135 < rotation_angle <= -45: final_rotation = -90
                    elif rotation_angle >= 135 or rotation_angle <= -135: final_rotation = 180
                    
                    if final_rotation != 0:
                        logger.info(f"🔄 Applying rotation {final_rotation} (Detected: {rotation_angle:.2f})")
                        if final_rotation == 90: pil_image = pil_image.rotate(90, expand=True)
                        elif final_rotation == -90: pil_image = pil_image.rotate(-90, expand=True)
                        elif final_rotation == 180: pil_image = pil_image.rotate(180, expand=True)
                        # Если повернули, координаты старого ответа Google уже не валидны для кропа.
                        # Возвращаем повернутый оригинал.
                        return pil_image, extracted_text

            # 3. Логика обрезки (Crop)
            if response.full_text_annotation:
                min_x, min_y = 10000, 10000
                max_x, max_y = 0, 0
                
                # Находим границы всего текста
                for page in response.full_text_annotation.pages:
                    for block in page.blocks:
                        v = block.bounding_box.vertices
                        for point in v:
                            min_x = min(min_x, point.x)
                            min_y = min(min_y, point.y)
                            max_x = max(max_x, point.x)
                            max_y = max(max_y, point.y)
                
                # Добавляем отступы
                pad = 30
                w_orig, h_orig = pil_image.size
                min_x = max(0, min_x - pad)
                min_y = max(0, min_y - pad)
                max_x = min(w_orig, max_x + pad)
                max_y = min(h_orig, max_y + pad)

                # --- ВАЖНАЯ ПРОВЕРКА РАЗМЕРА ---
                area_crop = (max_x - min_x) * (max_y - min_y)
                area_total = w_orig * h_orig
                ratio = area_crop / area_total

                logger.info(f"📐 Text Coverage: {ratio:.1%}")

                # Если текст занимает меньше 20% страницы (это штамп на пустом листе)
                if ratio < 0.20:
                    logger.warning(f"⚠️ Text area too small ({ratio:.1%}). Skipping crop to keep context.")
                    return pil_image, extracted_text # Возвращаем ПОЛНЫЙ лист

                # Если текст занимает почти весь лист (это скан)
                if ratio > 0.90:
                     logger.info(f"✅ Document fills page ({ratio:.1%}). Skipping crop.")
                     return pil_image, extracted_text

                # Иначе - режем (это паспорт на столе)
                logger.info(f"✂️ Google Crop: {min_x},{min_y} -> {max_x},{max_y}")
                pil_image = pil_image.crop((min_x, min_y, max_x, max_y))

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
            if is_pdf: pil_images = self._convert_pdf_to_jpg(local_path)
            else: pil_images = [self._fix_exif_orientation_pil(Image.open(local_path))]
        except Exception as e: return [{"status": "error", "message": f"Read error: {e}"}]

        if not pil_images: return [{"status": "error", "message": "No images"}]
        source_file_uploaded = False

        for i, img in enumerate(pil_images, start=1):
            page_suffix = f"_page{i}"
            temp_page_jpg = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}.jpg")
            
            try:
                # 1. Google Vision
                img, ocr_text = self._google_vision_process(img)

                # 2. Enhance & Save
                img = self._enhance_image(img)
                img.save(temp_page_jpg, "JPEG", quality=90)
                
                # 3. Classify (OpenAI) - ГИБРИДНЫЙ МЕТОД
                doc_data = {"doc_type": "Document", "person_name": "Unknown"}
                
                # Если OCR текста много (>50 символов) -> шлем текст (быстро, без цензуры)
                # Если мало (штампы) -> шлем КАРТИНКУ (надежнее для штампов)
                
                prompt = ""
                image_arg = None
                
                if ocr_text and len(ocr_text) > 50:
                    logger.info("🚀 Sending OCR Text to OpenAI")
                    prompt = f"""
                    Analyze this extracted text from a document page:
                    '''{ocr_text[:3000]}''' 
                    
                    1. Classify Type: ID_Document, Passport, Birth_Certificate, Marriage_Certificate, Divorce_Certificate, etc.
                    2. Extract Full Name (Latin). Look for "Name", "Given Name", or transliterated names.
                    
                    Return JSON: {{"doc_type": "...", "person_name": "..."}}
                    """
                    image_arg = None
                else:
                    logger.warning("⚠️ Little text found, sending IMAGE to OpenAI")
                    image_arg = self._encode_image(temp_page_jpg)
                    prompt = """
                    Classify document and extract Name (Latin).
                    JSON: {{"doc_type": "...", "person_name": "..."}}
                    """

                try:
                    res = analyze_document(image_arg, prompt)
                    if res: doc_data = res
                except Exception as e: logger.error(f"AI Classify Error: {e}")

                # 4. Save PDF & Upload
                final_pdf_path = os.path.join(self.temp_dir, f"temp_{user_phone}_p{i}.pdf")
                with open(temp_page_jpg, "rb") as f: pdf_bytes = img2pdf.convert(f.read())
                with open(final_pdf_path, "wb") as f: f.write(pdf_bytes)

                person = "".join(c for c in doc_data.get('person_name', 'Client') if c.isalnum() or c in ' _-').strip()
                base_folder = f"/Clients/{user_phone}/{person or 'Client'}"
                date_s = datetime.now().strftime("%Y-%m-%d")
                dtype = doc_data.get('doc_type', 'Doc')
                remote_filename = f"{date_s}_{dtype}{page_suffix}.pdf"
                remote_path_pdf = f"{base_folder}/{remote_filename}"

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
                logger.error(f"Process Page {i} Error: {e}")
                processed_results.append({"status": "error", "message": str(e)})
            finally:
                for p in {temp_page_jpg, final_pdf_path}:
                    if p and os.path.exists(p): os.remove(p)

        return processed_results