from flask import Flask, render_template, jsonify, request
import basketball_api as bapi
import value_hunting_basket as vh

app = Flask(__name__)

@app.route("/api/analyze/<int:home_id>/<int:away_id>")
def api_analyze(home_id, away_id):
    try:
        # Verileri çek
        stats = bapi.get_team_stats_for_match(home_id, away_id)
        odds  = bapi.get_odds_for_match(request.args.get("match_id"))
        
        # Analiz et (Yeni system_prediction objesi burada üretilir)
        analysis = vh.analyze_match(stats["home_name"], stats["away_name"], stats, odds)
        
        return jsonify({"success": True, "analysis": analysis})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == "__main__":
    app.run(debug=True)
