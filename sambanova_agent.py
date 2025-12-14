import requests
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


API_KEY = os.getenv("SAMBANOVA_API_KEY")
sambanova_headers ={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
def translate_with_sambanova(prompt, source_lang="en", target_lang="fr", model="DeepSeek-V3-0324"):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    # For longer documents, split into chunks to avoid hitting token limits
    if len(prompt) > 2000:
        chunks = [prompt[i:i+2000] for i in range(0, len(prompt), 2000)]
        translations = []
        
        for chunk in chunks:
            
            formatted_prompt = f"""
        You are a professional translator. 
        Translate the following text from {source_lang} to {target_lang}.
        Provide ONLY the translation without any additional commentary.
        Text to translate:
        {prompt}
        """
            data = {
                "model": model,
                "prompt": formatted_prompt,
                "parameters": {
                    "temperature": 0.3,
                    "max_tokens": 2000
                }
            }
            
            try:
                response = requests.post("https://api.sambanova.ai/v1/completions", json=data, headers=headers)
                
                if response.status_code == 200:
                    json_response = response.json()
                    if "choices" in json_response and json_response["choices"]:
                        translations.append(json_response["choices"][0].get("text", "").strip())
                    else:
                        return "Error: No usable output in response."
                else:
                    return f"Error: {response.status_code}, {response.text}"
                
            except Exception as e:
                return f"Request failed: {str(e)}"
        
        return " ".join(translations)
    else:
        formatted_prompt = f"Translate the following text from {source_lang} to {target_lang}:\n\n{prompt}\n\nTranslation:"

        data = {
            "model": model,
            "prompt": formatted_prompt,
            "parameters": {
                "temperature": 0.3,
                "max_tokens": 2000
            }
        }

        try:
            response = requests.post("https://api.sambanova.ai/v1/completions", json=data, headers=headers)
            print("🔁 Raw response:", response.text)

            if response.status_code == 200:
                json_response = response.json()
                print("✅ Full response:", json_response)

                if "choices" in json_response and json_response["choices"]:
                    return json_response["choices"][0].get("text", "").strip()
                else:
                    return "No usable output in response."
            else:
                return f"❌ Error: {response.status_code}, {response.text}"

        except Exception as e:
            return f"⚠️ Request failed: {str(e)}"