"""
BasketOracle - Value Hunting Motoru
Basketbol tahmin motoru:
- Maç sonucu (1/2)
- Handikap (-3.5 / +3.5 gibi)
- Alt/Üst (toplam skor)
- İY sonucu ve Alt/Üst
"""
import math

# ════════════════════════════════════════════════
# SABITLER
# ════════════════════════════════════════════════
HOME_ADV    = 3.5    # Ev sahibi handikap avantajı (basketbol ortalaması)
PAYOUT      = 0.92   # %92 payout
LEAGUE_AVG  = 105.0  # Ortalama maç skoru (toplam)
IY_RATIO    = 0.48   # İlk yarı / toplam oran
STD_DEV     = 12.0   # Basketbol skor standart sapması


def safe_avg(lst, key, default=LEAGUE_AVG/2):
    vals = [x[key] for x in lst if x.get(key) is not None]
    return sum(vals) / len(vals) if vals else default


def normalize_stats(matches, is_home_team):
    """Son maçlardan temel istatistikleri hesapla."""
    if not matches:
        return {
            "avg_scored": LEAGUE_AVG / 2,
            "avg_conceded": LEAGUE_AVG / 2,
            "avg_total": LEAGUE_AVG,
            "avg_iy_scored": LEAGUE_AVG * IY_RATIO / 2,
            "avg_iy_conceded": LEAGUE_AVG * IY_RATIO / 2,
            "win_rate": 0.5,
            "variance": STD_DEV ** 2,
            "n": 0,
        }

    scored    = [m["scored"] for m in matches]
    conceded  = [m["conceded"] for m in matches]
    totals    = [m["total"] for m in matches]
    iy_s      = [m["iy_scored"] for m in matches if m.get("iy_scored")]
    iy_c      = [m["iy_conceded"] for m in matches if m.get("iy_conceded")]
    wins      = sum(1 for m in matches if m["win"])

    avg_sc  = sum(scored) / len(scored)
    avg_co  = sum(conceded) / len(conceded)
    avg_tot = sum(totals) / len(totals)
    avg_iy_s = sum(iy_s) / len(iy_s) if iy_s else avg_sc * IY_RATIO
    avg_iy_c = sum(iy_c) / len(iy_c) if iy_c else avg_co * IY_RATIO

    # Varyans
    if len(scored) > 1:
        mean_s = avg_sc
        variance = sum((x - mean_s) ** 2 for x in scored) / len(scored)
    else:
        variance = STD_DEV ** 2

    return {
        "avg_scored":     avg_sc,
        "avg_conceded":   avg_co,
        "avg_total":      avg_tot,
        "avg_iy_scored":  avg_iy_s,
        "avg_iy_conceded":avg_iy_c,
        "win_rate":       wins / len(matches),
        "variance":       variance,
        "n":              len(matches),
    }


def compute_expected_scores(home_stats, away_stats,
                             home_venue_stats, away_venue_stats):
    """Beklenen skorları hesapla."""
    # Genel saldırı/savunma gücü
    home_attack  = (home_stats["avg_scored"]   + home_venue_stats["avg_scored"])   / 2
    home_defense = (home_stats["avg_conceded"] + home_venue_stats["avg_conceded"]) / 2
    away_attack  = (away_stats["avg_scored"]   + away_venue_stats["avg_scored"])   / 2
    away_defense = (away_stats["avg_conceded"] + away_venue_stats["avg_conceded"]) / 2

    # Beklenen skor = kendi saldırısı + rakip savunma zafiyeti
    exp_home = (home_attack + (LEAGUE_AVG / 2 - away_defense)) / 2 + HOME_ADV / 2
    exp_away = (away_attack + (LEAGUE_AVG / 2 - home_defense)) / 2 - HOME_ADV / 2

    # Makul sınırlar
    exp_home = max(70, min(140, exp_home))
    exp_away = max(70, min(140, exp_away))

    exp_total  = exp_home + exp_away
    exp_iy_home = exp_home * IY_RATIO
    exp_iy_away = exp_away * IY_RATIO
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
    """Normal dağılım CDF."""
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / std
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def compute_probabilities(exp, home_var, away_var):
    """Olasılıkları hesapla."""
    exp_home  = exp["exp_home"]
    exp_away  = exp["exp_away"]
    exp_total = exp["exp_total"]
    spread    = exp["spread"]

    std_home  = max(8, math.sqrt(home_var))
    std_away  = max(8, math.sqrt(away_var))
    std_diff  = math.sqrt(std_home**2 + std_away**2)
    std_total = math.sqrt(std_home**2 + std_away**2)

    # Maç sonucu
    # P(ev kazanır) = P(ev - dep > 0)
    p_home_win = 1 - normal_cdf(0, spread, std_diff)
    p_away_win = normal_cdf(-0.5, spread, std_diff)
    # Basketbolda beraberlik nadiren olur ama OT var
    p_draw     = max(0.02, 1 - p_home_win - p_away_win)

    # Normalize
    total = p_home_win + p_draw + p_away_win
    p_home_win /= total
    p_draw     /= total
    p_away_win /= total

    # Handikap çizgileri — spread'e göre dinamik
    base_hcap = round(spread / 2.5) * 2.5
    handicap_lines = []
    for offset in [-10, -6.5, -3.5, 0, 3.5, 6.5, 10]:
        hcap = base_hcap + offset - 0.5
        p_home_cover = 1 - normal_cdf(hcap, spread, std_diff)
        handicap_lines.append({
            "line": round(hcap, 1),
            "p_home_cover": round(p_home_cover * 100, 1),
            "p_away_cover": round((1 - p_home_cover) * 100, 1),
        })

    # Alt/Üst olasılıkları — beklenen skora göre dinamik çizgiler
    # Beklenen toplam skorun etrafında 6 çizgi oluştur
    base_ou = round(exp_total / 5) * 5  # 5'in katına yuvarla
    ou_lines = []
    for offset in [-12.5, -7.5, -2.5, 2.5, 7.5, 12.5]:
        line = base_ou + offset
        p_over = 1 - normal_cdf(line, exp_total, std_total)
        ou_lines.append({
            "line":    round(line, 1),
            "p_over":  round(p_over * 100, 1),
            "p_under": round((1 - p_over) * 100, 1),
        })

    # İY Alt/Üst — beklenen IY skoruna göre dinamik
    exp_iy_total = exp["exp_iy_total"]
    base_iy = round(exp_iy_total / 2.5) * 2.5
    iy_ou_lines = []
    for offset in [-7.5, -5.0, -2.5, 2.5, 5.0, 7.5]:
        line = base_iy + offset
        p_over = 1 - normal_cdf(line, exp_iy_total, std_total * IY_RATIO)
        iy_ou_lines.append({
            "line":    round(line, 1),
            "p_over":  round(p_over * 100, 1),
            "p_under": round((1 - p_over) * 100, 1),
        })

    # İY sonucu
    iy_spread   = exp["exp_iy_home"] - exp["exp_iy_away"]
    std_iy_diff = std_diff * IY_RATIO
    p_iy_home   = 1 - normal_cdf(0, iy_spread, std_iy_diff)
    p_iy_away   = normal_cdf(-0.5, iy_spread, std_iy_diff)
    p_iy_draw   = max(0.05, 1 - p_iy_home - p_iy_away)
    iy_total_p  = p_iy_home + p_iy_draw + p_iy_away
    p_iy_home  /= iy_total_p
    p_iy_draw  /= iy_total_p
    p_iy_away  /= iy_total_p

    return {
        "p_home_win":    round(p_home_win * 100, 1),
        "p_draw":        round(p_draw * 100, 1),
        "p_away_win":    round(p_away_win * 100, 1),
        "p_iy_home":     round(p_iy_home * 100, 1),
        "p_iy_draw":     round(p_iy_draw * 100, 1),
        "p_iy_away":     round(p_iy_away * 100, 1),
        "handicap_lines":handicap_lines,
        "ou_lines":      ou_lines,
        "iy_ou_lines":   iy_ou_lines,
    }


def prob_to_odd(prob_pct, payout=PAYOUT):
    if prob_pct <= 0:
        return 15.0
    odd = round((100 / prob_pct) / payout, 2)
    return min(odd, 15.0)


def compute_ratings(data):
    """G ve İD rating hesapla."""
    def rating(matches, limit=6):
        last = matches[:limit]
        return sum(m["scored"] - m["conceded"] for m in last)

    hg = rating(data.get("home_general", []))
    ag = rating(data.get("away_general", []))
    hi = rating(data.get("home_venue", []))
    ai = rating(data.get("away_venue", []))

    return {
        "g_rating":       hg - ag,
        "id_rating":      hi - ai,
        "home_g_rating":  hg,
        "away_g_rating":  ag,
        "home_id_rating": hi,
        "away_id_rating": ai,
    }


def run_value_hunting(home_general, home_venue, away_general, away_venue):
    """Ana analiz fonksiyonu."""
    hg = normalize_stats(home_general, True)
    av = normalize_stats(away_general, False)
    hv = normalize_stats(home_venue,   True)
    avv= normalize_stats(away_venue,   False)

    exp   = compute_expected_scores(hg, av, hv, avv)
    probs = compute_probabilities(exp, hg["variance"], av["variance"])

    # Ana tahmin
    if probs["p_home_win"] >= probs["p_away_win"]:
        prediction  = "1"
        pred_prob   = probs["p_home_win"]
    else:
        prediction  = "2"
        pred_prob   = probs["p_away_win"]

    # En iyi Alt/Üst tahmini (en yakın %50'ye uzak olan)
    best_ou = max(probs["ou_lines"], key=lambda x: abs(x["p_over"] - 50))
    best_iy_ou = max(probs["iy_ou_lines"], key=lambda x: abs(x["p_over"] - 50))

    # En iyi handikap tahmini
    best_hcap = max(probs["handicap_lines"], key=lambda x: abs(x["p_home_cover"] - 50))

    # Güven skoru
    confidence = min(99, int(pred_prob * 0.8 + abs(exp["spread"]) * 0.5 + 20))

    return {
        "prediction":    prediction,
        "confidence":    confidence,
        "prob_home":     probs["p_home_win"],
        "prob_draw":     probs["p_draw"],
        "prob_away":     probs["p_away_win"],
        "odd_home":      prob_to_odd(probs["p_home_win"]),
        "odd_draw":      prob_to_odd(probs["p_draw"]),
        "odd_away":      prob_to_odd(probs["p_away_win"]),
        "exp_home":      exp["exp_home"],
        "exp_away":      exp["exp_away"],
        "exp_total":     exp["exp_total"],
        "spread":        exp["spread"],
        # Handikap
        "hcap_line":     best_hcap["line"],
        "hcap_home_pct": best_hcap["p_home_cover"],
        "hcap_away_pct": best_hcap["p_away_cover"],
        "handicap_lines":probs["handicap_lines"],
        # Alt/Üst
        "ou_line":       best_ou["line"],
        "ou_over_pct":   best_ou["p_over"],
        "ou_under_pct":  best_ou["p_under"],
        "ou_lines":      probs["ou_lines"],
        # İY
        "iy_prob_home":  probs["p_iy_home"],
        "iy_prob_draw":  probs["p_iy_draw"],
        "iy_prob_away":  probs["p_iy_away"],
        "iy_ou_line":    best_iy_ou["line"],
        "iy_ou_over":    best_iy_ou["p_over"],
        "iy_ou_under":   best_iy_ou["p_under"],
        "iy_ou_lines":   probs["iy_ou_lines"],
    }


def fallback_result():
    return {
        "prediction":    "1",
        "confidence":    40,
        "prob_home":     40.0,
        "prob_draw":     20.0,
        "prob_away":     40.0,
        "odd_home":      2.5,
        "odd_draw":      5.0,
        "odd_away":      2.5,
        "exp_home":      round(LEAGUE_AVG/2 + HOME_ADV/2, 1),
        "exp_away":      round(LEAGUE_AVG/2 - HOME_ADV/2, 1),
        "exp_total":     LEAGUE_AVG,
        "spread":        HOME_ADV,
        "hcap_line":     -4.5,
        "hcap_home_pct": 52.0,
        "hcap_away_pct": 48.0,
        "handicap_lines":[],
        "ou_line":       round(LEAGUE_AVG + 0.5, 1),
        "ou_over_pct":   50.0,
        "ou_under_pct":  50.0,
        "ou_lines":      [],
        "iy_prob_home":  40.0,
        "iy_prob_draw":  20.0,
        "iy_prob_away":  40.0,
        "iy_ou_line":    round(LEAGUE_AVG * IY_RATIO + 0.5, 1),
        "iy_ou_over":    50.0,
        "iy_ou_under":   50.0,
        "iy_ou_lines":   [],
    }
