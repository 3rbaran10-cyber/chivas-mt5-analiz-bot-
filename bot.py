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
RATE_LIMIT_SECONDS = 300

# V4.2 AYARLAR
MAX_TARANAN = 30
MIN_OLASILIK = 55
MIN_SART = 4
HAFTALIK_UCRET = "100$"

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
# SQLITE
# ==========================================
DB_PATH = "/tmp/bot.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("""CREATE TABLE IF NOT EXISTS kuponlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cid INTEGER, tarih TEXT, maclar TEXT,
            toplam_oran REAL, guven INTEGER,
            sonuc TEXT DEFAULT NULL, ts INTEGER)""")
        conn.commit(); conn.close()
        print("DB hazir", flush=True)
    except Exception as e:
        print(f"DB hatasi: {e}", flush=True)

def get_offset():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT value FROM state WHERE key='offset'")
        row = cur.fetchone(); conn.close()
        return row[0] if row else 0
    except: return 0

def save_offset(o):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('offset', ?)", (o,))
        conn.commit(); conn.close()
    except: pass

def save_kupon(cid, maclar_json, toplam_oran, guven):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("""INSERT INTO kuponlar
            (cid, tarih, maclar, toplam_oran, guven, ts)
            VALUES (?,?,?,?,?,?)""",
            (cid, datetime.now().strftime("%d.%m.%Y %H:%M"),
             maclar_json, toplam_oran, guven, int(time.time())))
        conn.commit()
        rid = cur.lastrowid; conn.close()
        return rid
    except Exception as e:
        print(f"save_kupon: {e}", flush=True); return None

def update_sonuc(kid, sonuc):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("UPDATE kuponlar SET sonuc=? WHERE id=? AND sonuc IS NULL", (sonuc, kid))
        d = cur.rowcount; conn.commit(); conn.close()
        return d > 0
    except: return False

def get_gecmis(cid, limit=10):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("""SELECT id, tarih, toplam_oran, guven, sonuc
            FROM kuponlar WHERE cid=? ORDER BY id DESC LIMIT ?""", (cid, limit))
        rows = cur.fetchall(); conn.close()
        return rows
    except: return []

def get_istatistik(cid):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("""SELECT COUNT(*),
            SUM(CASE WHEN sonuc='tuttu' THEN 1 ELSE 0 END),
            SUM(CASE WHEN sonuc='tutmadi' THEN 1 ELSE 0 END)
            FROM kuponlar WHERE cid=?""", (cid,))
        total, tuttu, tutmadi = cur.fetchone()
        conn.close()
        return {"total": total or 0, "tuttu": tuttu or 0, "tutmadi": tutmadi or 0}
    except: return {"total": 0, "tuttu": 0, "tutmadi": 0}

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
            if 1 <= dakika <= 70:
                maclar.append(f)
        except: continue
    return maclar

def yaklasan_maclari_al(dakika_araligi=120):
    """X dakika icinde baslayacak maclar. UTC hesabi."""
    bugun = datetime.now().strftime("%Y-%m-%d")
    r = api_football("fixtures", {"date": bugun})
    if not r: return []
    simdi_utc = datetime.utcnow()
    maclar = []
    for f in r.get("response", []):
        try:
            mac_saati_utc = datetime.fromisoformat(f["fixture"]["date"].replace("Z", ""))
            fark = (mac_saati_utc - simdi_utc).total_seconds() / 60
            if 0 < fark <= dakika_araligi:
                maclar.append(f)
        except: continue
    return maclar

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
- Olasilik %55'in altindaysa "BEKLE" ver
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
# KUPON OLUSTURMA - SADECE ALT
# ==========================================
def oran_tahmin(olasilik):
    if olasilik <= 0: return 1.0
    return round(1 / (olasilik / 100.0), 2)

def kupon_olustur(cid):
    send_msg(cid, f"🔍 ALT için {MAX_TARANAN} maç taranıyor... (3-5 dk)")

    maclar = canli_maclari_al()
    print(f"Canli mac: {len(maclar)}", flush=True)

    if len(maclar) < 5:
        yaklasan = yaklasan_maclari_al(120)
        print(f"Yaklasan: {len(yaklasan)}", flush=True)
        maclar += yaklasan

    if len(maclar) < 5:
        send_msg(cid, "❌ Yeterli maç yok. Biraz sonra tekrar dene.")
        return

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

        print(f"[{idx}/{len(secilenler)}] {teams['home']['name']} vs {teams['away']['name']}", flush=True)

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

    if len(adaylar) < 2:
        send_msg(cid, f"❌ Yeterli ALT maçı yok ({len(adaylar)}/2).")
        return

    adaylar.sort(key=lambda x: (x["uyan_sart"], x["olasilik"]), reverse=True)
    kupon_maclar = adaylar[:2]

    print(f"=== SECILEN 2 MAC ===", flush=True)
    for km in kupon_maclar:
        print(f"  {km['mac']['teams']['home']['name']} vs {km['mac']['teams']['away']['name']} - {km['analiz']['tercih']}", flush=True)

    toplam_oran = 1.0
    for km in kupon_maclar:
        toplam_oran *= km["oran"]
    toplam_oran = round(toplam_oran, 2)

    ort_guven = round(sum(km["analiz"]["guven"] for km in kupon_maclar) / len(kupon_maclar))

    t = []
    t.append("🎯 GOL ALT KUPONU")
    t.append(f"⏰ {datetime.now().strftime('%d.%m.%Y - %H:%M')}")
    t.append("━━━━━━━━━━━━━━━━━━")

    for i, km in enumerate(kupon_maclar, 1):
        m = km["mac"]
        a = km["analiz"]
        teams = m["teams"]
        goals = m["goals"]
        dakika = m["fixture"]["status"]["elapsed"] or 0
        skor = f"{goals['home'] or 0}-{goals['away'] or 0}"

        t.append(f"{i}. {teams['home']['name']} - {teams['away']['name']}")
        t.append(f"⏱️ {dakika}' | {skor}")
        t.append(f"⚽ {a['tercih']} | 📊 %{a['olasilik']} | 💰 {km['oran']}")
        t.append(f"✅ Uyan şart: {a.get('uyan_sart', '?')}/7")

        gerekce = a.get('gerekce', '')
        if len(gerekce) > 80:
            gerekce = gerekce[:77] + "..."
        t.append(f"📝 {gerekce}")
        t.append("━━━━━━━━━━━━━━━━━━")

    t.append(f"📈 TOPLAM: {toplam_oran} | ⭐ %{ort_guven}")
    miktar = 100
    kazanc = round(miktar * toplam_oran)
    t.append(f"🎲 {miktar} TL → 💵 {kazanc} TL")
    t.append("")
    t.append("⚠️ Yatırım tavsiyesi değildir.")

    metin = "\n".join(t)

    maclar_json = json.dumps([{
        "mac": f"{km['mac']['teams']['home']['name']} - {km['mac']['teams']['away']['name']}",
        "tercih": km["analiz"]["tercih"],
        "oran": km["oran"],
        "olasilik": km["analiz"]["olasilik"]
    } for km in kupon_maclar], ensure_ascii=False)

    kid = save_kupon(cid, maclar_json, toplam_oran, ort_guven)

    rm = None
    if kid:
        rm = {"inline_keyboard": [[
            {"text": "✅ Tuttu", "callback_data": f"sonuc:tuttu:{kid}"},
            {"text": "❌ Tutmadı", "callback_data": f"sonuc:tutmadi:{kid}"}
        ]]}

    send_msg(cid, metin, None, rm)

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
    return (f"🤖 *GOL ALT KUPON BOTU v4.2*\n\n"
            f"🎯 Sadece *GOL ALT* bahisleri\n"
            f"🔍 {MAX_TARANAN} maç taranır\n"
            f"✅ En iyi 2 ALT maçı seçilir\n"
            f"🧠 Gemini 3.1 Pro analizi\n\n"
            f"💎 *Haftalık abonelik: {HAFTALIK_UCRET}*\n\n"
            f"⬇️ Menüden seç:")

def show_menu(cid, mid=None):
    if mid: edit_message_text(cid, mid, _menu_text(), "Markdown", _menu_keyboard())
    else: send_msg(cid, _menu_text(), "Markdown", _menu_keyboard())

def show_gecmis(cid, mid=None):
    rows = get_gecmis(cid, 10)
    if not rows:
        text = "📭 *Geçmiş boş*"
    else:
        s = {"tuttu": "✅", "tutmadi": "❌", None: "⏳"}
        sat = ["📜 *SON 10 KUPON*", ""]
        for i, (kid, tarih, oran, guven, sonuc) in enumerate(rows, 1):
            sat.append(f"{i}. Oran: *{oran}* | Güven: %{guven}")
            sat.append(f"     {s.get(sonuc,'?')} {tarih}")
        text = "\n".join(sat)
    if mid: edit_message_text(cid, mid, text, "Markdown", _ana_menu_buton())
    else: send_msg(cid, text, "Markdown", _ana_menu_buton())

def show_istatistik(cid, mid=None):
    st = get_istatistik(cid)
    if st["total"] == 0:
        text = "📭 *İstatistik yok*"
    else:
        sc = st["tuttu"] + st["tutmadi"]
        oran = round(st["tuttu"] / sc * 100, 1) if sc > 0 else 0
        text = (f"📊 *İSTATİSTİK*\n\n"
                f"📈 Toplam: *{st['total']}*\n"
                f"✅ Tuttu: *{st['tuttu']}*\n"
                f"❌ Tutmadı: *{st['tutmadi']}*\n"
                f"🎯 Başarı: *%{oran}*")
    if mid: edit_message_text(cid, mid, text, "Markdown", _ana_menu_buton())
    else: send_msg(cid, text, "Markdown", _ana_menu_buton())

def show_yardim(cid, mid=None):
    text = (f"❓ *YARDIM - v4.2*\n\n"
            f"🎯 /start — Menü\n"
            f"⚽ /kupon — ALT kuponu al\n"
            f"📜 /gecmis — Son kuponlar\n"
            f"📊 /istatistik — Başarı oranı\n\n"
            f"📌 *Bu bot ne yapar?*\n"
            f"• {MAX_TARANAN} maçı analiz eder\n"
            f"• Sadece GOL ALT bahisleri\n"
            f"• 1.5 ALT / 2.5 ALT / 3.5 ALT\n"
            f"• En iyi 2 maçı seçer\n"
            f"• Olasılık eşiği: %{MIN_OLASILIK}\n"
            f"• Min şart: {MIN_SART}/7\n\n"
            f"💎 Haftalık abonelik: {HAFTALIK_UCRET}\n\n"
            f"⚠️ Yatırım tavsiyesi değildir.")
    if mid: edit_message_text(cid, mid, text, "Markdown", _ana_menu_buton())
    else: send_msg(cid, text, "Markdown", _ana_menu_buton())

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

        if data.startswith("sonuc:"):
            _, sonuc, kid_str = data.split(":")
            if update_sonuc(int(kid_str), sonuc):
                edit_reply_markup(cid, mid, None)
                e = "✅" if sonuc == "tuttu" else "❌"
                send_msg(cid, f"{e} Kupon #{kid_str} kaydedildi.")
            else:
                send_msg(cid, "⚠️ Bu kupon zaten işaretlenmiş.")
    except Exception as e:
        print(f"callback: {e}", flush=True)

# ==========================================
# ANA DONGU
# ==========================================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    init_db()
    print("=== ALT KUPON BOTU v4.2 BASLADI ===", flush=True)
    print(f"API-Football: {'VAR' if API_FOOTBALL_KEY else 'YOK'}", flush=True)
    print(f"Gemini: {'VAR' if GEMINI_API_KEY else 'YOK'}", flush=True)
    print(f"Telegram: {'VAR' if TELEGRAM_TOKEN else 'YOK'}", flush=True)

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
