import os, json, base64, requests
from PIL import Image
import io

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Diagnostic: 3 farklı model dene, her birinin cevabını yaz
TEST_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
    "gemini-3.5-flash",
]

# Basit bir test görseli oluştur (1x1 piksel)
img = Image.new("RGB", (100, 100), color=(50, 50, 50))
buf = io.BytesIO()
img.save(buf, format="JPEG")
b64 = base64.b64encode(buf.getvalue()).decode()

payload = {
    "contents": [{
        "parts": [
            {"text": "Bu görselde ne görüyorsun? Kısa cevap ver."},
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
        ]
    }]
}

headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

for model in TEST_MODELS:
    print("=" * 60)
    print(f"🔍 TEST: {model}")
    print("=" * 60)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        print(f"HTTP Status: {resp.status_code}")
        print(f"Response Headers (önemli):")
        for k in ["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"]:
            if k in resp.headers:
                print(f"  {k}: {resp.headers[k]}")
        print(f"Response Body:")
        try:
            print(json.dumps(resp.json(), indent=2, ensure_ascii=False)[:2000])
        except:
            print(resp.text[:2000])
    except Exception as e:
        print(f"HATA: {e}")
    print()
