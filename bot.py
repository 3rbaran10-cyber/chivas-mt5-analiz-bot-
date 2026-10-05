import os
import io, json, base64, time, math, requests, threading, re, sqlite3
from PIL import Image, ImageDraw, ImageFont
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

GEMINI_LOCK = threading.Lock()
SON_ISTEK_ZAMANI = [0.0]
MIN_ISTEK_ARASI = 3.0

# ==========================================
# ABONELİK AYARLARI
# ==========================================
ADMIN_ID = 5504006147
ABONELIK_YILDIZ = 4166
ABONELIK_GUN = 7
FREE_ANALIZ_HAKKI = 1  # Her kullanıcıya 1 ücretsiz analiz

# ==========================================
# DESTEKLENEN SEMBOLLER
# ==========================================
ALLOWED_SYMBOLS = [
    "GainX 1200", "GainX 999", "MAX GainX 1000", "MAX GainX 2000",
    "MAX PainX 1000", "MAX PainX 2000", "PainX 1200", "PainX 400",
    "PainX 800", "PainX 999",
]
ALLOWED_SYMBOLS_NORM = {
    s.lower().replace("-", " ").replace("/", " ").strip(): s for s in ALLOWED_SYMBOLS
}

SYMBOL_ATR = {
    "GainX 1200": 9, "GainX 999": 22, "MAX GainX 1000": 72, "MAX GainX 2000": 193,
    "MAX PainX 1000": 156, "MAX PainX 2000": 225, "PainX 1200": 9, "PainX 400": 10,
    "PainX 800": 7, "PainX 999": 19,
}
VARSAYILAN_ATR = 20

MIN_ATR_CARPAN = 1.0
MAX_ATR_CARPAN = 3.0

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
# ORTAK KURALLAR & PROMPT'LAR
# ==========================================
SEVIYE_KURALLARI = """
M1 ODAKLI İŞLEM:
Bu görselde M30, M15, M1 birlikte olabilir. Ama GİRİŞ SADECE M1 yapısına göre verilir.
M30 ve M15 SADECE TREND ONAYI için kullanılır (yön doğrulaması).

GİRİŞ NOKTASI:
Giriş noktasını M1'deki güncel fiyata YAKIN ver.
Anlık fiyattan en fazla 5-10 puan uzakta olsun.

SL/TP FİYATLARI (ÖNEMLİ):
Sana bu sembolün M1 ATR değeri bildirilecek.
- SL mesafesi: Bu ATR değerinin 1x - 3x arası olmalı
- TP1 mesafesi: SL mesafesiyle aynı veya biraz fazla (R/R 1:1 veya 1:1.5)
- TP2 mesafesi: TP1'in %20 üstü
- Bu sınırların dışına ÇIKMA.

YOL PUANI SIRALAMASI:
yol_puani dizisini YÖN ile uyumlu sırala.
- SHORT yönünde: ilk puan EN YÜKSEK (örn: 90), son puan EN DÜŞÜK (örn: 10) olmalı.
- LONG yönünde: ilk puan EN DÜŞÜK (örn: 10), son puan EN YÜKSEK (örn: 90) olmalı.
- BEKLE yönünde: yol_puani dizisi göndermek zorunlu değil.
"""

PROMPT_TEMPLATE = """Sen dünyanın en iyi {sembol} analiz uzmanısın. 15+ yıllık deneyimli profesyonelsin. Smart Money konseptlerini (ICT) derinlemesine bilirsin.

Sana {sembol} için BİR MT5 ekran görüntüsü gönderiliyor. Bu TEK bir fotoğraftır ama içinde YAN YANA bölünmüş 3 grafik olabilir: M30, M15, M1.

ÖNEMLİ BİLGİ:
{sembol} sembolünün M1 ATR değeri = {atr} puan.
SL mesafesi bu ATR'nin 1x - 3x arası olmalı (yani {min_sl} - {max_sl} puan arası).
TP1 mesafesi SL ile uyumlu olmalı.
TP2 mesafesi TP1'in %20 üstü.

GÖREV:
1. Görselde kaç zaman dilimi olduğunu tespit et.
2. Tüm TF'leri birlikte değerlendir (M30 ana trend, M15 onay, M1 giriş).
3. M1'deki yapıya göre GİRİŞ, SL, TP1, TP2 fiyatlarını ver.

""" + SEVIYE_KURALLARI + """
KULLANILACAK TEKNİKLER:
- Market yapısı: HH/LL, BOS, CHoCH
- Destek/direnç, Order Block, Supply/Demand
- VWAP, FVG, Liquidity Sweep
- EMA 20/50/200, RSI, MACD, Hacim
- Mum formasyonları
- Fibonacci retracement

KARAR KURALLARI:
1. Güven %65 altındaysa 'yon' = 'BEKLE'.
2. M30 ve M15 çelişiyorsa → 'BEKLE'.

M1 BÖLGE TESPİTİ:
Görselde M1 grafiğinin konumunu YÜZDE olarak bul (x, y, w, h: 0-100).

FORMAT: SADECE geçerli JSON. Türkçe yaz.

JSON ŞEMASI:
- sembol, yon ("LONG"|"SHORT"|"BEKLE"), guven (0-100)
- giris, stop_loss, take_profit (array)
- trend_m1, vwap_durumu, fvg_tespit, likidite_durumu
- destekler, direncler, formasyonlar, kullanilan_teknikler
- kisa_analiz, gerekce
- yol_puani (7 sayı, YÖN İLE UYUMLU)
- kalan_mum (1-3), mum_yonu, hareket_aciklamasi, sonraki_hamle, uyari
- m1_bolge (x, y, w, h)"""

PROMPT_TEMPLATE_MULTI = """Sen dünyanın en iyi {sembol} analiz uzmanısın. 15+ yıllık deneyimli profesyonelsin. Smart Money konseptlerini (ICT) derinlemesine bilirsin.

Sana {sembol} için birden fazla zaman diliminde grafik gönderiliyor (sırayla: M30 → M15 → M1).

ÖNEMLİ BİLGİ:
{sembol} sembolünün M1 ATR değeri = {atr} puan.
SL mesafesi bu ATR'nin 1x - 3x arası olmalı (yani {min_sl} - {max_sl} puan arası).
TP1 mesafesi SL ile uyumlu olmalı.
TP2 mesafesi TP1'in %20 üstü.

GÖREV:
1. Her grafiğin TF etiketini oku (M30, M15, M1).
2. Trendleri sırala (M30 ana, M15 onay, M1 giriş).
3. M1'e göre GİRİŞ, SL, TP1, TP2 ver.

ÇOKLU TF KURALLARI:
- M30 ve M15 aynı yön → güven yüksek
- M30 ve M15 çelişiyor → 'BEKLE'

""" + SEVIYE_KURALLARI + """
KULLANILACAK TEKNİKLER:
- Market yapısı: HH/LL, BOS, CHoCH
- Destek/direnç, Order Block, Supply/Demand
- VWAP, FVG, Liquidity Sweep
- EMA 20/50/200, RSI, MACD, Hacim
- Mum formasyonları
- Fibonacci retracement

KARAR KURALLARI:
1. Güven %65 altı → 'BEKLE'.

M1 BÖLGE TESPİTİ:
M1 grafiğinin konumunu YÜZDE olarak bul (x, y, w, h: 0-100).

FORMAT: SADECE geçerli JSON. Türkçe yaz.

JSON ŞEMASI:
- sembol, yon, guven, giris, stop_loss, take_profit
- trend_m1, vwap_durumu, fvg_tespit, likidite_durumu
- destekler, direncler, formasyonlar, kullanilan_teknikler
- kisa_analiz, gerekce
- yol_puani, kalan_mum, mum_yonu, hareket_aciklamasi, sonraki_hamle, uyari
- m1_bolge (x, y, w, h)"""

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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                cid INTEGER PRIMARY KEY,
                isim TEXT,
                abonelik_baslangic INTEGER,
                abonelik_bitis INTEGER,
                toplam_odeme INTEGER DEFAULT 0,
                free_used INTEGER DEFAULT 0
            )
        """)
        # Migration: free_used kolonu yoksa ekle
        try:
            conn.execute("ALTER TABLE users ADD COLUMN free_used INTEGER DEFAULT 0")
        except:
            pass
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

# ==========================================
# KULLANICI / ABONELİK FONKSİYONLARI
# ==========================================
def is_admin(cid):
    return int(cid) == int(ADMIN_ID)

def get_user(cid):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT cid, isim, abonelik_baslangic, abonelik_bitis, toplam_odeme, free_used FROM users WHERE cid=?", (cid,))
        row = cur.fetchone()
        conn.close()
        return row
    except:
        return None

def save_user(cid, isim=""):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR IGNORE INTO users (cid, isim) VALUES (?, ?)", (cid, isim))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"save_user hatası: {e}", flush=True)

def is_subscribed(cid):
    if is_admin(cid):
        return True
    user = get_user(cid)
    if not user:
        return False
    bitis = user[3] or 0
    return bitis > int(time.time())

def free_hakki_var(cid):
    """Kullanıcının ücretsiz analiz hakkı var mı?"""
    if is_admin(cid):
        return False  # Admin her zaman abone sayılır
    user = get_user(cid)
    if not user:
        return True  # Yeni kullanıcı, hakkı var
    free_used = user[5] if len(user) > 5 else 0
    return (free_used or 0) < FREE_ANALIZ_HAKKI

def free_hakki_kullan(cid):
    """Ücretsiz analiz hakkını kullanıldı olarak işaretle."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET free_used = COALESCE(free_used, 0) + 1 WHERE cid=?", (cid,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"free_hakki_kullan hatası: {e}", flush=True)

def kalan_sure_metni(cid):
    if is_admin(cid):
        return "Admin (süresiz)"
    user = get_user(cid)
    if not user:
        return "Abonelik yok"
    bitis = user[3] or 0
    kalan = bitis - int(time.time())
    if kalan <= 0:
        return "Süresi dolmuş"
    gun = kalan // 86400
    saat = (kalan % 86400) // 3600
    if gun > 0:
        return f"{gun} gün {saat} saat"
    return f"{saat} saat"

def activate_subscription(cid, gun=ABONELIK_GUN, yildiz=0):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT abonelik_bitis FROM users WHERE cid=?", (cid,))
        row = cur.fetchone()
        simdi = int(time.time())
        if row and row[0] and row[0] > simdi:
            yeni_bitis = row[0] + (gun * 86400)
        else:
            yeni_bitis = simdi + (gun * 86400)

        conn.execute("""
            INSERT INTO users (cid, abonelik_baslangic, abonelik_bitis, toplam_odeme)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cid) DO UPDATE SET
                abonelik_baslangic = COALESCE(users.abonelik_baslangic, ?),
                abonelik_bitis = ?,
                toplam_odeme = COALESCE(users.toplam_odeme, 0) + ?
        """, (cid, simdi, yeni_bitis, yildiz, simdi, yeni_bitis, yildiz))
        conn.commit()
        conn.close()
        return yeni_bitis
    except Exception as e:
        print(f"activate_subscription hatası: {e}", flush=True)
        return None

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
        cur = conn.execute("UPDATE analyses SET sonuc=? WHERE id=? AND sonuc IS NULL", (sonuc, analiz_id))
        degisti = cur.rowcount
        conn.commit()
        conn.close()
        return degisti > 0
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
# HİBRİT SL/TP KIRPMA
# ==========================================
def kirp_sl_tp(sembol, giris, gemini_sl, gemini_tp1, gemini_tp2):
    try:
        giris_f = float(str(giris).replace(",", "."))
        sl_f = float(str(gemini_sl).replace(",", "."))
        tp1_f = float(str(gemini_tp1).replace(",", "."))
        tp2_f = float(str(gemini_tp2).replace(",", "."))
    except (ValueError, TypeError):
        return None

    atr = SYMBOL_ATR.get(sembol, VARSAYILAN_ATR)
    min_mesafe = atr * MIN_ATR_CARPAN
    max_mesafe = atr * MAX_ATR_CARPAN

    gemini_sl_mesafe = abs(giris_f - sl_f)
    gemini_tp1_mesafe = abs(giris_f - tp1_f)
    gemini_tp2_mesafe = abs(giris_f - tp2_f)

    sl_kirp = max(min_mesafe, min(gemini_sl_mesafe, max_mesafe))
    tp1_kirp = max(min_mesafe, min(gemini_tp1_mesafe, max_mesafe))
    tp2_kirp = max(min_mesafe, min(gemini_tp2_mesafe, max_mesafe * 1.5))

    return {
        "giris": round(giris_f, 2),
        "atr": atr,
        "sl_mesafe": round(sl_kirp, 2),
        "tp1_mesafe": round(tp1_kirp, 2),
        "tp2_mesafe": round(tp2_kirp, 2),
    }

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
# GEMINI İSTEK YÖNETİCİSİ
# ==========================================
def gemini_istek_at(url, payload, headers):
    global SON_ISTEK_ZAMANI
    with GEMINI_LOCK:
        gecen = time.time() - SON_ISTEK_ZAMANI[0]
        if gecen < MIN_ISTEK_ARASI:
            time.sleep(MIN_ISTEK_ARASI - gecen)
        resp = requests.post(url, headers=headers, json=payload, timeout=300)
        SON_ISTEK_ZAMANI[0] = time.time()
        return resp

# ==========================================
# GEMINI ANALİZ
# ==========================================
def analyze_chart(images_bytes_list, cid, sembol, coklu=False):
    print(f"🔍 Analiz başladı (Sembol: {sembol}, Görsel: {len(images_bytes_list)}, Çoklu: {coklu})", flush=True)

    atr = SYMBOL_ATR.get(sembol, VARSAYILAN_ATR)
    min_sl = int(atr * MIN_ATR_CARPAN)
    max_sl = int(atr * MAX_ATR_CARPAN)

    template = PROMPT_TEMPLATE_MULTI if coklu else PROMPT_TEMPLATE
    prompt_text = template.format(sembol=sembol, atr=atr, min_sl=min_sl, max_sl=max_sl)

    parts = [{"text": prompt_text}]

    for img_bytes in images_bytes_list:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        b64 = base64.b64encode(buf.getvalue()).decode()
        del img, buf
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        del b64

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 1.0,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingLevel": "low"},
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
                "required": ["sembol", "yon", "guven", "giris", "stop_loss", "take_profit",
                             "kisa_analiz", "gerekce", "yol_puani", "kalan_mum",
                             "mum_yonu", "hareket_aciklamasi", "m1_bolge"]
            }
        }
    }

    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    bekleme_siralama = [10, 30, 60, 120]
    max_deneme = len(bekleme_siralama) + 1

    for deneme in range(max_deneme):
        try:
            resp = gemini_istek_at(GEMINI_URL, payload, headers)
            print(f"🔍 Gemini HTTP = {resp.status_code} (Deneme {deneme+1}/{max_deneme})", flush=True)

            if resp.status_code == 200:
                a = json_parse_et(resp, cid)
                if a:
                    return a
                return None

            elif resp.status_code in (429, 503):
                if deneme < max_deneme - 1:
                    bekleme = bekleme_siralama[deneme]
                    send_msg(cid, f"⏳ Sistem yoğun. {bekleme} sn sonra tekrar denenecek... ({deneme+1}/{max_deneme})")
                    time.sleep(bekleme)
                    continue
                else:
                    send_msg(cid, "❌ Sistem şu an meşgul. Lütfen 30 dakika sonra tekrar deneyin.")
                    return None
            else:
                send_msg(cid, f"❌ Gemini Hatası ({resp.status_code}): {resp.text[:400]}")
                return None
        except Exception as e:
            send_msg(cid, f"❌ Analiz Hatası: {str(e)[:200]}")
            return None

    return None

def json_parse_et(resp, cid):
    r = resp.json()
    try:
        text = r["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        try:
            fr = r["candidates"][0].get("finishReason", "?")
        except:
            fr = "?"
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
                send_msg(cid, "❌ Gemini cevabı bozuk JSON.")
                return None
        else:
            send_msg(cid, "❌ Gemini JSON vermedi.")
            return None

    if "giris" in a:
        a["giris"] = temizle_sayi(a["giris"])
    if "stop_loss" in a:
        a["stop_loss"] = temizle_sayi(a["stop_loss"])
    if "take_profit" in a and isinstance(a["take_profit"], list):
        a["take_profit"] = [temizle_sayi(x) for x in a["take_profit"]]

    try:
        g = int(a.get("guven", 0))
    except:
        g = 0
    if g < 65:
        a["yon"] = "BEKLE"

    try:
        km = int(a.get("kalan_mum", 1))
        if km < 1: km = 1
        if km > 3: km = 3
        a["kalan_mum"] = km
    except:
        a["kalan_mum"] = 1

    return a

# ==========================================
# PROJEKSİYON ÇİZİMİ
# ==========================================
def draw_projection(img_bytes, yon, puanlar, sembol="XAU/USD", m1_bolge=None):
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
        except:
            bx, by, bw, bh = 0, 0, W, H
    else:
        bx, by, bw, bh = 0, 0, W, H

    x1 = bx + int(bw * 0.80)
    x2 = bx + int(bw * 0.98)
    y_top = by + int(bh * 0.15)
    y_bot = by + int(bh * 0.85)

    n = len(temiz)
    noktalar = []
    for i, p in enumerate(temiz):
        x = x1 + (x2 - x1) * i / (n - 1)
        y = y_bot - (p / 100.0) * (y_bot - y_top)
        noktalar.append((x, y))

    for i in range(len(noktalar) - 1):
        draw.line([noktalar[i], noktalar[i+1]], fill=renk, width=6)

    (xa, ya), (xe, ye) = noktalar[-2], noktalar[-1]
    angle = math.atan2(ye - ya, xe - xa)
    arrow_len = 30
    arrow_angle = math.pi / 6
    p1 = (xe, ye)
    p2 = (xe - arrow_len * math.cos(angle - arrow_angle), ye - arrow_len * math.sin(angle - arrow_angle))
    p3 = (xe - arrow_len * math.cos(angle + arrow_angle), ye - arrow_len * math.sin(angle + arrow_angle))
    draw.polygon([p1, p2, p3], fill=renk)
    draw.ellipse([noktalar[0][0]-6, noktalar[0][1]-6, noktalar[0][0]+6, noktalar[0][1]+6], fill=renk)

    font_size = max(20, int(bh * 0.035))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except:
            font = ImageFont.load_default()

    draw.text((bx + 20, by + bh - 60), f"{sembol} | {yon} | 2 Dk Projeksiyon", fill=renk, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# ==========================================
# MESAJ KARTI
# ==========================================
def build_card(a, sembol="XAU/USD", coklu=False):
    yon_emoji = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "🟡"}
    e = yon_emoji.get(a.get("yon", "BEKLE"), "⚪")
    t = []
    t.append("╔══════════════════════════╗")
    baslik = f"📊 {sembol} ANALİZİ" + (" (M30+M15+M1)" if coklu else "")
    t.append(f"║  {baslik}")
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
        t.append("║  ⏸️  Sinyal yok")
        uy = a.get("uyari", "")
        if uy:
            t.append(f"║  ⚠️  {str(uy)[:38]}")

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
# ABONELİK MESAJI
# ==========================================
def abonelik_mesaji(cid):
    user = get_user(cid)
    free_used = user[5] if (user and len(user) > 5) else 0
    free_kaldi = max(0, FREE_ANALIZ_HAKKI - (free_used or 0))

    if free_kaldi > 0:
        uyari = f"🎁 *{free_kaldi} ÜCRETSİZ ANALİZ HAKKIN VAR!*\n\nFotoğraf at, hemen dene 👇"
    else:
        uyari = "🔒 Abonelik gerekli. Devam etmek için aşağıdaki butona bas."

    text = (
        "🔒 *ABONELİK GEREKLİ*\n\n"
        f"📅 Haftalık: {ABONELIK_GUN} gün\n"
        f"💰 Ücret: 100$ (≈ {ABONELIK_YILDIZ} ⭐)\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📖 *NASIL KULLANILIR?*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ MT5'te 1 grafik aç:\n"
        "   • Tek foto: M1 (hızlı analiz)\n"
        "   • 3 foto albüm: M30 + M15 + M1 (detaylı)\n\n"
        "2️⃣ Ekran görüntüsü al\n\n"
        "3️⃣ Bota gönder\n\n"
        "4️⃣ Fotoğrafın ALTINA (caption) sembolü yaz:\n"
        "   Örnek: `PainX 999`\n"
        "   Örnek: `GainX 1200`\n\n"
        "📋 *10 sembol destekleniyor.*\n"
        "Detaylı liste: /semboller\n\n"
        f"{uyari}"
    )
    markup = {
        "inline_keyboard": [
            [{"text": f"💳 Abone Ol ({ABONELIK_YILDIZ} ⭐)", "callback_data": "abone_ol"}],
            [{"text": "📋 Semboller", "callback_data": "menu:semboller"},
             {"text": "❓ Yardım", "callback_data": "menu:yardim"}],
            [{"text": "🤝 Ortak Ol", "callback_data": "menu:ortaklik"}]
        ]
    }
    return text, markup

def send_abonelik_invoice(cid):
    try:
        payload = {
            "chat_id": cid,
            "title": f"Haftalık Abonelik ({ABONELIK_GUN} Gün)",
            "description": f"{ABONELIK_GUN} gün boyunca botu sınırsız kullanma hakkı",
            "payload": f"sub_{ABONELIK_GUN}d_{int(time.time())}",
            "provider_token": "",
            "currency": "XTR",
            "prices": [
                {"label": "Haftalık Abonelik", "amount": ABONELIK_YILDIZ}
            ]
        }
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendInvoice",
                          json=payload, timeout=15)
        result = r.json()
        if not result.get("ok"):
            print(f"❌ Invoice hatası: {result}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"send_invoice hatası: {e}", flush=True)
        return False

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
            ],
            [
                {"text": "💳 Abonelik Durumu", "callback_data": "menu:abonelik"},
                {"text": "🤝 Ortaklık", "callback_data": "menu:ortaklik"}
            ]
        ]
    }

def _menu_text(cid):
    kalan = kalan_sure_metni(cid)
    return (
        "🤖 *CHIVAS MT5 ANALİZ BOTU*\n\n"
        f"👤 *Durum:* {kalan}\n\n"
        "📸 *Nasıl analiz yaparım?*\n"
        "• Tek fotoğraf (M1) → caption: `PainX 999`\n"
        "• Albüm (M30+M15+M1) → 3 foto tek seferde, caption: `PainX 999`\n\n"
        "⬇️ Aşağıdaki butonlardan seç:"
    )

def show_menu(cid, message_id=None):
    text = _menu_text(cid)
    if message_id:
        edit_message_text(cid, message_id, text, "Markdown", _menu_keyboard())
    else:
        send_msg(cid, text, "Markdown", _menu_keyboard())

def show_abonelik(cid, message_id=None):
    kalan = kalan_sure_metni(cid)
    text = (
        "💳 *ABONELİK DURUMU*\n\n"
        f"👤 *Durum:* {kalan}\n"
        f"📅 *Süre:* {ABONELIK_GUN} gün\n"
        f"💰 *Ücret:* 100$ (≈ {ABONELIK_YILDIZ} ⭐)\n\n"
        "Yenilemek için aşağıdaki butona bas:"
    )
    markup = {
        "inline_keyboard": [
            [{"text": f"🔄 Yenile ({ABONELIK_YILDIZ} ⭐)", "callback_data": "abone_ol"}],
            [{"text": "🔙 Ana Menü", "callback_data": "menu:ana"}]
        ]
    }
    if message_id:
        edit_message_text(cid, message_id, text, "Markdown", markup)
    else:
        send_msg(cid, text, "Markdown", markup)

def show_ortaklik(cid, message_id=None):
    text = (
        "🤝 *ORTAKLIK PROGRAMI*\n\n"
        "Botumuzu tanıt, para kazan! 💰\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📋 *NASIL ÇALIŞIR?*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ Aşağıdaki butona bas\n"
        "2️⃣ Telegram sana özel davet linki verir\n"
        "3️⃣ Linki kitlenle paylaş\n"
        "4️⃣ Gelen her müşteriden kazan!\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "💰 *KAZANÇ*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "• Komisyon: *%30*\n"
        "• Süre: *2 yıl*\n"
        "• Her müşteri: ~*1250 ⭐*\n\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "👇 Ortaklık programına katılmak için:"
    )
    markup = {
        "inline_keyboard": [
            [{"text": "🤝 Ortaklık Programına Katıl",
              "url": "https://t.me/CHIVAS_MT5_bot?start=affiliate"}],
            [{"text": "🔙 Ana Menü", "callback_data": "menu:ana"}]
        ]
    }
    if message_id:
        edit_message_text(cid, message_id, text, "Markdown", markup)
    else:
        send_msg(cid, text, "Markdown", markup)

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
    lines = ["📋 *DESTEKLENEN 10 SEMBOL*", ""]
    for s in ALLOWED_SYMBOLS:
        a = SYMBOL_ATR.get(s, VARSAYILAN_ATR)
        lines.append(f"• {s} — ATR: {a} puan")
    text = "\n".join(lines)
    if message_id:
        edit_message_text(cid, message_id, text, "Markdown", _ana_menu_buton())
    else:
        send_msg(cid, text, "Markdown", _ana_menu_buton())

def show_yardim(cid, message_id=None):
    sembol_listesi = "\n".join([f"• `{s}`" for s in ALLOWED_SYMBOLS])
    text = (
        "❓ *YARDIM*\n\n"
        "📸 *NASIL ANALİZ YAPILIR?*\n\n"
        "1. MT5'te grafiği aç\n"
        "2. Ekran görüntüsü al\n"
        "3. Bota gönder\n"
        "4. ALTINA sembolü yaz\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📋 *DESTEKLENEN 10 SEMBOL*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        + sembol_listesi + "\n\n"
        "⚠️ Bu liste dışındaki semboller\n"
        "desteklenmez.\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📸 *TEK vs ALBÜM*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "Tek foto:\n"
        "• Sadece M1 grafiği yeterli\n\n"
        "Albüm (3 foto):\n"
        "• Sıra: M30 → M15 → M1\n"
        "• 3 fotoğrafı TEK SEFERDE seç\n"
        "• Caption'ı birine ekle\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ *KOMUTLAR*\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "/menu — Ana menü\n"
        "/gecmis — Son 10 analiz\n"
        "/istatistik — Başarı oranı\n"
        "/semboller — Sembol listesi\n"
        "/abonelik — Abonelik durumu\n"
        "/ortaklik — Ortaklık programı"
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

    if is_admin(cid):
        if cmd == "/admin":
            try:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.execute("SELECT COUNT(*) FROM users WHERE abonelik_bitis > ?", (int(time.time()),))
                aktif = cur.fetchone()[0]
                cur = conn.execute("SELECT COUNT(*), COALESCE(SUM(toplam_odeme),0) FROM users")
                toplam, yildiz = cur.fetchone()
                cur = conn.execute("SELECT COUNT(*) FROM users WHERE COALESCE(free_used,0) > 0")
                free_kullanan = cur.fetchone()[0]
                conn.close()
                send_msg(cid, f"👑 *ADMIN PANEL*\n\n"
                              f"👥 Toplam kullanıcı: {toplam}\n"
                              f"✅ Aktif abone: {aktif}\n"
                              f"🎁 Ücretsiz deneyen: {free_kullanan}\n"
                              f"💰 Toplam yıldız: {yildiz}")
            except Exception as e:
                send_msg(cid, f"Hata: {e}")
            return

    if cmd in ("/menu", "/start"):
        show_menu(cid)
        return
    if cmd == "/yardim":
        show_yardim(cid); return
    if cmd == "/gecmis":
        show_gecmis(cid); return
    if cmd == "/istatistik":
        show_istatistik(cid); return
    if cmd == "/semboller":
        show_semboller(cid); return
    if cmd == "/abonelik":
        show_abonelik(cid); return
    if cmd == "/ortaklik":
        show_ortaklik(cid); return

    if not is_admin(cid) and not is_subscribed(cid) and not free_hakki_var(cid):
        text_, markup = abonelik_mesaji(cid)
        send_msg(cid, text_, "Markdown", markup)
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
        if data == "menu:abonelik":
            show_abonelik(cid, message_id); return
        if data == "menu:ortaklik":
            show_ortaklik(cid, message_id); return

        if data == "abone_ol":
            basarili = send_abonelik_invoice(cid)
            if not basarili:
                send_msg(cid, "❌ Ödeme başlatılamadı. Lütfen sonra tekrar deneyin.")
            return

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
# ÖDEME HANDLER'LARI
# ==========================================
def handle_pre_checkout(q):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerPreCheckoutQuery",
                      json={"pre_checkout_query_id": q["id"], "ok": True}, timeout=10)
    except Exception as e:
        print(f"pre_checkout hatası: {e}", flush=True)

def handle_successful_payment(msg):
    try:
        cid = msg["chat"]["id"]
        payment = msg.get("successful_payment", {})
        yildiz = payment.get("total_amount", ABONELIK_YILDIZ)
        isim = msg.get("from", {}).get("first_name", "")

        save_user(cid, isim)
        yeni_bitis = activate_subscription(cid, ABONELIK_GUN, yildiz)

        if yeni_bitis:
            bitis_str = time.strftime("%d.%m.%Y %H:%M", time.localtime(yeni_bitis))
            send_msg(
                cid,
                f"✅ *Ödeme Başarılı!*\n\n"
                f"👤 Hoş geldin {isim}!\n"
                f"📅 Abonelik: *{ABONELIK_GUN} gün*\n"
                f"⏰ Bitiş: *{bitis_str}*\n"
                f"💰 Ödenen: *{yildiz} ⭐*\n\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "📖 *NASIL KULLANILIR?*\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                "1️⃣ MT5'te grafiği aç\n\n"
                "2️⃣ Ekran görüntüsü al\n\n"
                "3️⃣ Bota gönder\n\n"
                "4️⃣ Fotoğrafın ALTINA (caption) sembolü yaz:\n"
                "   Örnek: `PainX 999`\n"
                "   Örnek: `GainX 1200`\n\n"
                "⚠️ Sembol yazmazsan analiz yapılmaz!\n\n"
                "📋 Sembol listesi: /semboller\n"
                "❓ Detaylı yardım: /yardim\n\n"
                "Hadi başlayalım! 🚀",
                parse_mode="Markdown"
            )
            if cid != ADMIN_ID:
                send_msg(
                    ADMIN_ID,
                    f"💰 *YENİ ABONE!*\n\n"
                    f"👤 {isim}\n"
                    f"🆔 `{cid}`\n"
                    f"⭐ {yildiz} yıldız\n"
                    f"⏰ Bitiş: {bitis_str}",
                    parse_mode="Markdown"
                )
        else:
            send_msg(cid, "⚠️ Ödeme alındı ama abonelik başlatılamadı. Lütfen admin ile iletişime geç.")
    except Exception as e:
        print(f"successful_payment hatası: {e}", flush=True)

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
            if len(data["photos"]) >= 3 or (now - data["ts"]) >= 3.0:
                ready.append((mgid, data))
                to_delete.append(mgid)
        for mgid in to_delete:
            del ALBUM_BUFFER[mgid]
    return ready

# ==========================================
# ANALİZ AKIŞI
# ==========================================
def process_analysis(cid, images_bytes_list, sembol, coklu):
    # Erişim kontrolü
    if not is_admin(cid) and not is_subscribed(cid) and not free_hakki_var(cid):
        text_, markup = abonelik_mesaji(cid)
        send_msg(cid, text_, "Markdown", markup)
        return

    # Ücretsiz kullanıcı mı?
    using_free = (not is_admin(cid)) and (not is_subscribed(cid)) and free_hakki_var(cid)

    send_msg(cid, f"⏳ {sembol} analiz ediliyor... ({'M30+M15+M1' if coklu else 'Tek Grafik'})")

    a = analyze_chart(images_bytes_list, cid, sembol, coklu=coklu)
    if not a:
        send_msg(cid, "❌ Analiz başarısız, tekrar deneyin.")
        return

    if a.get("yon") in ("LONG", "SHORT"):
        gemini_sl = a.get("stop_loss")
        gemini_tps = a.get("take_profit") or []
        gemini_tp1 = gemini_tps[0] if len(gemini_tps) > 0 else None
        gemini_tp2 = gemini_tps[1] if len(gemini_tps) > 1 else None

        if gemini_sl and gemini_tp1 and a.get("giris"):
            if not gemini_tp2:
                try:
                    g_f = float(str(a["giris"]).replace(",", "."))
                    tp1_f = float(str(gemini_tp1).replace(",", "."))
                    if a["yon"] == "SHORT":
                        gemini_tp2 = str(tp1_f - abs(g_f - tp1_f) * 0.2)
                    else:
                        gemini_tp2 = str(tp1_f + abs(g_f - tp1_f) * 0.2)
                except:
                    gemini_tp2 = gemini_tp1

            hesap = kirp_sl_tp(sembol, a.get("giris"), gemini_sl, gemini_tp1, gemini_tp2)
            if hesap:
                giris_f = hesap["giris"]
                if a["yon"] == "SHORT":
                    sl = giris_f + hesap["sl_mesafe"]
                    tp1 = giris_f - hesap["tp1_mesafe"]
                    tp2 = giris_f - hesap["tp2_mesafe"]
                else:
                    sl = giris_f - hesap["sl_mesafe"]
                    tp1 = giris_f + hesap["tp1_mesafe"]
                    tp2 = giris_f + hesap["tp2_mesafe"]

                a["giris"] = str(round(giris_f, 2))
                a["stop_loss"] = str(round(sl, 2))
                a["take_profit"] = [str(round(tp1, 2)), str(round(tp2, 2))]

                rr = hesap["tp1_mesafe"] / hesap["sl_mesafe"] if hesap["sl_mesafe"] > 0 else 0
                a["risk_odul"] = f"1:{round(rr, 2)}"
            else:
                a["yon"] = "BEKLE"
                a["uyari"] = "SL/TP hesaplanamadı."
        else:
            a["yon"] = "BEKLE"
            a["uyari"] = "SL/TP verisi eksik."

    # Ücretsiz hakkı kullan
    if using_free:
        free_hakki_kullan(cid)

    aid = save_analysis(cid, sembol, a)
    kart = build_card(a, sembol, coklu=coklu)

    # Ücretsiz kullanıcıysa karta uyarı ekle
    if using_free:
        kart += "\n\n━━━━━━━━━━━━━━━━━━━\n"
        kart += "🎁 *Bu senin 1 ÜCRETSİZ analizin!*\n\n"
        kart += "Devam etmek için abone ol:\n"
        kart += f"💳 /abonelik — Haftalık 100$ ({ABONELIK_YILDIZ} ⭐)"

    reply_markup = None
    if a.get("yon") in ("LONG", "SHORT") and aid:
        reply_markup = {"inline_keyboard": [[
            {"text": "✅ Tuttu", "callback_data": f"sonuc:tuttu:{aid}"},
            {"text": "❌ Tutmadı", "callback_data": f"sonuc:tutmadi:{aid}"}
        ]]}

    gorsel = None
    if a.get("yon") != "BEKLE":
        gorsel = draw_projection(
            images_bytes_list[-1], a.get("yon"),
            a.get("yol_puani", []), sembol, m1_bolge=a.get("m1_bolge")
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
    print(f"=== SENTETİK ANALİZ BOTU v34 BAŞLADI (1 ÜCRETSİZ ANALİZ) ===", flush=True)
    print(f"=== Admin: {ADMIN_ID} | Haftalık: {ABONELIK_GUN} gün / {ABONELIK_YILDIZ} ⭐ ===", flush=True)
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

                if "pre_checkout_query" in u:
                    handle_pre_checkout(u["pre_checkout_query"])
                    continue

                msg = u.get("message", {})
                cid = msg.get("chat", {}).get("id")
                if not cid:
                    continue

                if "successful_payment" in msg:
                    handle_successful_payment(msg)
                    continue

                isim = msg.get("from", {}).get("first_name", "")
                if isim:
                    save_user(cid, isim)

                if "photo" in msg:
                    # Erişim kontrolü
                    if not is_admin(cid) and not is_subscribed(cid) and not free_hakki_var(cid):
                        text_, markup = abonelik_mesaji(cid)
                        send_msg(cid, text_, "Markdown", markup)
                        continue

                    mgid = msg.get("media_group_id")
                    if mgid:
                        buffer_album_photo(mgid, msg)
                    else:
                        if not check_rate_limit(cid):
                            continue
                        caption = (msg.get("caption") or "").strip()

                        if not caption:
                            send_msg(
                                cid,
                                "⚠️ *Sembol yazmadınız!*\n\n"
                                "Lütfen fotoğrafın altına (caption) sembolü yazın.\n\n"
                                "📋 Sembol listesi: /semboller\n\n"
                                "Örnek: `PainX 999`",
                                parse_mode="Markdown"
                            )
                            continue

                        sembol = caption_to_symbol(caption)

                        if not sembol:
                            send_msg(
                                cid,
                                "⚠️ *Bu sembol desteklenmiyor.*\n\n"
                                "📋 Desteklenen sembol listesi: /semboller",
                                parse_mode="Markdown"
                            )
                            continue

                        try:
                            img_bytes = get_file_bytes(msg["photo"][-1]["file_id"])
                            process_analysis(cid, [img_bytes], sembol, coklu=False)
                        except Exception as e:
                            send_msg(cid, f"❌ Hata: {str(e)[:200]}")
                    continue

                text = msg.get("text", "")
                if text:
                    handle_command(cid, text)
                    continue

                if not text and "photo" not in msg:
                    if not is_admin(cid) and not is_subscribed(cid) and not free_hakki_var(cid):
                        text_, markup = abonelik_mesaji(cid)
                        send_msg(cid, text_, "Markdown", markup)
                    else:
                        send_msg(cid, "ℹ️ Fotoğraf at ve altına sembol yaz. Menü için /menu")

            for mgid, data in get_ready_albums():
                try:
                    photos = data["photos"]
                    cid = data["cid"]

                    if not is_admin(cid) and not is_subscribed(cid) and not free_hakki_var(cid):
                        continue

                    if not check_rate_limit(cid):
                        continue

                    sembol = None
                    for p in photos:
                        cap = (p.get("caption") or "").strip()
                        s = caption_to_symbol(cap)
                        if s:
                            sembol = s
                            break

                    if not sembol:
                        send_msg(
                            cid,
                            "⚠️ *Sembol yazmadınız!*\n\n"
                            "Albümdeki bir fotoğrafın altına sembolü ekleyin.\n\n"
                            "📋 Sembol listesi: /semboller",
                            parse_mode="Markdown"
                        )
                        continue

                    images = []
                    for p in photos[:3]:
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
