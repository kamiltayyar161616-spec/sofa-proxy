"""
BasketOracle - Value Hunting Motoru
Son 6 genel / iç saha / dış saha maçlarına göre hesaplama.
Market odds (API'den gelen) ile value hunting yapılır.
"""

import math

HOME_ADV        = 3.5
PAYOUT          = 0.92
LEAGUE_AVG      = 105.0
IY_RATIO        = 0.48
STD_DEV         = 11.0
VALUE_THRESHOLD = 3.0   # Value sayılması için minimum % fark


def normalize_stats(matches):
    n = len(matches)
    if n == 0:
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

    avg_sc  = sum(scored)   / n
    avg_co  = sum(conceded) / n
    avg_tot = sum(totals)   / n
    avg_iy_s = sum(iy_s) / len(iy_s) if iy_s else avg_sc * IY_RATIO
    avg_iy_c = sum(iy_c) / len(iy_c) if iy_c else avg_co * IY_RATIO
    variance = (sum((x - avg_sc) ** 2 for x in scored) / n) if n > 1 else STD_DEV ** 2

    return {
        "avg_scored":      avg_sc,
        "avg_conceded":    avg_co,
        "avg_total":       avg_tot,
        "avg_iy_scored":   avg_iy_s,
        "avg_iy_conceded": avg_iy_c,
        "win_rate":        wins / n,
        "variance":        variance,
        "n":               n,
    }


def compute_expected_scores(hg, ag, hv, av):
    league_half = LEAGUE_AVG / 2

    if hv["n"] > 0:
        home_attack  = hv["avg_scored"]   * 0.60 + hg["avg_scored"]   * 0.40
        home_defense = hv["avg_conceded"] * 0.60 + hg["avg_conceded"] * 0.40
    else:
        home_attack  = hg["avg_scored"]
        home_defense = hg["avg_conceded"]

    if av["n"] > 0:
        away_attack  = av["avg_scored"]   * 0.60 + ag["avg_scored"]   * 0.40
        away_defense = av["avg_conceded"] * 0.60 + ag["avg_conceded"] * 0.40
    else:
        away_attack  = ag["avg_scored"]
        away_defense = ag["avg_conceded"]

    exp_home  = (home_attack + (league_half - away_defense + league_half)) / 2 + HOME_ADV / 2
    exp_away  = (away_attack + (league_half - home_defense + league_half)) / 2 - HOME_ADV / 2
    exp_home  = max(65, min(145, exp_home))
    exp_away  = max(65, min(145, exp_away))
    exp_total = exp_home + exp_away

    exp_iy_home  = (hv["avg_iy_scored"] * 0.6 + hg["avg_iy_scored"] * 0.4) if hv["n"] > 0 else hg["avg_iy_scored"]
    exp_iy_away  = (av["avg_iy_scored"] * 0.6 + ag["avg_iy_scored"] * 0.4) if av["n"] > 0 else ag["avg_iy_scored"]
    exp_iy_total = exp_iy_home + exp_iy_away

    return {
        "exp_home":     round(exp_home, 1),
        "exp_away":     round(exp_away, 1),
        "exp_total":    round(exp_total, 1),
        "exp_iy_home":  round(exp_iy_home, 1),
        "exp_iy_away":  round(exp_iy_away, 1),
        "exp_iy_total": round(exp_iy_total, 1),
        "spread":       round(exp_home - exp_away, 1),
    }


def normal_cdf(x, mean, std):
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / std
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def remove_vig(odd_home, odd_away):
    """Vig'i çıkar, gerçek implied prob döndür."""
    if not odd_home or not odd_away or odd_home <= 1.0 or odd_away <= 1.0:
        return None, None
    ip_h = 1 / odd_home
    ip_a = 1 / odd_away
    total = ip_h + ip_a
    if total <= 0:
        return None, None
    return round(ip_h / total * 100, 2), round(ip_a / total * 100, 2)


def compute_value(our_prob_pct, market_odd):
    """Value % = (bizim_prob * market_odd - 1) * 100"""
    if not market_odd or market_odd <= 1.0 or not our_prob_pct:
        return None
    return round(((our_prob_pct / 100) * market_odd - 1) * 100, 1)


def ou_value_analysis(exp_total, std, market_lines):
    """
    Market OU çizgileri için bizim olasılığımızı ve value'yu hesapla.
    """
    result = []
    for l in market_lines:
        line      = l["line"]
        odd_over  = l.get("odd_over")
        odd_under = l.get("odd_under")

        our_p_over  = round(max(1.0, min(99.0, (1 - normal_cdf(line, exp_total, std)) * 100)), 1)
        our_p_under = round(100 - our_p_over, 1)

        mkt_impl_over, mkt_impl_under = remove_vig(odd_over, odd_under)

        val_over  = compute_value(our_p_over,  odd_over)
        val_under = compute_value(our_p_under, odd_under)

        value_bet = None
        if val_over  is not None and val_over  >= VALUE_THRESHOLD:
            value_bet = "over"
        elif val_under is not None and val_under >= VALUE_THRESHOLD:
            value_bet = "under"

        result.append({
            "line":           line,
            "p_over":         our_p_over,
            "p_under":        our_p_under,
            "our_p_over":     our_p_over,
            "our_p_under":    our_p_under,
            "odd_over":       odd_over,
            "odd_under":      odd_under,
            "mkt_impl_over":  mkt_impl_over,
            "mkt_impl_under": mkt_impl_under,
            "val_over":       val_over,
            "val_under":      val_under,
            "value_bet":      value_bet,
        })
    return result


def compute_probabilities(exp, home_var, away_var,
                          market_ou_lines=None, market_iy_ou_lines=None):
    spread       = exp["spread"]
    exp_total    = exp["exp_total"]
    exp_iy_total = exp["exp_iy_total"]
    iy_spread    = exp["exp_iy_home"] - exp["exp_iy_away"]

    std_home  = max(8, math.sqrt(home_var))
    std_away  = max(8, math.sqrt(away_var))
    # Fark için std (maç sonucu / handikap)
    std_diff  = math.sqrt(std_home**2 + std_away**2)
    # Toplam için std (Alt/Üst) - farktan bağımsız, daha geniş
    std_total = max(12, math.sqrt(std_home**2 + std_away**2) * 1.15)
    # İY std - minimum 7 olmalı, yoksa aşırı kesin değerler çıkıyor
    std_iy    = max(7, std_total * IY_RATIO)

    # Maç sonucu
    p_home_win = 1 - normal_cdf(0.5,  spread, std_diff)
    p_away_win =     normal_cdf(-0.5, spread, std_diff)
    p_draw     = max(0.02, 1 - p_home_win - p_away_win)
    s = p_home_win + p_draw + p_away_win
    p_home_win /= s; p_draw /= s; p_away_win /= s

    # Handikap
    base_hcap = round(spread / 2.5) * 2.5
    handicap_lines = []
    for offset in [-10, -6.5, -3.5, 0, 3.5, 6.5, 10]:
        hcap  = base_hcap + offset - 0.5
        p_cov = 1 - normal_cdf(hcap, spread, std_diff)
        handicap_lines.append({
            "line":         round(hcap, 1),
            "p_home_cover": round(p_cov * 100, 1),
            "p_away_cover": round((1 - p_cov) * 100, 1),
        })

    # Alt/Üst — market çizgileri varsa onları kullan
    if market_ou_lines:
        ou_lines = ou_value_analysis(exp_total, std_total, market_ou_lines)
    else:
        base_ou  = round(exp_total / 2.5) * 2.5
        ou_lines = []
        for offset in [-12.5, -7.5, -2.5, 0, 2.5, 7.5, 12.5]:
            line   = base_ou + offset
            if line <= 0:
                continue
            p_over = round((1 - normal_cdf(line, exp_total, std_total)) * 100, 1)
            p_over = max(1.0, min(99.0, p_over))
            ou_lines.append({
                "line": round(line, 1), "p_over": p_over,
                "p_under": round(100 - p_over, 1),
                "our_p_over": p_over, "our_p_under": round(100 - p_over, 1),
                "odd_over": None, "odd_under": None,
                "val_over": None, "val_under": None, "value_bet": None,
            })

    # İY Alt/Üst
    if market_iy_ou_lines:
        iy_ou_lines = ou_value_analysis(exp_iy_total, std_iy, market_iy_ou_lines)
    else:
        base_iy = round(exp_iy_total / 2.5) * 2.5
        iy_ou_lines = []
        for offset in [-7.5, -5.0, -2.5, 0, 2.5, 5.0, 7.5]:
            line   = base_iy + offset
            if line <= 0:
                continue
            p_over = round((1 - normal_cdf(line, exp_iy_total, std_iy)) * 100, 1)
            p_over = max(1.0, min(99.0, p_over))
            iy_ou_lines.append({
                "line": round(line, 1), "p_over": p_over,
                "p_under": round(100 - p_over, 1),
                "our_p_over": p_over, "our_p_under": round(100 - p_over, 1),
                "odd_over": None, "odd_under": None,
                "val_over": None, "val_under": None, "value_bet": None,
            })

    # İY sonucu
    std_iy_diff = std_diff * IY_RATIO
    p_iy_home = 1 - normal_cdf(0.5,  iy_spread, std_iy_diff)
    p_iy_away =     normal_cdf(-0.5, iy_spread, std_iy_diff)
    p_iy_draw = max(0.05, 1 - p_iy_home - p_iy_away)
    s2 = p_iy_home + p_iy_draw + p_iy_away
    p_iy_home /= s2; p_iy_draw /= s2; p_iy_away /= s2

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


def run_value_hunting(home_general, home_venue, away_general, away_venue,
                      market_odds=None):
    """
    Ana analiz.
    market_odds = {
        "odd_home", "odd_draw", "odd_away",
        "ou_lines_market": [{"line":171.5,"odd_over":1.85,"odd_under":1.95},...],
        "iy_ou_lines_market": [...]
    }
    """
    mo = market_odds or {}

    hg  = normalize_stats(home_general[:6])
    ag  = normalize_stats(away_general[:6])
    hv  = normalize_stats(home_venue[:6])
    avv = normalize_stats(away_venue[:6])

    exp = compute_expected_scores(hg, ag, hv, avv)

    probs = compute_probabilities(
        exp, hg["variance"], ag["variance"],
        market_ou_lines    = mo.get("ou_lines_market"),
        market_iy_ou_lines = mo.get("iy_ou_lines_market"),
    )

    p_home = probs["p_home_win"]
    p_away = probs["p_away_win"]
    prediction = "1" if p_home >= p_away else "2"

    prob_gap   = abs(p_home - p_away)
    data_bonus = min(20, (hg["n"] + ag["n"]) * 1.5)
    confidence = min(95, int(40 + prob_gap * 0.6 + data_bonus))

    best_ou    = max(probs["ou_lines"],       key=lambda x: abs(x["p_over"] - 50))
    best_iy_ou = max(probs["iy_ou_lines"],    key=lambda x: abs(x["p_over"] - 50))
    best_hcap  = max(probs["handicap_lines"], key=lambda x: abs(x["p_home_cover"] - 50))

    market_odd_home = mo.get("odd_home")
    market_odd_away = mo.get("odd_away")
    market_odd_draw = mo.get("odd_draw")

    mkt_impl_home, mkt_impl_away = remove_vig(market_odd_home, market_odd_away)
    value_home = compute_value(p_home, market_odd_home)
    value_away = compute_value(p_away, market_odd_away)

    value_bet_1x2 = None
    if value_home is not None and value_home >= VALUE_THRESHOLD:
        value_bet_1x2 = {"side": "home", "value": value_home, "odd": market_odd_home}
    elif value_away is not None and value_away >= VALUE_THRESHOLD:
        value_bet_1x2 = {"side": "away", "value": value_away, "odd": market_odd_away}

    return {
        "prediction":       prediction,
        "confidence":       confidence,
        "prob_home":        p_home,
        "prob_draw":        probs["p_draw"],
        "prob_away":        p_away,
        "fair_odd_home":    prob_to_odd(p_home),
        "fair_odd_draw":    prob_to_odd(probs["p_draw"]),
        "fair_odd_away":    prob_to_odd(p_away),
        "market_odd_home":  market_odd_home,
        "market_odd_draw":  market_odd_draw,
        "market_odd_away":  market_odd_away,
        "mkt_impl_home":    mkt_impl_home,
        "mkt_impl_away":    mkt_impl_away,
        "value_home":       value_home,
        "value_away":       value_away,
        "value_bet_1x2":    value_bet_1x2,
        "ou_value_bets":    [l for l in probs["ou_lines"]    if l.get("value_bet")],
        "iy_ou_value_bets": [l for l in probs["iy_ou_lines"] if l.get("value_bet")],
        "exp_home":         exp["exp_home"],
        "exp_away":         exp["exp_away"],
        "exp_total":        exp["exp_total"],
        "spread":           exp["spread"],
        "hcap_line":        best_hcap["line"],
        "hcap_home_pct":    best_hcap["p_home_cover"],
        "hcap_away_pct":    best_hcap["p_away_cover"],
        "handicap_lines":   probs["handicap_lines"],
        "ou_line":          best_ou["line"],
        "ou_over_pct":      best_ou["p_over"],
        "ou_under_pct":     best_ou["p_under"],
        "ou_lines":         probs["ou_lines"],
        "iy_prob_home":     probs["p_iy_home"],
        "iy_prob_draw":     probs["p_iy_draw"],
        "iy_prob_away":     probs["p_iy_away"],
        "iy_ou_line":       best_iy_ou["line"],
        "iy_ou_over":       best_iy_ou["p_over"],
        "iy_ou_under":      best_iy_ou["p_under"],
        "iy_ou_lines":      probs["iy_ou_lines"],
    }


def fallback_result():
    return {
        "prediction":       "1",
        "confidence":       30,
        "prob_home":        52.0,
        "prob_draw":        4.0,
        "prob_away":        44.0,
        "fair_odd_home":    prob_to_odd(52.0),
        "fair_odd_draw":    prob_to_odd(4.0),
        "fair_odd_away":    prob_to_odd(44.0),
        "market_odd_home":  None,
        "market_odd_draw":  None,
        "market_odd_away":  None,
        "mkt_impl_home":    None,
        "mkt_impl_away":    None,
        "value_home":       None,
        "value_away":       None,
        "value_bet_1x2":    None,
        "ou_value_bets":    [],
        "iy_ou_value_bets": [],
        "exp_home":         round(LEAGUE_AVG / 2 + HOME_ADV / 2, 1),
        "exp_away":         round(LEAGUE_AVG / 2 - HOME_ADV / 2, 1),
        "exp_total":        LEAGUE_AVG,
        "spread":           HOME_ADV,
        "hcap_line":        -4.5,
        "hcap_home_pct":    52.0,
        "hcap_away_pct":    48.0,
        "handicap_lines":   [],
        "ou_line":          round(LEAGUE_AVG + 0.5, 1),
        "ou_over_pct":      52.0,
        "ou_under_pct":     48.0,
        "ou_lines":         [],
        "iy_prob_home":     52.0,
        "iy_prob_draw":     5.0,
        "iy_prob_away":     43.0,
        "iy_ou_line":       round(LEAGUE_AVG * IY_RATIO + 0.5, 1),
        "iy_ou_over":       52.0,
        "iy_ou_under":      48.0,
        "iy_ou_lines":      [],
    }
