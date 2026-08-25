@app.route("/analytics/week/<season_type>/<week_number>/<stat_type>")
def view_weekly_data(season_type, week_number, stat_type):

    allowed_types = [
        "schedules",
        "teamstats",
        "passing",
        "rushing",
        "receiving",
        "defense",
        "kicking"
    ]

    if stat_type not in allowed_types:
        return jsonify({
            "error": "Invalid stat type"
        }), 400

    filename = os.path.join(
        DATA_DIR,
        "weekly",
        season_type,
        f"week_{week_number}",
        f"{stat_type}.json"
    )

    if not os.path.exists(filename):
        return jsonify({
            "error": "Data not found",
            "file": filename
        }), 404

    with open(filename, "r") as f:
        data = json.load(f)

    return jsonify(data)
