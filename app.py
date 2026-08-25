from flask import Flask, request, jsonify
import json
import os

app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


# =========================================================
# HELPERS
# =========================================================

def load_json_file(filename):
    path = os.path.join(DATA_DIR, filename)

    if not os.path.exists(path):
        return None

    with open(path, "r") as f:
        return json.load(f)


def get_team_map():
    data = load_json_file("leagueteams.json")

    if not data:
        return {}

    teams = data.get("leagueTeamInfoList", [])

    team_map = {}

    for team in teams:
        team_map[str(team.get("teamId"))] = {
            "teamId": team.get("teamId"),
            "abbr": team.get("abbrName"),
            "city": team.get("cityName"),
            "name": team.get("displayName"),
            "nickname": team.get("nickName"),
            "division": team.get("divName"),
            "overall": team.get("ovrRating"),
            "injuries": team.get("injuryCount", 0),
            "user": team.get("userName", "")
        }

    return team_map


def get_schedule_file(season_type, week_number):
    return os.path.join(
        DATA_DIR,
        "weekly",
        season_type,
        f"week_{week_number}",
        "schedules.json"
    )


# =========================================================
# HOME / HEALTH
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "Project Madden Analytics"
    })


@app.route("/health")
def health():
    return jsonify({
        "online": True,
        "service": "Project Madden Analytics"
    })


# =========================================================
# SNALLABOT RECEIVER
# =========================================================

@app.route("/snallabot/<path:subpath>", methods=["GET", "POST", "PUT"])
def snallabot_receiver(subpath):

    if request.method == "GET":
        return jsonify({
            "working": True,
            "path": subpath
        })

    data = request.get_json(silent=True)

    if data is None:
        return jsonify({
            "success": False,
            "error": "No JSON received"
        }), 400

    parts = subpath.split("/")

    print("")
    print("========================================")
    print("PROJECT MADDEN EXPORT")
    print("Path:", subpath)
    print("========================================")

    # -----------------------------------------------------
    # LEAGUE TEAMS
    # -----------------------------------------------------

    if parts[-1] == "leagueteams":

        with open(
            os.path.join(DATA_DIR, "leagueteams.json"),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        print("Saved: LEAGUE TEAMS")

        return jsonify({
            "success": True,
            "type": "leagueteams"
        }), 200

    # -----------------------------------------------------
    # STANDINGS
    # -----------------------------------------------------

    if parts[-1] == "standings":

        with open(
            os.path.join(DATA_DIR, "standings.json"),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        print("Saved: STANDINGS")

        return jsonify({
            "success": True,
            "type": "standings"
        }), 200

    # -----------------------------------------------------
    # EXTRA DATA
    # -----------------------------------------------------

    if parts[-1] == "extra":

        with open(
            os.path.join(DATA_DIR, "extra.json"),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        print("Saved: EXTRA DATA")

        return jsonify({
            "success": True,
            "type": "extra"
        }), 200

    # -----------------------------------------------------
    # FREE AGENTS
    # -----------------------------------------------------

    if "freeagents" in parts and parts[-1] == "roster":

        players = data.get("rosterInfoList", [])

        with open(
            os.path.join(DATA_DIR, "freeagents_roster.json"),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        print(
            f"Saved free agents roster | "
            f"{len(players)} players"
        )

        return jsonify({
            "success": True,
            "type": "freeagents",
            "player_count": len(players)
        }), 200

    # -----------------------------------------------------
    # TEAM ROSTERS
    # -----------------------------------------------------

    if "team" in parts and parts[-1] == "roster":

        team_index = parts.index("team")
        team_id = parts[team_index + 1]

        players = data.get("rosterInfoList", [])

        with open(
            os.path.join(
                DATA_DIR,
                f"roster_{team_id}.json"
            ),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        print(
            f"Saved roster: Team {team_id} | "
            f"{len(players)} players"
        )

        return jsonify({
            "success": True,
            "type": "roster",
            "team_id": team_id,
            "player_count": len(players)
        }), 200

    # -----------------------------------------------------
    # WEEKLY DATA
    # -----------------------------------------------------

    if "week" in parts:

        try:
            week_index = parts.index("week")

            season_type = parts[week_index + 1]
            week_number = parts[week_index + 2]
            stat_type = parts[week_index + 3]

        except Exception:
            return jsonify({
                "success": False,
                "error": "Invalid weekly export path",
                "path": subpath
            }), 400

        weekly_dir = os.path.join(
            DATA_DIR,
            "weekly",
            season_type,
            f"week_{week_number}"
        )

        os.makedirs(
            weekly_dir,
            exist_ok=True
        )

        with open(
            os.path.join(
                weekly_dir,
                f"{stat_type}.json"
            ),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        print(
            f"Saved weekly data | "
            f"{season_type.upper()} "
            f"Week {week_number} | "
            f"{stat_type}"
        )

        return jsonify({
            "success": True,
            "type": "weekly",
            "season_type": season_type,
            "week": week_number,
            "stat_type": stat_type
        }), 200

    # -----------------------------------------------------
    # UNKNOWN
    # -----------------------------------------------------

    print("Unknown export:", subpath)

    return jsonify({
        "success": True,
        "type": "unknown",
        "path": subpath
    }), 200


# =========================================================
# RAW WEEKLY DATA
# =========================================================

@app.route("/analytics/week/<season_type>/<week_number>/<stat_type>")
def weekly_data(season_type, week_number, stat_type):

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


# =========================================================
# MATCHUPS
# =========================================================

@app.route("/analytics/matchups/<season_type>/<week_number>")
def weekly_matchups(season_type, week_number):

    filename = get_schedule_file(
        season_type,
        week_number
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

        away_id = str(
            game.get("awayTeamId")
        )

        home_id = str(
            game.get("homeTeamId")
        )

        away_team = team_map.get(
            away_id,
            {}
        )

        home_team = team_map.get(
            home_id,
            {}
        )

        matchups.append({
            "schedule_id": game.get("scheduleId"),
            "status": game.get("status"),

            "game_of_the_week": game.get(
                "isGameOfTheWeek",
                False
            ),

            "away": {
                "id": game.get("awayTeamId"),
                "abbr": away_team.get("abbr"),
                "city": away_team.get("city"),
                "team": away_team.get("name", "Unknown"),
                "division": away_team.get("division"),
                "overall": away_team.get("overall"),
                "injuries": away_team.get("injuries"),
                "user": away_team.get("user"),
                "score": game.get("awayScore", 0)
            },

            "home": {
                "id": game.get("homeTeamId"),
                "abbr": home_team.get("abbr"),
                "city": home_team.get("city"),
                "team": home_team.get("name", "Unknown"),
                "division": home_team.get("division"),
                "overall": home_team.get("overall"),
                "injuries": home_team.get("injuries"),
                "user": home_team.get("user"),
                "score": game.get("homeScore", 0)
            }
        })

    return jsonify({
        "season_type": season_type,
        "week": week_number,
        "game_count": len(matchups),
        "games": matchups
    })


# =========================================================
# PREGAME AROUND THE LEAGUE
# =========================================================

@app.route("/analytics/pregame/<season_type>/<week_number>")
def pregame_analytics(season_type, week_number):

    filename = get_schedule_file(
        season_type,
        week_number
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

    previews = []

    for game in games:

        away_id = str(
            game.get("awayTeamId")
        )

        home_id = str(
            game.get("homeTeamId")
        )

        away = team_map.get(
            away_id,
            {}
        )

        home = team_map.get(
            home_id,
            {}
        )

        away_ovr = away.get("overall")
        home_ovr = home.get("overall")

        ovr_gap = None
        favorite = "Even"
        favorite_location = None

        if (
            away_ovr is not None
            and home_ovr is not None
        ):

            ovr_gap = abs(
                away_ovr - home_ovr
            )

            if away_ovr > home_ovr:
                favorite = away.get(
                    "name",
                    "Away"
                )

                favorite_location = "away"

            elif home_ovr > away_ovr:
                favorite = home.get(
                    "name",
                    "Home"
                )

                favorite_location = "home"

        division_game = (
            away.get("division")
            == home.get("division")
            and away.get("division") is not None
        )

        user_game = bool(
            away.get("user")
            or home.get("user")
        )

        user_vs_user = bool(
            away.get("user")
            and home.get("user")
        )

        preview = {
            "schedule_id": game.get("scheduleId"),

            "away": {
                "abbr": away.get("abbr"),
                "team": away.get(
                    "name",
                    "Unknown"
                ),
                "overall": away_ovr,
                "injuries": away.get(
                    "injuries",
                    0
                ),
                "user": away.get(
                    "user",
                    ""
                )
            },

            "home": {
                "abbr": home.get("abbr"),
                "team": home.get(
                    "name",
                    "Unknown"
                ),
                "overall": home_ovr,
                "injuries": home.get(
                    "injuries",
                    0
                ),
                "user": home.get(
                    "user",
                    ""
                )
            },

            "overall_gap": ovr_gap,

            "favorite_by_overall": favorite,

            "favorite_location":
                favorite_location,

            "division_game":
                division_game,

            "user_game":
                user_game,

            "user_vs_user":
                user_vs_user,

            "game_of_the_week":
                game.get(
                    "isGameOfTheWeek",
                    False
                )
        }

        previews.append(preview)

    # -----------------------------------------------------
    # BIGGEST OVR GAP
    # -----------------------------------------------------

    games_with_ovr = [
        game
        for game in previews
        if game["overall_gap"] is not None
    ]

    biggest_mismatch = None
    closest_matchup = None
    highest_rated_matchup = None

    if games_with_ovr:

        biggest_mismatch = max(
            games_with_ovr,
            key=lambda g:
                g["overall_gap"]
        )

        closest_matchup = min(
            games_with_ovr,
            key=lambda g:
                g["overall_gap"]
        )

        highest_rated_matchup = max(
            games_with_ovr,
            key=lambda g:
                (
                    g["away"]["overall"]
                    + g["home"]["overall"]
                )
        )

    # -----------------------------------------------------
    # ROAD FAVORITES
    # -----------------------------------------------------

    road_favorites = [
        game
        for game in previews
        if game[
            "favorite_location"
        ] == "away"
    ]

    # -----------------------------------------------------
    # HOME FAVORITES
    # -----------------------------------------------------

    home_favorites = [
        game
        for game in previews
        if game[
            "favorite_location"
        ] == "home"
    ]

    # -----------------------------------------------------
    # EVEN MATCHUPS
    # -----------------------------------------------------

    even_matchups = [
        game
        for game in previews
        if game[
            "favorite_by_overall"
        ] == "Even"
    ]

    # -----------------------------------------------------
    # DIVISION GAMES
    # -----------------------------------------------------

    division_games = [
        game
        for game in previews
        if game["division_game"]
    ]

    # -----------------------------------------------------
    # USER GAMES
    # -----------------------------------------------------

    user_games = [
        game
        for game in previews
        if game["user_game"]
    ]

    user_vs_user_games = [
        game
        for game in previews
        if game["user_vs_user"]
    ]

    # -----------------------------------------------------
    # GAME OF THE WEEK
    # -----------------------------------------------------

    game_of_the_week = [
        game
        for game in previews
        if game["game_of_the_week"]
    ]

    return jsonify({
        "league": "Project Madden",

        "season_type": season_type,

        "week": week_number,

        "game_count": len(previews),

        "featured": {
            "highest_rated_matchup":
                highest_rated_matchup,

            "biggest_overall_mismatch":
                biggest_mismatch,

            "closest_overall_matchup":
                closest_matchup
        },

        "home_favorites":
            home_favorites,

        "road_favorites":
            road_favorites,

        "even_matchups":
            even_matchups,

        "division_games":
            division_games,

        "user_games":
            user_games,

        "user_vs_user_games":
            user_vs_user_games,

        "game_of_the_week":
            game_of_the_week,

        "all_matchups":
            previews
    })


# =========================================================
# CLEAN TEAM LIST
# =========================================================

@app.route("/analytics/teams")
def analytics_teams():

    team_map = get_team_map()

    return jsonify({
        "team_count": len(team_map),
        "teams": team_map
    })


# =========================================================
# RAW STANDINGS
# =========================================================

@app.route("/analytics/standings")
def analytics_standings():

    data = load_json_file(
        "standings.json"
    )

    if not data:
        return jsonify({
            "error":
                "Standings data not found"
        }), 404

    return jsonify(data)


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
