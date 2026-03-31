"""
BasketOracle - Value Hunting Motoru
İstatistiksel Öngörü (Son 6 Maç) + Piyasa Analizi
"""
import math

HOME_ADV        = 3.0
PAYOUT          = 0.90  # Güncellendi: %90
LEAGUE_AVG      = 108.0
IY_RATIO        = 0.47
STD_MS          = 12.0
STD_TOTAL       = 14.0

def normal_cdf(x, mean, std):
    if std <= 0: return 1.0 if x >= mean else 0.0
    z = (x - mean) / std
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def expected_from_odds(market_odds):
    mo = market_odds or {}; result = {}
    oh = mo.get("odd_home"); oa = mo.get("odd_away")
    if oh and oa and oh > 1.0 and oa > 1.0:
        ip_h = (1/oh)/PAYOUT; ip_a = (1/oa)/PAYOUT; total_ip = ip_h + ip_a
        p_h = ip_h/total_ip; p_a = ip_a/total_ip
        result["implied_spread"] = round((p_h - 0.5) * 2 * STD_MS if p_h > 0.5 else -(p_a - 0.5) * 2 * STD_MS, 1)
    
    ou_lines = mo.get("ou_lines_market", [])
    if ou_lines:
        ests = []
        for l in ou_lines:
            if l.get("odd_over") and l.get("odd_under"):
                p_o = (1/l["odd_over"]) / ((1/l["odd_over"]) + (1/l["odd_under"]))
                ests.append(l["line"] + (p_o - 0.5) * STD_TOTAL)
        if ests: result["implied_total"] = sum(ests)/len(ests)
    return result

def analyze_match(home_name, away_name, stats_dict, market_odds=None):
    hv = stats_dict.get("home_venue")
    av = stats_dict.get("away_venue")
    hg = stats_dict.get("home_general")
    ag = stats_dict.get("away_general")

    # --- SADECE İSTATİSTİKSEL ÖNGÖRÜ (API BAĞIMSIZ) ---
    s_h = (hv["avg_scored"] + av["avg_conceded"])/2 + (HOME_ADV/2) if hv and av else (hg["avg_scored"] + ag["avg_conceded"])/2
    s_a = (av["avg_scored"] + hv["avg_conceded"])/2 - (HOME_ADV/2) if hv and av else (ag["avg_scored"] + hg["avg_conceded"])/2
    
    s_total = s_h + s_a
    s_hcap  = s_a - s_h
    s_iy_h  = s_h * IY_RATIO
    s_iy_a  = s_a * IY_RATIO

    # --- HİBRİT ANALİZ (MEVCUT SİSTEM) ---
    odds_imp = expected_from_odds(market_odds)
    exp_total = odds_imp.get("implied_total", s_total)
    exp_spread = odds_imp.get("implied_spread", s_h - s_a)
    
    exp_h = (exp_total + exp_spread) / 2
    exp_a = (exp_total - exp_spread) / 2

    return {
        "match": f"{home_name} vs {away_name}",
        "prob_home": round(normal_cdf(0, exp_a - exp_h, STD_MS * 1.41) * 100, 1),
        "prob_away": round((1 - normal_cdf(0, exp_a - exp_h, STD_MS * 1.41)) * 100, 1),
        
        # YENİ EKLENEN İSTATİSTİK BAREMLERİ
        "system_prediction": {
            "exp_ms": f"{round(s_h, 1)} - {round(s_a, 1)}",
            "exp_ms_total": round(s_total, 1),
            "exp_iy": f"{round(s_iy_h, 1)} - {round(s_iy_a, 1)}",
            "exp_iy_total": round(s_iy_h + s_iy_a, 1),
            "exp_hcap": f"{'+' if s_hcap > 0 else ''}{round(s_hcap, 1)}"
        },
        
        "exp_home": round(exp_h, 1),
        "exp_away": round(exp_a, 1),
        "exp_total": round(exp_total, 1),
        "ou_lines": [{"line": l, "p_over": round((1-normal_cdf(l, exp_total, STD_TOTAL))*100, 1)} for l in [155.5, 165.5, 175.5, 185.5]]
    }
