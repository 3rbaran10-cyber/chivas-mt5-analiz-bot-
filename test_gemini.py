import os, requests

API_KEY = os.environ.get("GEMINI_API_KEY")

print("=" * 60)
print(f"API KEY VAR MI?: {'EVET' if API_KEY else 'HAYIR'}")
print("=" * 60, flush=True)

ADAYLAR = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-exp",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-3-pro-preview",
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-pro-latest",
    "gemini-flash-latest",
]

payload = {"contents": [{"parts": [{"text": "Merhaba, 1+1 kaç?"}]}]}

for model in ADAYLAR:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        r = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
            json=payload,
            timeout=20
        )
        detay = ""
        if r.status_code != 200:
            try:
                detay = r.json().get("error", {}).get("message", "")[:200]
            except:
                detay = r.text[:200]
        else:
            try:
                detay = r.json()["candidates"][0]["content"]["parts"][0]["text"][:60]
            except:
                detay = "(cevap parse edilemedi)"

        print(f"{r.status_code} | {model} | {detay}", flush=True)
    except Exception as e:
        print(f"ERR | {model} | {str(e)[:150]}", flush=True)

print("=" * 60, flush=True)
print("BITTI", flush=True)
