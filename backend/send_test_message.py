
import os

import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("WHATSAPP_ACCESS_TOKEN")
phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
api_version = os.getenv("WHATSAPP_API_VERSION")
recipient = os.getenv("WHATSAPP_TEST_RECIPIENT")

if not all([token, phone_id, api_version, recipient]):
    raise ValueError("Missing required environment variables")

url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"

payload = {
    "messaging_product": "whatsapp",
    "to": recipient,
    "type": "template",
    "template": {
        "name": "hello_world",
        "language": {"code": "en_US"}
    }
}

response = requests.post(
    url,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    },
    json=payload,
    timeout=30
)

print("Status:", response.status_code)
print("Response:", response.json())
