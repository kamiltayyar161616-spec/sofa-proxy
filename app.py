"""
BasketOracle - Flask Backend
"""
import os
import datetime
from flask import Flask, render_template, jsonify, request
import basketball_api as bapi
import value_hunting_basket as vh

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "basketoracle-secret")

# Basit in-memory cache
_cache = {}

class SimpleCache:
    def get(self, key):
        import time
        item = _cache.get(key)
        if item and item["exp"] > time.time():
            return item["val"]
        return None
    def set(self, key, val, timeout=3600):
        import time
        _cache[key] = {"val": val, "exp": time.time() + timeout}
    def clear(self):
        _cache.clear()

cache = SimpleCache()


def today_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/fixtures")
def api_fixtures():
    date      = request.args.get("date", today_str())
    cache_key = f"fix_{date}"
    cached = cache.get(cache_key)
    if cached:
        return jsonify({"success": True, "matches": cached, "date": date})
    try:
        matches = bapi.get_fixtures_by_date(date)
        cache.set(cache_key, matches, timeout=300)
        return jsonify({"success": True, "matches": matches, "date": date})
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"[fixtures] HATA: {err}")
        return jsonify({"success": False, "matches": [], "error": str(e), "trace": err}), 200


@app.route("/api/analyze/<int:home_id>/<int:away_id>")
def api_analyze(home_id, away_id):
    league_key = request.args.get("lk", type=int)
    no_cache   = request.args.get("nc", "0")
    cache_key  = f"ana_{home_id}_{away_id}"

    if no_cache == "0":
        cached = cache.get(cache_key)
        if cached:
            return jsonify({"success": True, "analysis": cached})

    try:
        data = bapi.get_match_data(home_id, away_id, league_key)
        if not data["home_general"] and not data["away_general"]:
            return jsonify({"success": True, "analysis": vh.fallback_result()})

        result  = vh.run_value_hunting(
            data["home_general"], data["home_venue"],
            data["away_general"], data["away_venue"]
        )
        ratings = vh.compute_ratings(data)
        result.update(ratings)
        cache.set(cache_key, result)
        return jsonify({"success": True, "analysis": result})
    except Exception as e:
        print(f"[analyze] hata: {e}")
        return jsonify({"success": False, "error": str(e), "analysis": vh.fallback_result()})


@app.route("/api/debug/<int:team_id>/<int:league_id>")
def api_debug(team_id, league_id):
    bapi._league_cache.pop(league_id, None)
    matches = bapi.get_team_matches_from_league(team_id, league_id, 10)
    raw = bapi._league_cache.get(league_id, [])
    team_raw = [ev for ev in raw if
                int(ev.get("home_team_key",0)) == team_id or
                int(ev.get("away_team_key",0)) == team_id]
    return jsonify({
        "total_league": len(raw),
        "team_matches":  len(team_raw),
        "parsed":        len(matches),
        "last6":         matches[:6],
    })


@app.route("/api/clear-cache")
def api_clear_cache():
    cache.clear()
    bapi._league_cache.clear()
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
