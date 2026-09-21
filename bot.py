import os
import io, json, base64, time, math, requests, threading
from PIL import Image, ImageDraw, ImageFont
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# AYARLAR (Render'daki Environment Variables)
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq'un GÜNCEL ve ÖNERİLEN görsel destekli modeli:
GROQ_MODEL = "qwen/qwen3.8-27b" 

# ==========================================
# RENDER UYANIK KALSIN DİYE SAĞLIK SUNUCUSU
# ==========================================
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def logap_message(self, *aıs):
        pass

def run_health_server():
    port = int(osı.environ.get("PORT", 10000:))
    HTTPServer(("0.0.0.0", port), Health).serve_forever()

# ==========================================
# XAU/USD M1 İÇİN ÖZEL PROMPT (Yapay Zeka Tembelliği Kırıldı)
# ==========================================
PROMPT = """Sen dünyanın en iyi XAU/USD (Altın) M1 scalping uzmanısın. 15+ yıllık deneyimli bir profesyonelsin.

Sana TEK bir XAU/USD M1 grafiği gönderiliyor.
GÖREV: Bu grafiği analiz et ve sonraki 2 dakikalık (2 mumluk) fiyat projeksiyonunu tahmin et.

KULLANMAN GEREKEN TÜM TEKNİKLER:
- Market y HH/LL, BOS, CHoCH
- Destek/direnç seviyeleri, Order Block, Likidite boşlukları
- EMA 20/50/200, RSI, MACD, Hacim analizi
- Mum formasyonları (engulfing, pin bar, doji, hammer)
- Fibonacci retracement

ÇOK ÖNEMLİ KURALLAR:
1. Eğer güven oranın %65'in ALTINDA ise, "yon" alanına MUTLAKA "BEKLE" yaz.
2. %65 ve üzeri güvende LONG veya SHORT sinyali ver.
3. KESİNLİKLE örnek JSON'daki değerleri kopyalama, grafiğe göre kendi objektif kararını ver.

SADECE şu JSON formatında cevap ver, başka hiçbir şey yazma. Örnek değerleri KOPYALAMA:

{
  "sembol": "XAU/USD",
  "yon": "LONG veya SHORT veya BEKLE",
  "guven": 0-100 arası tam sayı,
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
- yon: SADECE "LONG", "SHORT" veya "BEKLE"
- yol_puani: 5-8 nokta, 0=en alt, 100=en üst. (LONG ise yukarı giden, SHORT ise aşağı giden bir yol çiz)
- Türkçe yaz."""

# ==========================================
# TELEGRAM YARDIMCI FONKSİYONLARI
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
# GROQ ANALİZ MOTORU (Hata Düzeltildi)
# ==========================================
def analyze_chart(img_bytes, cid):
    send_msg(cid, "🔍 DEBUG: XAU/USD M1 analiz ediliyor...")
    
    # Görseli optimize et (Bellek ve hız için)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((800, 800)) # Boyutu küçültüldü
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70) # Kalite düşürüldü
    b64 = base64.b64encode(buf.getvalue()).decode()
    del img
    del buf

    content = [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
    ]
    del b64
    
    send_msg(cid, "🔍 DEBUG: Groq'a gönderiliyor...")
    
    # İstek gövdesi Groq API'sine uygun hale getirildi
    body = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 1500  # max_completion_tokens yerine max_tokens kullanıldı
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }
    
    try:
        resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=90)
        send_msg(cid, f"🔍 DEBUG: Groq HTTP = {resp.status_code}")
        
        if resp.status_code != 200:
            # Hata detayını Telegram'a gönder ki ne olduğunu görelim
            hata_detayi = resp.text[:400] 
            send_msg(cid, f"❌ Groq Hatası: {hata_detayi}")
            return None
            
        r = resp.json()
        text = r["choices"][0]["message"]["content"].strip()
        
        # JSON temizleme
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip().rstrip("`").strip()
        
        a = json.loads(text)
        try:
            g = int(a.get("guven", 0))
        except:
            g = 0
            
        if g < 65:
            a["yon"] = "BEKLE"
            
        return a
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
    
    # Yön ve Renk Belirleme (Metin ile Görsel Uyumu)
    if yon == "LONG":
        renk = (0, 220, 0) # Yeşil
    elif yon == "SHORT":
        renk = (230, 30, 30) # Kırmızı
    else:
        return None # BEKLE durumunda çizim yapma
        
    n = len(puanlar)
    if n < 2:
        return None
        
    # Grafiğin sağ tarafına projeksiyon çizimi (Gelecek 2 dakika)
    x1, x2 = int(W * 0.85), int(W * 0.99)
    y_top, y_bot = int(H * 0.10), int(H * 0.90)
    
    noktalar = []
    for i, p in enumerate(puanlar):
        x = x1 + (x2 - x1) * i / (n - 1)
        y = y_bot - (p / 100.0) * (y_bot - y_top)
        noktalar.append((x, y))
        
    # Çizgiyi çiz
    for i in range(len(noktalar) - 1):
        draw.line([noktalar[i], noktalar[i+1]], fill=renk, width=6)
        
    # Ok başı ekle
    (xa, ya), (xb, yb) = noktalar[-2], noktalar[-1]
    a = math.atan2(yb - ya, xb - xa)
    s = 25
    for off in (a + math.pi * 0.85, a - math.pi * 0.85):
        draw.line([(xb, yb), (xb + s * math.cos(off), yb + s * math.sin(off))], fill=renk, width=6)
        
    # Başlangıç noktasına daire
    draw.ellipse([noktalar[0][0]-6, noktalar[0][1]-6, noktalar[0][0]+6, noktalar[0][1]+6], fill=renk)
    
    # Görselin üstüne metin ekleme
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
# TELEGRAM MESAJ KARTI OLUŞTURMA
# ==========================================
def build_card(a):
    yon_emoji = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "🟡"}
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
# ANA DÖNGÜ (TELEGRAM POLLING)
# ==========================================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    print("=== XAU/USD M1 BOTU BAŞLADI ===", flush=True)
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
                    
                    # Sadece XAU/USD yazılıysa analiz et
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
                            
                            # Eğer işlem sinyali varsa görsel projeksiyon çiz
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
