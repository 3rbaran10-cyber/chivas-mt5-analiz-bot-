import os
import io, json, base64, time, math, requests, threading, re, sqlite3
from PIL import Image, ImageDraw, ImageFont
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# ==========================================
# DESTEKLENEN SEMBOLLER
# ==========================================
ALLOWED_SYMBOLS = [
    "BreakX 1800",
    "GainX 400",
    "GainX 600",
    "GainX 800",
    "GainX 999",
    "GainX 1200",
    "MAX GainX 1000",
    "MAX GainX 2000",
    "MAX PainX 1000",
    "MAX PainX 2000",
    "PainX 400",
    "PainX 600",
    "PainX 800",
    "PainX 999",
    "PainX 1200",
    "SwitchX 1800",
    "TrendX 1800",
]
ALLOWED_SYMBOLS_NORM = {
    s.lower().replace("-", " ").replace("/", " ").strip(): s for s in ALLOWED_SYMBOLS
}

# ==========================================
# SEMBOL BAZLI M1 ATR ÖLÇEĞİ
# ==========================================
SYMBOL_ATR_SCALE = {
    "GainX 400": (8, 25),
    "GainX 600": (10, 30),
    "GainX 800": (12, 35),
    "GainX 999": (15, 40),
    "GainX 1200": (18, 45),
    "MAX GainX 1000": (20, 55),
    "MAX GainX 2000": (25, 70),
    "MAX PainX 1000": (20, 55),
    "MAX PainX 2000": (25, 70),
    "PainX 400": (8, 25),
    "PainX 600": (10, 30),
    "PainX 800": (12, 35),
    "PainX 999": (15, 40),
    "PainX 1200": (18, 45),
    "BreakX 1800": (18, 50),
    "SwitchX 1800": (18, 50),
    "TrendX 1800": (18, 50),
}

# ==========================================
# HEALTH SERVER
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
# YARDIMCI: GÜVENLİ FLOAT PARSE
# ==========================================
def parse_float_guvenli(deger):
    if deger is None:
        return None
    s = str(deger).strip()
    s = s.replace(' ', '').replace('\u00a0', '').replace('$', '').replace('₺', '')
    if not s:
        return None

    if '.' in s and ',' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            s = s.replace(',', '')
        else:
            s = s.replace(',', '.')

    try:
        return float(s)
    except (ValueError, TypeError):
        return None

# ==========================================
# ORTAK SEVİYE KURALLARI
# ==========================================
SEVIYE_KURALLARI = """
ZAMAN DİLİMİ YAPISI:
- M5 = ANA TREND ve YÖN (EMA 20/50, yapı kırılımı BOS/CHoCH)
- M1 = GİRİŞ TETİKLEYİCİSİ (kısa vadeli yapı, likidite süpürme, FVG)

MTF UYUM KURALLARI:
- M5 ve M1 aynı yönde ise → güven yüksek (%75-90)
- M5 yukarı, M1 aşağı (geri çekilme) → M5 yönünde (LONG) fırsat kolla, güven %70
- M5 aşağı, M1 yukarı (geri çekilme) → M5 yönünde (SHORT) fırsat kolla, güven %70
- M5 ve M1 çelişiyor → 'BEKLE' ver
- M5 yatay, M1 yönlü → sadece M1 scalp yapılabilir, güven %60

SPIKE / TICK RADARI:
Görselin sol üstünde "one price drop per X ticks on average" gibi bir yazı olabilir.
Bunu OKU ve şu şekilde yorumla:
- Eğer fiyat son X tick'te çok yükseldiyse ve X tick kuralı yaklaşıyorsa → ani düşüş riski VAR
- LONG pozisyonlarda "spike_riski" = "var" olarak işaretle
- SHORT pozisyonlarda bu risk lehine çalışır ama yine de belirt
- Yazı yoksa "spike_riski" = "belirsiz"

SİKIŞMA / KIRILIM TESPİTİ:
M5 grafiğinde son 20 mumun boyutlarına bak.
- Mumlar küçülüyorsa (daralıyorsa) → "sikisma_durumu" = "var", "kirilim_beklentisi" = "yukari" veya "asagi"
- Mumlar büyüyorsa → "sikisma_durumu" = "yok"
- M5 daralıyor ve M1'de kırılım varsa → güçlü sinyal

VOLATİLİTE ÖLÇÜMÜ (M1 ATR):
M1 grafiğinde son 20 mumun ortalama (high - low) boyutunu puan cinsinden tahmin et.
Bunu JSON'a "m1_atr" olarak yaz. Bu değer Python tarafında SL/TP hesabı için kullanılacak.
Sadece sayı ver, yorum yapma.

SL/TP HESABI (PYTHON TARAFINDA YAPILIR):
Sen sadece GİRİŞ fiyatını (mevcut piyasa fiyatı) ve m1_atr'yi ver.
SL/TP mesafesini Python hesaplayacak. Sen SL/TP fiyatı VERME.

YOL PUANI SIRALAMASI:
yol_puani dizisini YÖN ile uyumlu sırala.
- SHORT yönünde: ilk puan EN YÜKSEK (örn: 90), son puan EN DÜŞÜK (örn: 10) olmalı.
- LONG yönünde: ilk puan EN DÜŞÜK (örn: 10), son puan EN YÜKSEK (örn: 90) olmalı.
- BEKLE yönünde: yol_puani dizisi göndermek zorunlu değil.
"""

# ==========================================
# PROMPT (TEKLİ FOTO - ÜST M1 / ALT M5)
# ==========================================
PROMPT_TEMPLATE = """Sen dünyanın en iyi {sembol} analiz uzmanısın. 15+ yıllık deneyimli profesyonelsin. Smart Money konseptlerini (ICT) derinlemesine bilirsin.

Sana {sembol} için BİR MT5 ekran görüntüsü gönderiliyor. Bu TEK fotoğraf dikey olarak İKİYE BÖLÜNMÜŞTÜR:
- ÜST YARI = M1 (1 dakikalık grafik)
- ALT YARI = M5 (5 dakikalık grafik)

GÖREV:
1. Görselin ÜST YARISINDAKİ M1 grafiğini ve ALT YARISINDAKİ M5 grafiğini incele.
2. M5 → ana trend yönü (EMA 20/50, BOS/CHoCH)
3. M1 → GİRİŞ/SL/TP için TEK referans
4. İkisini birleştirerek sonraki 2 dakikalık fiyat projeksiyonunu ver.

""" + SEVIYE_KURALLARI + """
KULLANILACAK TEKNİKLER:
- Market yapısı: HH/LL, BOS, CHoCH (M5 ve M1'de ayrı)
- Destek/direnç, Order Block, Supply/Demand
- VWAP, FVG, Liquidity Sweep
- EMA 20/50/200, RSI, MACD, Hacim
- Mum formasyonları (engulfing, pin bar, doji, hammer)
- Fibonacci retracement

KARAR KURALLARI:
1. Güven %65 altındaysa 'yon' = 'BEKLE'.
2. Güven oranını değişken ver (%50, %65, %75, %85, %95).
3. M5 ve M1 çelişiyorsa → güven düşür veya 'BEKLE' ver.

MUM SAYISI: M1 grafiğinde 2 dakika = 2 mum. 1-3 arası ver. 5+ verme.

M1 BÖLGE TESPİTİ (ÇOK ÖNEMLİ):
M1 grafiği görselin ÜST YARISINDADIR.
m1_bolge değerleri:
- x: 0-10
- y: 0-5
- w: 80-100
- h: 40-50
Eğer görselde sadece M1 varsa: x=0, y=0, w=100, h=100 ver.

FORMAT: SADECE geçerli JSON. Sayılarda NOKTA kullan. Türkçe yaz.

JSON ŞEMASI:
- sembol, yon ("LONG"|"SHORT"|"BEKLE"), guven (0-100)
- giris (sadece mevcut piyasa fiyatı, SL/TP VERME)
- m1_atr (integer, M1 ortalama mum boyutu)
- mtf_uyum ("uyumlu"|"celiskili"|"m5_yatay"|"tek_grafik")
- m5_trend ("yukari"|"asagi"|"yatay"|"belirsiz")
- spike_riski ("var"|"yok"|"belirsiz")
- tick_uyarisi (string, kısa)
- sikisma_durumu ("var"|"yok")
- kirilim_beklentisi ("yukari"|"asagi"|"belirsiz")
- trend_m1, vwap_durumu, fvg_tespit, likidite_durumu
- destekler (array), direncler (array)
- m5_destek (array), m5_direnc (array)
- formasyonlar (array)
- kullanilan_teknikler (array)
- kisa_analiz, gerekce (\\n ile maddeler)
- yol_puani (array, 7 sayı 0-100, YÖN İLE UYUMLU SIRALI)
- kalan_mum (1-3), mum_yonu, hareket_aciklamasi, sonraki_hamle, uyari
- m1_bolge (object: x, y, w, h)"""

# ==========================================
# PROMPT (ALBÜM - 2 AYRI FOTO: 1.M1, 2.M5)
# ==========================================
PROMPT_TEMPLATE_MULTI = """Sen dünyanın en iyi {sembol} analiz uzmanısın. 15+ yıllık deneyimli profesyonelsin. Smart Money konseptlerini (ICT) derinlemesine bilirsin.

Sana {sembol} için İKİ AYRI grafik fotoğrafı gönderiliyor.
- 1. FOTOĞRAF = M1 (1 dakikalık grafik)
- 2. FOTOĞRAF = M5 (5 dakikalık grafik)

GÖREV:
1. Her iki grafiği de incele.
2. M5 → ana trend yönü (EMA 20/50, BOS/CHoCH)
3. M1 → GİRİŞ/SL/TP için TEK referans
4. İkisini birleştirerek sonraki 2 dakikalık fiyat projeksiyonunu ver.

ÇOKLU TF KURALLARI:
- M5 ve M1 aynı yön → güven yüksek (%75-90)
- M5 ve M1 çelişiyor → 'BEKLE'
- İkisi uyumluysa → en güçlü sinyal

""" + SEVIYE_KURALLARI + """
KULLANILACAK TEKNİKLER:
- Market yapısı: HH/LL, BOS, CHoCH (her TF'de ayrı)
- Destek/direnç, Order Block, Supply/Demand
- VWAP, FVG, Liquidity Sweep
- EMA 20/50/200, RSI, MACD, Hacim
- Mum formasyonları
- Fibonacci retracement

KARAR KURALLARI:
1. Güven %65 altı → 'BEKLE'.
2. Güven değişken ver.

MUM SAYISI: M1'de 2 dakika = 2 mum. 1-3 arası ver.

M1 BÖLGE TESPİTİ:
1. FOTOĞRAF M1'dir. Bu fotoğrafta M1 grafiği tüm fotoğrafı kaplar.
- x=0, y=0, w=100, h=100

FORMAT: SADECE geçerli JSON. Sayılarda NOKTA kullan. Türkçe yaz.

JSON ŞEMASI:
- sembol, yon ("LONG"|"SHORT"|"BEKLE"), guven (0-100)
- giris (sadece mevcut piyasa fiyatı, SL/TP VERME)
- m1_atr (integer, M1 ortalama mum boyutu)
- mtf_uyum ("uyumlu"|"celiskili"|"m5_yatay"|"tek_grafik")
- m5_trend ("yukari"|"asagi"|"yatay"|"belirsiz")
- spike_riski ("var"|"yok"|"belirsiz")
- tick_uyarisi (string, kısa)
- sikisma_durumu ("var"|"yok")
- kirilim_beklentisi ("yukari"|"asagi"|"belirsiz")
- trend_m1, vwap_durumu, fvg_tespit, likidite_durumu
- destekler (array), direncler (array)
- m5_destek (array), m5_direnc (array)
- formasyonlar (array)
- kullanilan_teknikler (array)
- kisa_analiz, gerekce (\\n ile maddeler)
- yol_puani (array, 7 sayı 0-100, YÖN İLE UYUMLU SIRALI)
- kalan_mum (1-3), mum_yonu, hareket_aciklamasi, sonraki_hamle, uyari
- m1_bolge (object: x, y, w, h)"""

# ==========================================
# PYTHON TARAFINDA SL/TP HESAPLAMA
# ==========================================
def hesapla_sl_tp(giris_str, m1_atr, yon, sembol):
    giris = parse_float_guvenli(giris_str)
    if giris is None or giris <= 0:
        print(f"⚠️ Giriş parse edilemedi: {giris_str}", flush=True)
        return None

    scale = SYMBOL_ATR_SCALE.get(sembol, (10, 50))
    m1_atr_val = parse_float_guvenli(m1_atr)
    if m1_atr_val is None:
        m1_atr_val = scale[0]
    m1_atr_val = int(m1_atr_val)
    m1_atr_val = max(scale[0], min(scale[1], m1_atr_val))

    sl_mesafe = m1_atr_val * 2

    sl_min = max(20, int(giris * 0.0001))
    sl_max = min(m1_atr_val * 4, int(giris * 0.005))
    if sl_max < sl_min:
        sl_max = sl_min
    sl_mesafe = max(sl_min, min(sl_max, sl_mesafe))

    tp1_mesafe = sl_mesafe * 1.5
    tp2_mesafe = sl_mesafe * 2.5

    if yon == "LONG":
        sl = giris - sl_mesafe
        tp1 = giris + tp1_mesafe
        tp2 = giris + tp2_mesafe
    elif yon == "SHORT":
        sl = giris + sl_mesafe
        tp1 = giris - tp1_mesafe
        tp2 = giris - tp2_mesafe
    else:
        return None

    return {
        "giris": f"{giris:.2f}",
        "stop_loss": f"{sl:.2f}",
        "take_profit": [f"{tp1:.2f}", f"{tp2:.2f}"],
        "risk_odul": "1:1.5 / 1:2.5",
        "sl_mesafe": sl_mesafe,
        "m1_atr_kullanilan": m1_atr_val,
    }

# ==========================================
# SQLITE
# ==========================================
DB_PATH = "/tmp/telegram_offset.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cid INTEGER, sembol TEXT, yon TEXT, guven INTEGER,
                giris TEXT, stop_loss TEXT, tp1 TEXT, tp2 TEXT,
                kalan_mum INTEGER, mum_yonu TEXT, kisa_analiz TEXT,
                ts INTEGER, sonuc TEXT DEFAULT NULL
            )
        """)
        conn.commit()
        conn.close()
        print("✅ DB hazır", flush=True)
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
        print(f"Offset hatası: {e}", flush=True)

def save_analysis(cid, sembol, a):
    try:
        tps = a.get("take_profit") or []
        tp1 = tps[0] if len(tps) > 0 else None
        tp2 = tps[1] if len(tps) > 1 else None
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("""
            INSERT INTO analyses (cid, sembol, yon, guven, giris, stop_loss, tp1, tp2,
                                  kalan_mum, mum_yonu, kisa_analiz, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cid, sembol, a.get("yon"), a.get("guven"),
            a.get("giris"), a.get("stop_loss"), tp1, tp2,
            a.get("kalan_mum"), a.get("mum_yonu"),
            a.get("kisa_analiz"), int(time.time())
        ))
        conn.commit()
        rid = cur.lastrowid
        conn.close()
        return rid
    except Exception as e:
        print(f"save_analysis hatası: {e}", flush=True)
        return None

def update_sonuc(analiz_id, sonuc):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE analyses SET sonuc=? WHERE id=? AND sonuc IS NULL", (sonuc, analiz_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"update_sonuc hatası: {e}", flush=True)
        return False

def get_gecmis(cid, limit=10):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("""
            SELECT id, sembol, yon, guven, sonuc, ts
            FROM analyses WHERE cid=?
            ORDER BY id DESC LIMIT ?
        """, (cid, limit))
        rows = cur.fetchall()
        conn.close()
        return rows
    except:
        return []

def get_istatistik(cid):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN sonuc='tuttu' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN sonuc='tutmadi' THEN 1 ELSE 0 END)
            FROM analyses WHERE cid=? AND yon != 'BEKLE'
        """, (cid,))
        total, tuttu, tutmadi = cur.fetchone()
        total = total or 0
        tuttu = tuttu or 0
        tutmadi = tutmadi or 0

        cur = conn.execute("""
            SELECT sembol, COUNT(*),
                   SUM(CASE WHEN sonuc='tuttu' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN sonuc='tutmadi' THEN 1 ELSE 0 END)
            FROM analyses WHERE cid=? AND yon != 'BEKLE'
            GROUP BY sembol HAVING COUNT(*) > 0
            ORDER BY COUNT(*) DESC
        """, (cid,))
        semboller = cur.fetchall()
        conn.close()
        return {"total": total, "tuttu": tuttu, "tutmadi": tutmadi, "semboller": semboller}
    except Exception as e:
        print(f"istatistik hatası: {e}", flush=True)
        return {"total": 0, "tuttu": 0, "tutmadi": 0, "semboller": []}

# ==========================================
# YARDIMCILAR
# ==========================================
def send_msg(cid, text, parse_mode=None, reply_markup=None):
    try:
        payload = {"chat_id": cid, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json=payload, timeout=10)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"send_msg hatası: {e}", flush=True)
        return None

def send_photo(cid, photo_bytes, caption="", reply_markup=None):
    try:
        data = {"chat_id": cid, "caption": caption[:1024]}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                          data=data, files={"photo": ("chart.png", photo_bytes)}, timeout=30)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"send_photo hatası: {e}", flush=True)
        return None

def edit_reply_markup(cid, message_id, reply_markup=None):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageReplyMarkup",
                      json={"chat_id": cid, "message_id": message_id, "reply_markup": reply_markup or {"inline_keyboard": []}},
                      timeout=10)
    except Exception as e:
        print(f"edit_markup hatası: {e}", flush=True)

def edit_message_text(cid, message_id, text, parse_mode=None, reply_markup=None):
    try:
        payload = {"chat_id": cid, "message_id": message_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
                      json=payload, timeout=10)
    except Exception as e:
        print(f"edit_text hatası: {e}", flush=True)

def get_file_bytes(file_id):
    r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
                     params={"file_id": file_id}, timeout=20).json()
    url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{r['result']['file_path']}"
    return requests.get(url, timeout=30).content

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
# RATE LIMIT
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
# GEMINI ANALİZ
# ==========================================
def analyze_chart(images_bytes_list, cid, sembol, coklu=False):
    print(f"🔍 Analiz başladı (Sembol: {sembol}, Görsel: {len(images_bytes_list)}, Çoklu: {coklu})", flush=True)

    parts = [{"text": (PROMPT_TEMPLATE_MULTI if coklu else PROMPT_TEMPLATE).format(sembol=sembol)}]

    for img_bytes in images_bytes_list:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((1400, 1400))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        del img, buf
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        del b64

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 32768,
            "thinkingConfig": {"thinkingBudget": 1024},
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "sembol": {"type": "string"},
                    "yon": {"type": "string", "enum": ["LONG", "SHORT", "BEKLE"]},
                    "guven": {"type": "integer"},
                    "giris": {"type": "string"},
                    "m1_atr": {"type": "integer"},
                    "mtf_uyum": {"type": "string"},
                    "m5_trend": {"type": "string"},
                    "spike_riski": {"type": "string"},
                    "tick_uyarisi": {"type": "string"},
                    "sikisma_durumu": {"type": "string"},
                    "kirilim_beklentisi": {"type": "string"},
                    "trend_m1": {"type": "string"},
                    "vwap_durumu": {"type": "string"},
                    "fvg_tespit": {"type": "string"},
                    "likidite_durumu": {"type": "string"},
                    "destekler": {"type": "array", "items": {"type": "string"}},
                    "direncler": {"type": "array", "items": {"type": "string"}},
                    "m5_destek": {"type": "array", "items": {"type": "string"}},
                    "m5_direnc": {"type": "array", "items": {"type": "string"}},
                    "formasyonlar": {"type": "array", "items": {"type": "string"}},
                    "kullanilan_teknikler": {"type": "array", "items": {"type": "string"}},
                    "kisa_analiz": {"type": "string"},
                    "gerekce": {"type": "string"},
                    "yol_puani": {"type": "array", "items": {"type": "number"}},
                    "kalan_mum": {"type": "integer"},
                    "mum_yonu": {"type": "string"},
                    "hareket_aciklamasi": {"type": "string"},
                    "sonraki_hamle": {"type": "string"},
                    "uyari": {"type": "string"},
                    "m1_bolge": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "w": {"type": "integer"},
                            "h": {"type": "integer"}
                        }
                    }
                },
                "required": ["sembol", "yon", "guven", "giris", "m1_atr", "kisa_analiz", "gerekce",
                             "yol_puani", "kalan_mum", "mum_yonu", "hareket_aciklamasi", "m1_bolge"]
            }
        }
    }

    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    max_deneme = 3
    for deneme in range(max_deneme):
        try:
            resp = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=180)
            print(f"🔍 Gemini HTTP = {resp.status_code}", flush=True)

            if resp.status_code == 200:
                r = resp.json()
                try:
                    text = r["candidates"][0]["content"]["parts"][0]["text"].strip()
                except (KeyError, IndexError):
                    try:
                        fr = r["candidates"][0].get("finishReason", "?")
                    except:
                        fr = "?"
                    print(f"❌ Boş cevap. finishReason={fr}", flush=True)
                    send_msg(cid, f"❌ Gemini boş cevap döndü. (Sebep: {fr})")
                    return None

                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    p = text.split("```")
                    if len(p) >= 2:
                        text = p[1]
                        if text.startswith("json"):
                            text = text[4:]
                text = text.strip()

                a = None
                try:
                    a = json.loads(text)
                except json.JSONDecodeError:
                    bas = text.find('{'); son = text.rfind('}')
                    if bas != -1 and son > bas:
                        try:
                            a = json.loads(text[bas:son+1])
                        except Exception as e2:
                            print(f"Kurtarma başarısız: {e2}", flush=True)
                            print(f"❌ Ham cevap: {text[:600]}", flush=True)
                            send_msg(cid, "❌ Gemini cevabı bozuk JSON.")
                            return None
                    else:
                        try:
                            fr = r["candidates"][0].get("finishReason", "?")
                        except:
                            fr = "?"
                        print(f"❌ JSON YOK. finishReason={fr}", flush=True)
                        print(f"❌ Ham cevap: {text[:600]}", flush=True)
                        send_msg(cid, f"❌ Gemini JSON vermedi. (Sebep: {fr})")
                        return None

                if a.get("yon") in ("LONG", "SHORT"):
                    sl_tp = hesapla_sl_tp(a.get("giris"), a.get("m1_atr"), a.get("yon"), sembol)
                    if sl_tp:
                        a["giris"] = sl_tp["giris"]
                        a["stop_loss"] = sl_tp["stop_loss"]
                        a["take_profit"] = sl_tp["take_profit"]
                        a["risk_odul"] = sl_tp["risk_odul"]
                        a["sl_mesafe_hesaplanan"] = sl_tp["sl_mesafe"]
                        print(f"✅ SL/TP Python: SL={sl_tp['stop_loss']}, TP={sl_tp['take_profit']}", flush=True)
                    else:
                        print("⚠️ SL/TP hesaplanamadı (parse hatası)", flush=True)
                        a["stop_loss"] = None
                        a["take_profit"] = []
                        a["sl_tp_hatasi"] = "Giriş fiyatı okunamadı"
                else:
                    a["stop_loss"] = None
                    a["take_profit"] = []

                try:
                    g = int(a.get("guven", 0))
                except:
                    g = 0
                if g < 65:
                    a["yon"] = "BEKLE"

                print(f"🎯 M1 bölge: {a.get('m1_bolge')} | M1 ATR: {a.get('m1_atr')} | Yön: {a.get('yon')}", flush=True)
                return a

            elif resp.status_code == 503:
                if deneme < max_deneme - 1:
                    bekleme = (deneme + 1) * 10
                    send_msg(cid, f"⏳ Gemini yoğun. {bekleme} sn sonra tekrar... ({deneme+1}/{max_deneme})")
                    time.sleep(bekleme)
                    continue
                else:
                    send_msg(cid, "❌ Gemini şu an aşırı yoğun.")
                    return None
            elif resp.status_code == 429:
                send_msg(cid, "⚠️ Çok fazla istek. 30 sn bekleyin.")
                time.sleep(30)
                continue
            else:
                send_msg(cid, f"❌ Gemini Hatası ({resp.status_code}): {resp.text[:400]}")
                return None
        except Exception as e:
            send_msg(cid, f"❌ Analiz Hatası: {str(e)[:200]}")
            return None

# ==========================================
# MUM FORMATINDA PROJEKSİYON ÇİZİMİ
# ==========================================
def draw_projection(img_bytes, yon, puanlar, sembol="XAU/USD", m1_bolge=None, m1_atr=30, tek_foto=False):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img)

    if yon == "LONG":
        renk = (0, 200, 80)
    elif yon == "SHORT":
        renk = (230, 40, 40)
    else:
        return None

    temiz = validate_yol_puani(puanlar)
    if not temiz:
        return None

    if yon == "SHORT":
        if temiz[0] < temiz[-1]:
            temiz = temiz[::-1]
    elif yon == "LONG":
        if temiz[0] > temiz[-1]:
            temiz = temiz[::-1]

    if m1_bolge and all(k in m1_bolge for k in ["x", "y", "w", "h"]):
        try:
            bx = int(W * float(m1_bolge["x"]) / 100)
            by = int(H * float(m1_bolge["y"]) / 100)
            bw = int(W * float(m1_bolge["w"]) / 100)
            bh = int(H * float(m1_bolge["h"]) / 100)
            bx = max(0, min(bx, W - 50))
            by = max(0, min(by, H - 50))
            bw = max(50, min(bw, W - bx))
            bh = max(50, min(bh, H - by))
            print(f"🎯 M1 piksel bölge: x={bx} y={by} w={bw} h={bh}", flush=True)
        except Exception as ex:
            print(f"M1 bölge parse hatası: {ex}", flush=True)
            bx, by, bw, bh = 0, 0, W, H
    else:
        bx, by, bw, bh = 0, 0, W, H

    # Tek fotoğrafta (üst M1, alt M5) M1 bölgesi tüm görseli kaplıyorsa
    # üst yarıya sabitle. Albümde 1. foto tamamen M1 olduğu için bu blok atlanır.
    if bh > H * 0.6 and tek_foto:
        by = int(H * 0.05)
        bh = int(H * 0.45)
        print(f"🔧 Tek foto: M1 üst yarıya sabitlendi: y={by} h={bh}", flush=True)

    x_start = bx + int(bw * 0.75)
    x_end = bx + int(bw * 0.97)
    y_top = by + int(bh * 0.10)
    y_bot = by + int(bh * 0.90)

    n = len(temiz)
    if n < 2:
        return None

    step_x = (x_end - x_start) / max(n - 1, 1)
    alan_h = y_bot - y_top

    max_body_h = max(4, min(int(bh * 0.04), int(alan_h / (n * 1.8))))
    max_wick_h = max(3, int(max_body_h * 0.4))

    for i, p in enumerate(temiz):
        x_center = x_start + step_x * i
        y_center = y_bot - (p / 100.0) * alan_h

        body_h = max(4, int((p / 100.0) * max_body_h))
        body_h = max(4, min(body_h, int(max_body_h * 1.2)))
        candle_w = max(3, int(step_x * 0.5))

        if yon == "SHORT":
            open_y = y_center - body_h / 2
            close_y = y_center + body_h / 2
            draw.line([(x_center, open_y), (x_center, open_y - max_wick_h)], fill=renk, width=2)
            draw.line([(x_center, close_y), (x_center, close_y + max_wick_h)], fill=renk, width=2)
            draw.rectangle([x_center - candle_w/2, open_y, x_center + candle_w/2, close_y],
                           fill=renk, outline=renk)
        else:
            open_y = y_center + body_h / 2
            close_y = y_center - body_h / 2
            draw.line([(x_center, open_y), (x_center, open_y + max_wick_h)], fill=renk, width=2)
            draw.line([(x_center, close_y), (x_center, close_y - max_wick_h)], fill=renk, width=2)
            draw.rectangle([x_center - candle_w/2, close_y, x_center + candle_w/2, open_y],
                           fill=renk, outline=renk)

    font_size = max(16, int(bh * 0.03))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except:
            font = ImageFont.load_default()

    draw.text((bx + 15, by + bh - 40), f"{sembol} | {yon} | Mum Projeksiyon", fill=renk, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# ==========================================
# MESAJ KARTI
# ==========================================
def build_card(a, sembol="XAU/USD", coklu=False):
    yon_emoji = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "🟡"}
    e = yon_emoji.get(a.get("yon", "BEKLE"), "⚪")

    guven = 0
    try:
        guven = int(a.get("guven", 0))
    except:
        guven = 0

    baslik_ek = ""
    if guven >= 85 and a.get("yon") in ("LONG", "SHORT"):
        baslik_ek = "🚨 ACİL SİNYAL 🚨\n"

    t = []
    t.append("╔══════════════════════════╗")
    baslik = f"📊 {sembol} ANALİZİ" + (" (M5+M1)" if coklu else "")
    t.append(f"║  {baslik}")
    t.append("╠══════════════════════════╣")
    if baslik_ek:
        t.append(f"║  {baslik_ek.strip()}")
    t.append(f"║  {e} YÖN: {a.get('yon','?')}")
    t.append(f"║  🎯 GÜVEN: %{a.get('guven','?')}")
    if a.get('m1_atr'):
        t.append(f"║  📏 M1 ATR: {a.get('m1_atr')} puan")
    t.append("╠══════════════════════════╣")

    if a.get("yon") != "BEKLE":
        t.append(f"║  💰 GİRİŞ: {a.get('giris','?')}")
        sl = a.get('stop_loss')
        if sl:
            t.append(f"║  🛑 SL:    {sl}")
        else:
            t.append(f"║  🛑 SL:    Hesaplanamadı")
        tps = a.get("take_profit", [])
        if tps:
            for i, tp in enumerate(tps, 1):
                t.append(f"║  ✅ TP{i}:   {tp}")
        else:
            t.append(f"║  ✅ TP:    Hesaplanamadı")
        t.append(f"║  ⚖️ R/R:   {a.get('risk_odul','?')}")
    else:
        t.append("║  ⏸️  Sinyal yok")
        uy = a.get("uyari", "")
        if uy:
            t.append(f"║  ⚠️  {str(uy)[:38]}")

    t.append("╚══════════════════════════╝")
    t.append("")

    mtf = a.get("mtf_uyum", "")
    m5_trend = a.get("m5_trend", "")
    if mtf or m5_trend:
        t.append("🧭 MTF UYUM")
        if m5_trend:
            t.append(f"• M5 Trend: {m5_trend}")
        if mtf:
            t.append(f"• Uyum: {mtf}")
        t.append("")

    spike = a.get("spike_riski", "")
    tick_uy = a.get("tick_uyarisi", "")
    if spike in ("var", "belirsiz") or tick_uy:
        t.append("⚠️ SPIKE / TICK RADARI")
        if spike:
            t.append(f"• Spike Riski: {spike.upper()}")
        if tick_uy:
            t.append(f"• {tick_uy}")
        t.append("")

    sik = a.get("sikisma_durumu", "")
    kir = a.get("kirilim_beklentisi", "")
    if sik == "var" or kir:
        t.append("🧲 SIKIŞMA / KIRILIM")
        if sik:
            t.append(f"• Sıkışma: {sik.upper()}")
        if kir:
            t.append(f"• Kırılım Beklentisi: {kir.upper()}")
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

    m5_destek = a.get("m5_destek", [])
    m5_direnc = a.get("m5_direnc", [])
    if m5_destek or m5_direnc:
        t.append("")
        t.append("📊 M5 SEVİYELERİ")
        if m5_destek:
            t.append(f"🟢 M5 Destek: {', '.join(map(str, m5_destek))}")
        if m5_direnc:
            t.append(f"🔴 M5 Direnç: {', '.join(map(str, m5_direnc))}")

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
    t.append(a.get('kisa_analiz', ''))
    t.append("")
    t.append("🔍 GEREKÇELER")
    for g in str(a.get("gerekce", "")).replace("\\n", "\n").split("\n"):
        if g.strip():
            t.append(f"• {g.strip()}")

    t.append("")
    t.append(f"⚠️ {a.get('uyari','Yatırım tavsiyesi değildir.')}")
    return "\n".join(t)

# ==========================================
# MENÜ FONKSİYONLARI
# ==========================================
def _ana_menu_buton():
    return {"inline_keyboard": [[{"text": "🔙 Ana Menü", "callback_data": "menu:ana"}]]}

def _menu_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📜 Geçmiş", "callback_data": "menu:gecmis"},
                {"text": "📊 İstatistik", "callback_data": "menu:istatistik"}
            ],
            [
                {"text": "📋 Semboller", "callback_data": "menu:semboller"},
                {"text": "❓ Yardım", "callback_data": "menu:yardim"}
            ]
        ]
    }

def _menu_text():
    return (
        "🤖 *CHIVAS MT5 ANALİZ BOTU*\n\n"
        "📸 *Nasıl analiz yaparım?*\n"
        "• Tek fotoğraf (üst M1, alt M5) → caption: `GainX 999`\n"
        "• Albüm (1. M1, 2. M5) → 2 foto tek seferde, caption: `GainX 999`\n\n"
        "⬇️ Aşağıdaki butonlardan seç:"
    )

def show_menu(cid, message_id=None):
    if message_id:
        edit_message_text(cid, message_id, _menu_text(), "Markdown", _menu_keyboard())
    else:
        send_msg(cid, _menu_text(), "Markdown", _menu_keyboard())

def show_gecmis(cid, message_id=None):
    rows = get_gecmis(cid, 10)
    if not rows:
        text = "📭 *Geçmiş boş*\n\nHenüz analiz yapmadın. Bir fotoğraf at, başlayalım!"
    else:
        e = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "🟡"}
        s = {"tuttu": "✅", "tutmadi": "❌", None: "⏳"}
        satirlar = ["📜 *SON 10 ANALİZ*", ""]
        for i, (aid, sembol, yon, guven, sonuc, ts) in enumerate(rows, 1):
            tarih = time.strftime("%d.%m %H:%M", time.localtime(ts))
            e_ = e.get(yon, "⚪")
            s_ = s.get(sonuc, "?")
            satirlar.append(f"{i}. {e_} *{sembol}* — {yon} — %{guven}")
            satirlar.append(f"     {s_} {tarih}")
        text = "\n".join(satirlar)

    if message_id:
        edit_message_text(cid, message_id, text, "Markdown", _ana_menu_buton())
    else:
        send_msg(cid, text, "Markdown", _ana_menu_buton())

def show_istatistik(cid, message_id=None):
    st = get_istatistik(cid)
    if st["total"] == 0:
        text = "📭 *İstatistik yok*\n\nAnaliz yap ve ✅/❌ ile işaretle."
    else:
        sonuclanan = st["tuttu"] + st["tutmadi"]
        oran = round(st["tuttu"] / sonuclanan * 100, 1) if sonuclanan > 0 else 0
        satirlar = [
            "📊 *İSTATİSTİK*", "",
            f"📈 Toplam sinyal: *{st['total']}*",
            f"✅ Tuttu: *{st['tuttu']}*",
            f"❌ Tutmadı: *{st['tutmadi']}*",
            f"🎯 Başarı oranı: *%{oran}*",
        ]
        if st["semboller"]:
            satirlar.append("")
            satirlar.append("📋 *Sembol Bazında:*")
            for sembol, tot, tut, tutm in st["semboller"]:
                if tut + tutm > 0:
                    r = round(tut / (tut + tutm) * 100, 0)
                    satirlar.append(f"• {sembol}: {tut}/{tut+tutm} (%{int(r)})")
                else:
                    satirlar.append(f"• {sembol}: {tot} sinyal")
        text = "\n".join(satirlar)

    if message_id:
        edit_message_text(cid, message_id, text, "Markdown", _ana_menu_buton())
    else:
        send_msg(cid, text, "Markdown", _ana_menu_buton())

def show_semboller(cid, message_id=None):
    text = "📋 *DESTEKLENEN SEMBOLLER*\n\n" + "\n".join([f"• {s}" for s in ALLOWED_SYMBOLS])
    if message_id:
        edit_message_text(cid, message_id, text, "Markdown", _ana_menu_buton())
    else:
        send_msg(cid, text, "Markdown", _ana_menu_buton())

def show_yardim(cid, message_id=None):
    text = (
        "❓ *YARDIM*\n\n"
        "📸 *Analiz nasıl yapılır?*\n"
        "1. MT5'te grafiği ayarla (üstte M1, altta M5 olacak şekilde)\n"
        "2. Screenshot al\n"
        "3. Bota gönder\n"
        "4. Caption'a sembolü yaz (örn: `GainX 999`)\n\n"
        "📸 *Alternatif: Albüm*\n"
        "• 2 ayrı fotoğraf seç (1. M1, 2. M5)\n"
        "• Caption birine ekle\n\n"
        "🎯 *Sonuç işaretleme:*\n"
        "Analizden sonra ✅ Tuttu / ❌ Tutmadı butonuna bas\n\n"
        "📊 *Komutlar:*\n"
        "/menu — Menü\n"
        "/gecmis — Geçmiş\n"
        "/istatistik — Başarı oranı\n"
        "/semboller — Sembol listesi\n\n"
        "⚠️ Yatırım tavsiyesi değildir."
    )
    if message_id:
        edit_message_text(cid, message_id, text, "Markdown", _ana_menu_buton())
    else:
        send_msg(cid, text, "Markdown", _ana_menu_buton())

# ==========================================
# KOMUTLAR
# ==========================================
def handle_command(cid, text):
    text = (text or "").strip()
    cmd = text.split()[0].lower() if text else ""

    if cmd in ("/menu", "/start"):
        show_menu(cid)
        return
    if cmd == "/yardim":
        show_yardim(cid)
        return
    if cmd == "/gecmis":
        show_gecmis(cid)
        return
    if cmd == "/istatistik":
        show_istatistik(cid)
        return
    if cmd == "/semboller":
        show_semboller(cid)
        return

    send_msg(cid, "ℹ️ Fotoğraf at ve altına sembol yaz. Menü için /menu")

# ==========================================
# CALLBACK
# ==========================================
def handle_callback(cq):
    try:
        cid = cq["message"]["chat"]["id"]
        message_id = cq["message"]["message_id"]
        data = cq.get("data", "")
        cb_id = cq["id"]

        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                      json={"callback_query_id": cb_id}, timeout=10)

        if data == "menu:ana":
            show_menu(cid, message_id); return
        if data == "menu:gecmis":
            show_gecmis(cid, message_id); return
        if data == "menu:istatistik":
            show_istatistik(cid, message_id); return
        if data == "menu:semboller":
            show_semboller(cid, message_id); return
        if data == "menu:yardim":
            show_yardim(cid, message_id); return

        if data.startswith("sonuc:"):
            _, sonuc, aid_str = data.split(":")
            aid = int(aid_str)
            if update_sonuc(aid, sonuc):
                edit_reply_markup(cid, message_id, None)
                emoji = "✅" if sonuc == "tuttu" else "❌"
                send_msg(cid, f"{emoji} Analiz #{aid} kaydedildi: {sonuc.upper()}")
            else:
                send_msg(cid, "⚠️ Bu analiz zaten işaretlenmiş.")
    except Exception as e:
        print(f"callback hatası: {e}", flush=True)

# ==========================================
# ALBÜM BUFFER
# ==========================================
ALBUM_BUFFER = {}
ALBUM_LOCK = threading.Lock()

def buffer_album_photo(mgid, msg):
    with ALBUM_LOCK:
        if mgid not in ALBUM_BUFFER:
            ALBUM_BUFFER[mgid] = {"photos": [], "ts": time.time(), "cid": msg["chat"]["id"]}
        ALBUM_BUFFER[mgid]["photos"].append(msg)
        ALBUM_BUFFER[mgid]["ts"] = time.time()

def get_ready_albums():
    ready = []
    now = time.time()
    with ALBUM_LOCK:
        to_delete = []
        for mgid, data in ALBUM_BUFFER.items():
            if len(data["photos"]) >= 2 or (now - data["ts"]) >= 2.0:
                ready.append((mgid, data))
                to_delete.append(mgid)
        for mgid in to_delete:
            del ALBUM_BUFFER[mgid]
    return ready

# ==========================================
# ANALİZ AKIŞI
# ==========================================
def process_analysis(cid, images_bytes_list, sembol, coklu):
    send_msg(cid, f"⏳ {sembol} analiz ediliyor... ({'M5+M1' if coklu else 'Tek Grafik'})")

    a = analyze_chart(images_bytes_list, cid, sembol, coklu=coklu)
    if not a:
        send_msg(cid, "❌ Analiz başarısız, tekrar deneyin.")
        return

    aid = save_analysis(cid, sembol, a)
    kart = build_card(a, sembol, coklu=coklu)

    reply_markup = None
    if a.get("yon") in ("LONG", "SHORT") and aid:
        reply_markup = {"inline_keyboard": [[
            {"text": "✅ Tuttu", "callback_data": f"sonuc:tuttu:{aid}"},
            {"text": "❌ Tutmadı", "callback_data": f"sonuc:tutmadi:{aid}"}
        ]]}

    # Mum çizimi: her zaman M1 fotoğrafına (index 0)
    gorsel = None
    if a.get("yon") != "BEKLE":
        m1_img = images_bytes_list[0]
        gorsel = draw_projection(
            m1_img, a.get("yon"),
            a.get("yol_puani", []), sembol,
            m1_bolge=a.get("m1_bolge"),
            m1_atr=a.get("m1_atr", 30),
            tek_foto=(not coklu)
        )

    if gorsel:
        if len(kart) > 1024:
            send_photo(cid, gorsel, caption=f"{sembol} | {a.get('yon')} | %{a.get('guven')}")
            send_msg(cid, kart, reply_markup=reply_markup)
        else:
            send_photo(cid, gorsel, caption=kart, reply_markup=reply_markup)
    else:
        send_msg(cid, kart, reply_markup=reply_markup)

# ==========================================
# ANA DÖNGÜ
# ==========================================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    init_db()
    print(f"=== SENTETİK ANALİZ BOTU v12 BAŞLADI (GEMINI {GEMINI_MODEL}) ===", flush=True)
    offset = get_offset()

    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                             params={"offset": offset, "timeout": 30}, timeout=40).json()

            for u in r.get("result", []):
                offset = u["update_id"] + 1
                save_offset(offset)

                if "callback_query" in u:
                    handle_callback(u["callback_query"])
                    continue

                msg = u.get("message", {})
                cid = msg.get("chat", {}).get("id")
                if not cid:
                    continue

                if "photo" in msg:
                    mgid = msg.get("media_group_id")
                    if mgid:
                        buffer_album_photo(mgid, msg)
                    else:
                        if not check_rate_limit(cid):
                            continue
                        caption = (msg.get("caption") or "").strip()
                        sembol = caption_to_symbol(caption)
                        if not sembol:
                            send_msg(cid, f"⚠️ Lütfen fotoğrafın altına sembolü tam yazın.\nÖrnek: `{ALLOWED_SYMBOLS[0]}`", parse_mode="Markdown")
                            continue
                        try:
                            img_bytes = get_file_bytes(msg["photo"][-1]["file_id"])
                            # Tek fotoğraf: her zaman PROMPT_TEMPLATE (üst M1, alt M5)
                            process_analysis(cid, [img_bytes], sembol, coklu=False)
                        except Exception as e:
                            send_msg(cid, f"❌ Hata: {str(e)[:200]}")
                    continue

                text = msg.get("text", "")
                if text:
                    handle_command(cid, text)
                    continue

                if not text and "photo" not in msg:
                    send_msg(cid, "ℹ️ Fotoğraf at ve altına sembol yaz. Menü için /menu")

            for mgid, data in get_ready_albums():
                try:
                    photos = data["photos"]
                    cid = data["cid"]

                    if not check_rate_limit(cid):
                        continue

                    sembol = None
                    for p in photos:
                        cap = (p.get("caption") or "").strip()
                        sembol = caption_to_symbol(cap)
                        if sembol:
                            break

                    if not sembol:
                        send_msg(cid, "⚠️ Albümdeki bir fotoğrafın altına sembolü yazın.\nÖrnek: `GainX 999`", parse_mode="Markdown")
                        continue

                    images = []
                    for p in photos:
                        fid = p["photo"][-1]["file_id"]
                        images.append(get_file_bytes(fid))

                    if len(images) >= 2:
                        process_analysis(cid, images, sembol, coklu=True)
                    else:
                        process_analysis(cid, images, sembol, coklu=False)

                except Exception as e:
                    print(f"Albüm işleme hatası: {e}", flush=True)
                    try:
                        send_msg(data["cid"], f"❌ Albüm hatası: {str(e)[:200]}")
                    except:
                        pass

        except Exception as e:
            print(f"=== LOOP HATASI: {e} ===", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
