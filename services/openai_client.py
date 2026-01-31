import os
import json
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def analyze_document(image_base64, prompt_text):
    """
    Если image_base64 передан -> используем Vision (GPT-4o).
    Если image_base64 is None -> используем Text (GPT-4o-mini), это дешевле и нет цензуры на картинки.
    """
    try:
        messages = []
        
        if image_base64:
            # Режим Vision (Картинка + Текст)
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                        },
                    ],
                }
            ]
            model = "gpt-4o"
        else:
            # Режим Текст (Только промпт)
            messages = [
                {"role": "system", "content": "You are a helpful JSON parser."},
                {"role": "user", "content": prompt_text}
            ]
            model = "gpt-4o-mini" # Дешево и быстро для текста

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=300,
            response_format={"type": "json_object"} # Форсируем JSON
        )

        content = response.choices[0].message.content
        logger.info(f"🤖 RAW AI RESPONSE: {content}")

        return json.loads(content)

    except Exception as e:
        logger.error(f"OpenAI Error: {e}")
        return None