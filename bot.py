import os
import io, json, base64, time, math, requests
from PIL import Image, ImageDraw
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

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
1. M15 (15 dakikalık) - ana trend
2. M30 (30 dakikalık) - orta trend
3. H1 (1 saatlik) - büyük trend
4. M1 (1 dakikalık) - giriş zamanlaması

GÖREV: M15 + M30 + H1 grafiklerini analiz edip, M1 grafiği için giriş sinyali üret.

KULLANMAN GEREKEN TEKNİKLER:
- Çoklu zaman dilimi konfluens (MTF)
- Market yapısı (HH/LL, BOS, CHoCH)
- Destek/direnç seviyeleri
- Trend çizgileri ve kanallar
- EMA 20/50/200
- RSI divergence
- MACD cross
- Bollinger Band squeeze/expansion
- Fibonacci retracement
- Mum formasyonları
- Grafik formasyonları
- SENTETİK ENDEKS ÖZEL DAVRANIŞLARI:
  * Crash 1000: ~1000 tick'te bir aşağı spike
  * Boom 1000: ~1000 tick'te bir yukarı spike
  * Volatility 100/999: saf rastgele, mean reversion
  * GainX: yavaş yükseliş + ara sıra sıçrama
  * PainX: yavaş düşüş + ara sıra sıçrama
  * SwitchX: her sıçramada yön değişir
  * TrendX: sıçramada yeni trend
  * MAX GainX: büyüyen sıçrama boyutu

SADECE şu JSON formatında cevap ver:

{
  "sembol": "Volatility 100",
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
  "formasyonlar": ["bearish engulfing", "üçgen kırılımı"],
  "kullanilan_teknikler": ["MTF konfluens", "RSI divergence", "destek kırılımı"],
  "kisa_analiz": "2-3 cümle net özet",
  "gerekce": "Madde 1\\nMadde 2\\nMadde 3\\nMadde 4\\nMadde 5",
  "m1_tahmini": "M1'de sonraki 5-15 dakikada beklenen hareket: ...",
  "yol_puani": [50, 45, 40, 35, 30, 25, 20],
  "uyari": "Yatırım tavsiyesi değildir."
}

KURALLAR:
- yon: sadece LONG, SHORT veya BEKLE
- yol_puani: Grafiğin sağ tarafına çizilecek tahmini fiyat yolu. 8-10 nokta.
- Türkçe, profesyonel, net yaz."""


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
    parts = [{"text": PROMPT}]
    for tf in ["M15", "M30", "H1", "M1"]:
        send_msg(cid, f"🔍 DEBUG: {tf} işleniyor")
        img_small = Image.open(io.BytesIO(imgs[tf])).convert("RGB")
        img_small.thumbnail((600, 600))
        buf = io.BytesIO()
        img_small.save(buf, format="JPEG", quality=60)
        b64 = base64.b64encode(buf.getvalue()).decode()
        del img_small
        del buf
        parts.append({"text": f"--- {tf} grafiği ---"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        del b64
    send_msg(cid, "🔍 DEBUG: Gemini'ye gönderiliyor...")
    body = {"contents": [{"parts": parts}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    resp = requests.post(GEMINI_URL, headers=headers, json=body, timeout=90)
    send_msg(cid, f"🔍 DEBUG: Gemini HTTP = {resp.status_code}")
    if resp.status_code != 200:
        send_msg(cid, f"🔍 Gemini cevap: {resp.text[:300]}")
        raise Exception(f"HTTP {resp.status_code}")
    r = resp.json()
    if "candidates" not in r:
        send_msg(cid, f"🔍 candidates yok: {json.dumps(r, ensure_ascii=False)[:300]}")
        raise Exception("candidates yok")
    send_msg(cid, "🔍 DEBUG: Gemini cevap verdi")
    text = r["candidates"][0]["content"]["parts"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

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
    e = yon_emoji.get(a["yon"], "⚪")
    t = []
    t.append("╔══════════════════════════╗")
    t.append(f"║  📊 {a.get('sembol','?')}")
    t.append(f"║  ⏱ M1 SENARYO")
    t.append("╠══════════════════════════╣")
    t.append(f"║  {e} YÖN: {a['yon']}")
    t.append(f"║  🎯 GÜVEN: %{a['guven']}")
    t.append("╠══════════════════════════╣")
    t.append(f"║  💰 GİRİŞ: {a['giris']}")
    t.append(f"║  🛑 SL:    {a['stop_loss']}")
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
                            gorsel = draw_path(user_charts[cid]["imgs"]["M1"], a["yon"], a.get("yol_puani", []))
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
