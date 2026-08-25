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


def get_weekly_file(season_type, week_number, stat_type):
    return os.path.join(
        DATA_DIR,
        "weekly",
        season_type,
        f"week_{week_number}",
        f"{stat_type}.json"
    )


# =========================================================
# STORY DETECTOR
# =========================================================

def classify_game_story(game, team_map):

    home_id = str(game.get("homeTeamId"))
    away_id = str(game.get("awayTeamId"))

    home = team_map.get(home_id, {})
    away = team_map.get(away_id, {})

    home_score = game.get("homeScore", 0)
    away_score = game.get("awayScore", 0)

    home_ovr = home.get("overall")
    away_ovr = away.get("overall")

    stories = []

    result = {
        "schedule_id": game.get("scheduleId"),

        "away_team": away.get(
            "name",
            "Unknown"
        ),

        "home_team": home.get(
            "name",
            "Unknown"
        ),

        "away_score": away_score,
        "home_score": home_score,

        "away_overall": away_ovr,
        "home_overall": home_ovr,

        "stories": stories
    }

    # Pregame only
    if home_score == 0 and away_score == 0:

        if (
            home_ovr is not None
            and away_ovr is not None
        ):

            gap = abs(
                home_ovr - away_ovr
            )

            if gap == 0:

                stories.append({
                    "type": "even_matchup",
                    "severity": "medium",
                    "headline":
                        "Evenly matched teams"
                })

            elif gap >= 6:

                stories.append({
                    "type": "major_mismatch",
                    "severity": "high",
                    "headline":
                        "Major overall mismatch"
                })

            elif gap >= 4:

                stories.append({
                    "type": "clear_favorite",
                    "severity": "medium",
                    "headline":
                        "Clear favorite by overall"
                })

        if game.get(
            "isGameOfTheWeek",
            False
        ):

            stories.append({
                "type": "game_of_the_week",
                "severity": "high",
                "headline":
                    "Game of the Week"
            })

        if (
            home.get("user")
            or away.get("user")
        ):

            stories.append({
                "type": "user_game",
                "severity": "medium",
                "headline":
                    "User-controlled team involved"
            })

        return result

    # Postgame
    margin = abs(
        home_score - away_score
    )

    result["margin"] = margin

    if home_score > away_score:

        winner = home
        loser = away

        winner_score = home_score
        loser_score = away_score

        winner_location = "home"

    elif away_score > home_score:

        winner = away
        loser = home

        winner_score = away_score
        loser_score = home_score

        winner_location = "away"

    else:

        stories.append({
            "type": "tie",
            "severity": "medium",
            "headline":
                "Game ended in a tie"
        })

        return result

    result["winner"] = winner.get(
        "name",
        "Unknown"
    )

    result["loser"] = loser.get(
        "name",
        "Unknown"
    )

    result["winner_location"] = (
        winner_location
    )

    # Blowout logic
    if margin >= 30:

        stories.append({
            "type": "embarrassing_blowout",
            "severity": "critical",
            "headline":
                "Embarrassing blowout"
        })

    elif margin >= 20:

        stories.append({
            "type": "blowout",
            "severity": "high",
            "headline":
                "Lopsided loss"
        })

    elif margin <= 3:

        stories.append({
            "type": "nail_biter",
            "severity": "high",
            "headline":
                "Nail-biter"
        })

    elif margin <= 7:

        stories.append({
            "type": "close_game",
            "severity": "medium",
            "headline":
                "One-score game"
        })

    # Road win
    if winner_location == "away":

        stories.append({
            "type": "road_win",
            "severity": "medium",
            "headline":
                "Road victory"
        })

    # Upset
    winner_ovr = winner.get(
        "overall"
    )

    loser_ovr = loser.get(
        "overall"
    )

    if (
        winner_ovr is not None
        and loser_ovr is not None
        and winner_ovr < loser_ovr
    ):

        difference = (
            loser_ovr - winner_ovr
        )

        severity = "medium"

        if difference >= 5:
            severity = "high"

        if difference >= 8:
            severity = "critical"

        stories.append({
            "type": "upset",
            "severity": severity,
            "headline":
                "Upset victory",

            "overall_difference":
                difference
        })

    # Favorite failed badly
    if (
        winner_ovr is not None
        and loser_ovr is not None
    ):

        if (
            loser_ovr > winner_ovr
            and margin >= 14
        ):

            stories.append({
                "type":
                    "favorite_disappointment",

                "severity":
                    "high",

                "headline":
                    "Favorite failed to deliver"
            })

    # Shutout
    if loser_score == 0:

        stories.append({
            "type": "shutout",
            "severity": "high",
            "headline":
                "Shutout"
        })

    return result


# =========================================================
# PLAYER PERFORMANCE DETECTORS
# =========================================================

def qb_story(
    name,
    yards,
    touchdowns,
    interceptions,
    completion_pct=None
):

    stories = []

    grade = "C"

    if (
        touchdowns >= 5
        and interceptions == 0
    ):
        grade = "A+"

        stories.append({
            "type": "elite_qb_game",
            "severity": "critical",
            "headline":
                "Elite quarterback performance"
        })

    elif (
        touchdowns >= 4
        and interceptions <= 1
    ):
        grade = "A"

        stories.append({
            "type": "great_qb_game",
            "severity": "high",
            "headline":
                "Outstanding quarterback performance"
        })

    elif interceptions >= 4:

        grade = "F"

        stories.append({
            "type": "qb_disaster",
            "severity": "critical",
            "headline":
                "Quarterback disaster"
        })

    elif interceptions >= 3:

        grade = "D"

        stories.append({
            "type": "bad_qb_game",
            "severity": "high",
            "headline":
                "Turnover-heavy quarterback performance"
        })

    elif (
        yards < 150
        and touchdowns == 0
    ):

        grade = "D"

        stories.append({
            "type": "ineffective_qb_game",
            "severity": "medium",
            "headline":
                "Ineffective quarterback performance"
        })

    elif yards >= 350:

        grade = "A"

        stories.append({
            "type": "big_passing_game",
            "severity": "high",
            "headline":
                "Huge passing performance"
        })

    if (
        completion_pct is not None
        and completion_pct < 50
    ):

        stories.append({
            "type": "poor_accuracy",
            "severity": "medium",
            "headline":
                "Poor passing accuracy"
        })

    return {
        "player": name,
        "grade": grade,
        "stories": stories
    }


def rushing_story(
    name,
    yards,
    touchdowns,
    carries=None
):

    stories = []
    grade = "C"

    if yards >= 200:

        grade = "A+"

        stories.append({
            "type": "monster_rushing_game",
            "severity": "critical",
            "headline":
                "Monster rushing performance"
        })

    elif yards >= 150:

        grade = "A"

        stories.append({
            "type": "dominant_rushing_game",
            "severity": "high",
            "headline":
                "Dominant rushing performance"
        })

    elif (
        yards < 30
        and carries is not None
        and carries >= 10
    ):

        grade = "D"

        stories.append({
            "type": "poor_rushing_game",
            "severity": "medium",
            "headline":
                "Ineffective rushing performance"
        })

    if touchdowns >= 3:

        stories.append({
            "type": "multi_td_rushing_game",
            "severity": "high",
            "headline":
                "Three-touchdown rushing game"
        })

    return {
        "player": name,
        "grade": grade,
        "stories": stories
    }


def receiving_story(
    name,
    yards,
    touchdowns,
    receptions=None
):

    stories = []
    grade = "C"

    if yards >= 200:

        grade = "A+"

        stories.append({
            "type": "monster_receiving_game",
            "severity": "critical",
            "headline":
                "Monster receiving performance"
        })

    elif yards >= 150:

        grade = "A"

        stories.append({
            "type": "dominant_receiving_game",
            "severity": "high",
            "headline":
                "Dominant receiving performance"
        })

    elif (
        yards < 30
        and receptions is not None
        and receptions >= 4
    ):

        grade = "D"

        stories.append({
            "type": "quiet_receiving_game",
            "severity": "medium",
            "headline":
                "Disappointing receiving performance"
        })

    if touchdowns >= 3:

        stories.append({
            "type": "multi_td_receiving_game",
            "severity": "high",
            "headline":
                "Three-touchdown receiving game"
        })

    return {
        "player": name,
        "grade": grade,
        "stories": stories
    }


def defense_story(
    name,
    sacks=0,
    interceptions=0,
    forced_fumbles=0
):

    stories = []
    grade = "C"

    if sacks >= 4:

        grade = "A+"

        stories.append({
            "type": "historic_pass_rush",
            "severity": "critical",
            "headline":
                "Historic pass-rushing performance"
        })

    elif sacks >= 3:

        grade = "A"

        stories.append({
            "type": "dominant_pass_rush",
            "severity": "high",
            "headline":
                "Dominant pass-rushing performance"
        })

    if interceptions >= 2:

        grade = "A"

        stories.append({
            "type": "ballhawk_game",
            "severity": "high",
            "headline":
                "Ballhawk defensive performance"
        })

    if forced_fumbles >= 2:

        grade = "A"

        stories.append({
            "type": "turnover_creator",
            "severity": "high",
            "headline":
                "Turnover-forcing performance"
        })

    return {
        "player": name,
        "grade": grade,
        "stories": stories
    }


# =========================================================
# HOME / HEALTH
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service":
            "Project Madden Analytics"
    })


@app.route("/health")
def health():
    return jsonify({
        "online": True
    })


# =========================================================
# SNALLABOT RECEIVER
# =========================================================

@app.route(
    "/snallabot/<path:subpath>",
    methods=["GET", "POST", "PUT"]
)
def snallabot_receiver(subpath):

    if request.method == "GET":

        return jsonify({
            "working": True,
            "path": subpath
        })

    data = request.get_json(
        silent=True
    )

    if data is None:

        return jsonify({
            "success": False,
            "error": "No JSON received"
        }), 400

    parts = subpath.split("/")

    print(
        "PROJECT MADDEN EXPORT:",
        subpath
    )

    # League teams
    if parts[-1] == "leagueteams":

        with open(
            os.path.join(
                DATA_DIR,
                "leagueteams.json"
            ),
            "w"
        ) as f:

            json.dump(
                data,
                f,
                indent=2
            )

        return jsonify({
            "success": True,
            "type": "leagueteams"
        }), 200

    # Standings
    if parts[-1] == "standings":

        with open(
            os.path.join(
                DATA_DIR,
                "standings.json"
            ),
            "w"
        ) as f:

            json.dump(
                data,
                f,
                indent=2
            )

        return jsonify({
            "success": True,
            "type": "standings"
        }), 200

    # Extra
    if parts[-1] == "extra":

        with open(
            os.path.join(
                DATA_DIR,
                "extra.json"
            ),
            "w"
        ) as f:

            json.dump(
                data,
                f,
                indent=2
            )

        return jsonify({
            "success": True,
            "type": "extra"
        }), 200

    # Free agents
    if (
        "freeagents" in parts
        and parts[-1] == "roster"
    ):

        with open(
            os.path.join(
                DATA_DIR,
                "freeagents_roster.json"
            ),
            "w"
        ) as f:

            json.dump(
                data,
                f,
                indent=2
            )

        return jsonify({
            "success": True,
            "type": "freeagents"
        }), 200

    # Team rosters
    if (
        "team" in parts
        and parts[-1] == "roster"
    ):

        team_index = parts.index(
            "team"
        )

        team_id = parts[
            team_index + 1
        ]

        with open(
            os.path.join(
                DATA_DIR,
                f"roster_{team_id}.json"
            ),
            "w"
        ) as f:

            json.dump(
                data,
                f,
                indent=2
            )

        return jsonify({
            "success": True,
            "type": "roster",
            "team_id": team_id
        }), 200

    # Weekly data
    if "week" in parts:

        try:

            week_index = parts.index(
                "week"
            )

            season_type = parts[
                week_index + 1
            ]

            week_number = parts[
                week_index + 2
            ]

            stat_type = parts[
                week_index + 3
            ]

        except Exception:

            return jsonify({
                "success": False,
                "error":
                    "Invalid weekly export path"
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

            json.dump(
                data,
                f,
                indent=2
            )

        return jsonify({
            "success": True,
            "type": "weekly",
            "season_type":
                season_type,
            "week":
                week_number,
            "stat_type":
                stat_type
        }), 200

    return jsonify({
        "success": True,
        "type": "unknown",
        "path": subpath
    }), 200


# =========================================================
# STORY DETECTOR ENDPOINT
# =========================================================

@app.route(
    "/analytics/stories/"
    "<season_type>/"
    "<week_number>"
)
def weekly_stories(
    season_type,
    week_number
):

    filename = get_schedule_file(
        season_type,
        week_number
    )

    if not os.path.exists(
        filename
    ):

        return jsonify({
            "error":
                "Schedule data not found"
        }), 404

    with open(
        filename,
        "r"
    ) as f:

        schedule_data = json.load(
            f
        )

    team_map = get_team_map()

    games = schedule_data.get(
        "gameScheduleInfoList",
        []
    )

    stories = []

    for game in games:

        stories.append(
            classify_game_story(
                game,
                team_map
            )
        )

    important_stories = []

    for game_story in stories:

        for story in game_story[
            "stories"
        ]:

            if story[
                "severity"
            ] in [
                "high",
                "critical"
            ]:

                important_stories.append({
                    "away_team":
                        game_story[
                            "away_team"
                        ],

                    "home_team":
                        game_story[
                            "home_team"
                        ],

                    "story":
                        story
                })

    return jsonify({
        "league":
            "Project Madden",

        "season_type":
            season_type,

        "week":
            week_number,

        "important_story_count":
            len(
                important_stories
            ),

        "important_stories":
            important_stories,

        "games":
            stories
    })


# =========================================================
# PREVIEW ANALYST FEED
# =========================================================

@app.route(
    "/analyst/pregame/"
    "<season_type>/"
    "<week_number>"
)
def analyst_pregame(
    season_type,
    week_number
):

    filename = get_schedule_file(
        season_type,
        week_number
    )

    if not os.path.exists(
        filename
    ):

        return jsonify({
            "error":
                "Schedule data not found"
        }), 404

    with open(
        filename,
        "r"
    ) as f:

        schedule_data = json.load(
            f
        )

    team_map = get_team_map()

    games = schedule_data.get(
        "gameScheduleInfoList",
        []
    )

    feed = []

    for game in games:

        story_data = (
            classify_game_story(
                game,
                team_map
            )
        )

        if not story_data[
            "stories"
        ]:
            continue

        feed.append(
            story_data
        )

    return jsonify({
        "analyst":
            "Project Madden Analyst",

        "season_type":
            season_type,

        "week":
            week_number,

        "story_count":
            len(feed),

        "stories":
            feed
    })


# =========================================================
# MATCHUPS
# =========================================================

@app.route(
    "/analytics/matchups/"
    "<season_type>/"
    "<week_number>"
)
def weekly_matchups(
    season_type,
    week_number
):

    filename = get_schedule_file(
        season_type,
        week_number
    )

    if not os.path.exists(
        filename
    ):

        return jsonify({
            "error":
                "Schedule data not found"
        }), 404

    with open(
        filename,
        "r"
    ) as f:

        schedule_data = json.load(
            f
        )

    team_map = get_team_map()

    games = schedule_data.get(
        "gameScheduleInfoList",
        []
    )

    matchups = []

    for game in games:

        away_id = str(
            game.get(
                "awayTeamId"
            )
        )

        home_id = str(
            game.get(
                "homeTeamId"
            )
        )

        away = team_map.get(
            away_id,
            {}
        )

        home = team_map.get(
            home_id,
            {}
        )

        matchups.append({
            "away": {
                "team":
                    away.get(
                        "name",
                        "Unknown"
                    ),
                "overall":
                    away.get(
                        "overall"
                    ),
                "score":
                    game.get(
                        "awayScore",
                        0
                    )
            },

            "home": {
                "team":
                    home.get(
                        "name",
                        "Unknown"
                    ),
                "overall":
                    home.get(
                        "overall"
                    ),
                "score":
                    game.get(
                        "homeScore",
                        0
                    )
            },

            "status":
                game.get(
                    "status"
                )
        })

    return jsonify({
        "season_type":
            season_type,

        "week":
            week_number,

        "games":
            matchups
    })


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
