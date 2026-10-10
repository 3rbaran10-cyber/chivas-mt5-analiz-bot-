import os
import io, json, time, requests, threading, sqlite3, re
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# AYARLAR
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

GEMINI_MODEL = "gemini-3.1-pro-preview"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
API_FOOTBALL_HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

ADMIN_ID = 5504006147

GEMINI_LOCK = threading.Lock()
SON_ISTEK_ZAMANI = [0.0]
MIN_ISTEK_ARASI = 3.0

USER_COOLDOWN = {}
RATE_LIMIT_SECONDS = 180

# V8 AYARLAR
MAX_TARANAN = 6
MIN_OLASILIK = 45
MIN_SART = 2
MIN_DAKIKA = 25
MAX_DAKIKA = 80
HAFTALIK_UCRET = "100$"

SONUC_KONTROL_ARASI = 300   # 5 dakikada bir kontrol
AYNI_MAC_ENGELLE_DK = 30    # son 30 dk ayni maci tekrar onerme

# ==========================================
# SQLITE - THREAD SAFE
# ==========================================
DB_PATH = "/tmp/bot.db"
_DB_LOCK = threading.Lock()

def db_conn():
    """Her cagride yeni baglanti, thread-safe."""
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    return conn

def init_db():
    try:
        with _DB_LOCK:
            conn = db_conn()
            conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER)")
            conn.execute("""CREATE TABLE IF NOT EXISTS kuponlar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cid INTEGER, tarih TEXT, maclar TEXT,
                toplam_oran REAL, guven INTEGER,
                sonuc TEXT DEFAULT NULL, ts INTEGER)""")

            # Yeni sutunlari ekle (yoksa)
            mevcut = [row[1] for row in conn.execute("PRAGMA table_info(kuponlar)").fetchall()]
            if "fixture_id" not in mevcut:
                conn.execute("ALTER TABLE kuponlar ADD COLUMN fixture_id INTEGER")
            if "tercih" not in mevcut:
                conn.execute("ALTER TABLE kuponlar ADD COLUMN tercih TEXT")
            if "kontrol_edildi" not in mevcut:
                conn.execute("ALTER TABLE kuponlar ADD COLUMN kontrol_edildi INTEGER DEFAULT 0")
            conn.commit()
            conn.close()
        print("DB hazir", flush=True)
    except Exception as e:
        print(f"DB hatasi: {e}", flush=True)

def get_offset():
    try:
        with _DB_LOCK:
            conn = db_conn()
            cur = conn.execute("SELECT value FROM state WHERE key='offset'")
            row = cur.fetchone(); conn.close()
            return row[0] if row else 0
    except: return 0

def save_offset(o):
    try:
        with _DB_LOCK:
            conn = db_conn()
            conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('offset', ?)", (o,))
            conn.commit(); conn.close()
    except: pass

def save_kupon(cid, maclar_json, toplam_oran, guven, fixture_id, tercih):
    try:
        with _DB_LOCK:
            conn = db_conn()
            cur = conn.execute("""INSERT INTO kuponlar
                (cid, tarih, maclar, toplam_oran, guven, fixture_id, tercih, kontrol_edildi, ts)
                VALUES (?,?,?,?,?,?,?,0,?)""",
                (cid, datetime.now().strftime("%d.%m.%Y %H:%M"),
                 maclar_json, toplam_oran, guven, fixture_id, tercih, int(time.time())))
            conn.commit()
            rid = cur.lastrowid; conn.close()
            return rid
    except Exception as e:
        print(f"save_kupon: {e}", flush=True); return None

def update_sonuc(kid, sonuc):
    try:
        with _DB_LOCK:
            conn = db_conn()
            cur = conn.execute("UPDATE kuponlar SET sonuc=?, kontrol_edildi=1 WHERE id=? AND sonuc IS NULL", (sonuc, kid))
            d = cur.rowcount; conn.commit(); conn.close()
            return d > 0
    except: return False

def bekleyen_kuponlar():
    try:
        with _DB_LOCK:
            conn = db_conn()
            cur = conn.execute("""SELECT id, cid, fixture_id, tercih
                FROM kuponlar WHERE kontrol_edildi=0 AND fixture_id IS NOT NULL AND sonuc IS NULL""")
            rows = cur.fetchall(); conn.close()
            return rows
    except: return []

def son_onekli_fixture_idleri():
    """Son X dakikada onerilen fixture id'leri."""
    try:
        esik = int(time.time()) - (AYNI_MAC_ENGELLE_DK * 60)
        with _DB_LOCK:
            conn = db_conn()
            cur = conn.execute("""SELECT fixture_id FROM kuponlar
                WHERE ts >= ? AND fixture_id IS NOT NULL""", (esik,))
            rows = cur.fetchall(); conn.close()
            return set(r[0] for r in rows)
    except: return set()

def get_gecmis(cid, limit=10):
    try:
        with _DB_LOCK:
            conn = db_conn()
            cur = conn.execute("""SELECT id, tarih, toplam_oran, guven, sonuc
                FROM kuponlar WHERE cid=? ORDER BY id DESC LIMIT ?""", (cid, limit))
            rows = cur.fetchall(); conn.close()
            return rows
    except: return []

def get_istatistik(cid):
    try:
        with _DB_LOCK:
            conn = db_conn()
            cur = conn.execute("""SELECT COUNT(*),
                SUM(CASE WHEN sonuc='tuttu' THEN 1 ELSE 0 END),
                SUM(CASE WHEN sonuc='tutmadi' THEN 1 ELSE 0 END)
                FROM kuponlar WHERE cid=? AND sonuc IS NOT NULL""", (cid,))
            total, tuttu, tutmadi = cur.fetchone()
            conn.close()
            return {"total": total or 0, "tuttu": tuttu or 0, "tutmadi": tutmadi or 0}
    except: return {"total": 0, "tuttu": 0, "tutmadi": 0}

# ==========================================
# HEALTH SERVER
# ==========================================
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Health).serve_forever()

# ==========================================
# API-FOOTBALL
# ==========================================
def api_football(endpoint, params=None):
    try:
        r = requests.get(f"{API_FOOTBALL_BASE}/{endpoint}",
                         headers=API_FOOTBALL_HEADERS,
                         params=params or {}, timeout=20)
        if r.status_code != 200:
            print(f"API-Football hata: {r.status_code}", flush=True)
            return None
        return r.json()
    except Exception as e:
        print(f"API-Football istek: {str(e)[:150]}", flush=True)
        return None

def canli_maclari_al():
    r = api_football("fixtures", {"live": "all"})
    if not r: return []
    maclar = []
    for f in r.get("response", []):
        try:
            dakika = f["fixture"]["status"]["elapsed"] or 0
            if MIN_DAKIKA <= dakika <= MAX_DAKIKA:
                maclar.append(f)
        except: continue
    return maclar

def mac_detay_al(fixture_id):
    r = api_football("fixtures", {"id": fixture_id})
    if not r: return None
    resp = r.get("response", [])
    return resp[0] if resp else None

def mac_istatistik_al(fixture_id):
    r = api_football("fixtures/statistics", {"fixture": fixture_id})
    if not r: return None
    return r.get("response", [])

def mac_sakatlik_al(fixture_id):
    r = api_football("injuries", {"fixture": fixture_id})
    if not r: return None
    return r.get("response", [])

def h2h_al(team1, team2):
    r = api_football("fixtures/headtohead", {"h2h": f"{team1}-{team2}", "last": 5})
    if not r: return None
    return r.get("response", [])

def puan_durumu_al(league_id, season):
    r = api_football("standings", {"league": league_id, "season": season})
    if not r: return None
    resp = r.get("response", [])
    if resp and resp[0].get("league", {}).get("standings"):
        return resp[0]["league"]["standings"][0]
    return None

def takim_sezon_istatistik_al(team_id, league_id, season):
    r = api_football("teams/statistics", {
        "team": team_id, "league": league_id, "season": season
    })
    if not r: return None
    return r.get("response", {})

def sezon_ozetle(sezon, takim_adi):
    if not sezon:
        return f"{takim_adi}: sezon verisi yok"
    try:
        form = sezon.get("form", "?")
        played = sezon.get("fixtures", {}).get("played", {}).get("total", 0)
        wins = sezon.get("fixtures", {}).get("wins", {}).get("total", 0)
        draws = sezon.get("fixtures", {}).get("draws", {}).get("total", 0)
        loses = sezon.get("fixtures", {}).get("loses", {}).get("total", 0)
        g_for_avg = sezon.get("goals", {}).get("for", {}).get("average", {}).get("total", "?")
        g_ag_avg = sezon.get("goals", {}).get("against", {}).get("average", {}).get("total", "?")
        try:
            toplam_gol_ort = float(g_for_avg) + float(g_ag_avg)
        except:
            toplam_gol_ort = "?"
        return (f"{takim_adi}:\n"
                f"  • Form: {form} ({wins}G-{draws}B-{loses}M)\n"
                f"  • Gol ort: {g_for_avg} / Yedig: {g_ag_avg} / TOPLAM: {toplam_gol_ort}")
    except Exception as e:
        print(f"sezon_ozetle: {e}", flush=True)
        return f"{takim_adi}: ozet hatasi"

# ==========================================
# ISTATISTIK OZETLEME
# ==========================================
def istatistik_ozetle(stats):
    if not stats: return {}
    sonuc = {}
    for takim in stats:
        takim_adi = takim.get("team", {}).get("name", "?")
        sonuc[takim_adi] = {}
        for s in takim.get("statistics", []):
            tip = s.get("type", "")
            deger = s.get("value")
            if deger is not None:
                sonuc[takim_adi][tip] = deger
    return sonuc

def h2h_ozetle(h2h_list):
    if not h2h_list: return "H2H verisi yok"
    satirlar = []
    alt_sayisi = 0
    for m in h2h_list[:5]:
        try:
            ev = m["teams"]["home"]["name"]
            dep = m["teams"]["away"]["name"]
            ev_gol = m['goals']['home'] or 0
            dep_gol = m['goals']['away'] or 0
            toplam = ev_gol + dep_gol
            if toplam <= 2.5:
                alt_sayisi += 1
            skor = f"{ev_gol}-{dep_gol}"
            tarih = m["fixture"]["date"][:10]
            satirlar.append(f"{tarih}: {ev} {skor} {dep} (toplam: {toplam})")
        except: continue
    ozet = f"SON 5 MAÇTA 2.5 ALT: {alt_sayisi}/5"
    return ozet + "\n" + "\n".join(satirlar) if satirlar else "H2H verisi yok"

def sakatlik_ozetle(inj_list, team_id):
    if not inj_list: return "Sakatlik bilgisi yok"
    satirlar = []
    for i in inj_list:
        try:
            if i["team"]["id"] != team_id: continue
            oyuncu = i["player"]["name"]
            sebep = i["player"].get("reason", "?")
            satirlar.append(f"{oyuncu} ({sebep})")
        except: continue
    return ", ".join(satirlar) if satirlar else "Sakat oyuncu yok"

def puan_durumu_ozetle(standings, ev_id, dep_id):
    if not standings: return "Puan durumu yok"
    satirlar = []
    for s in standings:
        try:
            if s["team"]["id"] in (ev_id, dep_id):
                satirlar.append(f"{s['team']['name']}: {s['rank']}. sira, {s['points']} puan")
        except: continue
    return "\n".join(satirlar) if satirlar else "Puan durumu yok"

# ==========================================
# GEMINI
# ==========================================
def gemini_istek_at(payload):
    global SON_ISTEK_ZAMANI
    with GEMINI_LOCK:
        gecen = time.time() - SON_ISTEK_ZAMANI[0]
        if gecen < MIN_ISTEK_ARASI:
            time.sleep(MIN_ISTEK_ARASI - gecen)
        headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
        resp = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=300)
        SON_ISTEK_ZAMANI[0] = time.time()
        return resp

MAC_ANALIZ_PROMPT = """Sen dunyanin en iyi futbol ALT bahis uzmanisin. GOREVIN: SADECE GOL ALT bahisleri icin en uygun maci bulmak.

=== MAC BILGILERI ===
Lig: {lig}
Mac: {ev_sahibi} vs {deplasman}
Durum: {durum}
Skor: {skor}
Dakika: {dakika}

=== CANLI ISTATISTIKLER ===
{istatistikler}

=== TAKIM SEZON ISTATISTIKLERI ===
{ev_sezon}

{dep_sezon}

=== SAKATLIKLAR ===
{ev_sakat}
{dep_sakat}

=== SON 5 MAC (H2H) ===
{h2h}

=== PUAN DURUMU (ozet) ===
{puan_durumu}

=== GOREV ===
Bu macin GOL ALT bitme ihtimalini degerlendir.

SADECE SU TERCİHLERDEN BIRINI SEÇ:
- 1.5 ALT (0 veya 1 gol)
- 2.5 ALT (0, 1 veya 2 gol)
- 3.5 ALT (0, 1, 2 veya 3 gol)

ALT ICIN ARANAN SARTLAR (kac tanesi uyuyor?):
1. Takimlarin TOPLAM gol ortalamasi dusuk mu (< 2.5 ideal)
2. H2H'de son 5 macta 2.5 ALT orani yuksek mi (>= 3/5 ideal)
3. Skor su an 0-0 veya 1-0 mi
4. Dakika 25+ mi (mac oturmus mu)
5. Canli sut sayisi dusuk mu (< 8 toplam)
6. Iki takim da defansif mi (puan durumunda gol ort. dusuk)
7. Sakatliklar hucum oyuncularini mi vurmus

KARAR:
- 5+ sart uyuyorsa -> "2.5 ALT" veya "1.5 ALT" ver
- 3-4 sart uyuyorsa -> "3.5 ALT" ver
- 2 veya daha az sart uyuyorsa -> "BEKLE" ver

ONEMLI:
- Sadece ALT tercihi ver, UST asla verme
- KG, korner, kart verme - sadece gol ALT
- Olasilik %45'in altindaysa "BEKLE" ver
- Gerekce 2-3 cumle

=== CIKTI (SADECE JSON) ===
{{
  "tercih": "2.5 ALT",
  "olasilik": 75,
  "gerekce": "...",
  "guven": 78,
  "uyan_sart": 5
}}"""

def maci_analiz_et(mac, stats, sakatliklar, h2h, puan_durumu, ev_sezon, dep_sezon):
    f = mac["fixture"]
    teams = mac["teams"]
    goals = mac["goals"]
    lig = mac["league"]["name"]
    ev_id = teams["home"]["id"]
    dep_id = teams["away"]["id"]

    ev_sahibi = teams["home"]["name"]
    deplasman = teams["away"]["name"]
    durum = f["status"]["short"]
    dakika = f["status"]["elapsed"] or 0
    skor = f"{goals['home'] or 0}-{goals['away'] or 0}"

    ist_str = ""
    if stats:
        ozet = istatistik_ozetle(stats)
        for takim, veri in ozet.items():
            ist_str += f"\n{takim}:\n"
            for k, v in veri.items():
                ist_str += f"  - {k}: {v}\n"
    if not ist_str:
        ist_str = "Canli istatistik yok"

    ev_sakat = f"{ev_sahibi}: " + sakatlik_ozetle(sakatliklar, ev_id)
    dep_sakat = f"{deplasman}: " + sakatlik_ozetle(sakatliklar, dep_id)
    h2h_str = h2h_ozetle(h2h)
    pd_str = puan_durumu_ozetle(puan_durumu, ev_id, dep_id)

    ev_sezon_str = sezon_ozetle(ev_sezon, ev_sahibi)
    dep_sezon_str = sezon_ozetle(dep_sezon, deplasman)

    prompt = MAC_ANALIZ_PROMPT.format(
        lig=lig, ev_sahibi=ev_sahibi, deplasman=deplasman,
        durum=durum, skor=skor, dakika=dakika,
        istatistikler=ist_str,
        ev_sezon=ev_sezon_str, dep_sezon=dep_sezon_str,
        ev_sakat=ev_sakat, dep_sakat=dep_sakat,
        h2h=h2h_str, puan_durumu=pd_str
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "tercih": {"type": "string"},
                    "olasilik": {"type": "integer"},
                    "gerekce": {"type": "string"},
                    "guven": {"type": "integer"},
                    "uyan_sart": {"type": "integer"}
                },
                "required": ["tercih", "olasilik", "gerekce", "guven", "uyan_sart"]
            }
        }
    }

    resp = gemini_istek_at(payload)
    if resp.status_code != 200:
        print(f"Gemini hata: {resp.status_code}", flush=True)
        return None
    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        a = json.loads(text)
        return a
    except Exception as e:
        print(f"Gemini parse: {e}", flush=True)
        return None

# ==========================================
# TELEGRAM
# ==========================================
def send_msg(cid, text, parse_mode=None, reply_markup=None):
    try:
        p = {"chat_id": cid, "text": text}
        if parse_mode: p["parse_mode"] = parse_mode
        if reply_markup: p["reply_markup"] = reply_markup
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json=p, timeout=15)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"send_msg: {e}", flush=True); return None

def edit_reply_markup(cid, mid, rm=None):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageReplyMarkup",
                      json={"chat_id": cid, "message_id": mid,
                            "reply_markup": rm or {"inline_keyboard": []}}, timeout=10)
    except: pass

def edit_message_text(cid, mid, text, pm=None, rm=None):
    try:
        p = {"chat_id": cid, "message_id": mid, "text": text}
        if pm: p["parse_mode"] = pm
        if rm: p["reply_markup"] = rm
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
                      json=p, timeout=10)
    except: pass

def check_rate_limit(cid):
    now = time.time()
    last = USER_COOLDOWN.get(cid, 0)
    if now - last < RATE_LIMIT_SECONDS:
        kalan = int(RATE_LIMIT_SECONDS - (now - last))
        send_msg(cid, f"⏳ Çok hızlı. {kalan // 60} dk {kalan % 60} sn bekleyin.")
        return False
    USER_COOLDOWN[cid] = now
    return True

# ==========================================
# OTOMATIK SONUC KONTROLU
# ==========================================
def tercih_tuttumu(tercih, ev_gol, dep_gol):
    if not tercih:
        return None
    toplam = ev_gol + dep_gol
    tercih_up = tercih.upper()
    if "1.5 ALT" in tercih_up:
        return toplam <= 1
    if "2.5 ALT" in tercih_up:
        return toplam <= 2
    if "3.5 ALT" in tercih_up:
        return toplam <= 3
    return None

def sonuc_kontrol_worker():
    print("Sonuc kontrol worker basladi", flush=True)
    while True:
        try:
            time.sleep(SONUC_KONTROL_ARASI)
            bekleyenler = bekleyen_kuponlar()
            if not bekleyenler:
                continue
            print(f"Bekleyen kupon: {len(bekleyenler)}", flush=True)
            for (kid, cid, fixture_id, tercih) in bekleyenler:
                try:
                    detay = mac_detay_al(fixture_id)
                    if not detay:
                        continue
                    durum = detay["fixture"]["status"]["short"]
                    if durum not in ("FT", "AET", "PEN"):
                        continue
                    ev_gol = detay["goals"]["home"] or 0
                    dep_gol = detay["goals"]["away"] or 0
                    tuttu = tercih_tuttumu(tercih, ev_gol, dep_gol)
                    if tuttu is None:
                        continue
                    sonuc = "tuttu" if tuttu else "tutmadi"
                    if update_sonuc(kid, sonuc):
                        e = "✅" if tuttu else "❌"
                        send_msg(cid, f"{e} Kupon #{kid} OTOMATIK sonuclandi: {sonuc.upper()}\n"
                                      f"⚽ Skor: {ev_gol}-{dep_gol} | Tercih: {tercih}")
                        print(f"Kupon #{kid} sonuclandi: {sonuc}", flush=True)
                except Exception as e:
                    print(f"Kupon kontrol hatasi: {e}", flush=True)
        except Exception as e:
            print(f"Worker hatasi: {e}", flush=True)
            time.sleep(60)

# ==========================================
# KUPON OLUSTURMA - TEK MAC
# ==========================================
def oran_tahmin(olasilik):
    if olasilik <= 0: return 1.0
    return round(1 / (olasilik / 100.0), 2)

def kupon_olustur(cid):
    send_msg(cid, f"🔍 Canlı maçlar taranıyor... ({MIN_DAKIKA}-{MAX_DAKIKA}. dk arası)")

    maclar = canli_maclari_al()
    print(f"Canli mac ({MIN_DAKIKA}-{MAX_DAKIKA}. dk): {len(maclar)}", flush=True)

    if len(maclar) < 3:
        send_msg(cid, f"❌ Şu an {MIN_DAKIKA}-{MAX_DAKIKA}. dakikada yeterli canlı maç yok.")
        return

    # Ayni mac tekrarini engelle
    onerilen_fixture_idler = son_onekli_fixture_idleri()
    print(f"Son {AYNI_MAC_ENGELLE_DK} dk onerilen mac: {len(onerilen_fixture_idler)}", flush=True)

    def oncelik(m):
        return m["fixture"]["status"]["elapsed"] or 0

    maclar.sort(key=oncelik, reverse=True)
    secilenler = maclar[:MAX_TARANAN]

    print(f"Taranacak: {len(secilenler)}", flush=True)

    adaylar = []
    for idx, mac in enumerate(secilenler, 1):
        f = mac["fixture"]
        teams = mac["teams"]
        fid = f["id"]
        lig_id = mac["league"]["id"]
        sezon = mac["league"]["season"]

        # Ayni mac tekrarini atla
        if fid in onerilen_fixture_idler:
            print(f"[{idx}/{len(secilenler)}] {teams['home']['name']} vs {teams['away']['name']} - ATLANDI (yakinda onerildi)", flush=True)
            continue

        print(f"[{idx}/{len(secilenler)}] {teams['home']['name']} vs {teams['away']['name']} ({(f['status']['elapsed'] or 0)}')", flush=True)

        ist = mac_istatistik_al(fid)
        sak = mac_sakatlik_al(fid)
        h2h = h2h_al(teams["home"]["id"], teams["away"]["id"])
        pd = puan_durumu_al(lig_id, sezon)
        ev_sezon = takim_sezon_istatistik_al(teams["home"]["id"], lig_id, sezon)
        dep_sezon = takim_sezon_istatistik_al(teams["away"]["id"], lig_id, sezon)

        analiz = maci_analiz_et(mac, ist, sak, h2h, pd, ev_sezon, dep_sezon)
        if not analiz:
            continue

        tercih = analiz.get("tercih", "")
        if "ALT" not in tercih.upper():
            print(f"   ⏭️ BEKLE/ALT değil: {tercih}", flush=True)
            continue

        if analiz.get("olasilik", 0) < MIN_OLASILIK:
            print(f"   ⏭️ Olasılık düşük: %{analiz.get('olasilik')}", flush=True)
            continue

        if analiz.get("uyan_sart", 0) < MIN_SART:
            print(f"   ⏭️ Şart düşük: {analiz.get('uyan_sart')}/7", flush=True)
            continue

        oran = oran_tahmin(analiz["olasilik"])
        adaylar.append({
            "mac": mac,
            "analiz": analiz,
            "oran": oran,
            "uyan_sart": analiz.get("uyan_sart", 0),
            "olasilik": analiz.get("olasilik", 0)
        })
        print(f"   ✅ ADAY: {tercih} | %{analiz['olasilik']} | {oran}", flush=True)

    if len(adaylar) < 1:
        send_msg(cid, f"❌ Uygun ALT maçı yok.")
        return

    # EN YUKSEK OLASILIKLI MACI SEC
    adaylar.sort(key=lambda x: (x["olasilik"], x["uyan_sart"]), reverse=True)
    km = adaylar[0]

    print(f"=== SECILEN MAC ===", flush=True)
    print(f"  {km['mac']['teams']['home']['name']} vs {km['mac']['teams']['away']['name']} - {km['analiz']['tercih']} (%{km['analiz']['olasilik']})", flush=True)

    a = km["analiz"]
    m = km["mac"]
    teams = m["teams"]
    goals = m["goals"]
    fixture_id = m["fixture"]["id"]
    dakika = m["fixture"]["status"]["elapsed"] or 0
    skor = f"{goals['home'] or 0}-{goals['away'] or 0}"

    t = []
    t.append("🎯 GOL ALT KUPONU")
    t.append(f"⏰ {datetime.now().strftime('%d.%m.%Y - %H:%M')}")
    t.append("━━━━━━━━━━━━━━━━━━")
    t.append("")
    t.append(f"⚽ {teams['home']['name']} - {teams['away']['name']}")
    t.append(f"⏱️ {dakika}' | Skor: {skor}")
    t.append("")
    t.append(f"🎯 Tercih: {a['tercih']}")
    t.append(f"📊 Olasılık: %{a['olasilik']}")
    t.append(f"💰 Oran: {km['oran']}")
    t.append(f"✅ Uyan şart: {a.get('uyan_sart', '?')}/7")
    t.append("")
    gerekce = a.get('gerekce', '')
    if len(gerekce) > 150:
        gerekce = gerekce[:147] + "..."
    t.append(f"📝 {gerekce}")
    t.append("")
    t.append("━━━━━━━━━━━━━━━━━━")
    miktar = 100
    kazanc = round(miktar * km['oran'])
    t.append(f"🎲 {miktar} TL → 💵 {kazanc} TL")
    t.append("")
    t.append("🤖 Sonuç otomatik kontrol edilecek")
    t.append("⚠️ Yatırım tavsiyesi değildir.")

    metin = "\n".join(t)

    maclar_json = json.dumps([{
        "mac": f"{teams['home']['name']} - {teams['away']['name']}",
        "tercih": a["tercih"],
        "oran": km["oran"],
        "olasilik": a["olasilik"]
    }], ensure_ascii=False)

    kid = save_kupon(cid, maclar_json, km["oran"], a["guven"], fixture_id, a["tercih"])

    send_msg(cid, metin)

# ==========================================
# MENU
# ==========================================
def _ana_menu_buton():
    return {"inline_keyboard": [[{"text": "🔙 Ana Menü", "callback_data": "menu:ana"}]]}

def _menu_keyboard():
    return {"inline_keyboard": [
        [{"text": "🎯 ALT Kupon Al", "callback_data": "menu:kupon"}],
        [{"text": "📜 Geçmiş", "callback_data": "menu:gecmis"},
         {"text": "📊 İstatistik", "callback_data": "menu:istatistik"}],
        [{"text": "❓ Yardım", "callback_data": "menu:yardim"}]
    ]}

def _menu_text():
    return (f"🤖 GOL ALT KUPON BOTU v8\n\n"
            f"🎯 Sadece GOL ALT bahisleri\n"
            f"⚽ Canlı maçlar ({MIN_DAKIKA}-{MAX_DAKIKA}. dk)\n"
            f"🔍 {MAX_TARANAN} maç taranır\n"
            f"✅ En yüksek olasılıklı 1 maç\n"
            f"🤖 Sonuç otomatik kontrol\n"
            f"🧠 Gemini 3.1 Pro analizi\n\n"
            f"💎 Haftalık abonelik: {HAFTALIK_UCRET}\n\n"
            f"⬇️ Menüden seç:")

def show_menu(cid, mid=None):
    if mid: edit_message_text(cid, mid, _menu_text(), None, _menu_keyboard())
    else: send_msg(cid, _menu_text(), None, _menu_keyboard())

def show_gecmis(cid, mid=None):
    rows = get_gecmis(cid, 10)
    if not rows:
        text = "📭 Geçmiş boş"
    else:
        s = {"tuttu": "✅", "tutmadi": "❌", None: "⏳"}
        sat = ["📜 SON 10 KUPON", ""]
        for i, (kid, tarih, oran, guven, sonuc) in enumerate(rows, 1):
            sat.append(f"{i}. Oran: {oran} | Güven: %{guven}")
            sat.append(f"     {s.get(sonuc,'?')} {tarih}")
        text = "\n".join(sat)
    if mid: edit_message_text(cid, mid, text, None, _ana_menu_buton())
    else: send_msg(cid, text, None, _ana_menu_buton())

def show_istatistik(cid, mid=None):
    st = get_istatistik(cid)
    if st["total"] == 0:
        text = "📭 İstatistik yok (henüz sonuçlanmış kupon yok)"
    else:
        sc = st["tuttu"] + st["tutmadi"]
        oran = round(st["tuttu"] / sc * 100, 1) if sc > 0 else 0
        text = (f"📊 İSTATİSTİK\n\n"
                f"📈 Toplam: {st['total']}\n"
                f"✅ Tuttu: {st['tuttu']}\n"
                f"❌ Tutmadı: {st['tutmadi']}\n"
                f"🎯 Başarı: %{oran}")
    if mid: edit_message_text(cid, mid, text, None, _ana_menu_buton())
    else: send_msg(cid, text, None, _ana_menu_buton())

def show_yardim(cid, mid=None):
    text = (f"❓ YARDIM - v8\n\n"
            f"🎯 /start — Menü\n"
            f"⚽ /kupon — ALT kuponu al\n"
            f"📜 /gecmis — Son kuponlar\n"
            f"📊 /istatistik — Başarı oranı\n\n"
            f"📌 Bu bot ne yapar?\n"
            f"• Sadece canlı maçları tarar\n"
            f"• Dakika: {MIN_DAKIKA}-{MAX_DAKIKA}\n"
            f"• {MAX_TARANAN} maçı analiz eder\n"
            f"• Sadece GOL ALT bahisleri\n"
            f"• En yüksek olasılıklı 1 maç\n"
            f"• Olasılık eşiği: %{MIN_OLASILIK}\n"
            f"• Min şart: {MIN_SART}/7\n"
            f"• 🤖 Maç bitince OTOMATIK sonuç\n"
            f"• ⏳ Aynı maç {AYNI_MAC_ENGELLE_DK} dk tekrar önerilmez\n\n"
            f"💎 Haftalık abonelik: {HAFTALIK_UCRET}\n\n"
            f"⚠️ Yatırım tavsiyesi değildir.")
    if mid: edit_message_text(cid, mid, text, None, _ana_menu_buton())
    else: send_msg(cid, text, None, _ana_menu_buton())

# ==========================================
# KOMUTLAR
# ==========================================
def handle_command(cid, text):
    text = (text or "").strip()
    cmd = text.split()[0].lower() if text else ""

    if cmd == "/start":
        show_menu(cid); return
    if cmd == "/kupon":
        if not check_rate_limit(cid): return
        threading.Thread(target=kupon_olustur, args=(cid,), daemon=True).start()
        return
    if cmd == "/menu":
        show_menu(cid); return
    if cmd == "/gecmis":
        show_gecmis(cid); return
    if cmd == "/istatistik":
        show_istatistik(cid); return
    if cmd == "/yardim":
        show_yardim(cid); return
    send_msg(cid, "ℹ️ /start yazarak başlayın.")

def handle_callback(cq):
    try:
        cid = cq["message"]["chat"]["id"]
        mid = cq["message"]["message_id"]
        data = cq.get("data", "")
        cb_id = cq["id"]
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                      json={"callback_query_id": cb_id}, timeout=10)

        if int(cid) != int(ADMIN_ID):
            send_msg(cid, "🔒 Bu bot özel kullanımdadır."); return

        if data == "menu:ana": show_menu(cid, mid); return
        if data == "menu:gecmis": show_gecmis(cid, mid); return
        if data == "menu:istatistik": show_istatistik(cid, mid); return
        if data == "menu:yardim": show_yardim(cid, mid); return
        if data == "menu:kupon":
            if not check_rate_limit(cid): return
            edit_reply_markup(cid, mid, _ana_menu_buton())
            threading.Thread(target=kupon_olustur, args=(cid,), daemon=True).start()
            return
    except Exception as e:
        print(f"callback: {e}", flush=True)

# ==========================================
# ANA DONGU
# ==========================================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    init_db()
    threading.Thread(target=sonuc_kontrol_worker, daemon=True).start()
    print("=== ALT KUPON BOTU v8 BASLADI ===", flush=True)
    print(f"Dakika: {MIN_DAKIKA}-{MAX_DAKIKA} | Taranan: {MAX_TARANAN} | Olasilik: %{MIN_OLASILIK} | Sart: {MIN_SART}/7", flush=True)
    print(f"Ayni mac engelleme: {AYNI_MAC_ENGELLE_DK} dk", flush=True)
    print(f"API-Football: {'VAR' if API_FOOTBALL_KEY else 'YOK'}", flush=True)

    offset = get_offset()

    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                             params={"offset": offset, "timeout": 30}, timeout=40).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                save_offset(offset)
                if "callback_query" in u:
                    handle_callback(u["callback_query"]); continue
                msg = u.get("message", {})
                cid = msg.get("chat", {}).get("id")
                if not cid: continue
                if int(cid) != int(ADMIN_ID):
                    try: send_msg(cid, "🔒 Bu bot özel kullanımdadır.")
                    except: pass
                    continue
                text = msg.get("text", "")
                if text: handle_command(cid, text); continue
                send_msg(cid, "ℹ️ /start yazarak başlayın.")
        except Exception as e:
            print(f"=== LOOP: {e} ===", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
