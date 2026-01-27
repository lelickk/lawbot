import base64
import os
import fitz  # Это PyMuPDF
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def encode_image(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')

def prepare_image(file_bytes, filename):
    """
    Если пришел PDF, берет первую страницу и делает из нее картинку PNG.
    Если пришла картинка - возвращает как есть.
    """
    if filename.lower().endswith(".pdf"):
        print(f"--- Обнаружен PDF: {filename}. Конвертирую первую страницу в IMG... ---")
        # Открываем PDF из памяти
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        # Берем первую страницу
        page = doc.load_page(0) 
        # Рендерим в картинку (Pixmap)
        pix = page.get_pixmap()
        # Возвращаем байты картинки в формате PNG
        return pix.tobytes("png")
    
    # Если это не PDF, возвращаем исходные байты
    return file_bytes

def analyze_document_with_ai(file_bytes, filename):
    # 1. Сначала превращаем в картинку, если надо
    image_bytes = prepare_image(file_bytes, filename)
    
    # 2. Кодируем для отправки
    base64_image = encode_image(image_bytes)

    # 3. Промпт
    prompt = """
    Ты - профессиональный юрист. Посмотри на этот документ.
    Мне нужно извлечь из него данные в формате JSON.
    
    Верни строго только JSON (без слова 'json' и кавычек ```):
    {
        "doc_type": "Тип документа (Паспорт, Справка, Заявление и т.д.)",
        "full_name": "ФИО владельца (если есть)",
        "doc_date": "Дата документа (если есть)",
        "confidence": "Твоя уверенность от 0 до 100"
    }
    Если документ нечитаемый, верни "doc_type": "unknown".
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        },
                    },
                ],
            }
        ],
        max_tokens=300,
    )

    return response.choices[0].message.content