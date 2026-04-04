import os
import datetime
import traceback
import threading
import math
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "basketoracle-secret")

# ── Cache ──────────────────────────────────────────────────────
_cache = {}
import time as _time

def _cget(k):
    v = _cache.get(k)
    return v["val"] if v and v["exp"] > _time.time() else None

def _cset(k, v, t=3600):
    _cache[k] = {"val": v, "exp": _time.time() + t}

def today_str():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


# ── Keep-Alive (Render uyku modunu engelle) ────────────────────
def _keep_alive():
    import requests as req
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://sofa-proxy-poj5.onrender.com")
    while True:
        _time.sleep(600)  # 10 dakikada bir ping (Render 15 dk'da uyutur)
        try:
            req.get(f"{url}/api/ping", timeout=10)
            print("[KeepAlive] Ping OK")
        except Exception as e:
            print(f"[KeepAlive] Hata: {e}")

threading.Thread(target=_keep_alive, daemon=True).start()


# ── Analiz motoru (tek maç) ────────────────────────────────────
def _analyze_match(home_id, away_id, home_team, away_team, match_date, league_key):
    cache_key = f"ana_{home_id}_{away_id}_{match_date}"
    if _cget(cache_key):
        return  # zaten var

    try:
        import basketball_api as bapi
        import value_hunting_basket as vh

        data = bapi.get_match_data(home_id, away_id, league_key)
        if not data["home_general"] and not data["away_general"]:
            result = vh.fallback_result()
        else:
            result = vh.run_value_hunting(
                data["home_general"], data["home_venue"],
                data["away_general"], data["away_venue"]
            )
            result.update(vh.compute_ratings(data))

        # SofaScore odds
        sofa_key = f"sofa_{home_team}_{away_team}_{match_date}"
        sofa_data = _cget(sofa_key)
        if sofa_data is None and home_team and away_team:
            try:
                import sofa_odds as sofa
                sofa_data = sofa.get_odds(home_team, away_team, match_date)
                _cset(sofa_key, sofa_data, 21600)
            except Exception as se:
                print(f"[Sofa] {home_team} hata: {se}")
                sofa_data = {"found": False}

        if sofa_data and sofa_data.get("found"):
            result["sofa_found"]      = True
            result["mkt_ms_home"]     = sofa_data.get("ms_home")
            result["mkt_ms_away"]     = sofa_data.get("ms_away")
            result["mkt_ou_line"]     = sofa_data.get("ou_line")
            result["mkt_ou_over"]     = sofa_data.get("ou_over")
            result["mkt_ou_under"]    = sofa_data.get("ou_under")
            result["mkt_iy_line"]     = sofa_data.get("iy_line")
            result["mkt_iy_over"]     = sofa_data.get("iy_over")
            result["mkt_iy_under"]    = sofa_data.get("iy_under")
            result["mkt_spread_line"] = sofa_data.get("spread_line")
            result["mkt_spread_home"] = sofa_data.get("spread_home")
            result["mkt_spread_away"] = sofa_data.get("spread_away")

            value_bets = []
            # MS value
            if sofa_data.get("ms_home") and result.get("prob_home"):
                val_h = round((result["prob_home"] / 100 * sofa_data["ms_home"] - 1) * 100, 1)
                result["fair_ms_home"] = round(100 / result["prob_home"], 2)
                result["val_ms_home"]  = val_h
                if val_h > 5:
                    value_bets.append(f"Maç Sonucu 1 @ {sofa_data['ms_home']} → +{val_h}% VALUE")
            if sofa_data.get("ms_away") and result.get("prob_away"):
                val_a = round((result["prob_away"] / 100 * sofa_data["ms_away"] - 1) * 100, 1)
                result["fair_ms_away"] = round(100 / result["prob_away"], 2)
                result["val_ms_away"]  = val_a
                if val_a > 5:
                    value_bets.append(f"Maç Sonucu 2 @ {sofa_data['ms_away']} → +{val_a}% VALUE")
            # OU value
            if sofa_data.get("ou_line") and sofa_data.get("ou_over") and result.get("exp_total"):
                std = 12.0 * math.sqrt(2)
                z   = (sofa_data["ou_line"] - result["exp_total"]) / std
                p_over = 1 - (0.5 * (1 + math.erf(z / math.sqrt(2))))
                val_ov = round((p_over * sofa_data["ou_over"] - 1) * 100, 1)
                val_un = round(((1-p_over) * sofa_data.get("ou_under", sofa_data["ou_over"]) - 1) * 100, 1)
                result["val_ou_over"]  = val_ov
                result["val_ou_under"] = val_un
                if val_ov > 5:
                    value_bets.append(f"Alt/Üst {sofa_data['ou_line']} ÜST @ {sofa_data['ou_over']} → +{val_ov}% VALUE")
                elif val_un > 5:
                    value_bets.append(f"Alt/Üst {sofa_data['ou_line']} ALT @ {sofa_data.get('ou_under')} → +{val_un}% VALUE")
            # IY OU value
            if sofa_data.get("iy_line") and sofa_data.get("iy_over") and result.get("exp_iy_total"):
                std_iy = 12.0 * 0.48 * math.sqrt(2)
                z_iy   = (sofa_data["iy_line"] - result["exp_iy_total"]) / std_iy
                p_iy   = 1 - (0.5 * (1 + math.erf(z_iy / math.sqrt(2))))
                val_iy_ov = round((p_iy * sofa_data["iy_over"] - 1) * 100, 1)
                val_iy_un = round(((1-p_iy) * sofa_data.get("iy_under", sofa_data["iy_over"]) - 1) * 100, 1)
                result["val_iy_over"]  = val_iy_ov
                result["val_iy_under"] = val_iy_un
                if val_iy_ov > 5:
                    value_bets.append(f"İY {sofa_data['iy_line']} ÜST @ {sofa_data['iy_over']} → +{val_iy_ov}% VALUE")
                elif val_iy_un > 5:
                    value_bets.append(f"İY {sofa_data['iy_line']} ALT @ {sofa_data.get('iy_under')} → +{val_iy_un}% VALUE")

            result["value_bets"] = value_bets

        _cset(cache_key, result, 21600)  # 6 saat cache
        print(f"[BG] Analiz tamam: {home_team} vs {away_team}")

    except Exception as e:
        print(f"[BG] Analiz hata {home_team} vs {away_team}: {traceback.format_exc()}")


def _analyze_all_bg(matches, match_date):
    """Tüm maçları arka planda analiz et — maçlar arasında 1sn bekle."""
    print(f"[BG] {len(matches)} maç analiz başlıyor...")
    for m in matches:
        try:
            cache_key = f"ana_{m['home_id']}_{m['away_id']}_{match_date}"
            if _cget(cache_key):
                continue  # cache'de var, atla
            _analyze_match(
                m["home_id"], m["away_id"],
                m["home_team"], m["away_team"],
                match_date, m.get("league_key")
            )
            _time.sleep(1)  # API rate limit için
        except Exception as e:
            print(f"[BG] Maç atlandı: {e}")
    print(f"[BG] Tüm analizler tamamlandı.")


# ── Routes ─────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ping")
def api_ping():
    return jsonify({"status": "ok", "time": today_str()})


@app.route("/api/sofa/events")
def api_sofa_events():
    """SofaScore maç listesi proxy — CORS engeli aşmak için."""
    date = request.args.get("date", today_str())
    cache_key = f"sofa_events_{date}"
    cached = _cget(cache_key)
    if cached:
        return jsonify(cached)
    try:
        import requests as req
        url = f"https://api.sofascore.com/api/v1/sport/basketball/scheduled-events/{date}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.sofascore.com/basketball",
        }
        r = req.get(url, headers=headers, timeout=10)
        data = r.json()
        _cset(cache_key, data, 21600)  # 6 saat cache
        return jsonify(data)
    except Exception as e:
        print(f"[SofaProxy/events] Hata: {e}")
        return jsonify({"events": []})


@app.route("/api/sofa/odds")
def api_sofa_odds():
    """SofaScore odds proxy — CORS engeli aşmak için."""
    event_id = request.args.get("event_id", "")
    if not event_id:
        return jsonify({})
    cache_key = f"sofa_odds_{event_id}"
    cached = _cget(cache_key)
    if cached:
        return jsonify(cached)
    try:
        import requests as req
        url = f"https://api.sofascore.com/api/v1/event/{event_id}/odds/1/all"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.sofascore.com/basketball",
        }
        r = req.get(url, headers=headers, timeout=10)
        data = r.json()
        _cset(cache_key, data, 21600)  # 6 saat cache
        return jsonify(data)
    except Exception as e:
        print(f"[SofaProxy/odds] Hata: {e}")
        return jsonify({"markets": []})


@app.route("/api/fixtures")
def api_fixtures():
    date   = request.args.get("date", today_str())
    cached = _cget(f"fix_{date}")
    if cached:
        return jsonify({"success": True, "matches": cached, "date": date})
    try:
        import basketball_api as bapi
        matches = bapi.get_fixtures_by_date(date)
        _cset(f"fix_{date}", matches, 300)

        # Arka planda tüm analizleri başlat
        t = threading.Thread(target=_analyze_all_bg, args=(matches, date), daemon=True)
        t.start()

        return jsonify({"success": True, "matches": matches, "date": date})
    except Exception as e:
        print(f"[fixtures] HATA:\n{traceback.format_exc()}")
        return jsonify({"success": False, "matches": [], "error": str(e)})


@app.route("/api/analyze/<int:home_id>/<int:away_id>")
def api_analyze(home_id, away_id):
    league_key = request.args.get("lk", type=int)
    home_team  = request.args.get("ht", "")
    away_team  = request.args.get("at", "")
    match_date = request.args.get("dt", today_str())
    cache_key  = f"ana_{home_id}_{away_id}_{match_date}"

    # Cache'de varsa hemen dön
    cached = _cget(cache_key)
    if cached:
        return jsonify({"success": True, "analysis": cached})

    # Yoksa hemen hesapla (arka plan henüz bitmemiş)
    try:
        import value_hunting_basket as vh
        _analyze_match(home_id, away_id, home_team, away_team, match_date, league_key)
        result = _cget(cache_key)
        if result:
            return jsonify({"success": True, "analysis": result})
        return jsonify({"success": True, "analysis": vh.fallback_result()})
    except Exception as e:
        print(f"[analyze] HATA:\n{traceback.format_exc()}")
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
    try:
        import sofa_odds as sofa
        sofa.clear_cache()
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
        return jsonify({"error": str(e), "trace": traceback.format_exc()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
