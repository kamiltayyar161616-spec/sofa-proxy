"""
SofaScore API — BasketOracle
Maç oranlarını SofaScore'dan çeker.

Endpoints:
  Günlük maçlar : https://api.sofascore.com/api/v1/sport/basketball/scheduled-events/{date}
  Maç oranları  : https://api.sofascore.com/api/v1/event/{id}/odds/1/all
"""

import re
import requests
import datetime
import difflib

BASE     = "https://api.sofascore.com/api/v1"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/basketball",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}
TIMEOUT  = 10

_events_cache = {}   # date_str → list of events
_odds_cache   = {}   # sofa_id → parsed odds
_id_map       = {}   # "home|away" → sofa_id


def _get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        print(f"[SofaOdds] HTTP {r.status_code}: {url} → {r.text[:100]}")
    except Exception as e:
        print(f"[SofaOdds] Hata: {e} → {url}")
    return None


def _frac_to_decimal(frac: str) -> float:
    """'10/11' → 1.91 formatına çevir."""
    try:
        if '/' in frac:
            n, d = frac.split('/')
            return round(float(n) / float(d) + 1, 2)
        return round(float(frac), 2)
    except Exception:
        return None


def _normalize(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r'\(.*?\)', '', n).strip()
    n = re.sub(r'\s+', ' ', n)
    return n


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def load_events(date_str: str) -> list:
    """Günün basketbol maçlarını SofaScore'dan çek."""
    if date_str in _events_cache:
        return _events_cache[date_str]

    url  = f"{BASE}/sport/basketball/scheduled-events/{date_str}"
    data = _get(url)
    if not data:
        _events_cache[date_str] = []
        return []

    events = data.get("events", [])
    print(f"[SofaOdds] {date_str}: {len(events)} basketbol maçı yüklendi")
    _events_cache[date_str] = events
    return events


def find_event_id(home_team: str, away_team: str, date_str: str) -> str:
    """AllSports takım isimlerini SofaScore event ID'sine eşleştir."""
    cache_key = f"{home_team}|{away_team}"
    if cache_key in _id_map:
        return _id_map[cache_key]

    events = load_events(date_str)
    if not events:
        _id_map[cache_key] = None
        return None

    best_id    = None
    best_score = 0.0

    for ev in events:
        ht = ev.get("homeTeam", {}).get("name", "")
        at = ev.get("awayTeam", {}).get("name", "")
        score = (_similarity(home_team, ht) + _similarity(away_team, at)) / 2
        if score > best_score:
            best_score = score
            best_id    = str(ev.get("id", ""))

    if best_score < 0.35:
        print(f"[SofaOdds] Bulunamadı: {home_team} vs {away_team} (max: {best_score:.2f})")
        _id_map[cache_key] = None
        return None

    print(f"[SofaOdds] Eşleşti: {home_team} vs {away_team} → sofa_id={best_id} ({best_score:.2f})")
    _id_map[cache_key] = best_id
    return best_id


def get_odds(home_team: str, away_team: str, date_str: str) -> dict:
    """
    Döner:
    {
      "found": True,
      "ou_line": 234.5,  "ou_over": 1.91,  "ou_under": 1.91,
      "iy_line": 112.5,  "iy_over": 1.87,  "iy_under": 1.87,
      "ms_home": 1.65,   "ms_away": 2.20,
      "spread_line": -2.5, "spread_home": 1.91, "spread_away": 1.91,
      "sofa_id": "14442222"
    }
    """
    sofa_id = find_event_id(home_team, away_team, date_str)
    if not sofa_id:
        return {"found": False}

    if sofa_id in _odds_cache:
        return _odds_cache[sofa_id]

    url  = f"{BASE}/event/{sofa_id}/odds/1/all"
    data = _get(url)
    if not data:
        _odds_cache[sofa_id] = {"found": False}
        return {"found": False}

    markets = data.get("markets", [])
    result  = {
        "found":        False,
        "sofa_id":      sofa_id,
        "ou_line":      None, "ou_over":    None, "ou_under":   None,
        "iy_line":      None, "iy_over":    None, "iy_under":   None,
        "ms_home":      None, "ms_away":    None,
        "spread_line":  None, "spread_home":None, "spread_away":None,
        "home_ou_line": None, "home_ou_over":None,"home_ou_under":None,
        "away_ou_line": None, "away_ou_over":None,"away_ou_under":None,
    }

    for market in markets:
        name    = (market.get("marketName") or "").lower()
        period  = (market.get("marketPeriod") or "").lower()
        choices = market.get("choices", [])
        group   = market.get("choiceGroup", "")

        # ── MS ────────────────────────────────────────────────────
        if name in ("full time", "match", "home/away") and period == "match":
            for c in choices:
                odd = _frac_to_decimal(c.get("fractionalValue", ""))
                if c.get("name") == "1" and odd:
                    result["ms_home"] = odd
                elif c.get("name") == "2" and odd:
                    result["ms_away"] = odd

        # ── MS Game Total (OU) ─────────────────────────────────────
        elif name == "game total" and period == "match":
            line = _parse_line(group)
            if line:
                result["ou_line"] = line
                for c in choices:
                    odd = _frac_to_decimal(c.get("fractionalValue", ""))
                    cn  = (c.get("name") or "").lower()
                    if cn == "over"  and odd: result["ou_over"]  = odd
                    if cn == "under" and odd: result["ou_under"] = odd

        # ── 1st Half Total (IY OU) ────────────────────────────────
        elif name in ("1st half total", "1st half over/under", "halftime total") or \
             (("1st half" in name or "first half" in name) and ("total" in name or "over" in name)):
            line = _parse_line(group)
            if line:
                result["iy_line"] = line
                for c in choices:
                    odd = _frac_to_decimal(c.get("fractionalValue", ""))
                    cn  = (c.get("name") or "").lower()
                    if cn == "over"  and odd: result["iy_over"]  = odd
                    if cn == "under" and odd: result["iy_under"] = odd

        # ── Point Spread / Handicap ───────────────────────────────
        elif name in ("point spread", "asian handicap", "handicap", "spread") and period == "match":
            for c in choices:
                odd  = _frac_to_decimal(c.get("fractionalValue", ""))
                cname = c.get("name", "")
                line  = _parse_line(cname)
                if line is not None and odd:
                    if line < 0:
                        result["spread_line"] = line
                        result["spread_away"] = odd   # favorite (-)
                    elif line > 0:
                        result["spread_home"] = odd   # underdog (+)

        # ── Ev Takımı Total ───────────────────────────────────────
        elif "home team total" in name or "home total" in name:
            line = _parse_line(group)
            if line:
                result["home_ou_line"] = line
                for c in choices:
                    odd = _frac_to_decimal(c.get("fractionalValue", ""))
                    cn  = (c.get("name") or "").lower()
                    if cn == "over"  and odd: result["home_ou_over"]  = odd
                    if cn == "under" and odd: result["home_ou_under"] = odd

        # ── Deplasman Takımı Total ────────────────────────────────
        elif "away team total" in name or "away total" in name:
            line = _parse_line(group)
            if line:
                result["away_ou_line"] = line
                for c in choices:
                    odd = _frac_to_decimal(c.get("fractionalValue", ""))
                    cn  = (c.get("name") or "").lower()
                    if cn == "over"  and odd: result["away_ou_over"]  = odd
                    if cn == "under" and odd: result["away_ou_under"] = odd

    result["found"] = bool(result["ou_line"] or result["ms_home"])
    _odds_cache[sofa_id] = result

    print(f"[SofaOdds] {home_team} vs {away_team}: "
          f"ou={result['ou_line']} iy={result['iy_line']} "
          f"ms={result['ms_home']}/{result['ms_away']}")
    return result


def _parse_line(s: str):
    """'234.5', '(-2.5) Hawks', '+2.5' gibi string'den sayı çıkar."""
    if not s:
        return None
    try:
        m = re.search(r'[-+]?\d+\.?\d*', str(s))
        if m:
            return float(m.group())
    except Exception:
        pass
    return None


def clear_cache():
    _events_cache.clear()
    _odds_cache.clear()
    _id_map.clear()
