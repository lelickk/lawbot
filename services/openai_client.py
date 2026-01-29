import os
import json
import logging
from openai import OpenAI

# Настройка логгера
logger = logging.getLogger(__name__)

# УБИРАЕМ глобальную инициализацию client = ...

def analyze_document(base64_image, prompt_text):
    """
    Отправляет картинку в GPT-4o и возвращает JSON с данными.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OpenAI API Key is missing!")
        return {"doc_type": "Ошибка настройки", "person_name": "Нет ключа API"}

    # Инициализируем клиента ТОЛЬКО когда он нужен
    client = OpenAI(api_key=api_key)

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
            response_format={"type": "json_object"},
            max_tokens=300,
        )

        result_text = response.choices[0].message.content
        return json.loads(result_text)

    except Exception as e:
        logger.error(f"OpenAI API Error: {e}")
        return {"doc_type": "Ошибка ИИ", "person_name": "Неизвестный"}