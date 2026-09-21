import os
import io, json, base64, time, math, requests, threading, re, sqlite3
from PIL import Image, ImageDraw, ImageFont
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ✅ DÜZELTME 1: Doğru model adı (404 hatası çözümü)
GEMINI_MODEL = "gemini-3.1-pro-preview"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

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
# XAU/USD M1 PROMPT (v2 - VWAP + FVG + Likidite + Hibrit SL/TP)
# ==========================================
PROMPT = """Sen dünyanın en iyi XAU/USD (Altın) M1 scalping uzmanısın. 15+ yıllık deneyimli bir profesyonelsin. Kurumsal trader'larla çalışmış, Smart Money konseptlerini (ICT) derinlemesine bilen bir analistsin.

Sana TEK bir XAU/USD M1 grafiği gönderiliyor.
GÖREV: Bu grafiği analiz et ve sonraki 2 dakikalık (2 mumluk) fiyat projeksiyonunu tahmin et.

KULLANMAN GEREKEN TÜM TEKNİKLER:
- Market yapısı: HH/LL, BOS, CHoCH
- Destek/direnç seviyeleri, Order Block, Likidite boşlukları
- Supply/Demand Zones (arz/talep bölgeleri)
- VWAP (Volume Weighted Average Price) - kurumsal referans çizgisi
- FVG (Fair Value Gap) - fiyat boşlukları
- Liquidity Sweep - stop avı tespiti
- Premium/Discount bölgeler (Fibonacci ile)
- EMA 20/50/200, RSI, MACD, Hacim analizi
- Mum formasyonları (engulfing, pin bar, doji, hammer)
- Fibonacci retracement

ÇOK ÖNEMLİ SEVİYE KURALLARI (HİBRİT SİSTEM):
1. SL seviyesini belirlerken ÖNCE en yakın destek (LONG için) veya direnç (SHORT için) seviyesini bul.
2. SL'yi bu seviyenin 1-2 puan ÖTESİNE koy (stop avına karşı koruma).
3. ANCAK minimum SL mesafesi 5 PUAN olmalıdır. Eğer yapı 5 puandan dar SL gerektiriyorsa, 5 puana genişlet.
4. Minimum TP1 mesafesi 8 PUAN, minimum TP2 mesafesi 12 PUAN olmalıdır.
5. Spread (0.20-0.50 puan) ve gürültüyü hesaba kat.
6. Eğer grafiğe göre 5 puanlık SL uygun değilse veya R/R 1:1.5'in altındaysa, "BEKLE" yaz.

DİĞER ÇOK ÖNEMLİ KURALLAR:
1. Eğer güven oranın %65'in ALTINDA ise, "yon" alanına MUTLAKA "BEKLE" yaz.
2. %65 ve üzeri güvende LONG veya SHORT sinyali ver.
3. KESİNLİKLE örnek JSON'daki değerleri kopyalama, grafiğe göre kendi objektif kararını ver.
4. Güven oranını %50, %65, %75, %85, %95 gibi gerçekçi ve değişken aralıklarda ver. Sürekli aynı sayıyı verme.
5. JSON içindeki metin alanlarında (kisa_analiz, gerekce, uyari vb.) ÇİFT TIRNAK (") KULLANMA. Satır atlamak için \\n kullan. Tek tırnak (') serbesttir.
6. Cevabın MUTLAKA geçerli ve tam bir JSON olarak bitmeli, yarıda KESİLMEMELİDİR.

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
  "vwap_durumu": "fiyat VWAP üstünde veya altında veya belirsiz",
  "fvg_tespit": "FVG var mı yok mu, varsa kısa açıklama",
  "likidite_durumu": "Liquidity sweep tespit edildi mi, kısa açıklama",
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
# KALICI OFFSET (SQLite ile)
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
    """✅ DÜZELTME 8: Hata mesajlarında parse_mode kaldırıldı"""
    try:
        payload = {"chat_id": cid, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json=payload, timeout=10)
    except Exception as e:
        print(f"send_msg hatası: {e}", flush=True)

def send_photo(cid, photo_bytes, caption=""):
    """✅ DÜZELTME 2: Caption 1024 karakter sınırı kontrolü"""
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

def escape_md(text):
    """✅ DÜZELTME 6: Markdown kaçış karakterleri temizlenir"""
    if not isinstance(text, str):
        text = str(text)
    for ch in ['_', '*', '[', ']', '`', '~']:
        text = text.replace(ch, '')
    return text

# ==========================================
# ✅ DÜZELTME 3: yol_puani doğrulaması
# ==========================================
def validate_yol_puani(puanlar):
    """Sadece geçerli sayıları al, en az 2 nokta olmalı"""
    if not isinstance(puanlar, list):
        return None
    temiz = []
    for p in puanlar:
        try:
            p = float(p)
            if 0 <= p <= 100:
                temiz.append(p)
        except (ValueError, TypeError):
            continue
    return temiz if len(temiz) >= 2 else None

# ==========================================
# ✅ DÜZELTME 9: Rate Limiting
# ==========================================
USER_COOLDOWN = {}
RATE_LIMIT_SECONDS = 15

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
# GEMINI ANALİZ MOTORU (PRO - Kusursuz JSON Okuma)
# ==========================================
def analyze_chart(img_bytes, cid):
    print(f"🔍 DEBUG: XAU/USD M1 analiz ediliyor... (Model: {GEMINI_MODEL})", flush=True)
    
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
            "maxOutputTokens": 4000,
            "responseMimeType": "application/json"
        }
    }
    del b64
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY  # ✅ DÜZELTME 10: API anahtarı başlıkta
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
                    send_msg(cid, "❌ Gemini boş cevap döndü.", parse_mode=None)
                    return None
                
                # JSON Temizleme
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    parts = text.split("```")
                    if len(parts) >= 2:
                        text = parts[1]
                        if text.startswith("json"):
                            text = text[4:]
                text = text.strip()

                # GÜVENLİ JSON OKUMA (Kurtarma Modu)
                a = None
                try:
                    a = json.loads(text)
                except json.JSONDecodeError as e:
                    print(f"JSON Hatası, kurtarma deneniyor: {e}", flush=True)
                    bas = text.find('{')
                    son = text.rfind('}')
                    if bas != -1 and son != -1 and son > bas:
                        try:
                            a = json.loads(text[bas:son+1])
                        except Exception as e2:
                            print(f"Kurtarma başarısız: {e2}", flush=True)
                            send_msg(cid, "❌ Gemini cevabı bozuk JSON içeriyor. Tekrar deneyin.", parse_mode=None)
                            return None
                    else:
                        send_msg(cid, "❌ Gemini cevabında JSON bulunamadı.", parse_mode=None)
                        return None
                
                # Güven kontrolü
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
                    send_msg(cid, "❌ Gemini sunucuları şu an aşırı yoğun. Lütfen birkaç dakika sonra tekrar deneyin.", parse_mode=None)
                    return None
            elif resp.status_code == 429:
                send_msg(cid, "⚠️ Çok fazla istek. 30 saniye bekleyip tekrar deneyin.", parse_mode=None)
                time.sleep(30)
                continue
            else:
                hata_detayi = resp.text[:400]
                send_msg(cid, f"❌ Gemini Hatası ({resp.status_code}): {hata_detayi}", parse_mode=None)
                return None
                
        except Exception as e:
            send_msg(cid, f"❌ Analiz Hatası: {str(e)}", parse_mode=None)
            return None

# ==========================================
# ✅ DÜZELTME 4: Görsel Projeksiyon Çizimi (Geliştirilmiş Ok Başı)
# ==========================================
def draw_projection(img_bytes, yon, puanlar):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img)
    
    if yon == "LONG":
        renk = (0, 220, 0)
    elif yon == "SHORT":
        renk = (230, 30, 30)
    else:
        return None
    
    # ✅ yol_puani doğrulaması
    temiz_puanlar = validate_yol_puani(puanlar)
    if not temiz_puanlar:
        return None
        
    n = len(temiz_puanlar)
    x1, x2 = int(W * 0.85), int(W * 0.99)
    y_top, y_bot = int(H * 0.10), int(H * 0.90)
    
    noktalar = []
    for i, p in enumerate(temiz_puanlar):
        x = x1 + (x2 - x1) * i / (n - 1)
        y = y_bot - (p / 100.0) * (y_bot - y_top)
        noktalar.append((x, y))
        
    for i in range(len(noktalar) - 1):
        draw.line([noktalar[i], noktalar[i+1]], fill=renk, width=6)
        
    # ✅ Geliştirilmiş ok başı (polygon ile)
    (xa, ya), (xb, yb) = noktalar[-2], noktalar[-1]
    angle = math.atan2(yb - ya, xb - xa)
    arrow_len = 30
    arrow_angle = math.pi / 6  # 30 derece
    
    p1 = (xb, yb)
    p2 = (xb - arrow_len * math.cos(angle - arrow_angle), 
          yb - arrow_len * math.sin(angle - arrow_angle))
    p3 = (xb - arrow_len * math.cos(angle + arrow_angle), 
          yb - arrow_len * math.sin(angle + arrow_angle))
    
    draw.polygon([p1, p2, p3], fill=renk)
    
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
# MESAJ KARTI (✅ v2: Yeni alanlar eklendi)
# ==========================================
def build_card(a):
    yon_emoji = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "🟡"}
    e = yon_emoji.get(a.get("yon","BEKLE"), "⚪")
    t = []
    t.append("╔══════════════════════════╗")
    t.append(f"║  📊 XAU/USD M1 ANALİZİ (PRO)")
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
    
    # ✅ YENİ: VWAP, FVG ve Likidite durumu
    vwap = a.get("vwap_durumu", "")
    if vwap and vwap.lower() != "belirsiz":
        t.append(f"• VWAP: {escape_md(vwap)}")
    
    t.append("")
    t.append("🎯 SEVİYELER")
    
    destekler = a.get("destekler", [])
    direncler = a.get("direncler", [])
    t.append(f"🟢 Destek: {', '.join(map(str, destekler)) if destekler else 'Belirsiz'}")
    t.append(f"🔴 Direnç: {', '.join(map(str, direncler)) if direncler else 'Belirsiz'}")
    
    # ✅ YENİ: Smart Money göstergeleri
    fvg = a.get("fvg_tespit", "")
    likidite = a.get("likidite_durumu", "")
    sm_list = []
    if fvg and fvg.lower() not in ["yok", "belirsiz", ""]:
        sm_list.append(f"📦 FVG: {escape_md(fvg)}")
    if likidite and likidite.lower() not in ["yok", "belirsiz", ""]:
        sm_list.append(f"💧 Likidite: {escape_md(likidite)}")
    
    if sm_list:
        t.append("")
        t.append("🧠 SMART MONEY")
        for s in sm_list:
            t.append(s)
    
    formasyonlar = a.get("formasyonlar", [])
    if formasyonlar:
        t.append("")
        t.append(f"🧩 Formasyon: {', '.join(map(str, formasyonlar))}")
        
    t.append("")
    t.append("📝 ÖZET")
    t.append(escape_md(a.get('kisa_analiz','')))
    t.append("")
    t.append("🔍 GEREKÇELER")
    gerekce_text = a.get("gerekce", "")
    for g in str(gerekce_text).replace("\\n", "\n").split("\n"):
        if g.strip():
            t.append(f"• {escape_md(g.strip())}")
            
    t.append("")
    t.append(f"⚠️ {escape_md(a.get('uyari','Yatırım tavsiyesi değildir.'))}")
    return "\n".join(t)

# ==========================================
# ANA DÖNGÜ (✅ DÜZELTME 7: Kalıcı Offset)
# ==========================================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    init_offset_db()
    print(f"=== XAU/USD M1 BOTU BAŞLADI (GEMINI {GEMINI_MODEL}) ===", flush=True)
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
                    
                    if "XAU" not in caption.upper() and "GOLD" not in caption.upper():
                        send_msg(cid, "⚠️ Lütfen sadece XAU/USD grafiği gönderin ve altına `XAU/USD` yazın.")
                        continue
                        
                    fid = msg["photo"][-1]["file_id"]
                    send_msg(cid, "⏳ XAU/USD M1 grafiği alındı, analiz ediliyor... (Pro modeli için 1-2 dakika sürebilir)")
                    
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
                                if len(kart) > 1024:
                                    send_photo(cid, gorsel, caption=f"XAU/USD M1 | {a.get('yon')} | %{a.get('guven')}")
                                    send_msg(cid, kart)
                                else:
                                    send_photo(cid, gorsel, caption=kart)
                            else:
                                send_msg(cid, kart)
                        else:
                            send_msg(cid, "❌ Analiz başarısız oldu, lütfen tekrar deneyin.", parse_mode=None)
                            
                    except Exception as e:
                        send_msg(cid, f"❌ Beklenmeyen Hata: {str(e)}", parse_mode=None)
                else:
                    send_msg(cid,
                        "📸 *XAU/USD M1 Analiz Botu (PRO v2)*\n\n"
                        "Kullanım:\n"
                        "1️⃣ MT5'ten XAU/USD M1 grafiğinin ekran görüntüsünü al.\n"
                        "2️⃣ Bu fotoğrafı bota gönder.\n"
                        "3️⃣ Altına (caption) `XAU/USD` yaz.\n\n"
                        "Bot artık VWAP, FVG ve Likidite analizini de kullanıyor. "
                        "Hibrit SL/TP sistemi ile minimum 5 puan SL koruması aktif.\n\n"
                        "⚠️ Yatırım tavsiyesi değildir.",
                        parse_mode="Markdown")
                        
        except Exception as e:
            print(f"=== LOOP HATASI: {e} ===", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
