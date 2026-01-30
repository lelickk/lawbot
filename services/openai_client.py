import os
import json
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

def analyze_document(base64_image, system_prompt):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY is missing")
        return None

    client = OpenAI(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": system_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
            max_tokens=1000,
            temperature=0.0, # Максимальная строгость
        )

        content = response.choices[0].message.content
        
        # --- ВАЖНО: Логируем сырой ответ, чтобы видеть ошибки ---
        logger.info(f"🤖 RAW AI RESPONSE: {content}")

        if not content:
            logger.error("OpenAI returned empty content")
            return None

        # --- ЧИСТКА ОТВЕТА ---
        # 1. Убираем Markdown обертки (```json ... ```)
        cleaned_content = content.replace("```json", "").replace("```", "").strip()
        
        # 2. Если GPT написал вступление, ищем первую скобку {
        start_index = cleaned_content.find("{")
        end_index = cleaned_content.rfind("}")
        
        if start_index != -1 and end_index != -1:
            cleaned_content = cleaned_content[start_index : end_index + 1]

        # 3. Парсим
        return json.loads(cleaned_content)

    except json.JSONDecodeError as e:
        logger.error(f"JSON Parsing Error: {e}. Content was: {content}")
        return None
    except Exception as e:
        logger.error(f"OpenAI General Error: {e}")
        return None