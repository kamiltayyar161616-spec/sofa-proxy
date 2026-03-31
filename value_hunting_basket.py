"""
BasketOracle - Value Hunting Motoru
Önce API oranlarından implied beklenti hesapla,
sonra son 6 istatistikle birleştir, value hunting yap.
"""

import math

HOME_ADV        = 3.0
PAYOUT          = 0.92
LEAGUE_AVG      = 108.0
IY_RATIO        = 0.47
STD_MS          = 12.0   # Maç sonucu fark std
STD_TOTAL       = 14.0   # Toplam skor std
VALUE_THRESHOLD = 3.0


# ─── Normal dağılım ─────────────────────────────────────────────

def normal_cdf(x, mean, std):
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / std
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ─── İstatistik normalize ────────────────────────────────────────

def normalize_stats(matches):
    n = len(matches)
    if n == 0:
        return None  # Veri yok

    scored   = [m["scored"]   for m in matches]
    conceded = [m["conceded"] for m in matches]
    totals   = [m["total"]    for m in matches]
    iy_s     = [m["iy_scored"]   for m in matches if m.get("iy_scored")]
    iy_c     = [m["iy_conceded"] for m in matches if m.get("iy_conceded")]
    wins     = sum(1 for m in matches if m["win"])

    avg_sc   = sum(scored)   / n
    avg_co   = sum(conceded) / n
    avg_tot  = sum(totals)   / n
    avg_iy_s = sum(iy_s) / len(iy_s) if iy_s else avg_sc * IY_RATIO
    avg_iy_c = sum(iy_c) / len(iy_c) if iy_c else avg_co * IY_RATIO
    variance = sum((x - avg_sc) ** 2 for x in scored) / n if n > 1 else STD_MS ** 2

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


# ─── API oranlarından beklenen değerler ─────────────────────────

def expected_from_odds(market_odds):
    """
    API'deki 1X2 oranlarından beklenen skor farkını,
    OU oranlarından beklenen toplam skoru çıkar.
    """
    mo = market_odds or {}
    result = {}

    # 1X2'den maç sonucu spreadi
    oh = mo.get("odd_home")
    oa = mo.get("odd_away")
    if oh and oa and oh > 1.0 and oa > 1.0:
        ip_h = (1 / oh) / PAYOUT
        ip_a = (1 / oa) / PAYOUT
        total_ip = ip_h + ip_a
        p_home = ip_h / total_ip
        p_away = ip_a / total_ip
        # Spread tahmini: normal dağılım tersinden
        # P(home win) = P(spread > 0.5) => spread = invnorm(1-p_home) * std + 0.5
        # Basit yaklaşım: fark oranı
        if p_home > 0.5:
            implied_spread = (p_home - 0.5) * 2 * STD_MS
        else:
            implied_spread = -(p_away - 0.5) * 2 * STD_MS
        result["implied_spread"] = round(implied_spread, 1)
        result["implied_p_home"] = round(p_home * 100, 1)
        result["implied_p_away"] = round(p_away * 100, 1)

    # OU çizgilerinden beklenen toplam
    ou_lines = mo.get("ou_lines_market", [])
    if ou_lines:
        # En yakın 50/50 çizgisini bul = beklenen toplam
        closest = min(ou_lines, key=lambda x: abs(
            (1/x["odd_over"] if x.get("odd_over") else 0.5) /
            ((1/x["odd_over"] if x.get("odd_over") else 0.5) +
             (1/x["odd_under"] if x.get("odd_under") else 0.5)) - 0.5
        ))
        # Ağırlıklı ortalama: tüm çizgilerden beklenti hesapla
        # Her çizgi için: beklenti = line + (p_over - 0.5) * std * 0.5
        estimates = []
        for l in ou_lines:
            if not l.get("odd_over") or not l.get("odd_under"):
                continue
            p_o = (1 / l["odd_over"]) / ((1/l["odd_over"]) + (1/l["odd_under"]))
            est = l["line"] + (p_o - 0.5) * STD_TOTAL
            estimates.append(est)
        if estimates:
            result["implied_total"] = round(sum(estimates) / len(estimates), 1)

    # İY OU'dan beklenen İY toplamı
    iy_lines = mo.get("iy_ou_lines_market", [])
    if iy_lines:
        estimates = []
        for l in iy_lines:
            if not l.get("odd_over") or not l.get("odd_under"):
                continue
            p_o = (1 / l["odd_over"]) / ((1/l["odd_over"]) + (1/l["odd_under"]))
            est = l["line"] + (p_o - 0.5) * (STD_TOTAL * IY_RATIO)
            estimates.append(est)
        if estimates:
            result["implied_iy_total"] = round(sum(estimates) / len(estimates), 1)

    return result


# ─── Beklenen skor hesabı ────────────────────────────────────────

def compute_expected_scores(hg, ag, hv, av, odds_implied):
    """
    API oranlarından gelen implied değerler varsa onları baz al,
    istatistiklerle ağırlıklı karıştır.
    Veri yoksa saf API implied kullan.
    """
    league_half = LEAGUE_AVG / 2

    has_stats = (hg is not None and ag is not None)

    # --- İstatistikten beklenen değerler ---
    if has_stats:
        if hv and hv["n"] > 0:
            home_attack  = hv["avg_scored"]   * 0.6 + hg["avg_scored"]   * 0.4
            home_defense = hv["avg_conceded"] * 0.6 + hg["avg_conceded"] * 0.4
        else:
            home_attack  = hg["avg_scored"]
            home_defense = hg["avg_conceded"]

        if av and av["n"] > 0:
            away_attack  = av["avg_scored"]   * 0.6 + ag["avg_scored"]   * 0.4
            away_defense = av["avg_conceded"] * 0.6 + ag["avg_conceded"] * 0.4
        else:
            away_attack  = ag["avg_scored"]
            away_defense = ag["avg_conceded"]

        stat_home  = (home_attack + away_defense) / 2 + HOME_ADV / 2
        stat_away  = (away_attack + home_defense) / 2 - HOME_ADV / 2
        stat_home  = max(60, min(150, stat_home))
        stat_away  = max(60, min(150, stat_away))
        stat_total = stat_home + stat_away
        stat_spread = stat_home - stat_away

        stat_iy_home = (hv["avg_iy_scored"] * 0.6 + hg["avg_iy_scored"] * 0.4) if (hv and hv["n"] > 0) else hg["avg_iy_scored"]
        stat_iy_away = (av["avg_iy_scored"] * 0.6 + ag["avg_iy_scored"] * 0.4) if (av and av["n"] > 0) else ag["avg_iy_scored"]
        stat_iy_total = stat_iy_home + stat_iy_away

        # İstatistik kalitesi: kaç maç var?
        n_total = hg["n"] + ag["n"]
        # 12 maç varsa %70 istatistik, 0 maçta %0
        stat_weight = min(0.70, n_total / 12 * 0.70)
        odds_weight = 1 - stat_weight
    else:
        stat_weight = 0
        odds_weight = 1
        stat_total = LEAGUE_AVG
        stat_spread = HOME_ADV
        stat_iy_total = LEAGUE_AVG * IY_RATIO

    # --- API implied değerler ---
    imp = odds_implied
    imp_total  = imp.get("implied_total",    LEAGUE_AVG)
    imp_spread = imp.get("implied_spread",   HOME_ADV)
    imp_iy     = imp.get("implied_iy_total", imp_total * IY_RATIO)

    # --- Karıştır ---
    if has_stats and stat_weight > 0:
        final_total  = stat_total  * stat_weight + imp_total  * odds_weight
        final_spread = stat_spread * stat_weight + imp_spread * odds_weight
        final_iy     = stat_iy_total * stat_weight + imp_iy * odds_weight
    else:
        final_total  = imp_total
        final_spread = imp_spread
        final_iy     = imp_iy

    final_home = (final_total + final_spread) / 2
    final_away = (final_total - final_spread) / 2
    final_home = max(55, min(160, final_home))
    final_away = max(55, min(160, final_away))
    final_total = final_home + final_away

    final_iy_home = final_iy * (final_home / final_total) if final_total > 0 else final_iy / 2
    final_iy_away = final_iy - final_iy_home

    return {
        "exp_home":     round(final_home, 1),
        "exp_away":     round(final_away, 1),
        "exp_total":    round(final_total, 1),
        "exp_iy_home":  round(final_iy_home, 1),
        "exp_iy_away":  round(final_iy_away, 1),
        "exp_iy_total": round(final_iy, 1),
        "spread":       round(final_spread, 1),
        "stat_weight":  round(stat_weight, 2),
    }


# ─── Vig çıkar ──────────────────────────────────────────────────

def remove_vig(odd_home, odd_away):
    if not odd_home or not odd_away or odd_home <= 1.0 or odd_away <= 1.0:
        return None, None
    ip_h  = 1 / odd_home
    ip_a  = 1 / odd_away
    total = ip_h + ip_a
    if total <= 0:
        return None, None
    return round(ip_h / total * 100, 2), round(ip_a / total * 100, 2)


def compute_value(our_prob_pct, market_odd):
    if not market_odd or market_odd <= 1.0 or not our_prob_pct:
        return None
    return round(((our_prob_pct / 100) * market_odd - 1) * 100, 1)


# ─── OU analiz ──────────────────────────────────────────────────

def ou_value_analysis(exp_total, std, market_lines):
    result = []
    for l in market_lines:
        line      = l["line"]
        odd_over  = l.get("odd_over")
        odd_under = l.get("odd_under")

        raw_over   = (1 - normal_cdf(line, exp_total, std)) * 100
        our_p_over  = round(max(1.0, min(99.0, raw_over)), 1)
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


# ─── Olasılık hesapları ──────────────────────────────────────────

def compute_probabilities(exp, home_var, away_var, market_ou_lines=None, market_iy_ou_lines=None):
    spread       = exp["spread"]
    exp_total    = exp["exp_total"]
    exp_iy_total = exp["exp_iy_total"]
    iy_spread    = exp["exp_iy_home"] - exp["exp_iy_away"]

    std_home  = max(8, math.sqrt(home_var)) if home_var else STD_MS
    std_away  = max(8, math.sqrt(away_var)) if away_var else STD_MS
    std_diff  = max(STD_MS,  math.sqrt(std_home**2 + std_away**2))
    std_total = max(STD_TOTAL, std_diff * 1.1)
    std_iy    = max(7, std_total * IY_RATIO)

    # Maç sonucu (basketbolda beraberlik çok nadir, %2-3 max)
    p_home_win = 1 - normal_cdf(0.5,  spread, std_diff)
    p_away_win =     normal_cdf(-0.5, spread, std_diff)
    p_draw     = max(0.01, min(0.03, 1 - p_home_win - p_away_win))
    s = p_home_win + p_draw + p_away_win
    p_home_win /= s
    p_draw     /= s
    p_away_win /= s

    # Handikap
    base_hcap = round(spread / 2.5) * 2.5
    handicap_lines = []
    for offset in [-10, -6.5, -3.5, 0, 3.5, 6.5, 10]:
        hcap  = base_hcap + offset - 0.5
        p_cov = max(0.01, min(0.99, 1 - normal_cdf(hcap, spread, std_diff)))
        handicap_lines.append({
            "line":         round(hcap, 1),
            "p_home_cover": round(p_cov * 100, 1),
            "p_away_cover": round((1 - p_cov) * 100, 1),
        })

    # Alt/Üst MS
    if market_ou_lines:
        ou_lines = ou_value_analysis(exp_total, std_total, market_ou_lines)
    else:
        base_ou = round(exp_total / 2.5) * 2.5
        ou_lines = []
        for offset in [-12.5, -7.5, -2.5, 0, 2.5, 7.5, 12.5]:
            line   = base_ou + offset
            if line <= 0:
                continue
            p_over = round(max(1.0, min(99.0, (1 - normal_cdf(line, exp_total, std_total)) * 100)), 1)
            ou_lines.append({
                "line": round(line, 1), "p_over": p_over, "p_under": round(100-p_over, 1),
                "our_p_over": p_over, "our_p_under": round(100-p_over, 1),
                "odd_over": None, "odd_under": None,
                "val_over": None, "val_under": None, "value_bet": None,
            })

    # Alt/Üst İY
    if market_iy_ou_lines:
        iy_ou_lines = ou_value_analysis(exp_iy_total, std_iy, market_iy_ou_lines)
    else:
        base_iy = round(exp_iy_total / 2.5) * 2.5
        iy_ou_lines = []
        for offset in [-7.5, -5.0, -2.5, 0, 2.5, 5.0, 7.5]:
            line   = base_iy + offset
            if line <= 0:
                continue
            p_over = round(max(1.0, min(99.0, (1 - normal_cdf(line, exp_iy_total, std_iy)) * 100)), 1)
            iy_ou_lines.append({
                "line": round(line, 1), "p_over": p_over, "p_under": round(100-p_over, 1),
                "our_p_over": p_over, "our_p_under": round(100-p_over, 1),
                "odd_over": None, "odd_under": None,
                "val_over": None, "val_under": None, "value_bet": None,
            })

    # İY sonucu
    std_iy_diff = max(7, std_diff * IY_RATIO)
    p_iy_home = 1 - normal_cdf(0.5,  iy_spread, std_iy_diff)
    p_iy_away =     normal_cdf(-0.5, iy_spread, std_iy_diff)
    p_iy_draw = max(0.02, min(0.04, 1 - p_iy_home - p_iy_away))
    s2 = p_iy_home + p_iy_draw + p_iy_away
    p_iy_home /= s2
    p_iy_draw /= s2
    p_iy_away /= s2

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
        "std_diff":       round(std_diff, 2),
        "std_total":      round(std_total, 2),
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


# ─── ANA FONKSİYON ──────────────────────────────────────────────

def run_value_hunting(home_general, home_venue, away_general, away_venue, market_odds=None):
    mo = market_odds or {}

    # İstatistikleri normalize et (veri yoksa None döner)
    hg  = normalize_stats(home_general[:6])  if home_general  else None
    ag  = normalize_stats(away_general[:6])  if away_general  else None
    hv  = normalize_stats(home_venue[:6])    if home_venue    else None
    av  = normalize_stats(away_venue[:6])    if away_venue    else None

    # Variance için
    home_var = hg["variance"] if hg else STD_MS**2
    away_var = ag["variance"] if ag else STD_MS**2

    # API oranlarından implied beklentiler
    odds_implied = expected_from_odds(mo)

    # Beklenen skorlar (API + istatistik karışımı)
    exp = compute_expected_scores(hg, ag, hv, av, odds_implied)

    # Olasılıklar
    probs = compute_probabilities(
        exp, home_var, away_var,
        market_ou_lines    = mo.get("ou_lines_market"),
        market_iy_ou_lines = mo.get("iy_ou_lines_market"),
    )

    p_home = probs["p_home_win"]
    p_away = probs["p_away_win"]
    prediction = "1" if p_home >= p_away else "2"

    # Güven skoru
    prob_gap   = abs(p_home - p_away)
    n_total    = (hg["n"] if hg else 0) + (ag["n"] if ag else 0)
    data_bonus = min(20, n_total * 1.5)
    has_odds   = 15 if odds_implied else 0
    confidence = min(92, int(35 + prob_gap * 0.5 + data_bonus + has_odds))

    best_ou    = max(probs["ou_lines"],       key=lambda x: abs(x["p_over"] - 50)) if probs["ou_lines"] else {}
    best_iy_ou = max(probs["iy_ou_lines"],    key=lambda x: abs(x["p_over"] - 50)) if probs["iy_ou_lines"] else {}
    best_hcap  = max(probs["handicap_lines"], key=lambda x: abs(x["p_home_cover"] - 50)) if probs["handicap_lines"] else {}

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
        "exp_iy_home":      exp["exp_iy_home"],
        "exp_iy_away":      exp["exp_iy_away"],
        "exp_iy_total":     exp["exp_iy_total"],
        "spread":           exp["spread"],
        "stat_weight":      exp["stat_weight"],
        "hcap_line":        best_hcap.get("line", 0),
        "hcap_home_pct":    best_hcap.get("p_home_cover", 50),
        "hcap_away_pct":    best_hcap.get("p_away_cover", 50),
        "handicap_lines":   probs["handicap_lines"],
        "ou_line":          best_ou.get("line", exp["exp_total"]),
        "ou_over_pct":      best_ou.get("p_over", 50),
        "ou_under_pct":     best_ou.get("p_under", 50),
        "ou_lines":         probs["ou_lines"],
        "iy_prob_home":     probs["p_iy_home"],
        "iy_prob_draw":     probs["p_iy_draw"],
        "iy_prob_away":     probs["p_iy_away"],
        "iy_ou_line":       best_iy_ou.get("line", exp["exp_iy_total"]),
        "iy_ou_over":       best_iy_ou.get("p_over", 50),
        "iy_ou_under":      best_iy_ou.get("p_under", 50),
        "iy_ou_lines":      probs["iy_ou_lines"],
        "odds_implied":     odds_implied,
    }


def fallback_result():
    """Hiç veri yoksa ve API oranı da yoksa döner."""
    return {
        "prediction":       "?",
        "confidence":       0,
        "prob_home":        50.0,
        "prob_draw":        2.0,
        "prob_away":        48.0,
        "fair_odd_home":    2.0,
        "fair_odd_draw":    15.0,
        "fair_odd_away":    2.1,
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
        "exp_home":         round(LEAGUE_AVG/2 + HOME_ADV/2, 1),
        "exp_away":         round(LEAGUE_AVG/2 - HOME_ADV/2, 1),
        "exp_total":        LEAGUE_AVG,
        "exp_iy_home":      round(LEAGUE_AVG*IY_RATIO/2, 1),
        "exp_iy_away":      round(LEAGUE_AVG*IY_RATIO/2, 1),
        "exp_iy_total":     round(LEAGUE_AVG*IY_RATIO, 1),
        "spread":           HOME_ADV,
        "stat_weight":      0,
        "hcap_line":        -3.5,
        "hcap_home_pct":    52.0,
        "hcap_away_pct":    48.0,
        "handicap_lines":   [],
        "ou_line":          LEAGUE_AVG,
        "ou_over_pct":      50.0,
        "ou_under_pct":     50.0,
        "ou_lines":         [],
        "iy_prob_home":     50.0,
        "iy_prob_draw":     3.0,
        "iy_prob_away":     47.0,
        "iy_ou_line":       round(LEAGUE_AVG*IY_RATIO, 1),
        "iy_ou_over":       50.0,
        "iy_ou_under":      50.0,
        "iy_ou_lines":      [],
        "odds_implied":     {},
    }
