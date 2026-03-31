"""
AllSports Basketball API
Fikstür + Takım geçmişi + Odds parse
"""

import requests
import datetime
import os

BASE      = "https://apiv2.allsportsapi.com/basketball/"
API_KEY   = os.environ.get("ALLSPORTS_BASKET_KEY", "aa5933f5de98821ee0d4c93d753510ee11e260318739091c74ed1a079641b40b")
HEADERS   = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TIMEOUT   = 15
TZ_OFFSET = 2

EXCLUDE_KW = [
    "women","woman","feminin","ladies","w ",
    "u16","u17","u18","u19","u20","u21","u22","u23",
    "youth","junior","reserve","academy",
    "3x3","beach","streetball","friendly","exhibition",
]

_league_cache = {}


def _get(params):
    params["APIkey"] = API_KEY
    try:
        r = requests.get(BASE, headers=HEADERS, params=params, timeout=TIMEOUT)
        if r.status_code == 200:
            d = r.json()
            if d.get("success") == 1:
                return d.get("result", [])
    except Exception as e:
        print(f"[BasketAPI] Hata: {e}")
    return None


def _parse_score(s):
    if not s or str(s).strip() in ("-", "", "- -"):
        return None, None
    try:
        s   = str(s).strip()
        sep = " - " if " - " in s else "-"
        p   = s.split(sep)
        if len(p) == 2:
            return int(p[0].strip()), int(p[1].strip())
    except Exception:
        pass
    return None, None


def _parse_quarter_scores(ev):
    scores = ev.get("scores", {})
    q1h = q1a = q2h = q2a = q3h = q3a = q4h = q4a = None
    try:
        q1 = scores.get("1stQuarter", [{}])
        if q1: q1h, q1a = int(q1[0].get("score_home", 0)), int(q1[0].get("score_away", 0))
        q2 = scores.get("2ndQuarter", [{}])
        if q2: q2h, q2a = int(q2[0].get("score_home", 0)), int(q2[0].get("score_away", 0))
        q3 = scores.get("3rdQuarter", [{}])
        if q3: q3h, q3a = int(q3[0].get("score_home", 0)), int(q3[0].get("score_away", 0))
        q4 = scores.get("4thQuarter", [{}])
        if q4: q4h, q4a = int(q4[0].get("score_home", 0)), int(q4[0].get("score_away", 0))
    except Exception:
        pass
    return {
        "q1_home": q1h, "q1_away": q1a,
        "q2_home": q2h, "q2_away": q2a,
        "q3_home": q3h, "q3_away": q3a,
        "q4_home": q4h, "q4_away": q4a,
    }


def _status(ev):
    live   = str(ev.get("event_live", "0")).strip()
    status = str(ev.get("event_status", "")).strip().lower()
    if live == "1":
        return "live"
    if status in {"finished", "ft", "aet"}:
        return "finished"
    h, a = _parse_score(ev.get("event_final_result", ""))
    if h is not None and live != "1":
        return "finished"
    return "upcoming"


def _is_excluded(league_name, country_name):
    full = f"{(country_name or '').lower()} {(league_name or '').lower()}"
    return any(k in full for k in EXCLUDE_KW)


def _best_odd(side_dict):
    """Birden fazla bookmaker varsa en yüksek oranı al."""
    if not side_dict or not isinstance(side_dict, dict):
        return None
    vals = []
    for v in side_dict.values():
        try:
            vals.append(float(v))
        except Exception:
            pass
    return max(vals) if vals else None


def _parse_odds(ev):
    """
    Fikstür event'inden odds parse et.
    3Way Result → odd_home, odd_draw, odd_away
    Over/Under X → ou_lines_market
    Over/Under 1st Half X → iy_ou_lines_market
    """
    odds_raw = ev.get("odds")
    if not odds_raw or not isinstance(odds_raw, dict):
        return {}

    result = {}

    # ── 1X2 ────────────────────────────────────────────────────
    way3 = odds_raw.get("3Way Result", {})
    if way3:
        oh = _best_odd(way3.get("Home", {}))
        od = _best_odd(way3.get("Draw", {}))
        oa = _best_odd(way3.get("Away", {}))
        if oh: result["odd_home"] = round(oh, 2)
        if od: result["odd_draw"] = round(od, 2)
        if oa: result["odd_away"] = round(oa, 2)

    # ── MS Over/Under ───────────────────────────────────────────
    ou_lines = []
    for key, val in odds_raw.items():
        if not key.startswith("Over/Under ") or "1st Half" in key:
            continue
        try:
            line = float(key.replace("Over/Under ", "").strip())
        except Exception:
            continue
        if not isinstance(val, dict):
            continue
        ov = _best_odd(val.get("Over",  {}))
        un = _best_odd(val.get("Under", {}))
        if ov and un:
            ou_lines.append({"line": line, "odd_over": round(ov, 2), "odd_under": round(un, 2)})

    if ou_lines:
        ou_lines.sort(key=lambda x: x["line"])
        result["ou_lines_market"] = ou_lines

    # ── İY Over/Under ───────────────────────────────────────────
    iy_ou_lines = []
    for key, val in odds_raw.items():
        if not key.startswith("Over/Under 1st Half "):
            continue
        try:
            line = float(key.replace("Over/Under 1st Half ", "").strip())
        except Exception:
            continue
        if not isinstance(val, dict):
            continue
        ov = _best_odd(val.get("Over",  {}))
        un = _best_odd(val.get("Under", {}))
        if ov and un:
            iy_ou_lines.append({"line": line, "odd_over": round(ov, 2), "odd_under": round(un, 2)})

    if iy_ou_lines:
        iy_ou_lines.sort(key=lambda x: x["line"])
        result["iy_ou_lines_market"] = iy_ou_lines

    return result


# ─────────────────────────────────────────────
# FİKSTÜR
# ─────────────────────────────────────────────

def get_fixtures_by_date(date_str):
    try:
        tr_day    = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        prev_date = (tr_day - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        next_date = (tr_day + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        prev_date = next_date = date_str

    all_results = []
    for d in [prev_date, date_str, next_date]:
        r = _get({"met": "Fixtures", "from": d, "to": d})
        if r:
            all_results.extend(r)

    live = _get({"met": "Livescore"})
    if live:
        all_results.extend(live)

    if not all_results:
        return []

    tr_start = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    tr_end   = tr_start + datetime.timedelta(hours=24)

    matches = []
    seen    = set()

    for ev in all_results:
        try:
            mid = str(ev.get("event_key", ""))
            if mid in seen:
                continue

            raw_time = ev.get("event_time", "00:00")[:5]
            raw_date = ev.get("event_date", date_str)
            api_dt   = datetime.datetime.strptime(f"{raw_date} {raw_time}", "%Y-%m-%d %H:%M")
            tr_dt    = api_dt + datetime.timedelta(hours=TZ_OFFSET)

            if not (tr_start <= tr_dt < tr_end):
                continue

            seen.add(mid)

            league  = ev.get("league_name", "")
            country = ev.get("country_name", "")
            if _is_excluded(league, country):
                continue

            st = _status(ev)
            es = str(ev.get("event_quarter", ev.get("event_status", ""))).strip()
            lm = ""
            if st == "live":
                lm = es if es else "CANLI"

            h, a = _parse_score(ev.get("event_final_result", ""))
            qs   = _parse_quarter_scores(ev)

            iy_home = iy_away = None
            if qs["q1_home"] is not None and qs["q2_home"] is not None:
                iy_home = qs["q1_home"] + qs["q2_home"]
                iy_away = qs["q1_away"] + qs["q2_away"]

            odds = _parse_odds(ev)

            matches.append({
                "match_id":           mid,
                "home_team":          ev["event_home_team"],
                "away_team":          ev["event_away_team"],
                "home_id":            int(ev["home_team_key"]),
                "away_id":            int(ev["away_team_key"]),
                "league":             f"{country} - {league}" if country else league,
                "league_name":        league,
                "league_key":         ev.get("league_key"),
                "time":               tr_dt.strftime("%H:%M"),
                "timestamp":          int(tr_dt.timestamp()),
                "status":             st,
                "live_min":           lm,
                "home_score":         h,
                "away_score":         a,
                "home_ht_score":      iy_home,
                "away_ht_score":      iy_away,
                "quarters":           qs,
                # Bahis oranları (API'den)
                "odd_home":           odds.get("odd_home"),
                "odd_draw":           odds.get("odd_draw"),
                "odd_away":           odds.get("odd_away"),
                "ou_lines_market":    odds.get("ou_lines_market", []),
                "iy_ou_lines_market": odds.get("iy_ou_lines_market", []),
            })
        except Exception:
            continue

    matches.sort(key=lambda x: x["timestamp"])
    return matches


# ─────────────────────────────────────────────
# TAKİM GEÇMİŞİ
# ─────────────────────────────────────────────

def _get_league_matches(league_id):
    if league_id in _league_cache:
        return _league_cache[league_id]

    today     = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    if today.month >= 7:
        season_start = datetime.date(today.year, 7, 1)
    else:
        season_start = datetime.date(today.year - 1, 7, 1)

    from_str = season_start.strftime("%Y-%m-%d")
    to_str   = yesterday.strftime("%Y-%m-%d")

    print(f"[BasketAPI] League {league_id} çekiliyor: {from_str} -> {to_str}")
    result = _get({"met": "Fixtures", "leagueId": league_id, "from": from_str, "to": to_str})

    if not result:
        _league_cache[league_id] = []
        return []

    print(f"[BasketAPI] League {league_id}: {len(result)} maç geldi")

    slim = []
    for ev in result:
        try:
            qs   = _parse_quarter_scores(ev)
            h, a = _parse_score(ev.get("event_final_result", ""))

            iy_home = iy_away = None
            if qs["q1_home"] is not None and qs["q2_home"] is not None:
                iy_home = qs["q1_home"] + qs["q2_home"]
                iy_away = qs["q1_away"] + qs["q2_away"]

            slim.append({
                "event_key":     str(ev.get("event_key", "")),
                "event_date":    ev.get("event_date", ""),
                "home_team_key": ev.get("home_team_key"),
                "away_team_key": ev.get("away_team_key"),
                "home_score":    h,
                "away_score":    a,
                "home_ht_score": iy_home,
                "away_ht_score": iy_away,
                "q1_home": qs["q1_home"], "q1_away": qs["q1_away"],
                "q2_home": qs["q2_home"], "q2_away": qs["q2_away"],
                "q3_home": qs["q3_home"], "q3_away": qs["q3_away"],
                "q4_home": qs["q4_home"], "q4_away": qs["q4_away"],
                "event_status": ev.get("event_status", ""),
                "event_live":   ev.get("event_live", "0"),
                "league_name":  ev.get("league_name", ""),
            })
        except Exception:
            continue

    slim.sort(key=lambda x: x.get("event_date", ""), reverse=True)
    _league_cache[league_id] = slim
    return slim


def _parse_team_match(ev, team_id):
    try:
        home_id = int(ev["home_team_key"])
        away_id = int(ev["away_team_key"])
        if team_id != home_id and team_id != away_id:
            return None

        status = str(ev.get("event_status", "")).lower()
        if status not in {"finished", "ft", "aet"}:
            return None

        h = ev.get("home_score")
        a = ev.get("away_score")
        if h is None or a is None:
            return None

        is_home   = (team_id == home_id)
        scored    = h if is_home else a
        conceded  = a if is_home else h

        iy_scored   = (ev.get("home_ht_score") if is_home else ev.get("away_ht_score")) or 0
        iy_conceded = (ev.get("away_ht_score") if is_home else ev.get("home_ht_score")) or 0

        return {
            "scored":      scored,
            "conceded":    conceded,
            "iy_scored":   iy_scored,
            "iy_conceded": iy_conceded,
            "total":       h + a,
            "is_home":     is_home,
            "win":         scored > conceded,
        }
    except Exception:
        return None


def get_team_matches_from_league(team_id, league_id, limit=20):
    league_matches = _get_league_matches(league_id)
    parsed = []
    seen   = set()
    for ev in league_matches:
        key = ev.get("event_key", "")
        if key in seen:
            continue
        seen.add(key)
        p = _parse_team_match(ev, team_id)
        if p:
            parsed.append(p)
    return parsed[:limit]


def get_match_data(home_id, away_id, league_key=None):
    if not league_key:
        return {
            "home_general": [], "home_venue": [],
            "away_general": [], "away_venue": [],
        }

    home_all = get_team_matches_from_league(home_id, league_key, 20)
    away_all = get_team_matches_from_league(away_id, league_key, 20)

    home_venue = [m for m in home_all if     m["is_home"]][:6]
    away_venue = [m for m in away_all if not m["is_home"]][:6]

    return {
        "home_general": home_all[:6],
        "home_venue":   home_venue,
        "away_general": away_all[:6],
        "away_venue":   away_venue,
    }
