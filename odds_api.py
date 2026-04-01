"""
OddsAPI.io — BasketOracle
Doğru API formatı:
  GET /v3/events?apiKey=KEY&sport=basketball&limit=100
  GET /v3/odds?apiKey=KEY&eventId=ID&bookmakers=Bet365
"""

import os
import re
import difflib
import requests

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "e097279bf7409b014ed0b9bdff8df8d8ce4045b2e335542d9cde58e0c3462a42")
BASE_URL     = "https://api.odds-api.io/v3"
BOOKMAKER    = "Bet365"
TIMEOUT      = 10

_event_cache = {}   # "home|away" → event_id
_odds_cache  = {}   # event_id → parsed odds
_events_list = []   # tüm basketbol eventleri (cache)
_events_date = ""   # hangi gün çekildi


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


def _safe_float(v):
    try:
        f = float(v)
        return round(f, 2) if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def _normalize(name: str) -> str:
    """Takım ismini normalize et — karşılaştırma için."""
    n = name.lower().strip()
    n = re.sub(r'\(.*?\)', '', n).strip()
    n = re.sub(r'\s+', ' ', n)
    # Yaygın kısaltmalar
    replacements = {
        "76ers": "philadelphia 76ers",
        "blazers": "trail blazers",
        "trail blazers": "portland trail blazers",
        "thunder": "oklahoma city thunder",
        "okc": "oklahoma city thunder",
        "gsw": "golden state warriors",
        "warriors": "golden state warriors",
        "lakers": "los angeles lakers",
        "clippers": "los angeles clippers",
        "la lakers": "los angeles lakers",
        "la clippers": "los angeles clippers",
    }
    for short, full in replacements.items():
        if short in n:
            return full
    return n


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _load_events(date_str: str):
    """Basketbol eventlerini yükle — günlük cache."""
    global _events_list, _events_date
    if _events_date == date_str and _events_list:
        return _events_list

    print(f"[OddsAPI] Eventler yükleniyor: {date_str}")
    # Tüm basketbol eventlerini çek (limit 200)
    data = _get("events", {"sport": "basketball", "limit": 200, "status": "pending"})
    if not data:
        # pending yoksa live de dene
        data = _get("events", {"sport": "basketball", "limit": 200})

    if not data:
        print("[OddsAPI] Event listesi boş")
        return []

    events = data if isinstance(data, list) else data.get("data", data.get("events", []))
    _events_list = events if events else []
    _events_date = date_str
    print(f"[OddsAPI] {len(_events_list)} basketbol eventi yüklendi")
    return _events_list


def _find_event(home_team: str, away_team: str, date_str: str):
    cache_key = f"{home_team}|{away_team}"
    if cache_key in _event_cache:
        return _event_cache[cache_key]

    events = _load_events(date_str)
    if not events:
        _event_cache[cache_key] = None
        return None

    best_id    = None
    best_score = 0.0

    for ev in events:
        api_home = ev.get("home", ev.get("home_team", ""))
        api_away = ev.get("away", ev.get("away_team", ""))
        score = (_similarity(home_team, api_home) + _similarity(away_team, api_away)) / 2
        if score > best_score:
            best_score = score
            best_id    = str(ev.get("id", ev.get("event_id", "")))

    if best_score < 0.35:
        print(f"[OddsAPI] Bulunamadı: {home_team} vs {away_team} (max skor: {best_score:.2f})")
        # En iyi 3 eşleşmeyi logla
        top3 = sorted(
            [{"home": ev.get("home",""), "away": ev.get("away",""),
              "score": (_similarity(home_team, ev.get("home","")) + _similarity(away_team, ev.get("away","")))/2}
             for ev in events],
            key=lambda x: -x["score"]
        )[:3]
        for t in top3:
            print(f"  → {t['home']} vs {t['away']} (skor: {t['score']:.2f})")
        _event_cache[cache_key] = None
        return None

    print(f"[OddsAPI] Bulundu: {home_team} vs {away_team} → id={best_id} (skor={best_score:.2f})")
    _event_cache[cache_key] = best_id
    return best_id


def _parse_odds(raw):
    result = {
        "ms_home": None, "ms_away": None,
        "ou_line": None, "ou_over": None, "ou_under": None,
        "iy_line": None, "iy_over": None, "iy_under": None,
        "home_ou_line": None, "home_ou_over": None, "home_ou_under": None,
        "away_ou_line": None, "away_ou_over": None, "away_ou_under": None,
    }
    if not raw:
        return result

    bookmakers = raw.get("bookmakers", {})
    markets    = bookmakers.get(BOOKMAKER, [])
    if not markets:
        if isinstance(raw, list):
            markets = raw
        elif isinstance(raw.get("data"), list):
            markets = raw["data"]

    for market in markets:
        name      = (market.get("name") or "").lower().strip()
        odds_list = market.get("odds", [])
        if not odds_list:
            continue

        # MS
        if name in ("ml", "1x2", "match result", "3way", "moneyline", "winner", "h2h"):
            o = odds_list[0]
            result["ms_home"] = _safe_float(o.get("home") or o.get("1"))
            result["ms_away"] = _safe_float(o.get("away") or o.get("2"))

        # MS OU — 50/50'ye en yakın line
        elif name in ("over/under", "totals", "total", "ou", "total points", "game total"):
            best = _find_closest_line(odds_list)
            if best:
                result["ou_line"]  = best[0]
                result["ou_over"]  = best[1]
                result["ou_under"] = best[2]

        # IY OU
        elif any(k in name for k in ("1st half", "first half", "halftime", "half time", "h1")):
            if any(k in name for k in ("over", "under", "total", "ou")):
                best = _find_closest_line(odds_list)
                if best:
                    result["iy_line"]  = best[0]
                    result["iy_over"]  = best[1]
                    result["iy_under"] = best[2]

        # Ev takımı OU
        elif any(k in name for k in ("home team total", "home total", "team total home")):
            best = _find_closest_line(odds_list)
            if best:
                result["home_ou_line"]  = best[0]
                result["home_ou_over"]  = best[1]
                result["home_ou_under"] = best[2]

        # Deplasman takımı OU
        elif any(k in name for k in ("away team total", "away total", "team total away")):
            best = _find_closest_line(odds_list)
            if best:
                result["away_ou_line"]  = best[0]
                result["away_ou_over"]  = best[1]
                result["away_ou_under"] = best[2]

    return result


def _find_closest_line(odds_list):
    """Oranlar birbirine en yakın (50/50) line'ı bul."""
    best = None
    best_dist = float("inf")
    for row in odds_list:
        line = _safe_float(row.get("max") or row.get("line") or row.get("points") or row.get("handicap"))
        ov   = _safe_float(row.get("over"))
        un   = _safe_float(row.get("under"))
        if line and ov and un:
            dist = abs(ov - un)
            if dist < best_dist:
                best_dist = dist
                best = (line, ov, un)
    return best


def get_market_odds(home_team: str, away_team: str, date_str: str) -> dict:
    event_id = _find_event(home_team, away_team, date_str)
    if not event_id:
        return {"found": False}

    if event_id in _odds_cache:
        return _odds_cache[event_id]

    raw    = _get("odds", {"eventId": event_id, "bookmakers": BOOKMAKER})
    parsed = _parse_odds(raw)
    parsed["found"] = bool(parsed.get("ou_line") or parsed.get("ms_home"))

    _odds_cache[event_id] = parsed
    print(f"[OddsAPI] Parsed: ou={parsed.get('ou_line')} iy={parsed.get('iy_line')} ms={parsed.get('ms_home')}/{parsed.get('ms_away')}")
    return parsed


def get_all_events(date_str: str) -> list:
    """Debug için tüm eventleri döndür."""
    return _load_events(date_str)


def clear_cache():
    global _events_list, _events_date
    _event_cache.clear()
    _odds_cache.clear()
    _events_list = []
    _events_date = ""
