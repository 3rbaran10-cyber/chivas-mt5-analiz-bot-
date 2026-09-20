import os
import io, json, base64, time, math, requests
from PIL import Image, ImageDraw
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.2-90b-vision-preview"

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Health).serve_forever()

user_charts = {}

PROMPT = """Sen dünyanın en iyi sentetik endeks trader'ısın. Deriv'in Volatility, Crash, Boom, GainX, PainX, SwitchX, TrendX, MAX GainX endekslerinde uzmanlaşmış, 10+ yıllık deneyimli profesyonelsin.

Sana 4 grafik gönderiliyor:
1. M15 - ana trend
2. M30 - orta trend
3. H1 - büyük trend
4. M1 - giriş zamanlaması

GÖREV: M15 + M30 + H1 grafiklerini analiz edip, M1 grafiği için giriş sinyali üret.

KULLANMAN GEREKEN TEKNİKLER:
- Çoklu zaman dilimi konfluens (MTF)
- Market yapısı (HH/LL, BOS, CHoCH)
- Destek/direnç, trend çizgileri
- EMA 20/50/200, RSI, MACD, Bollinger
- Fibonacci, mum formasyonları, grafik formasyonları
- Sentetik endeks özel davranışları (Crash/Boom spike, Volatility rastgele, GainX/PainX trend, SwitchX yön değişimi, TrendX yeni trend)

SADECE şu JSON formatında cevap ver, başka hiçbir şey yazma:

{
  "sembol": "GainX 999",
  "yon": "SHORT",
  "guven": 78,
  "giris": "91700.45",
  "stop_loss": "91725.00",
  "take_profit": ["91680.00", "91650.00"],
  "risk_odul": "1:2.5",
  "trend_m15": "düşüş",
  "trend_m30": "düşüş",
  "trend_h1": "yatay",
  "destekler": ["91680.00", "91650.00"],
  "direncler": ["91725.00", "91750.00"],
  "formasyonlar": ["bearish engulfing"],
  "kullanilan_teknikler": ["MTF konfluens", "RSI divergence"],
  "kisa_analiz": "2-3 cümle net özet",
  "gerekce": "Madde 1\\nMadde 2\\nMadde 3\\nMadde 4\\nMadde 5",
  "m1_tahmini": "M1'de sonraki 5-15 dakikada beklenen hareket",
  "yol_puani": [50, 45, 40, 35, 30, 25, 20],
  "uyari": "Yatırım tavsiyesi değildir."
}

yon sadece LONG, SHORT veya BEKLE olabilir. Türkçe yaz."""


def send_msg(cid, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": cid, "text": text, "parse_mode": "Markdown"},
                      timeout=10)
    except Exception as e:
        print(f"send_msg hatası: {e}", flush=True)

def send_photo(cid, photo_bytes, caption=""):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                      data={"chat_id": cid, "caption": caption[:1024]},
                      files={"photo": ("chart.png", photo_bytes)},
                      timeout=30)
    except Exception as e:
        print(f"send_photo hatası: {e}", flush=True)

def get_file_bytes(file_id):
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
                     params={"file_id": file_id}, timeout=20).json()
    url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{r['result']['file_path']}"
    return requests.get(url, timeout=30).content

def analyze_4_charts(imgs, cid):
    send_msg(cid, "🔍 DEBUG: analyze başladı")
    content = [{"type": "text", "text": PROMPT}]
    for tf in ["M15", "M30", "H1", "M1"]:
        send_msg(cid, f"🔍 DEBUG: {tf} işleniyor")
        img_small = Image.open(io.BytesIO(imgs[tf])).convert("RGB")
        img_small.thumbnail((800, 800))
        buf = io.BytesIO()
        img_small.save(buf, format="JPEG", quality=70)
        b64 = base64.b64encode(buf.getvalue()).decode()
        del img_small
        del buf
        content.append({"type": "text", "text": f"--- {tf} grafiği ---"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })
        del b64
    send_msg(cid, "🔍 DEBUG: Groq'a gönderiliyor...")
    body = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.3,
        "max_tokens": 2000
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }
    resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=90)
    send_msg(cid, f"🔍 DEBUG: Groq HTTP = {resp.status_code}")
    if resp.status_code != 200:
        send_msg(cid, f"🔍 Groq cevap: {resp.text[:300]}")
        raise Exception(f"HTTP {resp.status_code}")
    r = resp.json()
    text = r["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip().rstrip("`").strip()
    send_msg(cid, "🔍 DEBUG: Groq cevap verdi")
    return json.loads(text)

def draw_path(img_bytes, yon, puanlar):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img)
    renk = (0, 220, 0) if yon == "LONG" else (230, 30, 30) if yon == "SHORT" else (230, 200, 0)
    n = len(puanlar)
    if n < 2:
        return None
    x1, x2 = int(W * 0.75), int(W * 0.98)
    y_top, y_bot = int(H * 0.10), int(H * 0.90)
    noktalar = []
    for i, p in enumerate(puanlar):
        x = x1 + (x2 - x1) * i / (n - 1)
        y = y_bot - (p / 100.0) * (y_bot - y_top)
        noktalar.append((x, y))
    for i in range(len(noktalar) - 1):
        draw.line([noktalar[i], noktalar[i+1]], fill=renk, width=6)
    (xa, ya), (xb, yb) = noktalar[-2], noktalar[-1]
    a = math.atan2(yb - ya, xb - xa)
    s = 25
    for off in (a + math.pi * 0.85, a - math.pi * 0.85):
        draw.line([(xb, yb), (xb + s * math.cos(off), yb + s * math.sin(off))], fill=renk, width=6)
    draw.ellipse([noktalar[0][0]-6, noktalar[0][1]-6, noktalar[0][0]+6, noktalar[0][1]+6], fill=renk)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def build_card(a):
    yon_emoji = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "🟡"}
    e = yon_emoji.get(a.get("yon","BEKLE"), "⚪")
    t = []
    t.append("╔══════════════════════════╗")
    t.append(f"║  📊 {a.get('sembol','?')}")
    t.append(f"║  ⏱ M1 SENARYO")
    t.append("╠══════════════════════════╣")
    t.append(f"║  {e} YÖN: {a.get('yon','?')}")
    t.append(f"║  🎯 GÜVEN: %{a.get('guven','?')}")
    t.append("╠══════════════════════════╣")
    t.append(f"║  💰 GİRİŞ: {a.get('giris','?')}")
    t.append(f"║  🛑 SL:    {a.get('stop_loss','?')}")
    for i, tp in enumerate(a.get("take_profit", []), 1):
        t.append(f"║  ✅ TP{i}:   {tp}")
    t.append(f"║  ⚖️ R/R:   {a.get('risk_odul','?')}")
    t.append("╚══════════════════════════╝")
    t.append("")
    t.append("📈 TREND ANALİZİ")
    t.append(f"• M15: {a.get('trend_m15','?')}")
    t.append(f"• M30: {a.get('trend_m30','?')}")
    t.append(f"• H1:  {a.get('trend_h1','?')}")
    t.append("")
    t.append("🎯 SEVİYELER")
    t.append(f"🟢 Destek: {', '.join(a.get('destekler',[]))}")
    t.append(f"🔴 Direnç: {', '.join(a.get('direncler',[]))}")
    if a.get("formasyonlar"):
        t.append("")
        t.append(f"🧩 Formasyon: {', '.join(a['formasyonlar'])}")
    if a.get("kullanilan_teknikler"):
        t.append(f"🛠 Teknikler: {', '.join(a['kullanilan_teknikler'])}")
    t.append("")
    t.append("📝 ÖZET")
    t.append(a.get('kisa_analiz',''))
    t.append("")
    t.append("🔍 GEREKÇELER")
    for g in a.get("gerekce", "").split("\n"):
        if g.strip():
            t.append(f"• {g.strip()}")
    t.append("")
    t.append(f"🎯 M1 TAHMİNİ: {a.get('m1_tahmini','')}")
    t.append("")
    t.append(f"⚠️ {a.get('uyari','Yatırım tavsiyesi değildir.')}")
    return "\n".join(t)

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    print("=== BOT BAŞLADI ===", flush=True)
    offset = 0
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                             params={"offset": offset, "timeout": 30}, timeout=40).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message", {})
                cid = msg.get("chat", {}).get("id")
                if not cid:
                    continue

                if "photo" in msg:
                    fid = msg["photo"][-1]["file_id"]
                    img = get_file_bytes(fid)

                    if cid not in user_charts:
                        user_charts[cid] = {"step": 0, "imgs": {}}

                    step = user_charts[cid]["step"]
                    sirali = ["M15", "M30", "H1", "M1"]
                    if step >= 4:
                        user_charts[cid] = {"step": 0, "imgs": {}}
                        step = 0
                    tf = sirali[step]
                    user_charts[cid]["imgs"][tf] = img
                    user_charts[cid]["step"] += 1
                    kalan = 4 - user_charts[cid]["step"]

                    if kalan > 0:
                        send_msg(cid, f"✅ {tf} alındı.\n➡️ Sıradaki: *{sirali[step+1]}* ({kalan} grafik kaldı)")
                    else:
                        send_msg(cid, "⏳ 4 grafik analiz ediliyor... (30-60 sn)")
                        try:
                            a = analyze_4_charts(user_charts[cid]["imgs"], cid)
                            kart = build_card(a)
                            gorsel = draw_path(user_charts[cid]["imgs"]["M1"], a.get("yon","BEKLE"), a.get("yol_puani", []))
                            if gorsel:
                                send_photo(cid, gorsel, caption=kart)
                            else:
                                send_msg(cid, kart)
                        except Exception as e:
                            send_msg(cid, f"❌ Analiz hatası: {e}")
                        user_charts[cid] = {"step": 0, "imgs": {}}
                else:
                    send_msg(cid,
                        "📸 *MT5 Sentetik Endeks Analiz Botu*\n\n"
                        "Bana sırayla 4 grafik gönder:\n"
                        "1️⃣ M15\n"
                        "2️⃣ M30\n"
                        "3️⃣ H1\n"
                        "4️⃣ M1 (sonuncu)\n\n"
                        "Bot 4 grafiği birleştirip M1 için yön verecek. 🎯")
        except Exception as e:
            print(f"=== LOOP HATASI: {e} ===", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
