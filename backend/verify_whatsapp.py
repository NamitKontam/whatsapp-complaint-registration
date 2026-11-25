
import os
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("WHATSAPP_ACCESS_TOKEN")
phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
api_version = os.getenv("WHATSAPP_API_VERSION")

if not all([token, phone_id, api_version]):
    raise ValueError("Missing WhatsApp environment variables in .env")

url = f"https://graph.facebook.com/{api_version}/{phone_id}"

response = requests.get(
    url,
    headers={"Authorization": f"Bearer {token}"},
    params={
        "fields": "display_phone_number,verified_name"
    },
    timeout=30,
)

print("Status:", response.status_code)
print("Response:", response.json())
