"""
BasketOracle - Value Hunting Motoru

- Maç sonucu (1/2)
- Handikap — maçın gerçek spread'ine göre dinamik 4 çizgi
- Alt/Üst — yeşil=ÜST, kırmızı=ALT
- İY sonucu ve Alt/Üst
- Value hunting: bahis sitesi oranı vs model olasılığı
- Hesaplamalar: iç saha, dış saha ve genel SON 6 MAÇ istatistiklerine göre
"""

import math

# ════════════════════════════════════════════════
# SABİTLER
# ════════════════════════════════════════════════
HOME_ADV   = 3.5
PAYOUT     = 0.92
LEAGUE_AVG = 105.0
IY_RATIO   = 0.48
STD_DEV    = 12.0
LAST_N     = 6      # Son kaç maç kullanılacak


def normalize_stats(matches, is_home_team):
    """
    Son LAST_N (6) maçın istatistiklerini hesapla.
    Genel, iç saha veya dış saha maçları için ayrı ayrı çağrılır.
    """
    matches = matches[:LAST_N]  # Sadece son 6 maç

    if not matches:
        return {
            "avg_scored":      LEAGUE_AVG / 2,
            "avg_conceded":    LEAGUE_AVG / 2,
            "avg_total":       LEAGUE_AVG,
            "avg_iy_scored":   LEAGUE_AVG * IY_RATIO / 2,
            "avg_iy_conceded": LEAGUE_AVG * IY_RATIO / 2,
            "win_rate":        0.5,
            "variance":        STD_DEV ** 2,
            "n":               0,
        }

    scored   = [m["scored"]   for m in matches]
    conceded = [m["conceded"] for m in matches]
    totals   = [m["total"]    for m in matches]
    iy_s     = [m["iy_scored"]   for m in matches if m.get("iy_scored")]
    iy_c     = [m["iy_conceded"] for m in matches if m.get("iy_conceded")]
    wins     = sum(1 for m in matches if m["win"])

    avg_sc   = sum(scored)   / len(scored)
    avg_co   = sum(conceded) / len(conceded)
    avg_tot  = sum(totals)   / len(totals)
    avg_iy_s = sum(iy_s) / len(iy_s) if iy_s else avg_sc * IY_RATIO
    avg_iy_c = sum(iy_c) / len(iy_c) if iy_c else avg_co * IY_RATIO

    variance = (
        sum((x - avg_sc) ** 2 for x in scored) / len(scored)
        if len(scored) > 1 else STD_DEV ** 2
    )

    return {
        "avg_scored":      avg_sc,
        "avg_conceded":    avg_co,
        "avg_total":       avg_tot,
        "avg_iy_scored":   avg_iy_s,
        "avg_iy_conceded": avg_iy_c,
        "win_rate":        wins / len(matches),
        "variance":        variance,
        "n":               len(matches),
    }


def compute_expected_scores(home_stats, away_stats,
                            home_venue_stats, away_venue_stats):
    """
    Beklenen skorları hesapla.
    Venue verisi 3+ maç varsa %60 venue + %40 genel,
    1-2 maç varsa %40 venue + %60 genel,
    yoksa %100 genel.
    """
    def weighted(general, venue, key):
        g = general[key]
        v = venue[key]
        if venue["n"] >= 3:
            return g * 0.4 + v * 0.6
        elif venue["n"] >= 1:
            return g * 0.6 + v * 0.4
        return g

    home_attack  = weighted(home_stats, home_venue_stats, "avg_scored")
    home_defense = weighted(home_stats, home_venue_stats, "avg_conceded")
    away_attack  = weighted(away_stats, away_venue_stats, "avg_scored")
    away_defense = weighted(away_stats, away_venue_stats, "avg_conceded")

    exp_home = (home_attack  + (LEAGUE_AVG / 2 - away_defense)) / 2 + HOME_ADV / 2
    exp_away = (away_attack  + (LEAGUE_AVG / 2 - home_defense)) / 2 - HOME_ADV / 2

    exp_home  = max(70, min(140, exp_home))
    exp_away  = max(70, min(140, exp_away))
    exp_total = exp_home + exp_away

    return {
        "exp_home":     round(exp_home,  1),
        "exp_away":     round(exp_away,  1),
        "exp_total":    round(exp_total, 1),
        "exp_iy_home":  round(exp_home  * IY_RATIO, 1),
        "exp_iy_away":  round(exp_away  * IY_RATIO, 1),
        "exp_iy_total": round(exp_total * IY_RATIO, 1),
        "spread":       round(exp_home  - exp_away, 1),
    }


def normal_cdf(x, mean, std):
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / std
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def compute_probabilities(exp, home_var, away_var):
    spread    = exp["spread"]
    exp_total = exp["exp_total"]

    std_home  = max(8, math.sqrt(home_var))
    std_away  = max(8, math.sqrt(away_var))
    std_diff  = math.sqrt(std_home ** 2 + std_away ** 2)
    std_total = std_diff

    # ── Maç sonucu ──────────────────────────────────────────────────
    p_home_win = 1 - normal_cdf(0,    spread, std_diff)
    p_away_win =     normal_cdf(-0.5, spread, std_diff)
    p_draw     = max(0.02, 1 - p_home_win - p_away_win)
    total      = p_home_win + p_draw + p_away_win
    p_home_win /= total
    p_draw     /= total
    p_away_win /= total

    # ── HANDİKAP — spread'e göre dinamik 4 çizgi ───────────────────
    # Spread etrafında anlamlı 4 çizgi üret
    # Örnek spread=+5: çizgiler -0.5, +2.5, +5.5, +8.5
    s_base = round(spread * 2) / 2  # 0.5'in katına yuvarla
    handicap_lines = []
    for offset in [-6.0, -3.0, 0.0, +3.0]:
        line  = round(s_base + offset - 0.5, 1)
        p_hc  = 1 - normal_cdf(line, spread, std_diff)
        handicap_lines.append({
            "line":         line,
            "p_home_cover": round(p_hc       * 100, 1),
            "p_away_cover": round((1 - p_hc) * 100, 1),
        })

    # ── ALT / ÜST — exp_total etrafında 6 çizgi ────────────────────
    # p_over  = ÜST = yeşil bar (sol)
    # p_under = ALT = kırmızı bar (sağ)
    base_ou  = round(exp_total / 5) * 5
    ou_lines = []
    for offset in [-12.5, -7.5, -2.5, 2.5, 7.5, 12.5]:
        line    = base_ou + offset
        p_over  = 1 - normal_cdf(line, exp_total, std_total)
        p_under = 1 - p_over
        ou_lines.append({
            "line":    round(line, 1),
            "p_over":  round(p_over  * 100, 1),   # ÜST — yeşil
            "p_under": round(p_under * 100, 1),   # ALT — kırmızı
        })

    # ── İY ALT / ÜST ────────────────────────────────────────────────
    exp_iy_total = exp["exp_iy_total"]
    base_iy      = round(exp_iy_total / 2.5) * 2.5
    iy_ou_lines  = []
    for offset in [-7.5, -5.0, -2.5, 2.5, 5.0, 7.5]:
        line    = base_iy + offset
        p_over  = 1 - normal_cdf(line, exp_iy_total, std_total * IY_RATIO)
        p_under = 1 - p_over
        iy_ou_lines.append({
            "line":    round(line, 1),
            "p_over":  round(p_over  * 100, 1),   # ÜST — yeşil
            "p_under": round(p_under * 100, 1),   # ALT — kırmızı
        })

    # ── İY sonucu ───────────────────────────────────────────────────
    iy_spread   = exp["exp_iy_home"] - exp["exp_iy_away"]
    std_iy_diff = std_diff * IY_RATIO
    p_iy_home   = 1 - normal_cdf(0,    iy_spread, std_iy_diff)
    p_iy_away   =     normal_cdf(-0.5, iy_spread, std_iy_diff)
    p_iy_draw   = max(0.05, 1 - p_iy_home - p_iy_away)
    iy_total_p  = p_iy_home + p_iy_draw + p_iy_away
    p_iy_home  /= iy_total_p
    p_iy_draw  /= iy_total_p
    p_iy_away  /= iy_total_p

    return {
        "p_home_win":     round(p_home_win * 100, 1),
        "p_draw":         round(p_draw     * 100, 1),
        "p_away_win":     round(p_away_win * 100, 1),
        "p_iy_home":      round(p_iy_home  * 100, 1),
        "p_iy_draw":      round(p_iy_draw  * 100, 1),
        "p_iy_away":      round(p_iy_away  * 100, 1),
        "handicap_lines": handicap_lines,
        "ou_lines":       ou_lines,
        "iy_ou_lines":    iy_ou_lines,
    }


def prob_to_odd(prob_pct, payout=PAYOUT):
    if prob_pct <= 0:
        return 15.0
    return min(round((100 / prob_pct) / payout, 2), 15.0)


# ════════════════════════════════════════════════
# VALUE HUNTING
# ════════════════════════════════════════════════

def compute_value(model_prob_pct, bookmaker_odd):
    """
    Value = (model_prob × oran) - 1
    Pozitifse value bet.
    """
    if not bookmaker_odd or bookmaker_odd <= 1.0:
        return None
    implied_prob = (1.0 / bookmaker_odd) * 100
    value        = (model_prob_pct / 100) * bookmaker_odd - 1
    return {
        "bookmaker_odd": round(bookmaker_odd, 2),
        "implied_prob":  round(implied_prob,  1),
        "model_prob":    round(model_prob_pct, 1),
        "value_pct":     round(value * 100, 2),
        "is_value":      value > 0,
    }


def compute_ratings(data):
    def rating(matches, limit=6):
        return sum(m["scored"] - m["conceded"] for m in matches[:limit])

    hg = rating(data.get("home_general", []))
    ag = rating(data.get("away_general", []))
    hi = rating(data.get("home_venue",   []))
    ai = rating(data.get("away_venue",   []))

    return {
        "g_rating":       hg - ag,
        "id_rating":      hi - ai,
        "home_g_rating":  hg,
        "away_g_rating":  ag,
        "home_id_rating": hi,
        "away_id_rating": ai,
    }


# ════════════════════════════════════════════════
# ANA FONKSİYON
# ════════════════════════════════════════════════

def run_value_hunting(home_general, home_venue, away_general, away_venue, odds=None):
    """
    Veri akışı:
    1. home_general / away_general  → son 6 genel maç istatistiği
    2. home_venue   / away_venue    → son 6 iç/dış saha maç istatistiği
    3. compute_expected_scores()    → venue ağırlıklı beklenen skor hesabı
    4. compute_probabilities()      → spread'e göre dinamik handikap + alt/üst
    5. compute_value()              → bahis sitesi oranı vs model karşılaştırması
    """
    hg  = normalize_stats(home_general, True)
    av  = normalize_stats(away_general, False)
    hv  = normalize_stats(home_venue,   True)
    avv = normalize_stats(away_venue,   False)

    exp   = compute_expected_scores(hg, av, hv, avv)
    probs = compute_probabilities(exp, hg["variance"], av["variance"])

    if probs["p_home_win"] >= probs["p_away_win"]:
        prediction = "1"
        pred_prob  = probs["p_home_win"]
    else:
        prediction = "2"
        pred_prob  = probs["p_away_win"]

    best_ou    = max(probs["ou_lines"],       key=lambda x: abs(x["p_over"] - 50))
    best_iy_ou = max(probs["iy_ou_lines"],    key=lambda x: abs(x["p_over"] - 50))
    best_hcap  = max(probs["handicap_lines"], key=lambda x: abs(x["p_home_cover"] - 50))

    confidence = min(99, int(pred_prob * 0.8 + abs(exp["spread"]) * 0.5 + 20))

    result = {
        "prediction":  prediction,
        "confidence":  confidence,
        "prob_home":   probs["p_home_win"],
        "prob_draw":   probs["p_draw"],
        "prob_away":   probs["p_away_win"],
        "odd_home":    prob_to_odd(probs["p_home_win"]),
        "odd_draw":    prob_to_odd(probs["p_draw"]),
        "odd_away":    prob_to_odd(probs["p_away_win"]),
        "exp_home":    exp["exp_home"],
        "exp_away":    exp["exp_away"],
        "exp_total":   exp["exp_total"],
        "spread":      exp["spread"],
        # Handikap — spread'e göre dinamik 4 çizgi
        "hcap_line":      best_hcap["line"],
        "hcap_home_pct":  best_hcap["p_home_cover"],
        "hcap_away_pct":  best_hcap["p_away_cover"],
        "handicap_lines": probs["handicap_lines"],
        # Alt/Üst — p_over=ÜST=yeşil, p_under=ALT=kırmızı
        "ou_line":      best_ou["line"],
        "ou_over_pct":  best_ou["p_over"],
        "ou_under_pct": best_ou["p_under"],
        "ou_lines":     probs["ou_lines"],
        # İY
        "iy_prob_home": probs["p_iy_home"],
        "iy_prob_draw": probs["p_iy_draw"],
        "iy_prob_away": probs["p_iy_away"],
        "iy_ou_line":   best_iy_ou["line"],
        "iy_ou_over":   best_iy_ou["p_over"],
        "iy_ou_under":  best_iy_ou["p_under"],
        "iy_ou_lines":  probs["iy_ou_lines"],
        "bookmaker_odds": odds,
        # Debug: kaç maç kullanıldı
        "stats_used": {
            "home_general_n": hg["n"],
            "home_venue_n":   hv["n"],
            "away_general_n": av["n"],
            "away_venue_n":   avv["n"],
        },
    }

    # ── VALUE HUNTING ──────────────────────────────────────────────
    if odds:
        value_data = {}

        if odds.get("home"):
            value_data["home"] = compute_value(probs["p_home_win"], odds["home"])
        if odds.get("away"):
            value_data["away"] = compute_value(probs["p_away_win"], odds["away"])

        if odds.get("ou_line") and (odds.get("over") or odds.get("under")):
            target  = odds["ou_line"]
            closest = min(probs["ou_lines"], key=lambda x: abs(x["line"] - target))
            value_data["ou_bookmaker_line"] = target
            value_data["ou_model_line"]     = closest["line"]
            if odds.get("over"):
                value_data["over"]  = compute_value(closest["p_over"],  odds["over"])
            if odds.get("under"):
                value_data["under"] = compute_value(closest["p_under"], odds["under"])

        if odds.get("iy_ou_line") and (odds.get("iy_over") or odds.get("iy_under")):
            target_iy  = odds["iy_ou_line"]
            closest_iy = min(probs["iy_ou_lines"], key=lambda x: abs(x["line"] - target_iy))
            value_data["iy_ou_bookmaker_line"] = target_iy
            value_data["iy_ou_model_line"]     = closest_iy["line"]
            if odds.get("iy_over"):
                value_data["iy_over"]  = compute_value(closest_iy["p_over"],  odds["iy_over"])
            if odds.get("iy_under"):
                value_data["iy_under"] = compute_value(closest_iy["p_under"], odds["iy_under"])

        best_value = None
        best_pct   = -999
        for key, vd in value_data.items():
            if isinstance(vd, dict) and vd.get("is_value") and vd.get("value_pct", -999) > best_pct:
                best_pct   = vd["value_pct"]
                best_value = {"bet": key, **vd}

        value_data["best_value_bet"] = best_value
        result["value_hunting"]      = value_data
    else:
        result["value_hunting"] = None

    return result


def fallback_result():
    return {
        "prediction":  "1",
        "confidence":  40,
        "prob_home":   40.0,
        "prob_draw":   20.0,
        "prob_away":   40.0,
        "odd_home":    2.5,
        "odd_draw":    5.0,
        "odd_away":    2.5,
        "exp_home":    round(LEAGUE_AVG / 2 + HOME_ADV / 2, 1),
        "exp_away":    round(LEAGUE_AVG / 2 - HOME_ADV / 2, 1),
        "exp_total":   LEAGUE_AVG,
        "spread":      HOME_ADV,
        "hcap_line":      -4.5,
        "hcap_home_pct":  52.0,
        "hcap_away_pct":  48.0,
        "handicap_lines": [],
        "ou_line":      round(LEAGUE_AVG + 0.5, 1),
        "ou_over_pct":  50.0,
        "ou_under_pct": 50.0,
        "ou_lines":     [],
        "iy_prob_home": 40.0,
        "iy_prob_draw": 20.0,
        "iy_prob_away": 40.0,
        "iy_ou_line":   round(LEAGUE_AVG * IY_RATIO + 0.5, 1),
        "iy_ou_over":   50.0,
        "iy_ou_under":  50.0,
        "iy_ou_lines":  [],
        "bookmaker_odds": None,
        "value_hunting":  None,
        "stats_used": {"home_general_n": 0, "home_venue_n": 0,
                       "away_general_n": 0, "away_venue_n": 0},
    }
