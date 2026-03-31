"""
Odds-API.io entegrasyonu
Sadece oranları çeker. İstatistikler AllSports'tan gelmeye devam eder.
Base URL: https://api.odds-api.io/v3
"""

import requests
import datetime
import os

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "e097279bf7409b014ed0b9bdff8df8d8ce4045b2e335542d9cde58e0c3462a42")
ODDS_BASE    = "https://api.odds-api.io/v3"
HEADERS      = {"Accept": "application/json"}
TIMEOUT      = 10
BOOKMAKERS   = "Bet365,Unibet,1xBet,Pinnacle,William Hill"

_event_cache      = {}
_event_cache_time = None
_CACHE_SECONDS    = 600  # 10 dakika


def _get(path, params=None):
    p = params or {}
    p["apiKey"] = ODDS_API_KEY
    try:
        r = requests.get(f"{ODDS_BASE}{path}", headers=HEADERS, params=p, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        print(f"[OddsAPI] HTTP {r.status_code} {path}")
    except Exception as e:
        print(f"[OddsAPI] Bağlantı hatası: {e}")
    return None


def _norm(name):
    if not name:
        return ""
    n = name.lower().strip()
    for rm in [" fc", " bc", " bk", " basketball", " club", " hoops"]:
        n = n.replace(rm, "")
    return n.strip()


def _match(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    # İlk kelime
    return a.split()[0] == b.split()[0] if a.split() and b.split() else False


def refresh_event_cache():
    global _event_cache, _event_cache_time
    now = datetime.datetime.utcnow()
    if _event_cache_time and (now - _event_cache_time).seconds < _CACHE_SECONDS:
        return
    data = _get("/events", {"sport": "basketball", "status": "live,pending", "limit": 300})
    if not data or not isinstance(data, list):
        return
    cache = {}
    for ev in data:
        eid  = ev.get("id")
        home = ev.get("home", "")
        away = ev.get("away", "")
        if eid and home and away:
            cache[eid] = {"id": eid, "home": home, "away": away,
                          "league": ev.get("league", {}).get("name", ""),
                          "date": ev.get("date", ""), "status": ev.get("status", "")}
    _event_cache = cache
    _event_cache_time = now
    print(f"[OddsAPI] {len(cache)} basketbol maçı cache'lendi")


def find_event(home_team, away_team):
    refresh_event_cache()
    best, best_score = None, 0
    for ev in _event_cache.values():
        score = (_match(home_team, ev["home"]) * 2) + (_match(away_team, ev["away"]) * 2)
        if score > best_score:
            best_score, best = score, ev
    return best if best_score >= 3 else None


def _best_ou(all_lines):
    """Aynı çizgi için en iyi oranları birleştir."""
    d = {}
    for l in all_lines:
        k = l["line"]
        if k not in d:
            d[k] = dict(l)
        else:
            d[k]["odd_over"]  = max(d[k]["odd_over"],  l["odd_over"])
            d[k]["odd_under"] = max(d[k]["odd_under"], l["odd_under"])
    return sorted(d.values(), key=lambda x: x["line"])


def get_odds_for_match(home_team, away_team):
    """
    Odds-API'den belirtilen maçın oranlarını çek.
    Döner: { found, odd_home, odd_away, odd_draw,
              ou_lines_market, iy_ou_lines_market }
    """
    ev = find_event(home_team, away_team)
    if not ev:
        print(f"[OddsAPI] Eşleşme yok: {home_team} vs {away_team}")
        return {"found": False}

    data = _get("/odds", {"eventId": ev["id"], "bookmakers": BOOKMAKERS})
    if not data:
        return {"found": False}

    bookmakers = data.get("bookmakers", {})
    if not bookmakers:
        return {"found": False}

    ml_list  = []
    ou_list  = []
    iy_list  = []

    for bk, markets in bookmakers.items():
        if not isinstance(markets, list):
            continue
        for mkt in markets:
            name = mkt.get("name", "").lower()
            odds = mkt.get("odds", [])

            # ── ML ──────────────────────────────────────────────
            if name in ("ml", "moneyline", "match result", "1x2", "h2h", "winner"):
                for o in odds:
                    h = o.get("home") or o.get("1")
                    d = o.get("draw") or o.get("X")
                    a = o.get("away") or o.get("2")
                    if h and a:
                        try:
                            ml_list.append((float(h), float(d) if d else None, float(a)))
                        except Exception:
                            pass

            # ── Over/Under (MS) ─────────────────────────────────
            elif any(x in name for x in ["over/under", "totals", "total points", "o/u"]):
                is_half = any(x in name for x in ["half", "halftime", "1st", "2nd", "quarter"])
                for o in odds:
                    line  = o.get("max") or o.get("points") or o.get("line") or o.get("total")
                    over  = o.get("over")  or o.get("Over")
                    under = o.get("under") or o.get("Under")
                    if line and over and under:
                        try:
                            entry = {"line": float(line),
                                     "odd_over": round(float(over), 2),
                                     "odd_under": round(float(under), 2)}
                            (iy_list if is_half else ou_list).append(entry)
                        except Exception:
                            pass

    result = {"found": True, "event_id": ev["id"]}

    if ml_list:
        result["odd_home"] = round(max(o[0] for o in ml_list), 2)
        result["odd_away"] = round(max(o[2] for o in ml_list), 2)
        draws = [o[1] for o in ml_list if o[1]]
        if draws:
            result["odd_draw"] = round(max(draws), 2)

    if ou_list:
        result["ou_lines_market"] = _best_ou(ou_list)

    if iy_list:
        result["iy_ou_lines_market"] = _best_ou(iy_list)

    print(f"[OddsAPI] {home_team} vs {away_team} → "
          f"ML:{result.get('odd_home')}/{result.get('odd_away')} "
          f"OU:{len(result.get('ou_lines_market',[]))} "
          f"IY:{len(result.get('iy_ou_lines_market',[]))}")
    return result
