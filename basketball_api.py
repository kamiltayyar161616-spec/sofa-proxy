"""
AllSports Basketball API
Fiktur + Takim gecmisi + Odds
"""

import requests
import datetime
import os

BASE      = "https://apiv2.allsportsapi.com/basketball/"
API_KEY   = os.environ.get("ALLSPORTS_BASKET_KEY", "aa5933f5de98821ee0d4c93d753510ee11e260318739091c74ed1a079641b40b")
HEADERS   = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TIMEOUT   = 15
TZ_OFFSET = 1   # UTC+1

EXCLUDE_KW = [
    "women","woman","feminin","ladies","w ","women",
    "u16","u17","u18","u19","u20","u21","u22","u23",
    "youth","junior","reserve","academy",
    "3x3","beach","streetball","friendly","exhibition",
]

ALLOWED_LEAGUES = {
    "euroleague": True, "eurocup": True, "fiba": True,
    "basketball champions league": True,
    "bbl": True, "pro a": True, "lnb pro a": True, "pro b": True,
    "lega basket": True, "lba": True, "liga acb": True, "acb": True,
    "liga endesa": True, "bsn": True, "bnxt": True, "vbt": True,
    "beko bbl": True, "ebl": True, "nbl": True, "basketliiga": True,
    "kkl": True, "nba": True, "g league": True,
    "bsl": True, "bsktbl sl": True, "türkiye basket": True,
    "bsuperlig": True, "basket sl": True, "tb2l": True,
    "vef": True, "lkl": True, "vtb united": True, "vtb": True,
    "pbl": True, "ncaa": True, "superleague": True,
    "basket league": True, "lbbl": True,
}

_league_cache = {}
_odds_cache   = {}


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


def _safe_float(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
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
    ln   = (league_name  or "").lower()
    cn   = (country_name or "").lower()
    full = f"{cn} {ln}"
    return any(k in full for k in EXCLUDE_KW)


# ─────────────────────────────────────────────────────────────
# ODDS  —  Gerçek AllSports API yapısına göre parse
#
# API response örneği (2. ekrandan):
# {
#   "success": 1,
#   "result": [{
#     "248680": {
#       "3Way Result": {
#         "Home":   {"Marathon":"1.70","1xBet":"1.70",...},
#         "Draw":   {"Marathon":"13.50",...},
#         "Away":   {"Marathon":"2.17",...}
#       },
#       "Asian Handicap -0.5": {
#         "Home": {"bet365":"1.80",...},
#         "Away": {"bet365":"1.78",...}
#       },
#       "Over/Under 170": {
#         "Over":  {"bet365":"1.85",...},
#         "Under": {"bet365":"1.85",...}
#       },
#       "Over/Under 1st Half 87.5": {
#         "Over":  {"bet365":"1.85",...},
#         "Under": {"bet365":"1.85",...}
#       }
#     }
#   }]
# }
# ─────────────────────────────────────────────────────────────

# Tercihli bookmaker sırası (önce bulunana bak)
PREFERRED_BM = ["bet365", "marathon", "pncl", "1xbet", "betano", "williamhill", "sbo", "188bet"]


def _pick_bm_value(bm_dict):
    """Bookmaker dict'inden en güvenilir değeri seç."""
    if not bm_dict or not isinstance(bm_dict, dict):
        return None
    for bm in PREFERRED_BM:
        for k, v in bm_dict.items():
            if bm in k.lower():
                return _safe_float(v)
    # İlk mevcut değeri al
    for v in bm_dict.values():
        f = _safe_float(v)
        if f:
            return f
    return None


def _extract_number_from_key(key):
    """'Asian Handicap -3.5' veya 'Over/Under 170.5' gibi key'den sayıyı çıkar."""
    import re
    nums = re.findall(r'-?\d+\.?\d*', key)
    for n in nums:
        try:
            v = float(n)
            if abs(v) > 0.1:   # sıfır değil
                return v
        except ValueError:
            continue
    return None


def get_odds(event_key):
    """
    Maç için bahisçi oranlarını çek ve ayrıştır.
    Döner:
    {
        "odd_home": 1.70, "odd_draw": 13.50, "odd_away": 2.17,
        "ah_main_line": -0.5, "ah_main_home": 1.80, "ah_main_away": 1.78,
        "ou_lines_market":    [{"line":170.0,"odd_over":1.85,"odd_under":1.85}, ...],
        "iy_ou_lines_market": [{"line":87.5, "odd_over":1.85,"odd_under":1.85}, ...],
    }
    """
    ek = str(event_key)
    if ek in _odds_cache:
        return _odds_cache[ek]

    # AllSports odds için doğru parametre: eventId
    result = _get({"met": "Odds", "eventId": ek})
    if not result:
        print(f"[Odds] eventId={ek} için sonuç yok")
        _odds_cache[ek] = {}
        return {}

    odds_out  = {}
    ah_lines  = {}   # {line_val: {"home": x, "away": y}}
    ou_lines  = {}   # {line_val: {"over": x, "under": y}}
    iy_lines  = {}   # {line_val: {"over": x, "under": y}}

    try:
        # result[0] bir dict, key = event_key veya direkt market dict'i
        raw = result[0] if isinstance(result, list) and result else result

        # Event key'li wrapper varsa içine gir
        if isinstance(raw, dict):
            # {event_key: {markets}} veya direkt {market_name: {side: {bm: odd}}}
            keys = list(raw.keys())
            # İlk key sayısal ise wrapper
            if keys and str(keys[0]).isdigit():
                raw = raw[keys[0]]

        if not isinstance(raw, dict):
            print(f"[Odds] Beklenmeyen yapı: {type(raw)}")
            _odds_cache[ek] = {}
            return {}

        for market_name, sides in raw.items():
            if not isinstance(sides, dict):
                continue

            mlow = market_name.lower()

            # ── 3-Way Result (MS) ─────────────────────────────────
            if "3way" in mlow or ("result" in mlow and "asian" not in mlow and "1st" not in mlow):
                for side_name, bm_dict in sides.items():
                    slow = side_name.lower()
                    val  = _pick_bm_value(bm_dict)
                    if not val:
                        continue
                    if slow in ("home", "1"):
                        odds_out["odd_home"] = val
                    elif slow in ("draw", "x"):
                        odds_out["odd_draw"] = val
                    elif slow in ("away", "2"):
                        odds_out["odd_away"] = val

            # ── Asian Handicap ────────────────────────────────────
            elif "asian handicap" in mlow and "1st half" not in mlow and "half" not in mlow:
                line_val = _extract_number_from_key(market_name)
                if line_val is None:
                    continue
                for side_name, bm_dict in sides.items():
                    slow = side_name.lower()
                    val  = _pick_bm_value(bm_dict)
                    if not val:
                        continue
                    if line_val not in ah_lines:
                        ah_lines[line_val] = {}
                    if "home" in slow:
                        ah_lines[line_val]["home"] = val
                    elif "away" in slow:
                        ah_lines[line_val]["away"] = val

            # ── Over/Under Maç Toplam ─────────────────────────────
            elif "over/under" in mlow and "1st half" not in mlow and "half" not in mlow:
                line_val = _extract_number_from_key(market_name)
                if line_val is None or line_val <= 0:
                    continue
                for side_name, bm_dict in sides.items():
                    slow = side_name.lower()
                    val  = _pick_bm_value(bm_dict)
                    if not val:
                        continue
                    if line_val not in ou_lines:
                        ou_lines[line_val] = {}
                    if "over" in slow:
                        ou_lines[line_val]["over"] = val
                    elif "under" in slow:
                        ou_lines[line_val]["under"] = val

            # ── Over/Under İY (1st Half) ──────────────────────────
            elif "over/under" in mlow and ("1st half" in mlow or "half" in mlow):
                line_val = _extract_number_from_key(market_name)
                if line_val is None or line_val <= 0:
                    continue
                for side_name, bm_dict in sides.items():
                    slow = side_name.lower()
                    val  = _pick_bm_value(bm_dict)
                    if not val:
                        continue
                    if line_val not in iy_lines:
                        iy_lines[line_val] = {}
                    if "over" in slow:
                        iy_lines[line_val]["over"] = val
                    elif "under" in slow:
                        iy_lines[line_val]["under"] = val

    except Exception as e:
        print(f"[Odds] Parse hatasi eventId={ek}: {e}")
        import traceback; traceback.print_exc()

    # ── Asian Handicap sonuçları ──────────────────────────────────
    if ah_lines:
        # Sıfıra en yakın çizgiyi ana handikap olarak seç
        closest = min(ah_lines.keys(), key=lambda x: abs(x))
        odds_out["ah_main_line"] = closest
        odds_out["ah_main_home"] = ah_lines[closest].get("home")
        odds_out["ah_main_away"] = ah_lines[closest].get("away")
        odds_out["ah_lines"]     = ah_lines

    # ── OU listesi (sıralı, her iki tarafı olan çizgiler) ─────────
    if ou_lines:
        complete = {k: v for k, v in ou_lines.items() if "over" in v and "under" in v}
        odds_out["ou_lines_market"] = [
            {"line": k, "odd_over": complete[k]["over"], "odd_under": complete[k]["under"]}
            for k in sorted(complete.keys())
        ]

    # ── İY OU listesi ─────────────────────────────────────────────
    if iy_lines:
        complete_iy = {k: v for k, v in iy_lines.items() if "over" in v and "under" in v}
        odds_out["iy_ou_lines_market"] = [
            {"line": k, "odd_over": complete_iy[k]["over"], "odd_under": complete_iy[k]["under"]}
            for k in sorted(complete_iy.keys())
        ]

    if odds_out:
        print(f"[Odds] eventId={ek} → MS: {odds_out.get('odd_home')}/{odds_out.get('odd_away')} | "
              f"OU çizgi sayısı: {len(odds_out.get('ou_lines_market',[]))}")
    else:
        print(f"[Odds] eventId={ek} için oran çıkarılamadı")

    _odds_cache[ek] = odds_out
    return odds_out


# ─────────────────────────────────────────────
# FİKSTÜR
# ─────────────────────────────────────────────

def get_fixtures_by_date(date_str):
    try:
        tr_day    = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        prev_date = (tr_day - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        next_date = (tr_day + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        prev_date = date_str
        next_date = date_str

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

    # Türkiye günü sınırları (UTC+1 offset uygulandıktan sonra)
    tr_start = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    tr_end   = tr_start + datetime.timedelta(hours=24)

    matches = []
    seen    = set()

    for ev in all_results:
        try:
            mid = str(ev.get("event_key", ""))
            if mid in seen:
                continue

            raw_time = (ev.get("event_time", "00:00") or "00:00")[:5]
            raw_date = ev.get("event_date", date_str)

            # Bazı API cevaplarında event_date boş veya None gelebilir
            if not raw_date:
                raw_date = date_str

            try:
                api_dt = datetime.datetime.strptime(f"{raw_date} {raw_time}", "%Y-%m-%d %H:%M")
            except ValueError:
                api_dt = datetime.datetime.strptime(f"{raw_date} 00:00", "%Y-%m-%d %H:%M")

            # UTC+1 offset uygula → Türkiye saati
            tr_dt = api_dt + datetime.timedelta(hours=TZ_OFFSET)

            # Türkiye gününe ait mi kontrol et
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

            # Zorunlu alanları kontrol et
            home_team = ev.get("event_home_team") or ev.get("home_team", "")
            away_team = ev.get("event_away_team") or ev.get("away_team", "")
            home_key  = ev.get("home_team_key")
            away_key  = ev.get("away_team_key")
            if not home_team or not away_team or home_key is None or away_key is None:
                print(f"[BasketAPI] Eksik alan, maç atlandı: {mid}")
                continue

            # Odds çek (her fixture için)
            odds = get_odds(mid)

            matches.append({
                "match_id":           mid,
                "home_team":          home_team,
                "away_team":          away_team,
                "home_id":            int(home_key),
                "away_id":            int(away_key),
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
                # Bahisçi oranları (sayfada görünür)
                "odd_home":           odds.get("odd_home"),
                "odd_draw":           odds.get("odd_draw"),
                "odd_away":           odds.get("odd_away"),
                "ah_main_line":       odds.get("ah_main_line"),
                "ah_main_home":       odds.get("ah_main_home"),
                "ah_main_away":       odds.get("ah_main_away"),
                "ou_lines_market":    odds.get("ou_lines_market",    []),
                "iy_ou_lines_market": odds.get("iy_ou_lines_market", []),
            })
        except Exception as ex:
            print(f"[BasketAPI] Maç işleme hatası mid={mid}: {ex}")
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

    print(f"[BasketAPI] League {league_id} cekiliyor: {from_str} -> {to_str}")
    result = _get({
        "met":      "Fixtures",
        "leagueId": league_id,
        "from":     from_str,
        "to":       to_str,
    })

    if not result:
        _league_cache[league_id] = []
        return []

    print(f"[BasketAPI] League {league_id}: {len(result)} mac geldi")

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
                "event_status":  ev.get("event_status", ""),
                "event_live":    ev.get("event_live", "0"),
                "league_name":   ev.get("league_name", ""),
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

        is_home     = (team_id == home_id)
        scored      = h if is_home else a
        conceded    = a if is_home else h
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
            "home_general": [], "home_venue":   [],
            "away_general": [], "away_venue":   [],
        }

    home_all   = get_team_matches_from_league(home_id,  league_key, 12)
    away_all   = get_team_matches_from_league(away_id,  league_key, 12)
    home_venue = [m for m in home_all if     m["is_home"]]
    away_venue = [m for m in away_all if not m["is_home"]]

    return {
        "home_general": home_all[:6],
        "home_venue":   home_venue[:6],
        "away_general": away_all[:6],
        "away_venue":   away_venue[:6],
    }
