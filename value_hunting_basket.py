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
STD_MS          = 12.0
STD_TOTAL       = 14.0
VALUE_THRESHOLD = 3.0


# ─── Normal dağılım ─────────────────────────────────────────────

def normal_cdf(x, mean, std):
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / std
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ─── İstatistik normalize ────────────────────────────────────────

# Ağırlık: son 3 maça (en güncel) %20 fazla ağırlık
RECENT_BOOST = 1.20

def _weighted_avg(values, weights):
    total_w = sum(weights)
    if total_w == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_w

def _build_weights(n):
    return [RECENT_BOOST if i < 3 else 1.0 for i in range(n)]

def _remove_outliers(matches):
    if len(matches) < 3:
        return matches
    totals = [m["total"] for m in matches]
    avg = sum(totals) / len(totals)
    std = (sum((x-avg)**2 for x in totals) / len(totals)) ** 0.5
    threshold = max(30, std * 2.5)
    cleaned = [m for m in matches if abs(m["total"] - avg) <= threshold]
    return cleaned if len(cleaned) >= 2 else matches


def normalize_stats(matches, use_quarters_for_iy=False):
    """
    Weighted Average: son 3 maça %20 fazla ağırlık.
    use_quarters_for_iy=True → IY için sadece Q1+Q2 kullan (Madde 4).
    Pace ve Efficiency hesaplanır (Madde 3).
    """
    n = len(matches)
    if n == 0:
        return None

    weights  = _build_weights(n)
    scored   = [m["scored"]   for m in matches]
    conceded = [m["conceded"] for m in matches]
    totals   = [m["total"]    for m in matches]
    wins     = sum(1 for m in matches if m["win"])

    avg_sc  = _weighted_avg(scored,   weights)
    avg_co  = _weighted_avg(conceded, weights)
    avg_tot = _weighted_avg(totals,   weights)
    variance = _weighted_avg([(x - avg_sc)**2 for x in scored], weights) if n > 1 else STD_MS**2

    # IY: Madde 4 - Q1+Q2 çeyrek bazlı (mümkünse)
    if use_quarters_for_iy:
        q_sc, q_co, q_w = [], [], []
        for i, m in enumerate(matches):
            q1s = m.get("q1_scored",   0) or 0
            q2s = m.get("q2_scored",   0) or 0
            q1c = m.get("q1_conceded", 0) or 0
            q2c = m.get("q2_conceded", 0) or 0
            iy_s = q1s + q2s
            iy_c = q1c + q2c
            if iy_s > 0:
                q_sc.append(iy_s); q_co.append(iy_c); q_w.append(weights[i])
        if q_sc:
            avg_iy_s = _weighted_avg(q_sc, q_w)
            avg_iy_c = _weighted_avg(q_co, q_w)
        else:
            iy_s_l = [m["iy_scored"]   for m in matches if m.get("iy_scored")]
            iy_c_l = [m["iy_conceded"] for m in matches if m.get("iy_conceded")]
            avg_iy_s = sum(iy_s_l)/len(iy_s_l) if iy_s_l else avg_sc * IY_RATIO
            avg_iy_c = sum(iy_c_l)/len(iy_c_l) if iy_c_l else avg_co * IY_RATIO
    else:
        iy_s_l = [m["iy_scored"]   for m in matches if m.get("iy_scored")]
        iy_c_l = [m["iy_conceded"] for m in matches if m.get("iy_conceded")]
        iy_w   = [weights[i] for i, m in enumerate(matches) if m.get("iy_scored")]
        avg_iy_s = _weighted_avg(iy_s_l, iy_w) if iy_s_l else avg_sc * IY_RATIO
        avg_iy_c = _weighted_avg(iy_c_l, iy_w) if iy_c_l else avg_co * IY_RATIO

    # Madde 3 - Pace & Efficiency
    pace       = (avg_sc + avg_co) / 2
    efficiency = (avg_sc / pace * 100) if pace > 0 else 100.0

    return {
        "avg_scored":      avg_sc,
        "avg_conceded":    avg_co,
        "avg_total":       avg_tot,
        "avg_iy_scored":   avg_iy_s,
        "avg_iy_conceded": avg_iy_c,
        "win_rate":        wins / n,
        "variance":        variance,
        "pace":            round(pace, 1),
        "efficiency":      round(efficiency, 1),
        "n":               n,
    }


# ─── API oranlarından beklenen değerler ─────────────────────────

def expected_from_odds(market_odds):
    """
    API'deki 1X2 oranlarından implied spread,
    OU oranlarından implied toplam çıkar.
    """
    mo = market_odds or {}
    result = {}

    # 1X2'den spread
    oh = mo.get("odd_home")
    oa = mo.get("odd_away")
    if oh and oa and oh > 1.0 and oa > 1.0:
        ip_h = (1 / oh) / PAYOUT
        ip_a = (1 / oa) / PAYOUT
        total_ip = ip_h + ip_a
        p_home = ip_h / total_ip
        p_away = ip_a / total_ip
        if p_home > 0.5:
            implied_spread = (p_home - 0.5) * 2 * STD_MS
        else:
            implied_spread = -(p_away - 0.5) * 2 * STD_MS
        result["implied_spread"] = round(implied_spread, 1)
        result["implied_p_home"] = round(p_home * 100, 1)
        result["implied_p_away"] = round(p_away * 100, 1)

    # OU çizgilerinden implied toplam
    # Burada bahisçinin gerçek line'larını kullanıyoruz (örn. 214.5)
    ou_lines = mo.get("ou_lines_market", [])
    if ou_lines:
        estimates = []
        for l in ou_lines:
            if not l.get("odd_over") or not l.get("odd_under"):
                continue
            ov = l["odd_over"]
            un = l["odd_under"]
            # Vig'siz implied prob
            raw_p_over  = 1 / ov
            raw_p_under = 1 / un
            total_ip    = raw_p_over + raw_p_under
            p_o = raw_p_over / total_ip  # vig çıkarılmış
            # Eğer p_o == 0.5 ise line = beklenen toplam
            # p_o > 0.5 ise beklenen toplam > line
            est = l["line"] + (p_o - 0.5) * STD_TOTAL
            estimates.append(est)
        if estimates:
            result["implied_total"] = round(sum(estimates) / len(estimates), 1)
            # Bahisçi line'larının ortalaması = piyasa odak noktası
            result["market_ou_center"] = round(
                sum(l["line"] for l in ou_lines) / len(ou_lines), 1
            )

    # İY OU'dan implied İY toplamı
    iy_lines = mo.get("iy_ou_lines_market", [])
    if iy_lines:
        estimates = []
        for l in iy_lines:
            if not l.get("odd_over") or not l.get("odd_under"):
                continue
            ov = l["odd_over"]
            un = l["odd_under"]
            raw_p_over  = 1 / ov
            raw_p_under = 1 / un
            total_ip    = raw_p_over + raw_p_under
            p_o = raw_p_over / total_ip
            est = l["line"] + (p_o - 0.5) * (STD_TOTAL * IY_RATIO)
            estimates.append(est)
        if estimates:
            result["implied_iy_total"] = round(sum(estimates) / len(estimates), 1)
            result["market_iy_center"] = round(
                sum(l["line"] for l in iy_lines) / len(iy_lines), 1
            )

    return result


# ─── Beklenen skor hesabı ────────────────────────────────────────

def compute_expected_scores(hg, ag, hv, av, odds_implied):
    """
    Beklenen skorları hesapla.

    TEMEL KURAL:
    Eğer bahisçiden gerçek OU line'ları geldiyse (market_ou_center),
    model o noktayı merkez alır. İstatistik bu merkezi destekler veya
    küçük sapma yaratır — ama bahisçi line'ından uzaklaşmaz.

    Örnek: Bahisçi 214.5 satıyor → model 210-218 bandında çalışır.
    İstatistik 151.5 diyor → bu bilgi yok sayılmaz ama dominant değil.
    """
    has_stats = (hg is not None and ag is not None)

    # Bahisçinin piyasa merkezi (en güvenilir referans)
    market_center    = odds_implied.get("market_ou_center")
    market_iy_center = odds_implied.get("market_iy_center")
    imp_total        = odds_implied.get("implied_total")
    imp_spread       = odds_implied.get("implied_spread", HOME_ADV)
    imp_iy           = odds_implied.get("implied_iy_total")

    # ── İstatistikten raw beklenti ──
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

        stat_home   = (home_attack + away_defense) / 2 + HOME_ADV / 2
        stat_away   = (away_attack + home_defense) / 2 - HOME_ADV / 2
        stat_total  = stat_home + stat_away
        stat_spread = stat_home - stat_away

        stat_iy_home  = (hv["avg_iy_scored"] * 0.6 + hg["avg_iy_scored"] * 0.4) if (hv and hv["n"] > 0) else hg["avg_iy_scored"]
        stat_iy_away  = (av["avg_iy_scored"] * 0.6 + ag["avg_iy_scored"] * 0.4) if (av and av["n"] > 0) else ag["avg_iy_scored"]
        stat_iy_total = stat_iy_home + stat_iy_away

        n_total    = hg["n"] + ag["n"]
        # Bahisçi line'ı varsa istatistik max %10, yoksa max %30
        has_market  = odds_implied.get("market_ou_center") is not None
        stat_cap    = 0.10 if has_market else 0.30
        stat_weight = min(stat_cap, n_total / 12 * stat_cap)
        odds_weight = 1 - stat_weight
    else:
        stat_weight  = 0
        odds_weight  = 1
        stat_total   = LEAGUE_AVG
        stat_spread  = HOME_ADV
        stat_iy_total = LEAGUE_AVG * IY_RATIO

    # ── Bahisçi line'ı varsa: kalibrasyon ──
    # market_center (örn. 214.5) temel referans
    # imp_total oranlardan hesaplanan beklenti (214.8 gibi)
    # stat_total istatistikten (151.5 gibi) — ağırlığı düşük tutulur

    if market_center is not None:
        # Bahisçi line'ı var → dominant referans
        # İstatistik sadece SPREAD bilgisi için kullanılır, toplam için değil
        # Toplam: %90 bahisçi implied, %10 istatistik
        ref_total = imp_total if imp_total else market_center
        if has_stats and stat_weight > 0:
            # Bahisçi line'ı varken istatistiğin katkısı max %5 — nokta atışı için
            stat_contribution = min(0.05, stat_weight * 0.08)
            final_total = ref_total * (1 - stat_contribution) + stat_total * stat_contribution
        else:
            final_total = ref_total

        # Spread: istatistik daha anlamlı (bahisçi line'ı genelde yok)
        if has_stats and stat_weight > 0:
            final_spread = stat_spread * stat_weight + imp_spread * odds_weight
        else:
            final_spread = imp_spread

    elif imp_total is not None:
        # Oranlardan beklenti var ama merkez yok
        if has_stats and stat_weight > 0:
            # imp_total dominant; istatistik küçük düzeltme
            final_total  = imp_total  * 0.90 + stat_total  * 0.10
            final_spread = imp_spread * 0.70 + stat_spread * 0.30
        else:
            final_total  = imp_total
            final_spread = imp_spread

    else:
        # Bahisçi line'ı yok — istatistik dominant, lig ortalaması küçük çıpa
        if has_stats:
            data_trust = min(0.85, max(0.60, n_total / 12 * 0.85))
            # LEAGUE_AVG tek takım ortalaması; toplam için *2 kullan
            final_total  = stat_total  * data_trust + (LEAGUE_AVG * 2) * (1 - data_trust)
            final_spread = stat_spread * data_trust + HOME_ADV         * (1 - data_trust)
        else:
            final_total  = LEAGUE_AVG * 2
            final_spread = HOME_ADV

    # ── İY toplamı ──
    if market_iy_center is not None:
        ref_iy = imp_iy if imp_iy else market_iy_center
        if has_stats and stat_weight > 0:
            stat_contribution = min(0.05, stat_weight * 0.08)
            final_iy = ref_iy * (1 - stat_contribution) + stat_iy_total * stat_contribution
        else:
            final_iy = ref_iy
    elif imp_iy is not None:
        if has_stats and stat_weight > 0:
            final_iy = imp_iy * 0.90 + stat_iy_total * 0.10
        else:
            final_iy = imp_iy
    else:
        if has_stats:
            # Sadece istatistik: lig ortalamasıyla blend
            final_iy = stat_iy_total * stat_weight + (final_total * IY_RATIO) * (1 - stat_weight)
        else:
            final_iy = final_total * IY_RATIO

    # Skor sınırları — bahisçi line'ı varsa daha geniş aralık
    if market_center and market_center > 160:
        lo, hi = 80, 200
    else:
        lo, hi = 55, 160

    final_home = (final_total + final_spread) / 2
    final_away = (final_total - final_spread) / 2
    final_home = max(lo, min(hi, final_home))
    final_away = max(lo, min(hi, final_away))
    final_total = final_home + final_away

    final_iy_home = final_iy * (final_home / final_total) if final_total > 0 else final_iy / 2
    final_iy_away = final_iy - final_iy_home

    return {
        "exp_home":       round(final_home, 1),
        "exp_away":       round(final_away, 1),
        "exp_total":      round(final_total, 1),
        "exp_iy_home":    round(final_iy_home, 1),
        "exp_iy_away":    round(final_iy_away, 1),
        "exp_iy_total":   round(final_iy, 1),
        "spread":         round(final_spread, 1),
        "stat_weight":    round(stat_weight, 2),
        "market_center":  market_center,
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
    """
    Bahisçinin her line'ı için:
    - Modelimizin o line'a verdiği olasılık
    - Bahisçinin implied olasılığı (vig çıkarılmış)
    - Value farkı
    """
    result = []
    for l in market_lines:
        line      = l["line"]
        odd_over  = l.get("odd_over")
        odd_under = l.get("odd_under")

        raw_over    = (1 - normal_cdf(line, exp_total, std)) * 100
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

    std_home  = max(8, min(14, math.sqrt(home_var))) if home_var else STD_MS
    std_away  = max(8, min(14, math.sqrt(away_var))) if away_var else STD_MS
    std_diff  = max(STD_MS, min(18.0, math.sqrt(std_home**2 + std_away**2)))

    # ── Madde 2: Gerçek varyansdan Gaussian std (basketbol STD) ──
    # std_home/away zaten maç bazlı sapma; toplam için Gaussian kombinasyonu
    market_center = exp.get("market_center")
    # Referans toplam (bahisçi line'ı varsa daha güvenilir)
    ref_total = market_center if market_center else exp.get("exp_total", LEAGUE_AVG)

    # Basketbol OU std gerçek değeri: NBA ~11-13, diğer ligler ~10-12
    # ref_total * 0.055 → 215 * 0.055 = 11.8 (gerçekçi)
    gaussian_std_total = max(10.0, min(14.0, ref_total * 0.055))

    if market_center and market_center > 160:
        scale     = market_center / LEAGUE_AVG
        std_total = max(gaussian_std_total, min(std_diff, 15.0))
        std_iy    = max(5.5, std_total * IY_RATIO)
    else:
        std_total = max(gaussian_std_total, min(std_diff, 14.0))
        std_iy    = max(5.0, std_total * IY_RATIO)

    # Maç sonucu
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

    # Alt/Üst MS — bahisçi line'ları varsa onları kullan
    if market_ou_lines:
        ou_lines = ou_value_analysis(exp_total, std_total, market_ou_lines)
    else:
        # Bahisçi line'ı yok: exp_total etrafında barem oluştur
        base_ou = round(exp_total / 2.5) * 2.5
        ou_lines = []
        for offset in [-12.5, -7.5, -2.5, 0, 2.5, 7.5, 12.5]:
            line = base_ou + offset
            if line <= 0:
                continue
            p_over = round(max(1.0, min(99.0, (1 - normal_cdf(line, exp_total, std_total)) * 100)), 1)
            ou_lines.append({
                "line": round(line, 1), "p_over": p_over, "p_under": round(100-p_over, 1),
                "our_p_over": p_over, "our_p_under": round(100-p_over, 1),
                "odd_over": None, "odd_under": None,
                "val_over": None, "val_under": None, "value_bet": None,
            })

    # Alt/Üst İY — bahisçi line'ları varsa onları kullan
    if market_iy_ou_lines:
        iy_ou_lines = ou_value_analysis(exp_iy_total, std_iy, market_iy_ou_lines)
    else:
        base_iy = round(exp_iy_total / 2.5) * 2.5
        iy_ou_lines = []
        for offset in [-7.5, -5.0, -2.5, 0, 2.5, 5.0, 7.5]:
            line = base_iy + offset
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

    # Outlier temizle, sonra normalize et
    hg_raw = _remove_outliers(home_general[:6]) if home_general else []
    ag_raw = _remove_outliers(away_general[:6]) if away_general else []
    hv_raw = _remove_outliers(home_venue[:6])   if home_venue   else []
    av_raw = _remove_outliers(away_venue[:6])   if away_venue   else []
    hg  = normalize_stats(hg_raw) if hg_raw else None
    ag  = normalize_stats(ag_raw) if ag_raw else None
    hv  = normalize_stats(hv_raw) if hv_raw else None
    av  = normalize_stats(av_raw) if av_raw else None

    # Madde 4: IY sinyali için Q1+Q2 bazlı ayrı normalize
    hg_iy = normalize_stats(home_general[:6], use_quarters_for_iy=True) if home_general else hg
    ag_iy = normalize_stats(away_general[:6], use_quarters_for_iy=True) if away_general else ag
    hv_iy = normalize_stats(home_venue[:6],   use_quarters_for_iy=True) if home_venue   else hv
    av_iy = normalize_stats(away_venue[:6],   use_quarters_for_iy=True) if away_venue   else av

    home_var = hg["variance"] if hg else STD_MS**2
    away_var = ag["variance"] if ag else STD_MS**2

    # API oranlarından implied değerler (bahisçi line'larını içeriyor)
    odds_implied = expected_from_odds(mo)

    # Beklenen skorlar — bahisçi line'ına kalibre edilmiş
    exp = compute_expected_scores(hg, ag, hv, av, odds_implied)

    # Madde 4: IY skorlarını Q1+Q2 bazlı stats ile override et
    exp_iy = compute_expected_scores(hg_iy, ag_iy, hv_iy, av_iy, odds_implied)
    exp["exp_iy_home"]  = exp_iy["exp_iy_home"]
    exp["exp_iy_away"]  = exp_iy["exp_iy_away"]
    exp["exp_iy_total"] = exp_iy["exp_iy_total"]

    # Olasılıklar — bahisçi line'larını barem olarak kullan
    probs = compute_probabilities(
        exp, home_var, away_var,
        market_ou_lines    = mo.get("ou_lines_market"),
        market_iy_ou_lines = mo.get("iy_ou_lines_market"),
    )

    p_home = probs["p_home_win"]
    p_away = probs["p_away_win"]
    prediction = "1" if p_home >= p_away else "2"

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
        "market_center":    exp.get("market_center"),
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
        "market_center":    None,
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
