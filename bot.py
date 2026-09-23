import os
import io, json, base64, time, math, requests, threading, re, sqlite3
from PIL import Image, ImageDraw, ImageFont
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

GEMINI_MODEL = "gemini-3.1-pro-preview"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# ==========================================
# DESTEKLENEN SEMBOLLER (SABİT LİSTE)
# ==========================================
ALLOWED_SYMBOLS = [
    "MAX PainX 1000",
    "MAX GainX 2000",
    "MAX PainX 2000",
    "PainX 1200",
    "MAX GainX 1000",
    "PainX 999",
    "GainX 999",
    "PainX 600",
    "PainX 400",
    "PainX 800",
    "GainX 800",
    "GainX 600",
    "TrendX 1800",
    "BreakX 1800",
    "SwitchX 1800",
    "GainX 1200",
    "BreakX 1200",
    "TrendX 1200",
    "SwitchX 1200",
    "BreakX 600",
]

ALLOWED_SYMBOLS_NORM = {
    s.lower().replace("-", " ").replace("/", " ").strip(): s for s in ALLOWED_SYMBOLS
}

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
# ANALİZ PROMPT (DİNAMİK SEMBOL)
# ==========================================
PROMPT_TEMPLATE = """Sen dünyanın en iyi {sembol} analiz uzmanısın. 15+ yıllık deneyimli profesyonelsin. Smart Money konseptlerini (ICT) derinlemesine bilirsin.

Sana TEK bir {sembol} M1 grafiği gönderiliyor.
GÖREV: Grafiği analiz et ve sonraki 2 dakikalık fiyat projeksiyonunu tahmin et.
EK GÖREV: Fiyatın hangi yönde kaç mum daha devam edeceğini de tahmin et.
Örnek: "Gain düşüyor, 4-6 mum daha düşebilir" veya "Pain yükseliyor, 3-5 mum daha yükselebilir"

KULLANILACAK TEKNİKLER:
- Market yapısı: HH/LL, BOS, CHoCH
- Destek/direnç, Order Block, Supply/Demand
- VWAP (ekranda görünüyorsa), FVG, Liquidity Sweep
- EMA 20/50/200, RSI, MACD, Hacim
- Mum formasyonları (engulfing, pin bar, doji, hammer)
- Fibonacci retracement

PUAN BİRİMİ (ÇOK ÖNEMLİ):
Bunlar sentetik endeks sembolleridir (PainX, GainX, TrendX, BreakX, SwitchX).
1 puan = 1.00 fiyat birimi harekettir. Örnek: 104311.468 -> 104312.468 = 1 puan.
Bu semboller 100.000 civarında işlem görür, bu yüzden SL/TP mesafeleri büyük olmalıdır.

SEVİYE KURALLARI (HİBRİT SİSTEM):
1. ÖNCE en yakın destek (LONG) veya direnç (SHORT) seviyesini bul.
2. SL'yi bu seviyenin biraz ötesine koy.
3. Minimum SL mesafesi bu sentetik endekslerde en az 500 puan (500.00) olmalıdır.
   Tipik SL mesafesi 800 - 2000 puan arasıdır.
   Daha dar ASLA olmaz.
4. Minimum TP1 mesafesi SL'nin 1.5 katı, TP2 2.5 katı olmalı.
   Yani SL 1000 puan ise TP1 ~1500 puan, TP2 ~2500 puan olmalı.
5. Spread ve gürültüyü hesaba kat.
6. R/R 1:1.5'in altındaysa 'BEKLE' yaz.

KARAR KURALLARI:
1. Güven %65'in altındaysa 'yon' = 'BEKLE'.
2. %65+ güvende LONG veya SHORT.
3. Güven oranını değişken ver (%50, %65, %75, %85, %95 gibi), sürekli aynı sayıyı verme.
4. VWAP/FVG/Likidite grafikte net görünmüyorsa 'belirsiz' yaz, UYDURMA.

MUM SAYISI KURALLARI (ÇOK ÖNEMLİ):
1. Mevcut trendin gücüne göre kaç mum daha devam edeceğini tahmin et.
2. Güçlü trend + hacim artışı = 5-10 mum
3. Zayıf trend + hacim düşüşü = 2-4 mum
4. Yatay/kararsız = 1-3 mum
5. Sadece TAHMİN ver, kesinlik iddia etme.
6. hareket_aciklamasi alanı MUTLAKA "Sembol + yön + mum sayısı" içersin.
   Örnek: "GainX 800 4-6 mum daha düşebilir"
   Örnek: "PainX 1200 3-5 mum daha yükselebilir"

FORMAT KURALLARI:
1. SADECE VE SADECE JSON formatında cevap ver.
2. Sayılarda NOKTA kullan (104311.468), VİRGÜL kullanma.
3. Cevabın MUTLAKA tam ve geçerli JSON olmalı.

JSON ŞEMASI (SADECE BU ALANLARI DOLDUR):
- sembol: "{sembol}"
- yon: "LONG" | "SHORT" | "BEKLE"
- guven: 0-100 tam sayı
- giris: "fiyat"
- stop_loss: "fiyat"
- take_profit: ["fiyat1", "fiyat2"]
- risk_odul: "1:2.0"
- trend_m1: "yükseliş" | "düşüş" | "yatay"
- vwap_durumu: "fiyat VWAP üstünde" | "VWAP altında" | "belirsiz"
- fvg_tespit: "açıklama" | "yok" | "belirsiz"
- likidite_durumu: "açıklama" | "yok" | "belirsiz"
- destekler: ["fiyat1", "fiyat2"]
- direncler: ["fiyat1", "fiyat2"]
- formasyonlar: ["formasyon1"]
- kullanilan_teknikler: ["teknik1", "teknik2"]
- kisa_analiz: "2-3 cümle net özet"
- gerekce: "Madde 1\\nMadde 2\\nMadde 3"
- yol_puani: [50, 45, 60, 55, 40, 30, 20]
- kalan_mum: 0-20 arası tam sayı (kaç mum daha bu yönde devam eder)
- mum_yonu: "yükseliş" | "düşüş" | "yatay"
- hareket_aciklamasi: "Sembol + yön + kaç mum" formatında kısa cümle
- sonraki_hamle: "Bu hareket bittikten sonra ne olur" (1 cümle)
- uyari: "Mum sayısı tahminidir, kesinlik içermez. Yatırım tavsiyesi değildir."

Türkçe yaz."""

# ==========================================
# KALICI OFFSET (SQLite)
# ==========================================
DB_PATH = "/tmp/telegram_offset.db"

def init_offset_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER)")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB init hatası: {e}", flush=True)

def get_offset():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT value FROM state WHERE key='offset'")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else 0
    except:
        return 0

def save_offset(offset):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('offset', ?)", (offset,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Offset kaydetme hatası: {e}", flush=True)

# ==========================================
# YARDIMCI FONKSİYONLAR
# ==========================================
def send_msg(cid, text, parse_mode=None):
    try:
        payload = {"chat_id": cid, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json=payload, timeout=10)
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

def temizle_sayi(deger):
    if deger is None:
        return deger
    s = str(deger).strip()
    if ',' in s and '.' not in s:
        s = s.replace(',', '.')
    return s

def validate_yol_puani(puanlar):
    if not isinstance(puanlar, list):
        return None
    temiz = []
    for p in puanlar:
        try:
            p = float(str(p).replace(',', '.'))
            if 0 <= p <= 100:
                temiz.append(p)
        except (ValueError, TypeError):
            continue
    return temiz if len(temiz) >= 2 else None

def caption_to_symbol(caption):
    """Caption'dan desteklenen sembollerden birini çıkarır."""
    if not caption:
        return None
    norm = caption.lower().replace("-", " ").replace("/", " ").strip()
    norm = re.sub(r"\s+", " ", norm)

    for key, orijinal in ALLOWED_SYMBOLS_NORM.items():
        if norm == key or norm.startswith(key + " ") or norm.endswith(" " + key) or (" " + key + " ") in (" " + norm + " "):
            return orijinal

    for key, orijinal in ALLOWED_SYMBOLS_NORM.items():
        if key in norm:
            return orijinal

    return None

# ==========================================
# RATE LIMITING
# ==========================================
USER_COOLDOWN = {}
RATE_LIMIT_SECONDS = 10

def check_rate_limit(cid):
    now = time.time()
    last = USER_COOLDOWN.get(cid, 0)
    if now - last < RATE_LIMIT_SECONDS:
        kalan = int(RATE_LIMIT_SECONDS - (now - last))
        send_msg(cid, f"⏳ Çok hızlı gönderiyorsunuz. {kalan} saniye bekleyin.")
        return False
    USER_COOLDOWN[cid] = now
    return True

# ==========================================
# GEMINI ANALİZ MOTORU
# ==========================================
def analyze_chart(img_bytes, cid, sembol):
    print(f"🔍 DEBUG: Analiz başladı (Model: {GEMINI_MODEL}, Sembol: {sembol})", flush=True)
    
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((1400, 1400))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    b64 = base64.b64encode(buf.getvalue()).decode()
    del img
    del buf

    prompt = PROMPT_TEMPLATE.format(sembol=sembol)

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
            ]
        }],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4000,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "sembol": {"type": "string"},
                    "yon": {"type": "string", "enum": ["LONG", "SHORT", "BEKLE"]},
                    "guven": {"type": "integer"},
                    "giris": {"type": "string"},
                    "stop_loss": {"type": "string"},
                    "take_profit": {"type": "array", "items": {"type": "string"}},
                    "risk_odul": {"type": "string"},
                    "trend_m1": {"type": "string"},
                    "vwap_durumu": {"type": "string"},
                    "fvg_tespit": {"type": "string"},
                    "likidite_durumu": {"type": "string"},
                    "destekler": {"type": "array", "items": {"type": "string"}},
                    "direncler": {"type": "array", "items": {"type": "string"}},
                    "formasyonlar": {"type": "array", "items": {"type": "string"}},
                    "kullanilan_teknikler": {"type": "array", "items": {"type": "string"}},
                    "kisa_analiz": {"type": "string"},
                    "gerekce": {"type": "string"},
                    "yol_puani": {"type": "array", "items": {"type": "number"}},
                    "kalan_mum": {"type": "integer"},
                    "mum_yonu": {"type": "string"},
                    "hareket_aciklamasi": {"type": "string"},
                    "sonraki_hamle": {"type": "string"},
                    "uyari": {"type": "string"}
                },
                "required": ["sembol", "yon", "guven", "kisa_analiz", "gerekce", "yol_puani",
                             "kalan_mum", "mum_yonu", "hareket_aciklamasi"]
            }
        }
    }
    del b64
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }
    
    max_deneme = 3
    for deneme in range(max_deneme):
        try:
            resp = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=180)
            print(f"🔍 DEBUG: Gemini HTTP = {resp.status_code}", flush=True)
            
            if resp.status_code == 200:
                r = resp.json()
                try:
                    text = r["candidates"][0]["content"]["parts"][0]["text"].strip()
                except (KeyError, IndexError):
                    send_msg(cid, "❌ Gemini boş cevap döndü.")
                    return None
                
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    parts = text.split("```")
                    if len(parts) >= 2:
                        text = parts[1]
                        if text.startswith("json"):
                            text = text[4:]
                text = text.strip()

                a = None
                try:
                    a = json.loads(text)
                except json.JSONDecodeError as e:
                    print(f"JSON Hatası, kurtarma: {e}", flush=True)
                    bas = text.find('{')
                    son = text.rfind('}')
                    if bas != -1 and son != -1 and son > bas:
                        try:
                            a = json.loads(text[bas:son+1])
                        except Exception as e2:
                            print(f"Kurtarma başarısız: {e2}", flush=True)
                            send_msg(cid, "❌ Gemini cevabı bozuk JSON. Tekrar deneyin.")
                            return None
                    else:
                        send_msg(cid, "❌ Gemini cevabında JSON yok. Tekrar deneyin.")
                        return None
                
                for k in ["giris", "stop_loss"]:
                    if k in a:
                        a[k] = temizle_sayi(a[k])
                if "take_profit" in a and isinstance(a["take_profit"], list):
                    a["take_profit"] = [temizle_sayi(x) for x in a["take_profit"]]
                
                try:
                    g = int(a.get("guven", 0))
                except:
                    g = 0
                if g < 65:
                    a["yon"] = "BEKLE"
                    
                return a
            
            elif resp.status_code == 503:
                if deneme < max_deneme - 1:
                    bekleme = (deneme + 1) * 10
                    send_msg(cid, f"⏳ Gemini yoğun. {bekleme} sn sonra tekrar... ({deneme+1}/{max_deneme})")
                    time.sleep(bekleme)
                    continue
                else:
                    send_msg(cid, "❌ Gemini şu an aşırı yoğun. Sonra deneyin.")
                    return None
            elif resp.status_code == 429:
                send_msg(cid, "⚠️ Çok fazla istek. 30 sn bekleyin.")
                time.sleep(30)
                continue
            else:
                hata = resp.text[:400]
                send_msg(cid, f"❌ Gemini Hatası ({resp.status_code}): {hata}")
                return None
                
        except Exception as e:
            send_msg(cid, f"❌ Analiz Hatası: {str(e)[:200]}")
            return None

# ==========================================
# GÖRSEL PROJEKSİYON
# ==========================================
def draw_projection(img_bytes, yon, puanlar, sembol="XAU/USD"):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img)
    
    if yon == "LONG":
        renk = (0, 220, 0)
    elif yon == "SHORT":
        renk = (230, 30, 30)
    else:
        return None
    
    temiz = validate_yol_puani(puanlar)
    if not temiz:
        return None
        
    n = len(temiz)
    x1, x2 = int(W * 0.80), int(W * 0.98)
    y_top, y_bot = int(H * 0.15), int(H * 0.85)
    
    noktalar = []
    for i, p in enumerate(temiz):
        x = x1 + (x2 - x1) * i / (n - 1)
        y = y_bot - (p / 100.0) * (y_bot - y_top)
        noktalar.append((x, y))
        
    for i in range(len(noktalar) - 1):
        draw.line([noktalar[i], noktalar[i+1]], fill=renk, width=6)
        
    (xa, ya), (xb, yb) = noktalar[-2], noktalar[-1]
    angle = math.atan2(yb - ya, xb - xa)
    arrow_len = 30
    arrow_angle = math.pi / 6
    
    p1 = (xb, yb)
    p2 = (xb - arrow_len * math.cos(angle - arrow_angle),
          yb - arrow_len * math.sin(angle - arrow_angle))
    p3 = (xb - arrow_len * math.cos(angle + arrow_angle),
          yb - arrow_len * math.sin(angle + arrow_angle))
    
    draw.polygon([p1, p2, p3], fill=renk)
    draw.ellipse([noktalar[0][0]-6, noktalar[0][1]-6,
                  noktalar[0][0]+6, noktalar[0][1]+6], fill=renk)
    
    # Font: resim yüksekliğine göre ölçekle
    font_size = max(20, int(H * 0.035))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except:
            font = ImageFont.load_default()
        
    # Yazıyı SOL ALTA koy (üstteki başlıkla çakışmasın)
    draw.text((20, H - 60), f"{sembol} | {yon} | 2 Dk Projeksiyon", fill=renk, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# ==========================================
# MESAJ KARTI
# ==========================================
def build_card(a, sembol="XAU/USD"):
    yon_emoji = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "🟡"}
    e = yon_emoji.get(a.get("yon","BEKLE"), "⚪")
    t = []
    t.append("╔══════════════════════════╗")
    t.append(f"║  📊 {sembol} ANALİZİ (PRO)")
    t.append("╠══════════════════════════╣")
    t.append(f"║  {e} YÖN: {a.get('yon','?')}")
    t.append(f"║  🎯 GÜVEN: %{a.get('guven','?')}")
    t.append("╠══════════════════════════╣")
    
    if a.get("yon") != "BEKLE":
        t.append(f"║  💰 GİRİŞ: {a.get('giris','?')}")
        t.append(f"║  🛑 SL:    {a.get('stop_loss','?')}")
        tps = a.get("take_profit", [])
        if tps:
            for i, tp in enumerate(tps, 1):
                t.append(f"║  ✅ TP{i}:   {tp}")
        t.append(f"║  ⚖️ R/R:   {a.get('risk_odul','?')}")
    else:
        t.append("║  ⏸️  Şu an net sinyal yok")
        t.append("║  ⏳ Güven %65 altı, bekle")
        
    t.append("╚══════════════════════════╝")
    t.append("")
    t.append("📈 TREND ANALİZİ")
    t.append(f"• M1:  {a.get('trend_m1','?')}")
    
    vwap = a.get("vwap_durumu", "")
    if vwap and vwap.lower() not in ["belirsiz", ""]:
        t.append(f"• VWAP: {vwap}")
    
    t.append("")
    t.append("🎯 SEVİYELER")
    destekler = a.get("destekler", [])
    direncler = a.get("direncler", [])
    t.append(f"🟢 Destek: {', '.join(map(str, destekler)) if destekler else 'Belirsiz'}")
    t.append(f"🔴 Direnç: {', '.join(map(str, direncler)) if direncler else 'Belirsiz'}")
    
    fvg = a.get("fvg_tespit", "")
    likidite = a.get("likidite_durumu", "")
    sm = []
    if fvg and fvg.lower() not in ["yok", "belirsiz", ""]:
        sm.append(f"📦 FVG: {fvg}")
    if likidite and likidite.lower() not in ["yok", "belirsiz", ""]:
        sm.append(f"💧 Likidite: {likidite}")
    if sm:
        t.append("")
        t.append("🧠 SMART MONEY")
        for s in sm:
            t.append(s)
    
    formasyonlar = a.get("formasyonlar", [])
    if formasyonlar:
        t.append("")
        t.append(f"🧩 Formasyon: {', '.join(map(str, formasyonlar))}")
    
    # ⏱️ MUM TAHMİNİ
    kalan = a.get("kalan_mum", 0)
    mum_yonu = a.get("mum_yonu", "")
    hareket = a.get("hareket_aciklamasi", "")
    sonraki = a.get("sonraki_hamle", "")
    
    if kalan or hareket:
        t.append("")
        t.append("⏱️ MUM TAHMİNİ")
        if hareket:
            t.append(f"• {hareket}")
        elif kalan and mum_yonu:
            t.append(f"• {mum_yonu.capitalize()} yönünde ~{kalan} mum")
        if sonraki:
            t.append(f"• Sonrası: {sonraki}")
    
    t.append("")
    t.append("📝 ÖZET")
    t.append(a.get('kisa_analiz',''))
    t.append("")
    t.append("🔍 GEREKÇELER")
    for g in str(a.get("gerekce", "")).replace("\\n", "\n").split("\n"):
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
    init_offset_db()
    print(f"=== SENTETİK ENDEKS ANALİZ BOTU BAŞLADI (GEMINI {GEMINI_MODEL}) ===", flush=True)
    offset = get_offset()
    
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                             params={"offset": offset, "timeout": 30}, timeout=40).json()
                             
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                save_offset(offset)
                
                msg = u.get("message", {})
                cid = msg.get("chat", {}).get("id")
                if not cid:
                    continue

                if "photo" in msg:
                    if not check_rate_limit(cid):
                        continue
                    
                    caption = (msg.get("caption") or "").strip()
                    sembol = caption_to_symbol(caption)
                    
                    if not sembol:
                        ornek = ", ".join(ALLOWED_SYMBOLS[:3])
                        send_msg(cid, f"⚠️ Lütfen fotoğrafın altına sembolü tam yazın.\nÖrnek: `{ornek}` ...")
                        continue
                        
                    fid = msg["photo"][-1]["file_id"]
                    send_msg(cid, f"⏳ {sembol} grafiği alındı, analiz ediliyor...")
                    
                    try:
                        img_bytes = get_file_bytes(fid)
                        a = analyze_chart(img_bytes, cid, sembol)
                        
                        if a:
                            kart = build_card(a, sembol)
                            
                            if a.get("yon") != "BEKLE":
                                gorsel = draw_projection(img_bytes, a.get("yon"), a.get("yol_puani", []), sembol)
                            else:
                                gorsel = None
                                
                            if gorsel:
                                if len(kart) > 1024:
                                    send_photo(cid, gorsel, caption=f"{sembol} | {a.get('yon')} | %{a.get('guven')}")
                                    send_msg(cid, kart)
                                else:
                                    send_photo(cid, gorsel, caption=kart)
                            else:
                                send_msg(cid, kart)
                        else:
                            send_msg(cid, "❌ Analiz başarısız, tekrar deneyin.")
                            
                    except Exception as e:
                        send_msg(cid, f"❌ Hata: {str(e)[:200]}")
                else:
                    sembol_listesi = "\n".join([f"• {s}" for s in ALLOWED_SYMBOLS])
                    send_msg(cid,
                        "📸 *Sentetik Endeks Analiz Botu (PRO v3)*\n\n"
                        "Kullanım:\n"
                        "1️⃣ Grafiğin ekran görüntüsünü al\n"
                        "2️⃣ Fotoğrafı bota gönder\n"
                        "3️⃣ Altına sembolü tam yaz\n"
                        "   Örnek: `MAX PainX 1000`, `GainX 800`, `TrendX 1800`\n\n"
                        "Desteklenen semboller:\n"
                        f"{sembol_listesi}\n\n"
                        "⚠️ Yatırım tavsiyesi değildir.",
                        parse_mode="Markdown")
                        
        except Exception as e:
            print(f"=== LOOP HATASI: {e} ===", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
