@app.route(
    "/analytics/matchups/<season_type>/<week_number>"
)
def weekly_matchups(season_type, week_number):

    filename = os.path.join(
        DATA_DIR,
        "weekly",
        season_type,
        f"week_{week_number}",
        "schedules.json"
    )

    if not os.path.exists(filename):

        return jsonify({
            "error": "Schedule data not found"
        }), 404

    with open(filename, "r") as f:
        schedule_data = json.load(f)

    team_map = get_team_map()

    games = schedule_data.get(
        "gameScheduleInfoList",
        []
    )

    matchups = []

    for game in games:

        home_id = str(
            game.get("homeTeamId")
        )

        away_id = str(
            game.get("awayTeamId")
        )

        home_team = team_map.get(
            home_id,
            {}
        )

        away_team = team_map.get(
            away_id,
            {}
        )

        home_score = game.get(
            "homeScore",
            0
        )

        away_score = game.get(
            "awayScore",
            0
        )

        matchups.append({
            "schedule_id": game.get(
                "scheduleId"
            ),

            "away": {
                "id": game.get(
                    "awayTeamId"
                ),
                "team": away_team.get(
                    "name",
                    "Unknown"
                ),
                "city": away_team.get(
                    "city",
                    ""
                ),
                "abbr": away_team.get(
                    "abbr",
                    ""
                ),
                "overall": away_team.get(
                    "overall"
                ),
                "score": away_score
            },

            "home": {
                "id": game.get(
                    "homeTeamId"
                ),
                "team": home_team.get(
                    "name",
                    "Unknown"
                ),
                "city": home_team.get(
                    "city",
                    ""
                ),
                "abbr": home_team.get(
                    "abbr",
                    ""
                ),
                "overall": home_team.get(
                    "overall"
                ),
                "score": home_score
            },

            "status": game.get(
                "status"
            ),

            "game_of_the_week":
                game.get(
                    "isGameOfTheWeek",
                    False
                )
        })

    return jsonify({
        "season_type": season_type,
        "week": week_number,
        "game_count": len(matchups),
        "games": matchups
    })
