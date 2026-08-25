from flask import Flask, request, jsonify
import json
import os

app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


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
            "division": team.get("divName"),
            "overall": team.get("ovrRating"),
            "user": team.get("userName")
        }

    return team_map


@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "Project Madden Analytics"
    })


@app.route("/health")
def health():
    return jsonify({
        "online": True
    })


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

    print("PROJECT MADDEN EXPORT:", subpath)

    # League teams
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

    # Standings
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

    # Extra
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

    # Free agents
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

    # Team rosters
    if "team" in parts and parts[-1] == "roster":
        team_index = parts.index("team")
        team_id = parts[team_index + 1]

        players = data.get("rosterInfoList", [])

        with open(
            os.path.join(DATA_DIR, f"roster_{team_id}.json"),
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

    # Weekly data
    if "week" in parts:
        week_index = parts.index("week")

        season_type = parts[week_index + 1]
        week_number = parts[week_index + 2]
        stat_type = parts[week_index + 3]

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

    return jsonify({
        "success": True,
        "type": "unknown",
        "path": subpath
    }), 200


@app.route("/analytics/matchups/<season_type>/<week_number>")
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

        home_id = str(game.get("homeTeamId"))
        away_id = str(game.get("awayTeamId"))

        home_team = team_map.get(home_id, {})
        away_team = team_map.get(away_id, {})

        matchups.append({
            "schedule_id": game.get("scheduleId"),

            "away": {
                "id": game.get("awayTeamId"),
                "team": away_team.get("name", "Unknown"),
                "abbr": away_team.get("abbr", ""),
                "city": away_team.get("city", ""),
                "overall": away_team.get("overall"),
                "score": game.get("awayScore", 0)
            },

            "home": {
                "id": game.get("homeTeamId"),
                "team": home_team.get("name", "Unknown"),
                "abbr": home_team.get("abbr", ""),
                "city": home_team.get("city", ""),
                "overall": home_team.get("overall"),
                "score": game.get("homeScore", 0)
            },

            "status": game.get("status"),
            "game_of_the_week": game.get(
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
            "error": "Data not found"
        }), 404

    with open(filename, "r") as f:
        data = json.load(f)

    return jsonify(data)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
