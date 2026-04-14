import os
import requests

TOOL_URL = os.getenv("TOOL_URL")

if not TOOL_URL:
    raise ValueError("Missing TOOL_URL environment variable")

response = requests.post(f"{TOOL_URL.rstrip('/')}/run", timeout=60)
response.raise_for_status()

print("Triggered run successfully.")