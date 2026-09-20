import os
import io, json, base64, time, math, requests
from PIL import Image, ImageDraw
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "qwen/qwen3.8-27b"

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

PROMPT = """Sen dünyanın en iyi sentetik endeks trader'ısın. Deriv'in Crash ve Boom endekslerinde uzmanlaşmış, 15+ yıllık deneyimli profesyonelsin.

Sana TEK bir görselde 3 grafik gönderiliyor. Görsel YUKARIDAN AŞAĞIYA şu sırayla:
1. H1 (1 saatlik) - büyük trend
2. M30 (30 dakikalık) - orta trend
3. M1 (1 dakikalık) - giriş zamanlaması

KULLANICI SEMBOL ADINI VERDİ: {sembol}

GÖREV: H1 + M30 grafiklerini analiz edip, M1 grafiği için KESİN yön sinyali üret.

ÇOK ÖNEMLİ KURAL:
- Eğer analizin sonucunda güven oranın %65'in ALTINDA ise, "yon" alanına MUTLAKA "BEKLE" yaz.
- %65 ve üzeri güvende LONG veya SHORT sinyali ver.

KULLANMAN GEREKEN TÜM TEKNİKLER:
- Çoklu zaman dilimi konfluens (MTF)
- Market yapısı: HH/LL, BOS, CHoCH
- Destek/direnç seviyeleri
- Trend çizgileri, kanallar, wedge
- EMA 20/50/200
- RSI divergence
- MACD cross ve histogram
- Bollinger Band squeeze/expansion
- Fibonacci retracement
- Mum formasyonları (engulfing, pin bar, doji, hammer)
- Grafik formasyonları (üçgen, flama, OBO, çift tepe/dip)
- Hacim analizi
- CRASH/BOOM ÖZEL DAVRANIŞLARI:
  * Crash X: her X tick'te bir ani AŞAĞI spike → genelde SHORT
  * Boom X: her X tick'te bir ani YUKARI spike → genelde LONG

SADECE şu JSON formatında cevap ver, başka hiçbir şey yazma:

{{
  "sembol": "{sembol}",
  "yon": "SHORT",
  "guven": 78,
  "giris": "91700.45",
  "stop_loss": "91725.00",
  "take_profit": ["91680.00", "91650.00"],
  "risk_odul": "1:2.5",
  "trend_h1": "düşüş",
  "trend_m30": "düşüş",
  "destekler": ["91680.00", "91650.00"],
  "direncler": ["91725.00", "91750.00"],
  "formasyonlar": ["bearish engulfing"],
  "kullanilan_teknikler": ["MTF konfluens", "RSI divergence"],
  "kisa_analiz": "2-3 cümle net özet",
  "gerekce": "Madde 1\\nMadde 2\\nMadde 3\\nMadde 4\\nMadde 5",
  "m1_tahmini": "M1'de sonraki 5-15 dakikada beklenen hareket",
  "yol_puani": [50, 45, 40, 35, 30, 25, 20],
  "uyari": "Yatırım tavsiyesi değildir."
}}

KURALLAR:
- yon: SADECE "LONG", "SHORT" veya "BEKLE"
- guven: 0-100 arası tam sayı
- yol_puani: 8-10 nokta, 0=en alt, 100=en üst
- Türkçe yaz."""


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

def get_file_url(file_id):
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
                     params={"file_id": file_id}, timeout=20).json()
    return f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{r['result']['file_path']}"

def get_file_bytes(file_id):
    url = get_file_url(file_id)
    return requests.get(url, timeout=30).content

def analyze_chart(img_bytes, sembol, cid):
    send_msg(cid, f"🔍 DEBUG: {sembol} analiz ediliyor...")
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((1400, 1400))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    b64 = base64.b64encode(buf.getvalue()).decode()
    del img
    del buf

    prompt_full = PROMPT.format(sembol=sembol)
    content = [
        {"type": "text", "text": prompt_full},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
    ]
    del b64
    send_msg(cid, "🔍 DEBUG: Groq'a gönderiliyor...")
    body = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.3,
        "max_completion_tokens": 2500
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }
    resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=90)
    send_msg(cid, f"🔍 DEBUG: Groq HTTP = {resp.status_code}")
    if resp.status_code != 200:
        send_msg(cid, f"🔍 Groq cevap: {resp.text[:400]}")
        raise Exception(f"HTTP {resp.status_code}")
    r = resp.json()
    text = r["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip().rstrip("`").strip()
    send_msg(cid, "🔍 DEBUG: Groq cevap verdi")
    a = json.loads(text)
    try:
        g = int(a.get("guven", 0))
    except:
        g = 0
    if g < 65:
        a["yon"] = "BEKLE"
    return a

def merge_3_images(img_bytes_list):
    imgs = []
    for b in img_bytes_list:
        im = Image.open(io.BytesIO(b)).convert("RGB")
        im.thumbnail((900, 900))
        imgs.append(im)
    W = max(im.size[0] for im in imgs)
    H = sum(im.size[1] for im in imgs)
    combined = Image.new("RGB", (W, H), (0, 0, 0))
    y = 0
    for im in imgs:
        combined.paste(im, (0, y))
        y += im.size[1]
    combined.thumbnail((1400, 1400))
    buf = io.BytesIO()
    combined.save(buf, format="JPEG", quality=70)
    result = buf.getvalue()
    del combined
    del buf
    del imgs
    return result

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
        draw.line([noktalar[i], noktalar[i+1]], fill=renk, width=7)
    (xa, ya), (xb, yb) = noktalar[-2], noktalar[-1]
    a = math.atan2(yb - ya, xb - xa)
    s = 28
    for off in (a + math.pi * 0.85, a - math.pi * 0.85):
        draw.line([(xb, yb), (xb + s * math.cos(off), yb + s * math.sin(off))], fill=renk, width=7)
    draw.ellipse([noktalar[0][0]-7, noktalar[0][1]-7, noktalar[0][0]+7, noktalar[0][1]+7], fill=renk)
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
    if a.get("yon") != "BEKLE":
        t.append(f"║  💰 GİRİŞ: {a.get('giris','?')}")
        t.append(f"║  🛑 SL:    {a.get('stop_loss','?')}")
        for i, tp in enumerate(a.get("take_profit", []), 1):
            t.append(f"║  ✅ TP{i}:   {tp}")
        t.append(f"║  ⚖️ R/R:   {a.get('risk_odul','?')}")
    else:
        t.append("║  ⏸️  Şu an net sinyal yok")
        t.append("║  ⏳ Güven %65 altı, bekle")
    t.append("╚══════════════════════════╝")
    t.append("")
    t.append("📈 TREND ANALİZİ")
    t.append(f"• H1:  {a.get('trend_h1','?')}")
    t.append(f"• M30: {a.get('trend_m30','?')}")
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
                    caption = (msg.get("caption") or "").strip()
                    fid = msg["photo"][-1]["file_id"]
                    img_bytes = get_file_bytes(fid)

                    if cid not in user_charts:
                        user_charts[cid] = {"step": 0, "imgs": [], "sembol": None}

                    if caption:
                        user_charts[cid]["sembol"] = caption

                    step = user_charts[cid]["step"]
                    sirali = ["H1", "M30", "M1"]

                    if step >= 3:
                        user_charts[cid] = {"step": 0, "imgs": [], "sembol": None}
                        step = 0

                    tf = sirali[step]
                    user_charts[cid]["imgs"].append(img_bytes)
                    user_charts[cid]["step"] += 1
                    kalan = 3 - user_charts[cid]["step"]

                    if kalan > 0:
                        sembol_txt = f" ({user_charts[cid]['sembol']})" if user_charts[cid]["sembol"] else ""
                        send_msg(cid, f"✅ {tf}{sembol_txt} alındı.\n➡️ Sıradaki: *{sirali[step+1]}* ({kalan} grafik kaldı)")
                    else:
                        sembol = user_charts[cid]["sembol"]
                        if not sembol:
                            send_msg(cid, "⚠️ İlk fotoğrafta *caption* olarak sembol adını yazmalıydın. Sıfırlanıyor, tekrar dene.\n\nÖrnek: `Crash 600 Index`")
                            user_charts[cid] = {"step": 0, "imgs": [], "sembol": None}
                            continue
                        send_msg(cid, f"⏳ {sembol} analiz ediliyor... (30-60 sn)")
                        try:
                            merged = merge_3_images(user_charts[cid]["imgs"])
                            a = analyze_chart(merged, sembol, cid)
                            kart = build_card(a)
                            if a.get("yon") != "BEKLE":
                                gorsel = draw_path(merged, a.get("yon"), a.get("yol_puani", []))
                            else:
                                gorsel = None
                            if gorsel:
                                send_photo(cid, gorsel, caption=kart)
                            else:
                                send_msg(cid, kart)
                        except Exception as e:
                            send_msg(cid, f"❌ Analiz hatası: {e}")
                        user_charts[cid] = {"step": 0, "imgs": [], "sembol": None}
                else:
                    send_msg(cid,
                        "📸 *MT5 Crash/Boom Analiz Botu*\n\n"
                        "Kullanım (3 fotoğrafı TEK TEK gönder):\n\n"
                        "1️⃣ *H1* grafiğini gönder → caption: `Crash 600 Index`\n"
                        "2️⃣ *M30* grafiğini gönder (caption gerekmez)\n"
                        "3️⃣ *M1* grafiğini gönder (caption gerekmez)\n\n"
                        "Bot 3 grafiği birleştirip M1 için SHORT/LONG/BEKLE sinyali verecek.\n\n"
                        "⚠️ Albüm (3'lü) olarak DEĞİL, TEK TEK gönder!")
        except Exception as e:
            print(f"=== LOOP HATASI: {e} ===", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
