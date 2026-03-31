"""
BasketOracle - Flask Backend
"""

import os
import datetime
from flask import Flask, render_template, jsonify, request
from flask_caching import Cache
import basketball_api as bapi
import value_hunting_basket as vh

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "basketoracle-secret")
cache = Cache(app, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 3600})

def today_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/fixtures")
def api_fixtures():
    date      = request.args.get("date", today_str())
    cache_key = f"fix_{date}"
    cached    = cache.get(cache_key)
    if cached:
        return jsonify({"success": True, "matches": cached, "date": date})
    try:
        matches = bapi.get_fixtures_by_date(date)
        cache.set(cache_key, matches, timeout=300)
        return jsonify({"success": True, "matches": matches, "date": date})
    except Exception as e:
        print(f"[fixtures] hata: {e}")
        return jsonify({"success": False, "matches": [], "error": str(e)})

@app.route("/api/analyze/<int:home_id>/<int:away_id>")
def api_analyze(home_id, away_id):
    league_key = request.args.get("lk", type=int)
    match_id   = request.args.get("mid", "")
    no_cache   = request.args.get("nc", "0")

    cache_key = f"ana_{home_id}_{away_id}"
    if no_cache == "0":
        cached = cache.get(cache_key)
        if cached:
            return jsonify({"success": True, "analysis": cached})

    try:
        data = bapi.get_match_data(home_id, away_id, league_key)

        if not data["home_general"] and not data["away_general"]:
            fallback = vh.fallback_result()
            # Odds ekle (fallback'te bile)
            _inject_odds(fallback, match_id)
            return jsonify({"success": True, "analysis": fallback})

        result = vh.run_value_hunting(
            data["home_general"], data["home_venue"],
            data["away_general"], data["away_venue"]
        )
        ratings = vh.compute_ratings(data)
        result.update(ratings)

        # Veri bilgisi
        result["data_info"] = {
            "home_general_count": len(data["home_general"]),
            "home_venue_count":   len(data["home_venue"]),
            "away_general_count": len(data["away_general"]),
            "away_venue_count":   len(data["away_venue"]),
        }

        # ── Market oranlarını API parametrelerinden al ──
        oh  = request.args.get("oh",  type=float)
        od  = request.args.get("od",  type=float)
        oa  = request.args.get("oa",  type=float)

        import json
        ou_raw   = request.args.get("ou",   "")
        iyou_raw = request.args.get("iyou", "")

        ou_market   = json.loads(ou_raw)   if ou_raw   else []
        iy_ou_market= json.loads(iyou_raw) if iyou_raw else []

        # ── Ayrıca match_id ile doğrudan odds çek ──
        if match_id:
            live_odds = bapi.get_odds(match_id)
            if live_odds:
                if not oh  and live_odds.get("odd_home"): oh = live_odds["odd_home"]
                if not od  and live_odds.get("odd_draw"): od = live_odds["odd_draw"]
                if not oa  and live_odds.get("odd_away"): oa = live_odds["odd_away"]
                if not ou_market    and live_odds.get("ou_lines_market"):
                    ou_market = live_odds["ou_lines_market"]
                if not iy_ou_market and live_odds.get("iy_ou_lines_market"):
                    iy_ou_market = live_odds["iy_ou_lines_market"]
                # Asian Handicap
                if live_odds.get("ah_main_line") is not None:
                    result["api_ah_line"]  = live_odds["ah_main_line"]
                    result["api_ah_home"]  = live_odds["ah_main_home"]
                    result["api_ah_away"]  = live_odds["ah_main_away"]

        # MS oranları
        if oh:
            result["market_odd_home"] = oh
            result["mkt_impl_home"]   = round(100 / oh / 1.0, 1)
            result["fair_odd_home"]   = round(100 / result["prob_home"], 2) if result["prob_home"] > 0 else 15.0
            result["value_home"]      = round(result["prob_home"] - (100/oh), 1) if oh else None
        if oa:
            result["market_odd_away"] = oa
            result["mkt_impl_away"]   = round(100 / oa / 1.0, 1)
            result["fair_odd_away"]   = round(100 / result["prob_away"], 2) if result["prob_away"] > 0 else 15.0
            result["value_away"]      = round(result["prob_away"] - (100/oa), 1) if oa else None
        if od:
            result["market_odd_draw"] = od

        # Fair odds (market olmasa bile)
        if "fair_odd_home" not in result:
            result["fair_odd_home"] = round(100 / result["prob_home"], 2) if result["prob_home"] > 0 else 15.0
        if "fair_odd_away" not in result:
            result["fair_odd_away"] = round(100 / result["prob_away"], 2) if result["prob_away"] > 0 else 15.0

        # OU market çizgilerini sisteme işle
        if ou_market:
            enriched = []
            for l in ou_market:
                line      = l["line"]
                odd_over  = l.get("odd_over")
                odd_under = l.get("odd_under")
                # Sisteme en yakın OU çizgisini bul
                sys_line = _find_closest_ou(result.get("ou_lines",[]), line)
                entry = {
                    "line":      line,
                    "odd_over":  odd_over,
                    "odd_under": odd_under,
                    "p_over":    sys_line["p_over"]  if sys_line else 50.0,
                    "p_under":   sys_line["p_under"] if sys_line else 50.0,
                }
                # Implied ve value hesapla
                if odd_over:
                    impl_over  = round(100 / odd_over,  1)
                    entry["mkt_impl_over"]  = impl_over
                    entry["val_over"]       = round(entry["p_over"] - impl_over, 1)
                if odd_under:
                    impl_under = round(100 / odd_under, 1)
                    entry["mkt_impl_under"] = impl_under
                    entry["val_under"]      = round(entry["p_under"] - impl_under, 1)
                # Value bet flag
                vo = entry.get("val_over",  0) or 0
                vu = entry.get("val_under", 0) or 0
                if vo >= 3 and vo >= vu:
                    entry["value_bet"] = "over"
                elif vu >= 3:
                    entry["value_bet"] = "under"
                enriched.append(entry)
            result["ou_lines"] = enriched

        # IY OU market
        if iy_ou_market:
            enriched_iy = []
            for l in iy_ou_market:
                line      = l["line"]
                odd_over  = l.get("odd_over")
                odd_under = l.get("odd_under")
                sys_line  = _find_closest_ou(result.get("iy_ou_lines",[]), line)
                entry = {
                    "line":      line,
                    "odd_over":  odd_over,
                    "odd_under": odd_under,
                    "p_over":    sys_line["p_over"]  if sys_line else 50.0,
                    "p_under":   sys_line["p_under"] if sys_line else 50.0,
                }
                if odd_over:
                    impl_over  = round(100 / odd_over,  1)
                    entry["mkt_impl_over"]  = impl_over
                    entry["val_over"]       = round(entry["p_over"] - impl_over, 1)
                if odd_under:
                    impl_under = round(100 / odd_under, 1)
                    entry["mkt_impl_under"] = impl_under
                    entry["val_under"]      = round(entry["p_under"] - impl_under, 1)
                vo = entry.get("val_over",  0) or 0
                vu = entry.get("val_under", 0) or 0
                if vo >= 3 and vo >= vu:
                    entry["value_bet"] = "over"
                elif vu >= 3:
                    entry["value_bet"] = "under"
                enriched_iy.append(entry)
            result["iy_ou_lines"] = enriched_iy

        cache.set(cache_key, result)
        return jsonify({"success": True, "analysis": result})

    except Exception as e:
        import traceback
        print(f"[analyze] hata: {e}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "analysis": vh.fallback_result()})


def _find_closest_ou(ou_lines, target_line):
    """Sisteme ait OU çizgileri içinde hedef çizgiye en yakın olanı bul."""
    if not ou_lines:
        return None
    closest = min(ou_lines, key=lambda x: abs(x.get("line", 0) - target_line))
    return closest


def _inject_odds(result, match_id):
    """Fallback result'a odds ekle."""
    if not match_id:
        return
    try:
        odds = bapi.get_odds(match_id)
        if odds.get("odd_home"): result["market_odd_home"] = odds["odd_home"]
        if odds.get("odd_away"): result["market_odd_away"] = odds["odd_away"]
        if odds.get("odd_draw"): result["market_odd_draw"] = odds["odd_draw"]
        if odds.get("ou_lines_market"): result["ou_lines"] = odds["ou_lines_market"]
        if odds.get("iy_ou_lines_market"): result["iy_ou_lines"] = odds["iy_ou_lines_market"]
    except Exception:
        pass


@app.route("/api/debug/<int:team_id>/<int:league_id>")
def api_debug(team_id, league_id):
    bapi._league_cache.pop(league_id, None)
    matches = bapi.get_team_matches_from_league(team_id, league_id, 10)
    raw     = bapi._league_cache.get(league_id, [])
    team_raw= [ev for ev in raw if
               int(ev.get("home_team_key",0)) == team_id or
               int(ev.get("away_team_key",0)) == team_id]
    return jsonify({
        "total_league": len(raw),
        "team_matches": len(team_raw),
        "parsed":       len(matches),
        "last6":        matches[:6],
    })

@app.route("/api/clear-cache")
def api_clear_cache():
    cache.clear()
    bapi._league_cache.clear()
    bapi._odds_cache.clear()
    return jsonify({"success": True, "message": "Cache temizlendi"})

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Bulunamadi"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Sunucu hatasi"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
