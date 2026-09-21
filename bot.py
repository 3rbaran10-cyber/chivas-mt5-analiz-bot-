import os
import io, json, base64, time, math, requests, threading
from PIL import Image, ImageDraw, ImageFont
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# GERÇEK VE GÜNCEL MODEL (404 HATASI İÇİN DÜZELTİLDİ)
GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

# ==========================================
# RENDER SAĞLIK SUNUCUSU
# ==========================================
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

# ==========================================
# YENİ NESİL XAU/USD M1 PROMPT (LONG/SHORT YOK, TAHMİN VAR)
# ==========================================
PROMPT = """Sen dünyanın en iyi XAU/USD (Altın) M1 analiz uzmanısın. 15+ yıllık deneyimli bir profesyonelsin.

Sana TEK bir XAU/USD M1 grafiği gönderiliyor.
GÖREV: Bu grafiği analiz et ve fiyatın önümüzdeki 2-5 dakikada nereye gitmesini beklediğini tahmin et.

KULLANMAN GEREKEN TÜM TEKNİKLER:
- Market yapısı: HH/LL, BOS, CHoCH
- Destek/direnç seviyeleri, Order Block, Likidite boşlukları
- EMA 20/50/200, RSI, MACD, Hacim analizi
- Mum formasyonları (engulfing, pin bar, doji, hammer)
- Fibonacci retracement

ÇOK ÖNEMLİ KURALLAR:
1. Sana verilen grafikte fiyatın nereye gideceğini tahmin etmeye çalış. Sadece "yükseliyor, o zaman yükselecek" veya "düşüyor, o zaman düşecek" deme. Bunu herkes yapabilir.
2. Fiyatın kritik bir seviyeye (destek/direnç) yaklaşıp yaklaşmadığına bak. Eğer yaklaşıyorsa ve oradan RED yiyorsa (rejection), o yöne bir hareket bekleyebilirsin.
3. Eğer net bir sinyal yoksa veya piyasa kararsızsa, "yon" alanına MUTLAKA "BEKLE" yaz. Zorla bir yön uydurma.
4. Güven oranını %50, %65, %75, %85, %95 gibi gerçekçi ve değişken aralıklarda ver. Sürekli aynı sayıyı verme.
5. KESİNLİKLE örnek JSON'daki değerleri kopyalama, grafiğe göre kendi objektif kararını ver.
6. Eğer fiyat çok hızlı yükselmişse ve "aşırı alım" (overbought) görünüyorsa, düşüş bekleyebilirsin. Ama bu her zaman doğru değildir, dikkatli ol.

SADECE şu JSON formatında cevap ver, başka hiçbir şey yazma. Örnek değerleri KOPYALAMA:

{
  "sembol": "XAU/USD",
  "yon": "YUKARI veya AŞAĞI veya BEKLE",
  "guven": 0-100 arası tam sayı,
  "beklenen_hareket": "Fiyatın nereye gitmesini bekliyorsun? (örn: 4356'dan 4362'ye yükseliş)",
  "giris": "fiyat",
  "stop_loss": "fiyat",
  "take_profit": ["fiyat1", "fiyat2"],
  "risk_odul": "1:2.0",
  "trend_m1": "yükseliş veya düşüş veya yatay",
  "destekler": ["fiyat1", "fiyat2"],
  "direncler": ["fiyat1", "fiyat2"],
  "formasyonlar": ["formasyon1"],
  "kullanilan_teknikler": ["teknik1", "teknik2"],
  "kisa_analiz": "2-3 cümle net özet",
  "gerekce": "Madde 1\\nMadde 2\\nMadde 3",
  "yol_puani": [50, 45, 60, 55, 40, 30, 20],
  "uyari": "Yatırım tavsiyesi değildir."
}

KURALLAR:
- yon: SADECE "YUKARI", "AŞAĞI" veya "BEKLE"
- yol_puani: 5-8 nokta, 0=en alt, 100=en üst. (YUKARI ise yukarı giden, AŞAĞI ise aşağı giden bir yol çiz)
- Türkçe yaz."""

# ==========================================
# YARDIMCI FONKSİYONLAR
# ==========================================
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

# ==========================================
# GEMINI ANALİZ MOTORU (GÜNCEL MODEL & 4096 TOKEN)
# ==========================================
def analyze_chart(img_bytes, cid):
    send_msg(cid, "🔍 DEBUG: XAU/USD M1 analiz ediliyor...")
    
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((800, 800))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    b64 = base64.b64encode(buf.getvalue()).decode()
    del img
    del buf

    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
            ]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,  # JSON kesilmesini önlemek için 4096 yapıldı
            "responseMimeType": "application/json"
        }
    }
    del b64
    
    send_msg(cid, "🔍 DEBUG: Gemini'ye gönderiliyor...")
    headers = {"Content-Type": "application/json"}
    
    max_deneme = 3
    for deneme in range(max_deneme):
        try:
            resp = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=90)
            send_msg(cid, f"🔍 DEBUG: Gemini HTTP = {resp.status_code}")
            
            if resp.status_code == 200:
                r = resp.json()
                text = r["candidates"][0]["content"]["parts"][0]["text"].strip()
                
                # GÜVENLİ JSON TEMİZLEME
                start_idx = text.find('{')
                end_idx = text.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    text = text[start_idx:end_idx+1]
                
                a = json.loads(text)
                try:
                    g = int(a.get("guven", 0))
                except:
                    g = 0
                    
                if g < 65:
                    a["yon"] = "BEKLE"
                    
                return a
            
            elif resp.status_code == 503:
                if deneme < max_deneme - 1:
                    bekleme_suresi = (deneme + 1) * 10 
                    send_msg(cid, f"⏳ Gemini şu an çok yoğun. {bekleme_suresi} saniye sonra tekrar denenecek... ({deneme+1}/{max_deneme})")
                    time.sleep(bekleme_suresi)
                    continue
                else:
                    send_msg(cid, "❌ Gemini sunucuları şu an aşırı yoğun. Lütfen birkaç dakika sonra tekrar deneyin.")
                    return None
            else:
                hata_detayi = resp.text[:400] 
                send_msg(cid, f"❌ Gemini Hatası: {hata_detayi}")
                return None
                
        except Exception as e:
            send_msg(cid, f"❌ Analiz Hatası: {str(e)}")
            return None

# ==========================================
# GÖRSEL PROJEKSİYON ÇİZİMİ (PIL)
# ==========================================
def draw_projection(img_bytes, yon, puanlar):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img)
    
    if yon == "YUKARI":
        renk = (0, 220, 0)
    elif yon == "AŞAĞI":
        renk = (230, 30, 30)
    else:
        return None
        
    n = len(puanlar)
    if n < 2:
        return None
        
    x1, x2 = int(W * 0.85), int(W * 0.99)
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
    
    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except:
        font = ImageFont.load_default()
        
    text = f"XAU/USD M1 | {yon} | 2 Dk Projeksiyon"
    draw.text((20, 20), text, fill=renk, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# ==========================================
# MESAJ KARTI
# ==========================================
def build_card(a):
    yon_emoji = {"YUKARI": "🟢", "AŞAĞI": "🔴", "BEKLE": "🟡"}
    e = yon_emoji.get(a.get("yon","BEKLE"), "⚪")
    t = []
    t.append("╔══════════════════════════╗")
    t.append(f"║  📊 XAU/USD M1 ANALİZİ")
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
        t.append("╠══════════════════════════╣")
        t.append(f"║  🔮 BEKLENEN: {a.get('beklenen_hareket','?')[:20]}...")
    else:
        t.append("║  ⏸️  Şu an net sinyal yok")
        t.append("║  ⏳ Güven %65 altı, bekle")
        
    t.append("╚══════════════════════════╝")
    t.append("")
    t.append("📈 TREND ANALİZİ")
    t.append(f"• M1:  {a.get('trend_m1','?')}")
    t.append("")
    t.append("🎯 SEVİYELER")
    t.append(f"🟢 Destek: {', '.join(a.get('destekler',[]))}")
    t.append(f"🔴 Direnç: {', '.join(a.get('direncler',[]))}")
    
    if a.get("formasyonlar"):
        t.append("")
        t.append(f"🧩 Formasyon: {', '.join(a['formasyonlar'])}")
        
    t.append("")
    t.append("📝 ÖZET")
    t.append(a.get('kisa_analiz',''))
    t.append("")
    t.append("🔍 GEREKÇELER")
    for g in a.get("gerekce", "").split("\\n"):
        if g.strip():
            t.append(f"• {g.strip()}")
            
    t.append("")
    t.append(f"⚠️ {a.get('uyari','Yatırım tavsiyesi değildir.')}")
    return "\n".join(t)

# ==========================================
# ANA DÖNGÜ
# ==========================================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    print(f"=== XAU/USD M1 BOTU BAŞLADI (GEMINI {GEMINI_MODEL}) ===", flush=True)
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
                    
                    if "XAU" not in caption.upper() and "GOLD" not in caption.upper():
                        send_msg(cid, "⚠️ Lütfen sadece XAU/USD grafiği gönderin ve altına `XAU/USD` yazın.")
                        continue
                        
                    fid = msg["photo"][-1]["file_id"]
                    send_msg(cid, "⏳ XAU/USD M1 grafiği alındı, analiz ediliyor... (30-60 sn)")
                    
                    try:
                        img_bytes = get_file_bytes(fid)
                        a = analyze_chart(img_bytes, cid)
                        
                        if a:
                            kart = build_card(a)
                            
                            if a.get("yon") != "BEKLE":
                                gorsel = draw_projection(img_bytes, a.get("yon"), a.get("yol_puani", []))
                            else:
                                gorsel = None
                                
                            if gorsel:
                                send_photo(cid, gorsel, caption=kart)
                            else:
                                send_msg(cid, kart)
                        else:
                            send_msg(cid, "❌ Analiz başarısız oldu, lütfen tekrar deneyin.")
                            
                    except Exception as e:
                        send_msg(cid, f"❌ Beklenmeyen Hata: {str(e)}")
                else:
                    send_msg(cid,
                        "📸 *XAU/USD M1 Analiz Botu*\n\n"
                        "Kullanım:\n"
                        "1️⃣ MT5'ten XAU/USD M1 grafiğinin ekran görüntüsünü al.\n"
                        "2️⃣ Bu fotoğrafı bota gönder.\n"
                        "3️⃣ Altına (caption) `XAU/USD` yaz.\n\n"
                        "Bot senin için en iyi teknikleri kullanarak analiz edecek, "
                        "giriş/SL/TP seviyelerini verecek ve 2 dakika sonraki tahmini grafiği çizecektir.\n\n"
                        "⚠️ Yatırım tavsiyesi değildir.")
                        
        except Exception as e:
            print(f"=== LOOP HATASI: {e} ===", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
