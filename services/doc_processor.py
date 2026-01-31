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
from services.yandex_disk import upload_file_to_disk
from services.openai_client import analyze_document

logger = logging.getLogger(__name__)

# Путь к ключу, который ты скачал
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
            # DPI=200 достаточно
            return convert_from_path(pdf_path, dpi=200)
        except Exception as e:
            logger.error(f"PDF->JPG error: {e}")
            return None

    def _google_vision_process(self, pil_image):
        """
        Магия Google:
        1. Определяет угол текста.
        2. Определяет границы текста для обрезки.
        """
        try:
            # Конвертируем в байты для отправки
            img_byte_arr = io.BytesIO()
            pil_image.save(img_byte_arr, format='JPEG')
            content = img_byte_arr.getvalue()

            image = vision.Image(content=content)
            
            # Запрашиваем детекцию текста (DOCUMENT_TEXT_DETECTION лучше для документов)
            response = self.vision_client.document_text_detection(image=image)
            
            if response.error.message:
                raise Exception(f'{response.error.message}')

            # 1. ПОВОРОТ
            # Google часто возвращает блоки с property 'detected_break' или ориентацией
            # Но самый надежный способ - посмотреть на full_text_annotation.pages[0]
            
            angle = 0
            if response.full_text_annotation.pages:
                # Берем первый блок текста и смотрим его уверенность и ориентацию
                # Однако, проще положиться на анализ основных блоков
                pass
                
            # Простой метод ориентации:
            # Анализируем bounding box всего текста. 
            # Если ширина < высоты (а текст обычно горизонтальный), значит, возможно, надо повернуть.
            # НО! Google возвращает текст уже "как есть". 
            
            # ДАВАЙ ИСПОЛЬЗОВАТЬ "TEXT_DETECTION" для определения ориентации самого большого блока
            annotation = response.full_text_annotation
            
            if not annotation:
                logger.warning("Google Vision: No text found")
                return pil_image

            # Логика поворота: Ищем средний угол наклона слов
            # Google возвращает vertices (вершины). Считаем угол по первой и второй вершине первого слова.
            
            first_page = annotation.pages[0]
            if not first_page.blocks: return pil_image
            
            # Берем первый параграф
            paragraph = first_page.blocks[0].paragraphs[0]
            word = paragraph.words[0]
            
            # Координаты вершин слова
            v = word.bounding_box.vertices
            # v[0] = Top-Left, v[1] = Top-Right
            
            dx = v[1].x - v[0].x
            dy = v[1].y - v[0].y
            
            # Вычисляем угол наклона текста
            import math
            rotation_angle = math.degrees(math.atan2(dy, dx))
            
            logger.info(f"Google Detected Text Angle: {rotation_angle:.2f}")

            # Нормализуем угол (0, 90, -90, 180)
            final_rotation = 0
            if -45 < rotation_angle < 45: final_rotation = 0
            elif 45 <= rotation_angle < 135: final_rotation = 90 # Текст идет вниз -> надо повернуть на -90 (или +270)
            elif -135 < rotation_angle <= -45: final_rotation = -90 # Текст идет вверх -> надо повернуть на +90
            else: final_rotation = 180

            # --- ОБРЕЗКА (CROP) по границам текста ---
            # Находим min_x, min_y, max_x, max_y по ВСЕМУ тексту
            min_x, min_y = 10000, 10000
            max_x, max_y = 0, 0
            
            for page in annotation.pages:
                for block in page.blocks:
                    v = block.bounding_box.vertices
                    for point in v:
                        min_x = min(min_x, point.x)
                        min_y = min(min_y, point.y)
                        max_x = max(max_x, point.x)
                        max_y = max(max_y, point.y)
            
            # Добавляем отступы (Padding)
            pad = 50
            w_orig, h_orig = pil_image.size
            
            min_x = max(0, min_x - pad)
            min_y = max(0, min_y - pad)
            max_x = min(w_orig, max_x + pad)
            max_y = min(h_orig, max_y + pad)
            
            logger.info(f"Google Crop: {min_x},{min_y} -> {max_x},{max_y}")
            
            # Сначала режем
            pil_image = pil_image.crop((min_x, min_y, max_x, max_y))
            
            # Потом крутим (если нужно)
            # Внимание: если мы режем по наклонному тексту, мы получим ромб.
            # Правильнее сначала повернуть, потом резать. Но это сложная математика.
            # Для начала просто повернем ВЕСЬ лист, если угол критический (90/180).
            
            # Переоценка стратегии:
            # 1. Если угол близок к 90/180/-90 -> вращаем ВЕСЬ оригинал.
            # 2. Потом запускаем Vision СНОВА (или режем по координатам, пересчитав их).
            # Для экономии денег (1 запрос):
            
            if final_rotation != 0:
                logger.info(f"Applying rotation {final_rotation}")
                # PIL rotate крутит против часовой. 
                # Если текст наклонен на 90 (вниз), нам надо повернуть на -90 (вернуть вверх).
                # rotation_angle ~ 90 -> текст вертикально вниз. Чтобы стало ровно, крутим на -90.
                if final_rotation == 90: pil_image = pil_image.rotate(90, expand=True) # Был тест, показал так
                elif final_rotation == -90: pil_image = pil_image.rotate(-90, expand=True)
                elif final_rotation == 180: pil_image = pil_image.rotate(180, expand=True)
                
                # После поворота координаты кропа собьются. 
                # Проще вернуть просто повернутый (но полный) документ, 
                # либо (для идеала) можно обрезать "примерно" по центру, но это риск.
                # Вернем повернутый оригинал.
                return pil_image

            # Если угол 0 (текст ровный), то просто обрезаем лишние поля стола
            return pil_image # Пока возвращаем кропнутый выше

        except Exception as e:
            logger.error(f"Google Vision Error: {e}")
            return pil_image

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
                # --- GOOGLE VISION BLOCK ---
                # 1. Вращаем и режем через Google
                img = self._google_vision_process(img)

                # 2. Enhance
                img = self._enhance_image(img)
                img.save(temp_page_jpg, "JPEG", quality=90)
                
                # 3. Classify (OpenAI)
                doc_data = {"doc_type": "Document", "person_name": "Unknown"}
                try:
                    base64_img = self._encode_image(temp_page_jpg)
                    prompt = """
                    Classify document.
                    JSON: {"doc_type": "...", "person_name": "..."}
                    """
                    res = analyze_document(base64_img, prompt)
                    if res: doc_data = res
                except Exception as e: logger.error(f"AI Classify Error: {e}")

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
                        upload_file_to_disk(local_path, remote_orig)
                        source_file_uploaded = True
                    except: pass

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