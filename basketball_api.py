"""
AllSports Basketball API
Fikstür + Takım geçmişi + Odds parse
"""

import requests
import datetime
import os
import re

BASE      = "https://apiv2.allsportsapi.com/basketball/"
API_KEY   = os.environ.get("ALLSPORTS_BASKET_KEY", "aa5933f5de98821ee0d4c93d753510ee11e260318739091c74ed1a079641b40b")
HEADERS   = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TIMEOUT   = 15
TZ_OFFSET = 1

EXCLUDE_KW = [
    "women","woman","feminin","ladies","w ",
    "u16","u17","u18","u19","u20","u21","u22","u23",
    "youth","junior","reserve","academy",
    "3x3","beach","streetball","friendly","exhibition",
]

_league_cache = {}
_odds_cache   = {}   # match_id → parsed odds


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
    if not side_dict or not isinstance(side_dict, dict):
        return None
    vals = []
    for v in side_dict.values():
        try:
            vals.append(float(v))
        except Exception:
            pass
    return max(vals) if vals else None


# ─────────────────────────────────────────────
# ODDS — Ayrı endpoint ile çek
# ─────────────────────────────────────────────

def fetch_odds_for_match(match_id):
    """
    AllSports met=Odds endpoint'i ile belirli bir maçın oranlarını çek.
    Sonucu cache'le.
    """
    mid = str(match_id)
    if mid in _odds_cache:
        return _odds_cache[mid]

    result = _get({"met": "Odds", "matchId": mid})
    if not result:
        _odds_cache[mid] = {}
        return {}

    # result bir liste, ilk elemanın odds field'ı
    odds_raw = None
    if isinstance(result, list) and result:
        odds_raw = result[0].get("odds") if isinstance(result[0], dict) else None
    elif isinstance(result, dict):
        odds_raw = result.get("odds")

    parsed = _parse_odds_dict(odds_raw or {})
    _odds_cache[mid] = parsed
    print(f"[BasketAPI] Odds çekildi match {mid}: MS_OU={len(parsed.get('ou_lines_market',[]))}, IY_OU={len(parsed.get('iy_ou_lines_market',[]))}")
    return parsed


def _parse_odds_dict(odds_raw):
    """
    Ham odds dict'ini parse et.
    AllSports Basketball key formatları:
      MS : "Over/Under 172.5", "Total Points 172.5"
      İY : "Over/Under 1st Half 87.5", "1st Half Over/Under 87.5"
    """
    if not odds_raw or not isinstance(odds_raw, dict):
        return {}

    result = {}

    # ── 1X2 ──────────────────────────────────────────────────────
    way3 = (odds_raw.get("3Way Result") or
            odds_raw.get("Match Result") or
            odds_raw.get("Home/Away") or
            odds_raw.get("1X2") or {})
    if way3:
        oh = _best_odd(way3.get("Home") or way3.get("1") or {})
        od = _best_odd(way3.get("Draw") or way3.get("X") or {})
        oa = _best_odd(way3.get("Away") or way3.get("2") or {})
        if oh: result["odd_home"] = round(oh, 2)
        if od: result["odd_draw"] = round(od, 2)
        if oa: result["odd_away"] = round(oa, 2)

    # ── MS Over/Under ─────────────────────────────────────────────
    MS_PREFIXES = [
        "over/under ",
        "total over/under ",
        "totals ",
        "asian total ",
        "o/u ",
        "total points ",
        "total ",
    ]
    # İY olduğunu gösteren anahtar kelimeler
    IY_MARKERS = ["1st half", "half time", "halftime", "iy", "quarter"]

    ou_lines = []
    iy_ou_lines = []

    for key, val in odds_raw.items():
        if not isinstance(val, dict):
            continue
        key_lower = key.lower()
        is_iy = any(m in key_lower for m in IY_MARKERS)

        # Sayısal line değeri bul
        line = None
        for prefix in MS_PREFIXES + ["over/under 1st half ", "1st half over/under ",
                                      "over/under halftime ", "halftime over/under "]:
            if key_lower.startswith(prefix):
                tail = key[len(prefix):].strip()
                try:
                    line = float(tail)
                    break
                except Exception:
                    pass

        # Prefix eşleşmediyse sondaki sayıyı dene
        if line is None:
            m = re.search(r'(\d+\.?\d*)\s*$', key)
            if m:
                try:
                    line = float(m.group(1))
                except Exception:
                    pass

        if line is None:
            continue

        ov = _best_odd(val.get("Over")  or val.get("over")  or {})
        un = _best_odd(val.get("Under") or val.get("under") or {})
        if not (ov and un):
            continue

        entry = {"line": line, "odd_over": round(ov, 2), "odd_under": round(un, 2)}
        if is_iy:
            iy_ou_lines.append(entry)
        else:
            ou_lines.append(entry)

    # Duplikat line'ları temizle (en yüksek oran kazansın)
    def dedup(lines):
        d = {}
        for e in lines:
            l = e["line"]
            if l not in d or e["odd_over"] > d[l]["odd_over"]:
                d[l] = e
        return sorted(d.values(), key=lambda x: x["line"])

    if ou_lines:
        result["ou_lines_market"] = dedup(ou_lines)
    if iy_ou_lines:
        result["iy_ou_lines_market"] = dedup(iy_ou_lines)

    return result


def _parse_odds_from_event(ev):
    """Fikstür event içindeki odds field'ını parse et (fallback)."""
    return _parse_odds_dict(ev.get("odds") or {})


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

            # Önce event içindeki odds'ı dene, yoksa ayrı endpoint
            odds = _parse_odds_from_event(ev)
            if not odds.get("ou_lines_market") and not odds.get("odd_home"):
                odds = fetch_odds_for_match(mid)

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
        except Exception as e:
            print(f"[BasketAPI] Event parse hatası: {e}")
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
    if league_key is None:
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


# ─────────────────────────────────────────────
# DEBUG — Raw odds yapısını görmek için
# ─────────────────────────────────────────────

def get_raw_odds_sample(date_str=None):
    """Debug için: bugünkü maçlardan ilk 3'ünün ham odds key'lerini döndür."""
    if not date_str:
        date_str = datetime.date.today().strftime("%Y-%m-%d")

    results = _get({"met": "Fixtures", "from": date_str, "to": date_str})
    samples = []

    if results:
        for ev in results[:5]:
            mid = str(ev.get("event_key", ""))
            odds_in_event = ev.get("odds", {})

            # Ayrıca met=Odds ile de dene
            odds_separate = {}
            raw_sep = _get({"met": "Odds", "matchId": mid})
            if raw_sep and isinstance(raw_sep, list) and raw_sep:
                odds_separate = raw_sep[0].get("odds", {}) if isinstance(raw_sep[0], dict) else {}

            samples.append({
                "match":             f"{ev.get('event_home_team')} vs {ev.get('event_away_team')}",
                "match_id":          mid,
                "odds_in_fixture":   list(odds_in_event.keys()) if odds_in_event else [],
                "odds_separate_keys": list(odds_separate.keys()) if odds_separate else [],
                "parsed_fixture":    _parse_odds_dict(odds_in_event),
                "parsed_separate":   _parse_odds_dict(odds_separate),
            })

    return samples
