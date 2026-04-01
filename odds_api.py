"""
OddsAPI.io — BasketOracle
Bet365'ten tek MS baremi + tek OU baremi + tek IY baremi çeker.
Takım isimlerini normalize ederek eşleştirir.
"""

import os
import re
import difflib
import requests

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "e097279bf7409b014ed0b9bdff8df8d8ce4045b2e335542d9cde58e0c3462a42")
BASE_URL     = "https://api.odds-api.io/v3"
BOOKMAKER    = "Bet365"
TIMEOUT      = 8

_event_cache = {}
_odds_cache  = {}

# ─── Takım ismi normalizasyon sözlüğü ─────────────────────────────
# AllSports kısa isimler → Bet365 tam isimler
TEAM_ALIASES = {
    # NBA
    "atlanta":       "atlanta hawks",
    "boston":        "boston celtics",
    "brooklyn":      "brooklyn nets",
    "charlotte":     "charlotte hornets",
    "chicago":       "chicago bulls",
    "cleveland":     "cleveland cavaliers",
    "dallas":        "dallas mavericks",
    "denver":        "denver nuggets",
    "detroit":       "detroit pistons",
    "golden state":  "golden state warriors",
    "houston":       "houston rockets",
    "indiana":       "indiana pacers",
    "la clippers":   "los angeles clippers",
    "la lakers":     "los angeles lakers",
    "los angeles clippers": "los angeles clippers",
    "los angeles lakers":   "los angeles lakers",
    "memphis":       "memphis grizzlies",
    "miami":         "miami heat",
    "milwaukee":     "milwaukee bucks",
    "minnesota":     "minnesota timberwolves",
    "new orleans":   "new orleans pelicans",
    "new york":      "new york knicks",
    "oklahoma":      "oklahoma city thunder",
    "oklahoma city": "oklahoma city thunder",
    "orlando":       "orlando magic",
    "philadelphia":  "philadelphia 76ers",
    "phoenix":       "phoenix suns",
    "portland":      "portland trail blazers",
    "sacramento":    "sacramento kings",
    "san antonio":   "san antonio spurs",
    "toronto":       "toronto raptors",
    "utah":          "utah jazz",
    "washington":    "washington wizards",
    # EuroLeague
    "real madrid":   "real madrid",
    "barcelona":     "fc barcelona",
    "olympiacos":    "olympiacos piraeus",
    "panathinaikos": "panathinaikos",
    "fenerbahce":    "fenerbahce beko",
    "anadolu efes":  "anadolu efes",
    "cska":          "cska moscow",
    "maccabi":       "maccabi tel aviv",
    "alba berlin":   "alba berlin",
    "monaco":        "as monaco",
    "virtus bologna":"virtus bologna",
    "baskonia":      "td sistemas baskonia",
    "zalgiris":      "zalgiris kaunas",
    "zvezda":        "crvena zvezda",
    "red star":      "crvena zvezda",
}


def _normalize(name: str) -> str:
    """Takım ismini lowercase, temiz hale getir."""
    n = name.lower().strip()
    # Parantez içini sil: "Los Angeles Lakers (West)" → "los angeles lakers"
    n = re.sub(r'\(.*?\)', '', n).strip()
    # Çoklu boşluk temizle
    n = re.sub(r'\s+', ' ', n)
    # Sözlükte var mı?
    for key, full in TEAM_ALIASES.items():
        if key in n:
            return full
    return n


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _get(endpoint, params=None):
    params = params or {}
    params["apiKey"] = ODDS_API_KEY
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        print(f"[OddsAPI] HTTP {r.status_code}: {r.text[:150]}")
    except Exception as e:
        print(f"[OddsAPI] Hata: {e}")
    return None


def _safe_float(v):
    try:
        f = float(v)
        return round(f, 2) if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def _find_event(home_team: str, away_team: str, date_str: str):
    cache_key = f"{home_team}|{away_team}|{date_str}"
    if cache_key in _event_cache:
        return _event_cache[cache_key]

    data = _get("events", {"sport": "Basketball", "date": date_str, "bookmakers": BOOKMAKER})
    if not data:
        _event_cache[cache_key] = None
        return None

    events = data if isinstance(data, list) else data.get("data", data.get("events", []))
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
        print(f"[OddsAPI] Bulunamadı: {home_team} vs {away_team} (skor: {best_score:.2f})")
        _event_cache[cache_key] = None
        return None

    print(f"[OddsAPI] Bulundu: {home_team} vs {away_team} → id={best_id} (skor={best_score:.2f})")
    _event_cache[cache_key] = best_id
    return best_id


def _parse_odds(raw):
    """
    Ham yanıttan sadece şunları çıkar:
      - ou_line, ou_over, ou_under  (MS toplam — tek barem, merkeze en yakın)
      - iy_line, iy_over, iy_under  (IY toplam — tek barem)
      - ms_home, ms_away            (maç sonucu)
    """
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

        # ── MS ─────────────────────────────────────────────────
        if name in ("ml", "1x2", "match result", "3way", "moneyline", "winner"):
            o = odds_list[0]
            result["ms_home"] = _safe_float(o.get("home") or o.get("1"))
            result["ms_away"] = _safe_float(o.get("away") or o.get("2"))

        # ── MS OU — tek barem (50/50'ye en yakın) ──────────────
        elif name in ("over/under", "totals", "total", "ou", "total points"):
            best = None
            best_dist = float("inf")
            for row in odds_list:
                line = _safe_float(row.get("max") or row.get("line") or row.get("points") or row.get("handicap"))
                ov   = _safe_float(row.get("over"))
                un   = _safe_float(row.get("under"))
                if line and ov and un:
                    # Oranlar birbirine ne kadar yakın? Yakınsa 50/50 line
                    dist = abs(ov - un)
                    if dist < best_dist:
                        best_dist = dist
                        best = (line, ov, un)
            if best:
                result["ou_line"]  = best[0]
                result["ou_over"]  = best[1]
                result["ou_under"] = best[2]

        # ── Ev Takımı OU ────────────────────────────────────────
        elif any(k in name for k in ("home team total", "home total", "team total home", "home points")):
            best = None; best_dist = float("inf")
            for row in odds_list:
                line = _safe_float(row.get("max") or row.get("line") or row.get("points"))
                ov   = _safe_float(row.get("over"))
                un   = _safe_float(row.get("under"))
                if line and ov and un:
                    dist = abs(ov - un)
                    if dist < best_dist:
                        best_dist = dist; best = (line, ov, un)
            if best:
                result["home_ou_line"]  = best[0]
                result["home_ou_over"]  = best[1]
                result["home_ou_under"] = best[2]

        # ── Deplasman Takımı OU ─────────────────────────────────
        elif any(k in name for k in ("away team total", "away total", "team total away", "away points")):
            best = None; best_dist = float("inf")
            for row in odds_list:
                line = _safe_float(row.get("max") or row.get("line") or row.get("points"))
                ov   = _safe_float(row.get("over"))
                un   = _safe_float(row.get("under"))
                if line and ov and un:
                    dist = abs(ov - un)
                    if dist < best_dist:
                        best_dist = dist; best = (line, ov, un)
            if best:
                result["away_ou_line"]  = best[0]
                result["away_ou_over"]  = best[1]
                result["away_ou_under"] = best[2]

        # ── IY OU — tek barem ──────────────────────────────────
        elif any(k in name for k in ("1st half", "first half", "halftime", "half time")):
            if any(k in name for k in ("over", "under", "total", "ou")):
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
                if best:
                    result["iy_line"]  = best[0]
                    result["iy_over"]  = best[1]
                    result["iy_under"] = best[2]

    return result


def get_market_odds(home_team: str, away_team: str, date_str: str) -> dict:
    """
    Döner:
    {
      "found": True,
      "ms_home": 1.65, "ms_away": 2.20,
      "ou_line": 225.5, "ou_over": 1.87, "ou_under": 1.93,
      "iy_line": 107.5, "iy_over": 1.90, "iy_under": 1.83,
    }
    """
    event_id = _find_event(home_team, away_team, date_str)
    if not event_id:
        return {"found": False}

    if event_id in _odds_cache:
        return _odds_cache[event_id]

    raw    = _get("odds", {"eventId": event_id, "bookmakers": BOOKMAKER})
    parsed = _parse_odds(raw)
    parsed["found"] = bool(parsed.get("ou_line") or parsed.get("ms_home"))

    _odds_cache[event_id] = parsed
    return parsed


def clear_cache():
    _event_cache.clear()
    _odds_cache.clear()
