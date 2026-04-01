"""
OddsAPI.io entegrasyonu — BasketOracle
Sadece basketbol maçları için:
  - MS (1X2) oranı + baremi   → bet365
  - Toplam OU baremi + oranı  → bet365
  - IY OU baremi + oranı      → bet365
  - Ev/Dep handikap baremi    → bet365

AllSports API'ye dokunmaz. Sadece karşılaştırma verisi sağlar.
"""

import os
import requests
import difflib

ODDS_API_KEY = os.environ.get(
    "ODDS_API_KEY",
    "e097279bf7409b014ed0b9bdff8df8d8ce4045b2e335542d9cde58e0c3462a42"
)
BASE_URL  = "https://api.odds-api.io/v3"
BOOKMAKER = "Bet365"          # Tek bookmaker — karşılaştırma için
TIMEOUT   = 8

# ─── Cache ────────────────────────────────────────────────────────
_event_cache = {}   # "home|away|date" → event_id
_odds_cache  = {}   # event_id → parsed odds dict


def _get(endpoint, params=None):
    params = params or {}
    params["apiKey"] = ODDS_API_KEY
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        print(f"[OddsAPI] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[OddsAPI] Hata: {e}")
    return None


def _name_similarity(a, b):
    """İki takım isminin benzerlik skoru (0-1)."""
    return difflib.SequenceMatcher(
        None,
        a.lower().strip(),
        b.lower().strip()
    ).ratio()


def _find_event(home_team, away_team, date_str):
    """
    Odds-API.io'da basketbol maçını bul.
    date_str: "YYYY-MM-DD"
    Döner: event_id (str) veya None
    """
    cache_key = f"{home_team}|{away_team}|{date_str}"
    if cache_key in _event_cache:
        return _event_cache[cache_key]

    # Basketbol eventlerini çek
    data = _get("events", {
        "sport":     "Basketball",
        "date":      date_str,
        "bookmakers": BOOKMAKER,
    })

    if not data:
        _event_cache[cache_key] = None
        return None

    # Liste veya obje gelebilir
    events = data if isinstance(data, list) else data.get("data", data.get("events", []))
    if not events:
        _event_cache[cache_key] = None
        return None

    best_id    = None
    best_score = 0.0

    for ev in events:
        api_home = ev.get("home", ev.get("home_team", ""))
        api_away = ev.get("away", ev.get("away_team", ""))
        score = (
            _name_similarity(home_team, api_home) +
            _name_similarity(away_team, api_away)
        ) / 2
        if score > best_score:
            best_score = score
            best_id    = str(ev.get("id", ev.get("event_id", "")))

    # %40 benzerlik eşiği — çok düşük tutarsak yanlış maç gelir
    if best_score < 0.40:
        print(f"[OddsAPI] Maç bulunamadı: {home_team} vs {away_team} (en iyi skor: {best_score:.2f})")
        _event_cache[cache_key] = None
        return None

    print(f"[OddsAPI] Maç bulundu: {home_team} vs {away_team} → id={best_id} (skor={best_score:.2f})")
    _event_cache[cache_key] = best_id
    return best_id


def _safe_float(v):
    try:
        f = float(v)
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def _parse_odds(raw):
    """
    Ham odds yanıtından ihtiyacımız olan 3 market'i çıkar:
      - MS  : ML market  → odd_home, odd_draw, odd_away
      - OU  : Over/Under → en yakın merkez line, odd_over, odd_under
      - IY  : 1st Half Over/Under → line, odd_over, odd_under
      - HDP : Asian Handicap → line, odd_home, odd_away
    """
    result = {
        "ms_home": None, "ms_draw": None, "ms_away": None,
        "ou_line": None, "ou_over": None, "ou_under": None,
        "iy_line": None, "iy_over": None, "iy_under": None,
        "hdp_line": None, "hdp_home": None, "hdp_away": None,
    }

    if not raw:
        return result

    # Yanıt formatı: {"bookmakers": {"Bet365": [ {name, odds:[...]}, ... ]}}
    # veya direkt liste
    bookmakers = raw.get("bookmakers", {})
    markets = bookmakers.get(BOOKMAKER, [])

    if not markets:
        # Alternatif format dene
        if isinstance(raw, list):
            markets = raw
        elif isinstance(raw.get("data"), list):
            markets = raw["data"]

    for market in markets:
        name = (market.get("name") or "").lower()
        odds_list = market.get("odds", [])
        if not odds_list:
            continue
        o = odds_list[0]  # ilk satır

        # ── Maç Sonucu (ML / 1X2) ───────────────────────────────
        if name in ("ml", "1x2", "match result", "3way"):
            result["ms_home"] = _safe_float(o.get("home"))
            result["ms_draw"] = _safe_float(o.get("draw"))
            result["ms_away"] = _safe_float(o.get("away"))

        # ── Toplam OU ────────────────────────────────────────────
        elif name in ("over/under", "totals", "total", "ou"):
            # Birden fazla line varsa merkeze en yakını seç
            best_line = None
            best_dist = float("inf")
            for row in odds_list:
                line = _safe_float(row.get("max") or row.get("line") or row.get("points"))
                if line and abs(line - 215) < best_dist:  # basketbol merkezi ~215
                    best_dist = abs(line - 215)
                    best_line = row
            if best_line:
                result["ou_line"]  = _safe_float(best_line.get("max") or best_line.get("line") or best_line.get("points"))
                result["ou_over"]  = _safe_float(best_line.get("over"))
                result["ou_under"] = _safe_float(best_line.get("under"))

        # ── İlk Yarı OU ──────────────────────────────────────────
        elif "1st half" in name or "first half" in name or "halftime" in name or "ht" in name:
            if "over" in name or "under" in name or "total" in name or "ou" in name or name == "ht":
                best_line = None
                best_dist = float("inf")
                for row in odds_list:
                    line = _safe_float(row.get("max") or row.get("line") or row.get("points"))
                    if line and abs(line - 102) < best_dist:  # IY merkezi ~102
                        best_dist = abs(line - 102)
                        best_line = row
                if best_line:
                    result["iy_line"]  = _safe_float(best_line.get("max") or best_line.get("line") or best_line.get("points"))
                    result["iy_over"]  = _safe_float(best_line.get("over"))
                    result["iy_under"] = _safe_float(best_line.get("under"))

        # ── Handikap ─────────────────────────────────────────────
        elif name in ("asian handicap", "spread", "handicap", "point spread"):
            # Ev handikabı (genellikle -3.5, +3.5 gibi)
            best_line = None
            best_dist = float("inf")
            for row in odds_list:
                hdp = _safe_float(row.get("hdp") or row.get("line") or row.get("handicap"))
                if hdp is not None and abs(abs(hdp) - 3.5) < best_dist:
                    best_dist = abs(abs(hdp) - 3.5)
                    best_line = row
            if best_line:
                result["hdp_line"] = _safe_float(best_line.get("hdp") or best_line.get("line") or best_line.get("handicap"))
                result["hdp_home"] = _safe_float(best_line.get("home"))
                result["hdp_away"] = _safe_float(best_line.get("away"))

    return result


def get_market_odds(home_team, away_team, date_str):
    """
    Ana fonksiyon. app.py'den çağrılır.
    home_team, away_team: AllSports'tan gelen isimler
    date_str: "YYYY-MM-DD"

    Döner dict:
    {
        "found": True/False,
        "bookmaker": "Bet365",
        "ms_home": 1.70, "ms_draw": None, "ms_away": 2.10,
        "ou_line": 214.5, "ou_over": 1.87, "ou_under": 1.87,
        "iy_line": 102.5, "iy_over": 1.90, "iy_under": 1.83,
        "hdp_line": -3.5, "hdp_home": 1.85, "hdp_away": 1.87,
    }
    """
    event_id = _find_event(home_team, away_team, date_str)
    if not event_id:
        return {"found": False}

    if event_id in _odds_cache:
        return _odds_cache[event_id]

    raw = _get("odds", {
        "eventId":    event_id,
        "bookmakers": BOOKMAKER,
    })

    parsed = _parse_odds(raw)
    parsed["found"]      = any(v is not None for v in parsed.values())
    parsed["bookmaker"]  = BOOKMAKER
    parsed["event_id"]   = event_id

    _odds_cache[event_id] = parsed
    return parsed


def clear_cache():
    _event_cache.clear()
    _odds_cache.clear()
