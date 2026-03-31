"""
BasketOracle - Flask Backend
"""

import os
import json
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
    league_key = request.args.get("lk", default=None, type=int)
    no_cache   = request.args.get("nc", "0")

    # Fikstürden gelen market oranları (frontend query param olarak gönderir)
    odd_home = request.args.get("oh",  type=float)
    odd_draw = request.args.get("od",  type=float)
    odd_away = request.args.get("oa",  type=float)

    ou_lines_market    = []
    iy_ou_lines_market = []
    try:
        ou_raw = request.args.get("ou", "")
        if ou_raw:
            ou_lines_market = json.loads(ou_raw)
    except Exception:
        pass
    try:
        iy_ou_raw = request.args.get("iyou", "")
        if iy_ou_raw:
            iy_ou_lines_market = json.loads(iy_ou_raw)
    except Exception:
        pass

    has_market = bool(odd_home or odd_away or ou_lines_market)
    # Her zaman market_odds oluştur - league_key olmasa da API oranlarıyla analiz yapılabilsin
    market_odds = {
        "odd_home":           odd_home,
        "odd_draw":           odd_draw,
        "odd_away":           odd_away,
        "ou_lines_market":    ou_lines_market,
        "iy_ou_lines_market": iy_ou_lines_market,
    }

    cache_key = f"ana_{home_id}_{away_id}_{league_key}"
    if no_cache == "0" and not has_market:
        cached = cache.get(cache_key)
        if cached:
            return jsonify({"success": True, "analysis": cached})

    try:
        data = bapi.get_match_data(home_id, away_id, league_key)

        # İstatistik yoksa bile API oranlarıyla analiz yap (fallback değil)
        if not data["home_general"] and not data["away_general"]:
            print(f"[analyze] İstatistik yok: home={home_id} away={away_id} lk={league_key}, sadece API oranları kullanılıyor")
            result = vh.run_value_hunting([], [], [], [], market_odds=market_odds)
            result["data_info"] = {
                "home_general_count": 0, "home_venue_count": 0,
                "away_general_count": 0, "away_venue_count": 0,
            }
            return jsonify({"success": True, "analysis": result})

        result = vh.run_value_hunting(
            data["home_general"],
            data["home_venue"],
            data["away_general"],
            data["away_venue"],
            market_odds=market_odds,
        )

        ratings = vh.compute_ratings(data)
        result.update(ratings)

        result["data_info"] = {
            "home_general_count": len(data["home_general"]),
            "home_venue_count":   len(data["home_venue"]),
            "away_general_count": len(data["away_general"]),
            "away_venue_count":   len(data["away_venue"]),
        }

        if not has_market:
            cache.set(cache_key, result)
        return jsonify({"success": True, "analysis": result})

    except Exception as e:
        print(f"[analyze] hata: {e}")
        return jsonify({"success": False, "error": str(e), "analysis": vh.fallback_result()})


@app.route("/api/debug/<int:team_id>/<int:league_id>")
def api_debug(team_id, league_id):
    bapi._league_cache.pop(league_id, None)
    matches      = bapi.get_team_matches_from_league(team_id, league_id, 20)
    raw          = bapi._league_cache.get(league_id, [])
    home_matches = [m for m in matches if     m["is_home"]]
    away_matches = [m for m in matches if not m["is_home"]]
    return jsonify({
        "total_league":  len(raw),
        "parsed":        len(matches),
        "general_last6": matches[:6],
        "home_last6":    home_matches[:6],
        "away_last6":    away_matches[:6],
    })


@app.route("/api/clear-cache")
def api_clear_cache():
    cache.clear()
    bapi._league_cache.clear()
    return jsonify({"success": True, "message": "Cache temizlendi"})


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Bulunamadı"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Sunucu hatası"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
