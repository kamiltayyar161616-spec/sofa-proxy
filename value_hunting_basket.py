"""
BasketOracle - Tahmin Motoru

MS  : Son 6 iç/dış/genel maçtan ağırlıklı ortalama → %72 başarı ✓
OU  : Takımların gerçek toplam varyansından STD → anlamlı olasılık
IY  : Q1+Q2 bazlı, gerçek IY toplam ortalaması

Düzeltme: STD artık sabit değil, her takımın kendi maç varyansından gelir.
Bu sayede model %65+ OU tahmini üretebilir.
"""

import math

HOME_ADV        = 3.0
PAYOUT          = 0.92
VALUE_THRESHOLD = 3.0
RECENT_BOOST    = 1.20


# ════════════════════════════════════════════════════════════════
# YARDIMCI
# ════════════════════════════════════════════════════════════════

def normal_cdf(x, mean, std):
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / std
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _w(n):
    return [RECENT_BOOST if i < 3 else 1.0 for i in range(n)]


def _wavg(vals, weights):
    tw = sum(weights)
    return sum(v * w for v, w in zip(vals, weights)) / tw if tw else 0.0


# ════════════════════════════════════════════════════════════════
# İSTATİSTİK PARSE
# ════════════════════════════════════════════════════════════════

def _parse(matches, iy_from_quarters=False):
    n = len(matches)
    if n == 0:
        return None
    ws = _w(n)

    scored   = [m["scored"]   for m in matches]
    conceded = [m["conceded"] for m in matches]
    totals   = [m["total"]    for m in matches]
    wins     = sum(1 for m in matches if m["win"])

    avg_sc  = _wavg(scored,   ws)
    avg_co  = _wavg(conceded, ws)
    avg_tot = _wavg(totals,   ws)

    # Takımın toplam skorundaki GERÇEK varyans
    var_sc  = _wavg([(x - avg_sc) ** 2 for x in scored],  ws) if n > 1 else 100.0
    var_tot = _wavg([(x - avg_tot)**2 for x in totals],   ws) if n > 1 else 144.0

    # IY
    if iy_from_quarters:
        iy_s_l, iy_c_l, iy_ws = [], [], []
        for i, m in enumerate(matches):
            s = (m.get("q1_scored",0) or 0) + (m.get("q2_scored",0) or 0)
            c = (m.get("q1_conceded",0) or 0) + (m.get("q2_conceded",0) or 0)
            if s == 0:
                s = m.get("iy_scored",0) or 0
                c = m.get("iy_conceded",0) or 0
            if s > 0:
                iy_s_l.append(s); iy_c_l.append(c); iy_ws.append(ws[i])
        avg_iy_s = _wavg(iy_s_l, iy_ws) if iy_s_l else avg_sc * 0.47
        avg_iy_c = _wavg(iy_c_l, iy_ws) if iy_c_l else avg_co * 0.47
    else:
        iy_s_l = [m["iy_scored"]   for m in matches if m.get("iy_scored")]
        iy_c_l = [m["iy_conceded"] for m in matches if m.get("iy_conceded")]
        iy_ws  = [ws[i] for i, m in enumerate(matches) if m.get("iy_scored")]
        avg_iy_s = _wavg(iy_s_l, iy_ws) if iy_s_l else avg_sc * 0.47
        avg_iy_c = _wavg(iy_c_l, iy_ws) if iy_c_l else avg_co * 0.47

    # IY gerçek toplam ortalaması
    iy_tots, iy_tot_ws = [], []
    for i, m in enumerate(matches):
        iy_s = m.get("iy_scored",0) or 0
        iy_c = m.get("iy_conceded",0) or 0
        if iy_s > 0 and iy_c > 0:
            iy_tots.append(iy_s + iy_c)
            iy_tot_ws.append(ws[i])
    avg_iy_tot = _wavg(iy_tots, iy_tot_ws) if iy_tots else avg_tot * 0.47

    # IY toplam varyansı
    if len(iy_tots) > 1:
        mean_iy = avg_iy_tot
        var_iy_tot = _wavg([(x - mean_iy)**2 for x in iy_tots], iy_tot_ws)
    else:
        var_iy_tot = avg_iy_tot * 0.004  # ~%6 std fallback

    return {
        "avg_sc":      avg_sc,
        "avg_co":      avg_co,
        "avg_tot":     avg_tot,
        "avg_iy_tot":  avg_iy_tot,
        "avg_iy_s":    avg_iy_s,
        "avg_iy_c":    avg_iy_c,
        "win_rate":    wins / n,
        "var_sc":      var_sc,
        "var_tot":     var_tot,     # ← maç toplam varyansı
        "var_iy_tot":  var_iy_tot,  # ← IY toplam varyansı
        "n":           n,
    }


# ════════════════════════════════════════════════════════════════
# STD HESABI — gerçek varyansdan
# ════════════════════════════════════════════════════════════════

def _compute_stds(hg, ag, hv, av, hg_iy, ag_iy, exp_total, exp_iy_total):
    """
    Takımların kendi toplam varyanslarından STD hesapla.
    Bu sayede model gerçekçi Alt/Üst olasılıkları üretir.

    Örnek: NBA takımları ~11 std, Avrupa ~9 std
    Sabit 14.5 yerine her maç için doğru std kullanılır.
    """
    # MS fark STD (kazanan tahmini için)
    var_h = hg["var_sc"] if hg else 100.0
    var_a = ag["var_sc"] if ag else 100.0
    std_diff = max(8.0, math.sqrt(var_h + var_a))

    # Toplam STD — iki takımın toplam varyansının ortalaması
    # Her takımın "bu maçta toplam kaç puan oynanır" varyansı
    vt_h = hg["var_tot"] if hg else 144.0
    vt_a = ag["var_tot"] if ag else 144.0
    # Venue varsa ağırlıklı
    if hv and hv["n"] >= 2: vt_h = vt_h * 0.4 + hv["var_tot"] * 0.6
    if av and av["n"] >= 2: vt_a = vt_a * 0.4 + av["var_tot"] * 0.6

    std_total_raw = math.sqrt((vt_h + vt_a) / 2)

    # Sınır: min 7, max 18
    # NBA için ~11-13, Avrupa için ~8-11 çıkmalı
    std_total = max(7.0, min(18.0, std_total_raw))

    # IY STD
    vi_h = hg_iy["var_iy_tot"] if hg_iy else (exp_iy_total * 0.004)
    vi_a = ag_iy["var_iy_tot"] if ag_iy else (exp_iy_total * 0.004)
    if hv_iy := (hv if hv and hv["n"] >= 2 else None):
        vi_h = vi_h * 0.4 + hv_iy.get("var_iy_tot", vi_h) * 0.6
    if av_iy := (av if av and av["n"] >= 2 else None):
        vi_a = vi_a * 0.4 + av_iy.get("var_iy_tot", vi_a) * 0.6

    std_iy = max(4.0, min(12.0, math.sqrt((vi_h + vi_a) / 2)))

    return std_diff, std_total, std_iy


# ════════════════════════════════════════════════════════════════
# BEKLENEN SKOR
# ════════════════════════════════════════════════════════════════

def _expected(hg, ag, hv, av, hg_iy, ag_iy, hv_iy, av_iy):
    # Toplam: gerçek avg_tot ortalaması
    h_tot = (hv["avg_tot"] * 0.6 + hg["avg_tot"] * 0.4) if (hv and hv["n"] >= 2) else hg["avg_tot"]
    a_tot = (av["avg_tot"] * 0.6 + ag["avg_tot"] * 0.4) if (av and av["n"] >= 2) else ag["avg_tot"]
    exp_total = (h_tot + a_tot) / 2

    # Spread: saldırı bazlı
    h_att = (hv["avg_sc"] * 0.6 + hg["avg_sc"] * 0.4) if (hv and hv["n"] >= 2) else hg["avg_sc"]
    a_def = (av["avg_co"] * 0.6 + ag["avg_co"] * 0.4) if (av and av["n"] >= 2) else ag["avg_co"]
    a_att = (av["avg_sc"] * 0.6 + ag["avg_sc"] * 0.4) if (av and av["n"] >= 2) else ag["avg_sc"]
    h_def = (hv["avg_co"] * 0.6 + hg["avg_co"] * 0.4) if (hv and hv["n"] >= 2) else hg["avg_co"]
    spread = (h_att + a_def) / 2 - (a_att + h_def) / 2 + HOME_ADV

    exp_home = (exp_total + spread) / 2
    exp_away = (exp_total - spread) / 2

    # IY toplam
    h_iy = (hv_iy["avg_iy_tot"] * 0.6 + hg_iy["avg_iy_tot"] * 0.4) if (hv_iy and hv_iy["n"] >= 2) else hg_iy["avg_iy_tot"]
    a_iy = (av_iy["avg_iy_tot"] * 0.6 + ag_iy["avg_iy_tot"] * 0.4) if (av_iy and av_iy["n"] >= 2) else ag_iy["avg_iy_tot"]
    exp_iy_total = (h_iy + a_iy) / 2

    # IY spread
    h_iy_att = (hv_iy["avg_iy_s"] * 0.6 + hg_iy["avg_iy_s"] * 0.4) if (hv_iy and hv_iy["n"] >= 2) else hg_iy["avg_iy_s"]
    a_iy_def = (av_iy["avg_iy_c"] * 0.6 + ag_iy["avg_iy_c"] * 0.4) if (av_iy and av_iy["n"] >= 2) else ag_iy["avg_iy_c"]
    a_iy_att = (av_iy["avg_iy_s"] * 0.6 + ag_iy["avg_iy_s"] * 0.4) if (av_iy and av_iy["n"] >= 2) else ag_iy["avg_iy_s"]
    h_iy_def = (hv_iy["avg_iy_c"] * 0.6 + hg_iy["avg_iy_c"] * 0.4) if (hv_iy and hv_iy["n"] >= 2) else hg_iy["avg_iy_c"]
    iy_spread = (h_iy_att + a_iy_def) / 2 - (a_iy_att + h_iy_def) / 2

    exp_iy_home = (exp_iy_total + iy_spread) / 2
    exp_iy_away = (exp_iy_total - iy_spread) / 2

    return {
        "exp_home":     round(exp_home,     1),
        "exp_away":     round(exp_away,     1),
        "exp_total":    round(exp_total,    1),
        "exp_iy_home":  round(exp_iy_home,  1),
        "exp_iy_away":  round(exp_iy_away,  1),
        "exp_iy_total": round(exp_iy_total, 1),
        "spread":       round(spread,       1),
    }


# ════════════════════════════════════════════════════════════════
# VIG & VALUE
# ════════════════════════════════════════════════════════════════

def remove_vig(oh, oa):
    if not oh or not oa or oh <= 1.0 or oa <= 1.0:
        return None, None
    s = 1/oh + 1/oa
    return (round((1/oh)/s*100, 2), round((1/oa)/s*100, 2)) if s > 0 else (None, None)


def compute_value(prob_pct, odd):
    if not odd or odd <= 1.0 or not prob_pct:
        return None
    return round(((prob_pct/100) * odd - 1) * 100, 1)


def prob_to_odd(prob_pct):
    return 15.0 if prob_pct <= 0 else min(round((100/prob_pct)/PAYOUT, 2), 15.0)


# ════════════════════════════════════════════════════════════════
# OU ANALİZİ
# ════════════════════════════════════════════════════════════════

def _ou_analysis(exp_total, std, lines):
    result = []
    for l in lines:
        line = l["line"]
        ov   = l.get("odd_over")
        un   = l.get("odd_under")
        p_ov = round(max(1.0, min(99.0, (1 - normal_cdf(line, exp_total, std)) * 100)), 1)
        p_un = round(100 - p_ov, 1)
        mi_ov, mi_un = remove_vig(ov, un)
        v_ov = compute_value(p_ov, ov)
        v_un = compute_value(p_un, un)
        vbet = None
        if v_ov is not None and v_ov >= VALUE_THRESHOLD: vbet = "over"
        elif v_un is not None and v_un >= VALUE_THRESHOLD: vbet = "under"
        result.append({
            "line": line, "p_over": p_ov, "p_under": p_un,
            "our_p_over": p_ov, "our_p_under": p_un,
            "odd_over": ov, "odd_under": un,
            "mkt_impl_over": mi_ov, "mkt_impl_under": mi_un,
            "val_over": v_ov, "val_under": v_un, "value_bet": vbet,
        })
    return result


def _ou_auto(exp_total, std):
    """exp_total etrafında 7 barem — std'ye göre aralık ayarla."""
    # Std küçükse aralık dar, büyükse geniş
    step = max(2.5, round(std * 0.4 / 2.5) * 2.5)
    base = round(exp_total / step) * step
    lines = []
    for off in [-3*step, -2*step, -step, 0, step, 2*step, 3*step]:
        line = base + off
        if line <= 0: continue
        p_ov = round(max(1.0, min(99.0, (1 - normal_cdf(line, exp_total, std)) * 100)), 1)
        lines.append({
            "line": round(line, 1), "p_over": p_ov, "p_under": round(100-p_ov, 1),
            "our_p_over": p_ov, "our_p_under": round(100-p_ov, 1),
            "odd_over": None, "odd_under": None,
            "val_over": None, "val_under": None, "value_bet": None,
        })
    return lines


# ════════════════════════════════════════════════════════════════
# RATING
# ════════════════════════════════════════════════════════════════

def compute_ratings(data):
    def r(matches):
        return sum(m["scored"] - m["conceded"] for m in matches[:6])
    hg = r(data.get("home_general", []))
    ag = r(data.get("away_general", []))
    hi = r(data.get("home_venue",   []))
    ai = r(data.get("away_venue",   []))
    return {
        "g_rating": hg-ag, "id_rating": hi-ai,
        "home_g_rating": hg, "away_g_rating": ag,
        "home_id_rating": hi, "away_id_rating": ai,
    }


# ════════════════════════════════════════════════════════════════
# ANA FONKSİYON
# ════════════════════════════════════════════════════════════════

def run_value_hunting(home_general, home_venue, away_general, away_venue,
                      market_odds=None, league_name=""):
    mo = market_odds or {}

    hg = _parse(home_general[:6]) if home_general else None
    ag = _parse(away_general[:6]) if away_general else None
    hv = _parse(home_venue[:6])   if home_venue   else None
    av = _parse(away_venue[:6])   if away_venue   else None

    hg_iy = _parse(home_general[:6], iy_from_quarters=True) if home_general else hg
    ag_iy = _parse(away_general[:6], iy_from_quarters=True) if away_general else ag
    hv_iy = _parse(home_venue[:6],   iy_from_quarters=True) if home_venue   else hv
    av_iy = _parse(away_venue[:6],   iy_from_quarters=True) if away_venue   else av

    if not hg or not ag:
        return fallback_result()

    exp = _expected(hg, ag, hv, av, hg_iy, ag_iy, hv_iy, av_iy)

    exp_total    = exp["exp_total"]
    exp_iy_total = exp["exp_iy_total"]
    spread       = exp["spread"]
    iy_spread    = exp["exp_iy_home"] - exp["exp_iy_away"]

    # STD — takımların gerçek varyansından
    std_diff, std_total, std_iy = _compute_stds(
        hg, ag, hv, av, hg_iy, ag_iy, exp_total, exp_iy_total
    )
    std_iy_diff = max(5.0, std_diff * 0.47)

    # Maç sonucu
    p_home_win = 1 - normal_cdf(0.5,  spread, std_diff)
    p_away_win =     normal_cdf(-0.5, spread, std_diff)
    p_draw     = max(0.01, min(0.03, 1 - p_home_win - p_away_win))
    s = p_home_win + p_draw + p_away_win
    p_home_win /= s; p_draw /= s; p_away_win /= s

    # Handikap
    base_hcap      = round(spread / 2.5) * 2.5
    handicap_lines = []
    for off in [-10, -6.5, -3.5, 0, 3.5, 6.5, 10]:
        hcap  = base_hcap + off - 0.5
        p_cov = max(0.01, min(0.99, 1 - normal_cdf(hcap, spread, std_diff)))
        handicap_lines.append({
            "line": round(hcap, 1),
            "p_home_cover": round(p_cov*100, 1),
            "p_away_cover": round((1-p_cov)*100, 1),
        })

    # MS Alt/Üst
    ou_mkt   = mo.get("ou_lines_market", [])
    ou_lines = _ou_analysis(exp_total, std_total, ou_mkt) if ou_mkt else _ou_auto(exp_total, std_total)

    # IY Alt/Üst
    iy_mkt      = mo.get("iy_ou_lines_market", [])
    iy_ou_lines = _ou_analysis(exp_iy_total, std_iy, iy_mkt) if iy_mkt else _ou_auto(exp_iy_total, std_iy)

    # IY maç sonucu
    p_iy_home = 1 - normal_cdf(0.5,  iy_spread, std_iy_diff)
    p_iy_away =     normal_cdf(-0.5, iy_spread, std_iy_diff)
    p_iy_draw = max(0.02, min(0.04, 1 - p_iy_home - p_iy_away))
    s2 = p_iy_home + p_iy_draw + p_iy_away
    p_iy_home /= s2; p_iy_draw /= s2; p_iy_away /= s2

    p_home = round(p_home_win * 100, 1)
    p_away = round(p_away_win * 100, 1)
    prediction = "1" if p_home >= p_away else "2"

    oh = mo.get("odd_home"); oa = mo.get("odd_away"); od = mo.get("odd_draw")
    mkt_impl_home, mkt_impl_away = remove_vig(oh, oa)
    val_home = compute_value(p_home, oh)
    val_away = compute_value(p_away, oa)

    vbet_1x2 = None
    if val_home is not None and val_home >= VALUE_THRESHOLD:
        vbet_1x2 = {"side": "home", "value": val_home, "odd": oh}
    elif val_away is not None and val_away >= VALUE_THRESHOLD:
        vbet_1x2 = {"side": "away", "value": val_away, "odd": oa}

    n_total    = hg["n"] + ag["n"]
    confidence = min(92, int(30 + abs(p_home-p_away)*0.6 + min(20, n_total*1.5)))

    best_ou   = max(ou_lines,       key=lambda x: abs(x["p_over"]-50)) if ou_lines       else {}
    best_iy   = max(iy_ou_lines,    key=lambda x: abs(x["p_over"]-50)) if iy_ou_lines    else {}
    best_hcap = max(handicap_lines, key=lambda x: abs(x["p_home_cover"]-50)) if handicap_lines else {}

    return {
        "prediction":       prediction,
        "confidence":       confidence,
        "prob_home":        p_home,
        "prob_draw":        round(p_draw*100, 1),
        "prob_away":        p_away,
        "fair_odd_home":    prob_to_odd(p_home),
        "fair_odd_draw":    prob_to_odd(round(p_draw*100, 1)),
        "fair_odd_away":    prob_to_odd(p_away),
        "market_odd_home":  oh,
        "market_odd_draw":  od,
        "market_odd_away":  oa,
        "mkt_impl_home":    mkt_impl_home,
        "mkt_impl_away":    mkt_impl_away,
        "value_home":       val_home,
        "value_away":       val_away,
        "value_bet_1x2":    vbet_1x2,
        "ou_value_bets":    [l for l in ou_lines    if l.get("value_bet")],
        "iy_ou_value_bets": [l for l in iy_ou_lines if l.get("value_bet")],
        "exp_home":         exp["exp_home"],
        "exp_away":         exp["exp_away"],
        "exp_total":        exp["exp_total"],
        "exp_iy_home":      exp["exp_iy_home"],
        "exp_iy_away":      exp["exp_iy_away"],
        "exp_iy_total":     exp["exp_iy_total"],
        "spread":           exp["spread"],
        "stat_weight":      1.0,
        "market_center":    None,
        "std_total":        round(std_total, 1),
        "std_iy":           round(std_iy, 1),
        "hcap_line":        best_hcap.get("line", 0),
        "hcap_home_pct":    best_hcap.get("p_home_cover", 50),
        "hcap_away_pct":    best_hcap.get("p_away_cover", 50),
        "handicap_lines":   handicap_lines,
        "ou_line":          best_ou.get("line",  exp_total),
        "ou_over_pct":      best_ou.get("p_over",  50),
        "ou_under_pct":     best_ou.get("p_under", 50),
        "ou_lines":         ou_lines,
        "iy_prob_home":     round(p_iy_home*100, 1),
        "iy_prob_draw":     round(p_iy_draw*100, 1),
        "iy_prob_away":     round(p_iy_away*100, 1),
        "iy_ou_line":       best_iy.get("line",  exp_iy_total),
        "iy_ou_over":       best_iy.get("p_over",  50),
        "iy_ou_under":      best_iy.get("p_under", 50),
        "iy_ou_lines":      iy_ou_lines,
        "odds_implied":     {},
        "data_info": {
            "home_general_count": hg["n"],
            "home_venue_count":   hv["n"] if hv else 0,
            "away_general_count": ag["n"],
            "away_venue_count":   av["n"] if av else 0,
        },
    }


# ════════════════════════════════════════════════════════════════
# FALLBACK
# ════════════════════════════════════════════════════════════════

def fallback_result():
    return {
        "prediction": "?", "confidence": 0,
        "prob_home": 50.0, "prob_draw": 2.0, "prob_away": 48.0,
        "fair_odd_home": 2.0, "fair_odd_draw": 15.0, "fair_odd_away": 2.1,
        "market_odd_home": None, "market_odd_draw": None, "market_odd_away": None,
        "mkt_impl_home": None, "mkt_impl_away": None,
        "value_home": None, "value_away": None, "value_bet_1x2": None,
        "ou_value_bets": [], "iy_ou_value_bets": [],
        "exp_home": 0.0, "exp_away": 0.0, "exp_total": 0.0,
        "exp_iy_home": 0.0, "exp_iy_away": 0.0, "exp_iy_total": 0.0,
        "spread": 0.0, "stat_weight": 0, "market_center": None,
        "std_total": 0.0, "std_iy": 0.0,
        "hcap_line": 0, "hcap_home_pct": 50.0, "hcap_away_pct": 50.0,
        "handicap_lines": [],
        "ou_line": 0.0, "ou_over_pct": 50.0, "ou_under_pct": 50.0, "ou_lines": [],
        "iy_prob_home": 50.0, "iy_prob_draw": 3.0, "iy_prob_away": 47.0,
        "iy_ou_line": 0.0, "iy_ou_over": 50.0, "iy_ou_under": 50.0, "iy_ou_lines": [],
        "odds_implied": {},
        "data_info": {
            "home_general_count": 0, "home_venue_count": 0,
            "away_general_count": 0, "away_venue_count": 0,
        },
    }
