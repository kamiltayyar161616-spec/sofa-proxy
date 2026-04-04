import os
import datetime
import traceback
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "basketoracle-secret")

_cache = {}
import time as _time
def _cget(k):
    v = _cache.get(k)
    return v["val"] if v and v["exp"] > _time.time() else None
def _cset(k, v, t=3600):
    _cache[k] = {"val": v, "exp": _time.time() + t}

def today_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/fixtures")
def api_fixtures():
    date = request.args.get("date", today_str())
    cached = _cget(f"fix_{date}")
    if cached:
        return jsonify({"success": True, "matches": cached, "date": date})
    try:
        import basketball_api as bapi
        matches = bapi.get_fixtures_by_date(date)
        _cset(f"fix_{date}", matches, 300)
        return jsonify({"success": True, "matches": matches, "date": date})
    except Exception as e:
        err = traceback.format_exc()
        print(f"[fixtures] HATA:\n{err}")
        return jsonify({"success": False, "matches": [], "error": str(e)})

@app.route("/api/analyze/<int:home_id>/<int:away_id>")
def api_analyze(home_id, away_id):
    league_key = request.args.get("lk", type=int)
    cache_key  = f"ana_{home_id}_{away_id}"
    cached = _cget(cache_key)
    if cached:
        return jsonify({"success": True, "analysis": cached})
    try:
        import basketball_api as bapi
        import value_hunting_basket as vh
        data = bapi.get_match_data(home_id, away_id, league_key)
        if not data["home_general"] and not data["away_general"]:
            return jsonify({"success": True, "analysis": vh.fallback_result()})
        result = vh.run_value_hunting(
            data["home_general"], data["home_venue"],
            data["away_general"], data["away_venue"]
        )
        ratings = vh.compute_ratings(data)
        result.update(ratings)
        _cset(cache_key, result)
        return jsonify({"success": True, "analysis": result})
    except Exception as e:
        err = traceback.format_exc()
        print(f"[analyze] HATA:\n{err}")
        try:
            import value_hunting_basket as vh
            return jsonify({"success": False, "error": str(e), "analysis": vh.fallback_result()})
        except:
            return jsonify({"success": False, "error": str(e)})

@app.route("/api/clear-cache")
def api_clear_cache():
    _cache.clear()
    try:
        import basketball_api as bapi
        bapi._league_cache.clear()
    except:
        pass
    return jsonify({"success": True, "message": "Cache temizlendi"})

@app.route("/api/debug/<int:team_id>/<int:league_id>")
def api_debug(team_id, league_id):
    try:
        import basketball_api as bapi
        bapi._league_cache.pop(league_id, None)
        matches = bapi.get_team_matches_from_league(team_id, league_id, 10)
        raw = bapi._league_cache.get(league_id, [])
        return jsonify({"total_league": len(raw), "parsed": len(matches), "last6": matches[:6]})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
