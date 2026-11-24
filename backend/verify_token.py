
import os

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

token = os.getenv("WHATSAPP_ACCESS_TOKEN")
version = os.getenv("WHATSAPP_API_VERSION")

url = f"https://graph.facebook.com/{version}/me"

response = requests.get(
    url,
    headers={"Authorization": f"Bearer {token}"},
    params={"fields": "id,name"},
    timeout=30,
)

print("Status:", response.status_code)
print("Response:", response.json())
