import os
import datetime
import traceback
import threading
import math
import json
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
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%Y-%m-%d")


# ── Keep-Alive ────────────────────────────────────────────────
def _keep_alive():
    import requests as req
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://sofa-proxy-poj5.onrender.com")
    while True:
        _time.sleep(600)
        try:
            req.get(f"{url}/api/ping", timeout=10)
            print("[KeepAlive] Ping OK")
        except Exception as e:
            print(f"[KeepAlive] Hata: {e}")

threading.Thread(target=_keep_alive, daemon=True).start()


def _find_closest_ou(ou_lines, target_line):
    if not ou_lines:
        return None
    return min(ou_lines, key=lambda x: abs(x.get("line", 0) - target_line))


def _enrich_ou_lines(ou_market, sys_ou_lines):
    """Market OU çizgilerini sistem olasılıklarıyla zenginleştir."""
    enriched = []
    for l in ou_market:
        line      = l["line"]
        odd_over  = l.get("odd_over")
        odd_under = l.get("odd_under")
        sys_line  = _find_closest_ou(sys_ou_lines, line)
        entry = {
            "line":      line,
            "odd_over":  odd_over,
            "odd_under": odd_under,
            "p_over":    sys_line["p_over"]  if sys_line else 50.0,
            "p_under":   sys_line["p_under"] if sys_line else 50.0,
        }
        if odd_over and odd_over > 1:
            impl = round(100 / odd_over, 1)
            entry["mkt_impl_over"] = impl
            entry["val_over"]      = round(entry["p_over"] - impl, 1)
        if odd_under and odd_under > 1:
            impl = round(100 / odd_under, 1)
            entry["mkt_impl_under"] = impl
            entry["val_under"]      = round(entry["p_under"] - impl, 1)
        vo = entry.get("val_over",  0) or 0
        vu = entry.get("val_under", 0) or 0
        if vo >= 3 and vo >= vu:
            entry["value_bet"] = "over"
        elif vu >= 3:
            entry["value_bet"] = "under"
        enriched.append(entry)
    return enriched


# ── Analiz motoru ─────────────────────────────────────────────
def _analyze_match(home_id, away_id, match_date, league_key, match_id="", **kwargs):
    cache_key = f"ana_{home_id}_{away_id}_{match_date}"
    if _cget(cache_key):
        return

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

        result["data_info"] = {
            "home_general_count": len(data["home_general"]),
            "home_venue_count":   len(data["home_venue"]),
            "away_general_count": len(data["away_general"]),
            "away_venue_count":   len(data["away_venue"]),
        }

        # Fair (model) oranları
        result["fair_odd_home"] = round(100 / result["prob_home"], 2) if result.get("prob_home", 0) > 0 else 15.0
        result["fair_odd_away"] = round(100 / result["prob_away"], 2) if result.get("prob_away", 0) > 0 else 15.0
        result["fair_ms_home"]  = result["fair_odd_home"]
        result["fair_ms_away"]  = result["fair_odd_away"]

        # ── AllSports API odds — tek çağrı, cache'e al ────────────
        if match_id:
            try:
                odds = bapi.get_odds(match_id)   # zaten kendi cache'i var
                if odds:
                    oh = odds.get("odd_home")
                    oa = odds.get("odd_away")
                    od = odds.get("odd_draw")

                    # MS oranları
                    if oh:
                        result["market_odd_home"] = oh
                        result["mkt_ms_home"]     = oh
                        result["mkt_impl_home"]   = round(100 / oh, 1)
                        val_h = round(result["prob_home"] - (100 / oh), 1)
                        result["value_home"]  = val_h
                        result["val_ms_home"] = val_h
                    if oa:
                        result["market_odd_away"] = oa
                        result["mkt_ms_away"]     = oa
                        result["mkt_impl_away"]   = round(100 / oa, 1)
                        val_a = round(result["prob_away"] - (100 / oa), 1)
                        result["value_away"]  = val_a
                        result["val_ms_away"] = val_a
                    if od:
                        result["market_odd_draw"] = od

                    # Handikap — tek dengeli barem
                    if odds.get("ah_line") is not None:
                        result["api_ah_line"]  = odds["ah_line"]
                        result["api_ah_home"]  = odds.get("ah_home")
                        result["api_ah_away"]  = odds.get("ah_away")
                        result["ah_main_line"] = odds["ah_line"]
                        result["ah_main_home"] = odds.get("ah_home")
                        result["ah_main_away"] = odds.get("ah_away")
                        result["mkt_spread_line"] = odds["ah_line"]
                        result["mkt_spread_home"] = odds.get("ah_home")
                        result["mkt_spread_away"] = odds.get("ah_away")

                    # Alt/Üst — tek dengeli barem
                    ou_market = odds.get("ou_lines_market", [])
                    if ou_market:
                        result["mkt_ou_line"]  = ou_market[0]["line"]
                        result["mkt_ou_over"]  = ou_market[0]["odd_over"]
                        result["mkt_ou_under"] = ou_market[0]["odd_under"]
                        result["ou_lines"] = _enrich_ou_lines(
                            ou_market, result.get("ou_lines", []))

                    # İY Alt/Üst — tek dengeli barem
                    iy_market = odds.get("iy_ou_lines_market", [])
                    if iy_market:
                        result["mkt_iy_line"]  = iy_market[0]["line"]
                        result["mkt_iy_over"]  = iy_market[0]["odd_over"]
                        result["mkt_iy_under"] = iy_market[0]["odd_under"]
                        result["iy_ou_lines"] = _enrich_ou_lines(
                            iy_market, result.get("iy_ou_lines", []))

                    # Value bet listesi
                    value_bets = []
                    vh_val = result.get("val_ms_home", 0) or 0
                    va_val = result.get("val_ms_away", 0) or 0
                    if vh_val > 5:
                        value_bets.append(f"Maç Sonucu 1 @ {oh} → +{vh_val}% VALUE")
                    if va_val > 5:
                        value_bets.append(f"Maç Sonucu 2 @ {oa} → +{va_val}% VALUE")
                    for l in result.get("ou_lines", []):
                        if l.get("value_bet") == "over" and (l.get("val_over") or 0) > 5:
                            value_bets.append(f"Alt/Üst {l['line']} ÜST @ {l.get('odd_over')} → +{l['val_over']}% VALUE")
                        elif l.get("value_bet") == "under" and (l.get("val_under") or 0) > 5:
                            value_bets.append(f"Alt/Üst {l['line']} ALT @ {l.get('odd_under')} → +{l['val_under']}% VALUE")
                    for l in result.get("iy_ou_lines", []):
                        if l.get("value_bet") == "over" and (l.get("val_over") or 0) > 5:
                            value_bets.append(f"İY {l['line']} ÜST @ {l.get('odd_over')} → +{l['val_over']}% VALUE")
                        elif l.get("value_bet") == "under" and (l.get("val_under") or 0) > 5:
                            value_bets.append(f"İY {l['line']} ALT @ {l.get('odd_under')} → +{l['val_under']}% VALUE")
                    result["value_bets"] = value_bets

            except Exception as oe:
                print(f"[BG] Odds hatası match_id={match_id}: {oe}")

        # ── SofaScore oranları (MS + OU) ──────────────────────────
        try:
            import sofa_odds as sofa
            hn = kwargs.get("home_team", "")
            an = kwargs.get("away_team", "")
            dt = match_date
            if hn and an:
                sofa_data = sofa.get_odds(hn, an, dt)
                if sofa_data.get("found"):
                    oh = sofa_data.get("ms_home")
                    oa = sofa_data.get("ms_away")
                    if oh and not result.get("mkt_impl_home"):
                        result["market_odd_home"] = oh
                        result["mkt_impl_home"]   = round(100 / oh, 1)
                        result["value_home"]      = round(result.get("prob_home",50) - (100/oh), 1)
                    if oa and not result.get("mkt_impl_away"):
                        result["market_odd_away"] = oa
                        result["mkt_impl_away"]   = round(100 / oa, 1)
                        result["value_away"]      = round(result.get("prob_away",50) - (100/oa), 1)
                    result["bet365"] = sofa_data
        except Exception as se:
            print(f"[BG] SofaScore hatası: {se}")

        _cset(cache_key, result, 21600)
        print(f"[BG] Analiz tamam: home={home_id} away={away_id}")

    except Exception as e:
        print(f"[BG] Analiz hata: {traceback.format_exc()}")


def _analyze_all_bg(matches, match_date):
    print(f"[BG] {len(matches)} maç analiz başlıyor...")
    for m in matches:
        try:
            cache_key = f"ana_{m['home_id']}_{m['away_id']}_{match_date}"
            if _cget(cache_key):
                continue
            _analyze_match(
                m["home_id"], m["away_id"],
                match_date,
                m.get("league_key"),
                match_id=m.get("match_id", ""),
                home_team=m.get("home_team", ""),
                away_team=m.get("away_team", "")
            )
            _time.sleep(1)
        except Exception as e:
            print(f"[BG] Maç atlandı: {e}")
    print("[BG] Tüm analizler tamamlandı.")


# ── Routes ─────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ping")
def api_ping():
    return jsonify({"status": "ok", "time": today_str()})


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
        t = threading.Thread(target=_analyze_all_bg, args=(matches, date), daemon=True)
        t.start()
        return jsonify({"success": True, "matches": matches, "date": date})
    except Exception as e:
        print(f"[fixtures] HATA:\n{traceback.format_exc()}")
        return jsonify({"success": False, "matches": [], "error": str(e)})


@app.route("/api/analyze/<int:home_id>/<int:away_id>")
def api_analyze(home_id, away_id):
    league_key = request.args.get("lk",   type=int)
    match_id   = request.args.get("mid",  "")
    match_date = request.args.get("dt",   today_str())
    hn         = request.args.get("hn",   "")
    an         = request.args.get("an",   "")
    cache_key  = f"ana_{home_id}_{away_id}_{match_date}"

    cached = _cget(cache_key)
    if cached:
        return jsonify({"success": True, "analysis": cached})

    try:
        import value_hunting_basket as vh
        _analyze_match(home_id, away_id, match_date, league_key,
                       match_id=match_id, home_team=hn, away_team=an)
        result = _cget(cache_key) or vh.fallback_result()
        _cset(cache_key, result, 21600)
        return jsonify({"success": True, "analysis": result})
    except Exception as e:
        print(f"[analyze] HATA:\n{traceback.format_exc()}")
        try:
            import value_hunting_basket as vh
            return jsonify({"success": False, "error": str(e), "analysis": vh.fallback_result()})
        except:
            return jsonify({"success": False, "error": str(e)})


@app.route("/api/debug-sofa")
def api_debug_sofa():
    try:
        import sofa_odds as sofa
        dt = request.args.get("dt", today_str())
        hn = request.args.get("hn", "")
        an = request.args.get("an", "")
        events = sofa.load_events(dt)
        sample = [{"id": e.get("id"), "home": e.get("homeTeam",{}).get("name",""), "away": e.get("awayTeam",{}).get("name","")} for e in events[:20]]
        result = {"count": len(events), "sample": sample}
        if hn and an:
            result["odds"] = sofa.get_odds(hn, an, dt)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/debug-odds/<match_id>")
def api_debug_odds(match_id):
    try:
        import basketball_api as bapi
        bapi._odds_cache.pop(str(match_id), None)
        odds = bapi.get_odds(match_id)
        return jsonify({"match_id": match_id, "odds": odds, "found": bool(odds)})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()})


@app.route("/api/clear-cache")
def api_clear_cache():
    _cache.clear()
    try:
        import basketball_api as bapi
        bapi._league_cache.clear()
        bapi._odds_cache.clear()
    except: pass
    return jsonify({"success": True, "message": "Cache temizlendi"})


@app.route("/api/debug/<int:team_id>/<int:league_id>")
def api_debug(team_id, league_id):
    try:
        import basketball_api as bapi
        bapi._league_cache.pop(league_id, None)
        matches = bapi.get_team_matches_from_league(team_id, league_id, 10)
        raw     = bapi._league_cache.get(league_id, [])
        return jsonify({"total_league": len(raw), "parsed": len(matches), "last6": matches[:6]})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
