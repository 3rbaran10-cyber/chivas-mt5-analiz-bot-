import os
import io, json, base64, time, math, requests, threading, re, sqlite3, ssl, warnings
from PIL import Image, ImageDraw, ImageFont
from http.server import BaseHTTPRequestHandler, HTTPServer

import websocket
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

warnings.filterwarnings("ignore")

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

GEMINI_MODEL = "gemini-3.1-pro-preview"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

GEMINI_LOCK = threading.Lock()
SON_ISTEK_ZAMANI = [0.0]
MIN_ISTEK_ARASI = 3.0

# ==========================================
# OZEL KULLANIM
# ==========================================
ADMIN_ID = 5504006147

# ==========================================
# DERIV AYARLARI
# ==========================================
DERIV_APP_ID = "1089"
DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

# (Görünen isim, API kodu)
SYMBOLS = [
    ("Crash 600 Index",  "CRASH600"),
    ("Crash 900 Index",  "CRASH900"),
    ("Crash 1000 Index", "CRASH1000"),
    ("Boom 600 Index",   "BOOM600"),
    ("Boom 900 Index",   "BOOM900"),
    ("Boom 1000 Index",  "BOOM1000"),
]

SYMBOL_MAP = {kod: isim for isim, kod in SYMBOLS}

# TF granularity (saniye)
TF_M30 = 1800
TF_M15 = 900
TF_M1  = 60

ATR_PERIOD = 14
VARSAYILAN_ATR = 50
MIN_ATR_CARPAN = 1.0
MAX_ATR_CARPAN = 3.0

# Cache
_ATR_CACHE = {}      # {symbol: (atr, ts)}
_ATR_CACHE_TTL = 60

_CANDLE_CACHE = {}   # {(symbol, tf): (candles, ts)}
_CANDLE_CACHE_TTL = 30

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
# ORTAK KURALLAR
# ==========================================
SEVIYE_KURALLARI = """
M1 ODAKLI ISLEM:
Bu gorselde M30, M15, M1 yan yana gosteriliyor. GIRIS SADECE M1 yapisina gore verilir.
M30 ve M15 SADECE TREND ONAYI icin kullanilir.

GIRIS NOKTASI:
Giris noktasini M1'deki guncel fiyata YAKIN ver.
Sana asagida "GUNCEL FIYAT" bildirilecek. Giris noktasi bu fiyata cok yakin olsun.

SL/TP FIYATLARI (ONEMLI):
Sana bu sembolun M1 ATR degeri bildirilecek.
- SL mesafesi: Bu ATR degerinin 1x - 3x arasi olmali
- TP1 mesafesi: SL mesafesiyle ayni veya biraz fazla (R/R 1:1 veya 1:1.5)
- TP2 mesafesi: TP1'in %20 ustu
- Bu sinirlarin disina CIKMA.

YOL PUANI SIRALAMASI:
yol_puani dizisini YON ile uyumlu sirala.
- SHORT yonunde: ilk puan EN YUKSEK (orn: 90), son puan EN DUSUK (orn: 10) olmali.
- LONG yonunde: ilk puan EN DUSUK (orn: 10), son puan EN YUKSEK (orn: 90) olmali.
- BEKLE yonunde: yol_puani dizisi gondermek zorunlu degil.
"""

PROMPT_TEMPLATE = """Sen dunyanin en iyi {sembol} analiz uzmanisin. 15+ yillik deneyimli profesyonelsin. Smart Money konseptlerini (ICT) derinlesine bilirsin.

Sana {sembol} icin UC ayri grafik yan yana gonderiliyor: SOL = M30, ORTA = M15, SAG = M1.

CANLI VERILER:
- GUNCEL FIYAT: {fiyat}
- M1 ATR (14): {atr} puan
- M15 ATR (14): {atr_m15} puan
- M30 ATR (14): {atr_m30} puan
- SL mesafesi M1 ATR'nin 1x - 3x arasi olmali (yani {min_sl} - {max_sl} puan arasi).

GOREV:
1. Uc grafigi birlikte degerlendir:
   - M30 -> ana trend yonu
   - M15 -> orta vade yapi ve onay
   - M1  -> GIRIS icin TEK referans
2. M1'deki yapiya gore GIRIS, SL, TP1, TP2 fiyatlarini ver.
3. Giris noktasi GUNCEL FIYAT'a cok yakin olmali.

""" + SEVIYE_KURALLARI + """
KULLANILACAK TEKNIKLER:
- Market yapisi: HH/LL, BOS, CHoCH
- Destek/direnc, Order Block, Supply/Demand
- VWAP, FVG, Liquidity Sweep
- EMA 20/50/200, RSI, MACD, Hacim
- Mum formasyonlari (engulfing, pin bar, doji, hammer)
- Fibonacci retracement

KARAR KURALLARI:
1. Guven %65 altindaysa 'yon' = 'BEKLE'.
2. Guven oranini degisken ver (%50, %65, %75, %85, %95).
3. M30 ve M15 celisiyorsa -> guven dusur veya 'BEKLE' ver.

MUM SAYISI: M1 grafiginde 1-3 mum arasi ver.

FORMAT: SADECE gecerli JSON. Sayilarda NOKTA kullan. Turkce yaz.

JSON SEMASI:
- sembol, yon ("LONG"|"SHORT"|"BEKLE"), guven (0-100)
- giris (string, GUNCEL FIYAT'a yakin)
- stop_loss (string, fiyat)
- take_profit (array, [tp1_fiyat, tp2_fiyat])
- trend_m1, vwap_durumu, fvg_tespit, likidite_durumu
- destekler (array), direncler (array), formasyonlar (array)
- kullanilan_teknikler (array)
- kisa_analiz, gerekce
- yol_puani (array, 7 sayi 0-100, YON ILE UYUMLU SIRALI)
- kalan_mum (1-3), mum_yonu, hareket_aciklamasi, sonraki_hamle, uyari
"""

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
        print("DB hazir", flush=True)
    except Exception as e:
        print(f"DB init hatasi: {e}", flush=True)

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
        print(f"Offset hatasi: {e}", flush=True)

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
        print(f"save_analysis hatasi: {e}", flush=True)
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
        print(f"update_sonuc hatasi: {e}", flush=True)
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
        print(f"istatistik hatasi: {e}", flush=True)
        return {"total": 0, "tuttu": 0, "tutmadi": 0, "semboller": []}

# ==========================================
# TELEGRAM YARDIMCILARI
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
        print(f"send_msg hatasi: {e}", flush=True)
        return None

def send_photo(cid, photo_bytes, caption="", reply_markup=None):
    try:
        data = {"chat_id": cid, "caption": caption[:1024]}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                          data=data, files={"photo": ("chart.png", photo_bytes)}, timeout=60)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"send_photo hatasi: {e}", flush=True)
        return None

def edit_reply_markup(cid, message_id, reply_markup=None):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageReplyMarkup",
                      json={"chat_id": cid, "message_id": message_id,
                            "reply_markup": reply_markup or {"inline_keyboard": []}},
                      timeout=10)
    except Exception as e:
        print(f"edit_markup hatasi: {e}", flush=True)

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
        print(f"edit_text hatasi: {e}", flush=True)

def temizle_sayi(deger):
    if deger is None:
        return deger
    s = str(deger).strip()
    if ',' in s and '.' not in s:
        s = s.replace(',', '.')
    return s

# ==========================================
# DERIV API
# ==========================================
_deriv_lock = threading.Lock()

def deriv_call(payload, timeout=20):
    """Deriv WebSocket'e tek istek at, cevabı döndür."""
    req_id = payload.get("req_id", 1)
    with _deriv_lock:
        try:
            ws = websocket.create_connection(
                DERIV_WS_URL, timeout=timeout,
                sslopt={"cert_reqs": ssl.CERT_NONE}
            )
        except Exception as e:
            print(f"Deriv baglanti hatasi: {e}", flush=True)
            return None
        try:
            ws.send(json.dumps(payload))
            while True:
                raw = ws.recv()
                if not raw:
                    return None
                msg = json.loads(raw)
                if msg.get("req_id") == req_id:
                    if "error" in msg:
                        print(f"Deriv hata: {msg['error']}", flush=True)
                        return None
                    return msg
        except Exception as e:
            print(f"Deriv recv hatasi: {e}", flush=True)
            return None
        finally:
            try:
                ws.close()
            except:
                pass

def get_candles(symbol, granularity, count=100):
    """Deriv'den mum verisi çek (cache'li)."""
    key = (symbol, granularity)
    now = time.time()
    cached = _CANDLE_CACHE.get(key)
    if cached and (now - cached[1]) < _CANDLE_CACHE_TTL:
        return cached[0]

    payload = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": count,
        "end": "latest",
        "granularity": granularity,
        "style": "candles",
        "req_id": 1
    }
    resp = deriv_call(payload)
    if not resp:
        return None
    candles = resp.get("candles") or []
    if candles:
        _CANDLE_CACHE[key] = (candles, now)
    return candles

def get_current_price(symbol):
    """Son tick fiyatını al."""
    payload = {
        "ticks_history": symbol,
        "style": "ticks",
        "count": 1,
        "end": "latest",
        "req_id": 2
    }
    resp = deriv_call(payload)
    if not resp:
        return None
    prices = (resp.get("history") or {}).get("prices") or []
    return float(prices[-1]) if prices else None

def calculate_atr(candles, period=14):
    if not candles or len(candles) < 2:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i-1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return None
    n = min(period, len(trs))
    return round(sum(trs[-n:]) / n, 4)

def resolve_atr(symbol):
    """M1 ATR (cache'li)."""
    now = time.time()
    cached = _ATR_CACHE.get(symbol)
    if cached and (now - cached[1]) < _ATR_CACHE_TTL:
        return cached[0]
    candles = get_candles(symbol, TF_M1, ATR_PERIOD + 5)
    atr = calculate_atr(candles, ATR_PERIOD) if candles else None
    if atr and atr > 0:
        _ATR_CACHE[symbol] = (atr, now)
        return atr
    return VARSAYILAN_ATR

# ==========================================
# GRAFIK CIZIMI (M30 | M15 | M1)
# ==========================================
def _ciz_candles(ax, candles, baslik, renk_bas="green", renk_dus="red"):
    if not candles:
        ax.set_title(f"{baslik} (veri yok)")
        return
    for i, c in enumerate(candles):
        try:
            o = float(c['open']); h = float(c['high'])
            l = float(c['low']);  cl = float(c['close'])
        except (KeyError, ValueError, TypeError):
            continue
        renk = renk_bas if cl >= o else renk_dus
        ax.plot([i, i], [l, h], color=renk, linewidth=0.7)
        alt = min(o, cl)
        yuk = abs(cl - o) if cl != o else (h - l) * 0.01
        ax.add_patch(Rectangle((i - 0.3, alt), 0.6, yuk,
                                facecolor=renk, edgecolor=renk))
    ax.set_title(baslik, fontsize=13, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.set_xlim(-1, len(candles))

def draw_chart_3tf(symbol, isim, fiyat, atr_m1, atr_m15, atr_m30):
    """M30|M15|M1 3'lü mum grafiği çiz ve PNG bytes dön."""
    m30 = get_candles(symbol, TF_M30, 60)
    m15 = get_candles(symbol, TF_M15, 60)
    m1  = get_candles(symbol, TF_M1,  60)

    if not m1:
        return None

    fig, axes = plt.subplots(1, 3, figsize=(20, 7))
    fig.suptitle(f"{isim}  |  Guncel Fiyat: {fiyat}  |  M1 ATR: {atr_m1}",
                 fontsize=15, fontweight='bold')

    _ciz_candles(axes[0], m30, f"M30  (ATR: {atr_m30})")
    _ciz_candles(axes[1], m15, f"M15  (ATR: {atr_m15})")
    _ciz_candles(axes[2], m1,  f"M1  (ATR: {atr_m1})")

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    buf = io.BytesIO()
    plt.savefig(buf, format="PNG", dpi=100, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

# ==========================================
# KIRPMA (SL/TP)
# ==========================================
def kirp_sl_tp(symbol, giris, gemini_sl, gemini_tp1, gemini_tp2):
    try:
        giris_f = float(str(giris).replace(",", "."))
        sl_f    = float(str(gemini_sl).replace(",", "."))
        tp1_f   = float(str(gemini_tp1).replace(",", "."))
        tp2_f   = float(str(gemini_tp2).replace(",", "."))
    except (ValueError, TypeError):
        return None

    atr = resolve_atr(symbol)
    min_mesafe = atr * MIN_ATR_CARPAN
    max_mesafe = atr * MAX_ATR_CARPAN

    sl_mesafe_g  = abs(giris_f - sl_f)
    tp1_mesafe_g = abs(giris_f - tp1_f)
    tp2_mesafe_g = abs(giris_f - tp2_f)

    sl_kirp  = max(min_mesafe, min(sl_mesafe_g,  max_mesafe))
    tp1_kirp = max(min_mesafe, min(tp1_mesafe_g, max_mesafe))
    tp2_kirp = max(min_mesafe, min(tp2_mesafe_g, max_mesafe * 1.5))

    return {
        "giris": round(giris_f, 4),
        "atr": atr,
        "min_mesafe": round(min_mesafe, 4),
        "max_mesafe": round(max_mesafe, 4),
        "sl_mesafe":  round(sl_kirp, 4),
        "tp1_mesafe": round(tp1_kirp, 4),
        "tp2_mesafe": round(tp2_kirp, 4),
    }

# ==========================================
# RATE LIMIT
# ==========================================
USER_COOLDOWN = {}
RATE_LIMIT_SECONDS = 15

def check_rate_limit(cid):
    now = time.time()
    last = USER_COOLDOWN.get(cid, 0)
    if now - last < RATE_LIMIT_SECONDS:
        kalan = int(RATE_LIMIT_SECONDS - (now - last))
        send_msg(cid, f"⏳ Çok hızlı. {kalan} saniye bekleyin.")
        return False
    USER_COOLDOWN[cid] = now
    return True

# ==========================================
# GEMINI
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

def analyze_chart(image_bytes, cid, symbol, isim, fiyat, atr_m1, atr_m15, atr_m30):
    print(f"Analiz basladi: {isim} ({symbol})", flush=True)

    min_sl = round(atr_m1 * MIN_ATR_CARPAN, 2)
    max_sl = round(atr_m1 * MAX_ATR_CARPAN, 2)

    prompt_text = PROMPT_TEMPLATE.format(
        sembol=isim, fiyat=fiyat,
        atr=atr_m1, atr_m15=atr_m15, atr_m30=atr_m30,
        min_sl=min_sl, max_sl=max_sl
    )

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    b64 = base64.b64encode(buf.getvalue()).decode()
    del img, buf

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
            ]
        }],
        "generationConfig": {
            "temperature": 1.0,
            "maxOutputTokens": 16384,
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
                },
                "required": ["sembol", "yon", "guven", "giris", "stop_loss",
                             "take_profit", "kisa_analiz", "gerekce",
                             "kalan_mum", "mum_yonu", "hareket_aciklamasi"]
            }
        }
    }

    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    bekleme_siralama = [5, 15, 30]
    max_deneme = len(bekleme_siralama) + 1

    for deneme in range(max_deneme):
        try:
            resp = gemini_istek_at(GEMINI_URL, payload, headers)
            print(f"Gemini HTTP = {resp.status_code} (Deneme {deneme+1}/{max_deneme})", flush=True)

            if resp.status_code == 200:
                return json_parse_et(resp, cid)

            elif resp.status_code in (429, 503):
                if deneme < max_deneme - 1:
                    bekleme = bekleme_siralama[deneme]
                    send_msg(cid, f"⏳ Model yoğun. {bekleme} sn sonra tekrar... ({deneme+1}/{max_deneme})")
                    time.sleep(bekleme)
                    continue
                else:
                    send_msg(cid, "❌ Model şu an yanıt vermiyor.")
                    return None
            else:
                send_msg(cid, f"❌ Gemini Hatası ({resp.status_code}): {resp.text[:300]}")
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
        send_msg(cid, f"❌ Gemini boş cevap. (Sebep: {fr})")
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
        bas = text.find('{')
        son = text.rfind('}')
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
        a["kalan_mum"] = max(1, min(3, km))
    except:
        a["kalan_mum"] = 1

    return a

# ==========================================
# MESAJ KARTI
# ==========================================
def build_card(a, isim):
    yon_emoji = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "🟡"}
    e = yon_emoji.get(a.get("yon", "BEKLE"), "⚪")
    t = []
    t.append("╔══════════════════════════╗")
    t.append(f"║  📊 {isim} ANALİZİ")
    t.append("╠══════════════════════════╣")
    t.append(f"║  {e} YÖN: {a.get('yon','?')}")
    t.append(f"║  🎯 GÜVEN: %{a.get('guven','?')}")
    t.append("╠══════════════════════════╣")

    if a.get("yon") != "BEKLE":
        t.append(f"║  💰 GİRİŞ: {a.get('giris','?')}")
        t.append(f"║  🛑 SL:    {a.get('stop_loss','?')}")
        tps = a.get("take_profit", [])
        for i, tp in enumerate(tps, 1):
            t.append(f"║  ✅ TP{i}:   {tp}")
        if a.get("risk_odul"):
            t.append(f"║  ⚖️ R/R:   {a.get('risk_odul')}")
    else:
        t.append("║  ⏸️  Sinyal yok")
        if a.get("uyari"):
            t.append(f"║  ⚠️  {str(a['uyari'])[:38]}")

    t.append("╚══════════════════════════╝")
    t.append("")
    t.append("📈 TREND ANALİZİ")
    t.append(f"• M1: {a.get('trend_m1','?')}")
    if a.get("vwap_durumu"):
        t.append(f"• VWAP: {a['vwap_durumu']}")

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
    gerekce_text = str(a.get("gerekce", "")).replace("\\n", "\n")
    for g in gerekce_text.split("\n"):
        if g.strip():
            t.append(f"• {g.strip()}")

    t.append("")
    t.append(f"⚠️ {a.get('uyari','Yatırım tavsiyesi değildir.')}")
    return "\n".join(t)

# ==========================================
# MENULER
# ==========================================
def _ana_menu_buton():
    return {"inline_keyboard": [[{"text": "🔙 Ana Menü", "callback_data": "menu:ana"}]]}

def _menu_keyboard():
    # 6 sembol + menü butonları
    satir1 = [{"text": "🔴 Crash 600",  "callback_data": "analiz:CRASH600"}]
    satir1.append({"text": "🔴 Crash 900",  "callback_data": "analiz:CRASH900"})
    satir1.append({"text": "🔴 Crash 1000", "callback_data": "analiz:CRASH1000"})

    satir2 = [{"text": "🟢 Boom 600",  "callback_data": "analiz:BOOM600"}]
    satir2.append({"text": "🟢 Boom 900",  "callback_data": "analiz:BOOM900"})
    satir2.append({"text": "🟢 Boom 1000", "callback_data": "analiz:BOOM1000"})

    satir3 = [
        {"text": "📜 Geçmiş", "callback_data": "menu:gecmis"},
        {"text": "📊 İstatistik", "callback_data": "menu:istatistik"}
    ]
    satir4 = [
        {"text": "❓ Yardım", "callback_data": "menu:yardim"}
    ]
    return {"inline_keyboard": [satir1, satir2, satir3, satir4]}

def _menu_text():
    return (
        "🤖 *DERIV SENTETIK ANALIZ BOTU*\n\n"
        "📊 Analiz için bir sembol seç:\n"
        "• Bot M30 + M15 + M1 grafiğini çeker\n"
        "• ATR hesaplar\n"
        "• Gemini ile analiz eder\n"
        "• Sinyal gönderir\n\n"
        "⬇️ Sembol seç:"
    )

def show_menu(cid, message_id=None):
    if message_id:
        edit_message_text(cid, message_id, _menu_text(), "Markdown", _menu_keyboard())
    else:
        send_msg(cid, _menu_text(), "Markdown", _menu_keyboard())

def show_gecmis(cid, message_id=None):
    rows = get_gecmis(cid, 10)
    if not rows:
        text = "📭 *Geçmiş boş*\n\nHenüz analiz yapmadın."
    else:
        e = {"LONG": "🟢", "SHORT": "🔴", "BEKLE": "🟡"}
        s = {"tuttu": "✅", "tutmadi": "❌", None: "⏳"}
        satirlar = ["📜 *SON 10 ANALİZ*", ""]
        for i, (aid, sembol, yon, guven, sonuc, ts) in enumerate(rows, 1):
            tarih = time.strftime("%d.%m %H:%M", time.localtime(ts))
            satirlar.append(f"{i}. {e.get(yon,'⚪')} *{sembol}* — {yon} — %{guven}")
            satirlar.append(f"     {s.get(sonuc,'?')} {tarih}")
        text = "\n".join(satirlar)

    if message_id:
        edit_message_text(cid, message_id, text, "Markdown", _ana_menu_buton())
    else:
        send_msg(cid, text, "Markdown", _ana_menu_buton())

def show_istatistik(cid, message_id=None):
    st = get_istatistik(cid)
    if st["total"] == 0:
        text = "📭 *İstatistik yok*"
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

def show_yardim(cid, message_id=None):
    text = (
        "❓ *YARDIM*\n\n"
        "📊 *Analiz nasıl yapılır?*\n"
        "1. /menu yaz veya /start\n"
        "2. Sembol butonuna bas\n"
        "3. Bot otomatik analiz eder\n\n"
        "🖼️ *Bot ne yapar?*\n"
        "• Deriv'den M30 + M15 + M1 mumlarını çeker\n"
        "• Her TF için ATR hesaplar\n"
        "• 3'lü mum grafiği çizer\n"
        "• Gemini ile analiz eder\n"
        "• Telegram'a gönderir\n\n"
        "📋 *Semboller:*\n"
        "• Crash 600 / 900 / 1000\n"
        "• Boom 600 / 900 / 1000\n\n"
        "🎯 *Sonuç işaretleme:*\n"
        "Analizden sonra ✅ Tuttu / ❌ Tutmadı\n\n"
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
    if cmd == "/analiz":
        parts = text.split()
        if len(parts) < 2:
            send_msg(cid, "Kullanım: `/analiz CRASH1000`", "Markdown")
            return
        kod = parts[1].upper().replace(" ", "")
        if kod not in SYMBOL_MAP:
            send_msg(cid, f"❌ Bilinmeyen sembol: `{parts[1]}`\nMenü için /menu", "Markdown")
            return
        threading.Thread(target=process_analysis, args=(cid, kod), daemon=True).start()
        return

    send_msg(cid, "ℹ️ /menu yazarak başlayın.")

def handle_callback(cq):
    try:
        cid = cq["message"]["chat"]["id"]
        message_id = cq["message"]["message_id"]
        data = cq.get("data", "")
        cb_id = cq["id"]

        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                      json={"callback_query_id": cb_id}, timeout=10)

        if int(cid) != int(ADMIN_ID):
            send_msg(cid, "🔒 Bu bot özel kullanımdadır.")
            return

        if data == "menu:ana":
            show_menu(cid, message_id); return
        if data == "menu:gecmis":
            show_gecmis(cid, message_id); return
        if data == "menu:istatistik":
            show_istatistik(cid, message_id); return
        if data == "menu:yardim":
            show_yardim(cid, message_id); return

        if data.startswith("analiz:"):
            kod = data.split(":", 1)[1]
            if kod not in SYMBOL_MAP:
                send_msg(cid, f"❌ Bilinmeyen sembol: {kod}")
                return
            if not check_rate_limit(cid):
                return
            edit_reply_markup(cid, message_id, _ana_menu_buton())
            threading.Thread(target=process_analysis, args=(cid, kod), daemon=True).start()
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
        print(f"callback hatasi: {e}", flush=True)

# ==========================================
# ANALIZ AKISI
# ==========================================
def process_analysis(cid, symbol):
    isim = SYMBOL_MAP[symbol]
    send_msg(cid, f"⏳ *{isim}* analiz ediliyor...\nM30 + M15 + M1 çekiliyor...", "Markdown")

    # 1. Fiyat
    fiyat = get_current_price(symbol)
    if not fiyat:
        send_msg(cid, f"❌ {isim} için anlık fiyat alınamadı.")
        return

    # 2. Mumlar + ATR
    m1  = get_candles(symbol, TF_M1,  100)
    m15 = get_candles(symbol, TF_M15, 100)
    m30 = get_candles(symbol, TF_M30, 100)

    if not m1:
        send_msg(cid, f"❌ {isim} için M1 verisi alınamadı.")
        return

    atr_m1  = calculate_atr(m1,  ATR_PERIOD) or VARSAYILAN_ATR
    atr_m15 = calculate_atr(m15, ATR_PERIOD) or VARSAYILAN_ATR
    atr_m30 = calculate_atr(m30, ATR_PERIOD) or VARSAYILAN_ATR

    print(f"{isim} | fiyat={fiyat} | atr M1={atr_m1} M15={atr_m15} M30={atr_m30}", flush=True)

    # 3. Grafik çiz
    png = draw_chart_3tf(symbol, isim, fiyat, atr_m1, atr_m15, atr_m30)
    if not png:
        send_msg(cid, f"❌ {isim} grafiği çizilemedi.")
        return

    # 4. Gemini analizi
    a = analyze_chart(png, cid, symbol, isim, fiyat, atr_m1, atr_m15, atr_m30)
    if not a:
        send_msg(cid, "❌ Analiz başarısız.")
        return

    # 5. SL/TP kırpma
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

            hesap = kirp_sl_tp(symbol, a.get("giris"), gemini_sl, gemini_tp1, gemini_tp2)
            if hesap:
                giris_f = hesap["giris"]
                if a["yon"] == "SHORT":
                    sl  = giris_f + hesap["sl_mesafe"]
                    tp1 = giris_f - hesap["tp1_mesafe"]
                    tp2 = giris_f - hesap["tp2_mesafe"]
                else:
                    sl  = giris_f - hesap["sl_mesafe"]
                    tp1 = giris_f + hesap["tp1_mesafe"]
                    tp2 = giris_f + hesap["tp2_mesafe"]

                a["giris"] = str(round(giris_f, 4))
                a["stop_loss"] = str(round(sl, 4))
                a["take_profit"] = [str(round(tp1, 4)), str(round(tp2, 4))]

                rr = hesap["tp1_mesafe"] / hesap["sl_mesafe"] if hesap["sl_mesafe"] > 0 else 0
                a["risk_odul"] = f"1:{round(rr, 2)}"

                print(f"ATR={hesap['atr']} | SL={hesap['sl_mesafe']} TP1={hesap['tp1_mesafe']}", flush=True)
            else:
                a["yon"] = "BEKLE"
                a["uyari"] = "SL/TP hesaplanamadı."
        else:
            a["yon"] = "BEKLE"
            a["uyari"] = "SL/TP verisi eksik."

    # 6. Kaydet + gönder
    aid = save_analysis(cid, isim, a)
    kart = build_card(a, isim)

    reply_markup = None
    if a.get("yon") in ("LONG", "SHORT") and aid:
        reply_markup = {"inline_keyboard": [[
            {"text": "✅ Tuttu",   "callback_data": f"sonuc:tuttu:{aid}"},
            {"text": "❌ Tutmadı", "callback_data": f"sonuc:tutmadi:{aid}"}
        ]]}

    if len(kart) > 1024:
        send_photo(cid, png, caption=f"{isim} | {a.get('yon')} | %{a.get('guven')}")
        send_msg(cid, kart, reply_markup=reply_markup)
    else:
        send_photo(cid, png, caption=kart, reply_markup=reply_markup)

# ==========================================
# ANA DONGU
# ==========================================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    init_db()
    print(f"=== DERIV SENTETIK BOT BASLADI (ID: {ADMIN_ID}) ===", flush=True)
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

                if int(cid) != int(ADMIN_ID):
                    try:
                        send_msg(cid, "🔒 Bu bot özel kullanımdadır.")
                    except:
                        pass
                    continue

                text = msg.get("text", "")
                if text:
                    handle_command(cid, text)
                    continue

                send_msg(cid, "ℹ️ /menu yazarak başlayın.")

        except Exception as e:
            print(f"=== LOOP HATASI: {e} ===", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
