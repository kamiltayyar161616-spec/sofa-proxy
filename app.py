"""
BasketOracle - Flask Backend
"""

import os
import datetime
import json
import traceback

from flask import Flask, render_template, jsonify, request
from flask_caching import Cache

import basketball_api as bapi
import value_hunting_basket as vh

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "basketoracle-secret")
cache = Cache(app, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 3600})


def today_str():
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%Y-%m-%d")


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
        traceback.print_exc()
        return jsonify({"success": False, "matches": [], "error": str(e)})


@app.route("/api/analyze/<int:home_id>/<int:away_id>")
def api_analyze(home_id, away_id):
    league_key  = request.args.get("lk",  type=int)
    league_name = request.args.get("ln",  default="", type=str)
    match_id    = request.args.get("mid", "")
    no_cache   = request.args.get("nc", "0")

    cache_key = f"ana_{home_id}_{away_id}_{league_key}"
    if no_cache == "0":
        cached = cache.get(cache_key)
        if cached:
            return jsonify({"success": True, "analysis": cached})

    try:
        oh = request.args.get("oh", type=float)
        od = request.args.get("od", type=float)
        oa = request.args.get("oa", type=float)

        ou_market    = []
        iy_ou_market = []
        try:
            ou_raw = request.args.get("ou", "")
            if ou_raw:
                ou_market = json.loads(ou_raw)
        except Exception:
            pass
        try:
            iy_raw = request.args.get("iyou", "")
            if iy_raw:
                iy_ou_market = json.loads(iy_raw)
        except Exception:
            pass

        # API'den odds tamamla
        if match_id:
            try:
                live_odds = bapi.get_odds(match_id)
                if live_odds:
                    if not oh  and live_odds.get("odd_home"):           oh = live_odds["odd_home"]
                    if not od  and live_odds.get("odd_draw"):           od = live_odds["odd_draw"]
                    if not oa  and live_odds.get("odd_away"):           oa = live_odds["odd_away"]
                    if not ou_market    and live_odds.get("ou_lines_market"):
                        ou_market = live_odds["ou_lines_market"]
                    if not iy_ou_market and live_odds.get("iy_ou_lines_market"):
                        iy_ou_market = live_odds["iy_ou_lines_market"]
            except Exception as e:
                print(f"[analyze] odds fetch hatasi: {e}")

        market_odds = {
            "odd_home":           oh,
            "odd_draw":           od,
            "odd_away":           oa,
            "ou_lines_market":    ou_market,
            "iy_ou_lines_market": iy_ou_market,
        }

        data = bapi.get_match_data(home_id, away_id, league_key)

        if not data["home_general"] and not data["away_general"]:
            result = vh.fallback_result()
        else:
            result = vh.run_value_hunting(
                data["home_general"], data["home_venue"],
                data["away_general"], data["away_venue"],
                market_odds=market_odds,
                league_name=league_name,
            )
            ratings = vh.compute_ratings(data)
            result.update(ratings)

        result["data_info"] = {
            "home_general_count": len(data["home_general"]),
            "home_venue_count":   len(data["home_venue"]),
            "away_general_count": len(data["away_general"]),
            "away_venue_count":   len(data["away_venue"]),
        }

        # OU market çizgilerini modele işle
        if ou_market:
            enriched = []
            for l in ou_market:
                line      = l["line"]
                odd_over  = l.get("odd_over")
                odd_under = l.get("odd_under")
                sys_line  = _find_closest_ou(result.get("ou_lines", []), line)
                entry = {
                    "line":      line,
                    "odd_over":  odd_over,
                    "odd_under": odd_under,
                    "p_over":    sys_line["p_over"]  if sys_line else 50.0,
                    "p_under":   sys_line["p_under"] if sys_line else 50.0,
                }
                if odd_over:
                    impl = round(100 / odd_over, 1)
                    entry["mkt_impl_over"] = impl
                    entry["val_over"]      = round(entry["p_over"] - impl, 1)
                if odd_under:
                    impl = round(100 / odd_under, 1)
                    entry["mkt_impl_under"] = impl
                    entry["val_under"]      = round(entry["p_under"] - impl, 1)
                vo = entry.get("val_over",  0) or 0
                vu = entry.get("val_under", 0) or 0
                entry["value_bet"] = "over" if (vo >= 3 and vo >= vu) else ("under" if vu >= 3 else None)
                enriched.append(entry)
            result["ou_lines"] = enriched

        if iy_ou_market:
            enriched_iy = []
            for l in iy_ou_market:
                line      = l["line"]
                odd_over  = l.get("odd_over")
                odd_under = l.get("odd_under")
                sys_line  = _find_closest_ou(result.get("iy_ou_lines", []), line)
                entry = {
                    "line":      line,
                    "odd_over":  odd_over,
                    "odd_under": odd_under,
                    "p_over":    sys_line["p_over"]  if sys_line else 50.0,
                    "p_under":   sys_line["p_under"] if sys_line else 50.0,
                }
                if odd_over:
                    impl = round(100 / odd_over, 1)
                    entry["mkt_impl_over"] = impl
                    entry["val_over"]      = round(entry["p_over"] - impl, 1)
                if odd_under:
                    impl = round(100 / odd_under, 1)
                    entry["mkt_impl_under"] = impl
                    entry["val_under"]      = round(entry["p_under"] - impl, 1)
                vo = entry.get("val_over",  0) or 0
                vu = entry.get("val_under", 0) or 0
                entry["value_bet"] = "over" if (vo >= 3 and vo >= vu) else ("under" if vu >= 3 else None)
                enriched_iy.append(entry)
            result["iy_ou_lines"] = enriched_iy

        cache.set(cache_key, result)
        return jsonify({"success": True, "analysis": result})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "analysis": vh.fallback_result()})


def _find_closest_ou(ou_lines, target):
    if not ou_lines:
        return None
    return min(ou_lines, key=lambda x: abs(x.get("line", 0) - target))


@app.route("/api/debug/<int:team_id>/<int:league_id>")
def api_debug(team_id, league_id):
    bapi._league_cache.pop(league_id, None)
    matches = bapi.get_team_matches_from_league(team_id, league_id, 10)
    raw     = bapi._league_cache.get(league_id, [])
    return jsonify({
        "total_league": len(raw),
        "parsed":       len(matches),
        "last6":        matches[:6],
    })


@app.route("/api/debug-odds/<match_id>")
def api_debug_odds(match_id):
    bapi._odds_cache.pop(str(match_id), None)
    odds = bapi.get_odds(match_id)
    return jsonify({"match_id": match_id, "odds": odds})


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
