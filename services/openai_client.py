import os
import json
import logging
from openai import OpenAI

# Настройка логгера
logger = logging.getLogger(__name__)

# Инициализация клиента
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def analyze_document(base64_image, prompt_text):
    """
    Отправляет картинку в GPT-4o и возвращает JSON с данными.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "Ты - API, который возвращает ответ строго в формате JSON."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
            response_format={"type": "json_object"}, # Гарантирует JSON
            max_tokens=300,
        )

        # Парсим ответ
        result_text = response.choices[0].message.content
        return json.loads(result_text)

    except Exception as e:
        logger.error(f"OpenAI API Error: {e}")
        # Возвращаем заглушку, чтобы бот не падал
        return {"doc_type": "Ошибка распознавания", "person_name": "Неизвестный"}