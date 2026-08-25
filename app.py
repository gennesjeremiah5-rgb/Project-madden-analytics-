from flask import Flask, request, jsonify
import json
import os
import random

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
# GAME STORY DETECTOR
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

        "away_abbr": away.get("abbr"),
        "home_abbr": home.get("abbr"),

        "away_score": away_score,
        "home_score": home_score,

        "away_overall": away_ovr,
        "home_overall": home_ovr,

        "away_user": away.get("user", ""),
        "home_user": home.get("user", ""),

        "stories": []
    }

    stories = result["stories"]

    # =====================================================
    # PREGAME
    # =====================================================

    if home_score == 0 and away_score == 0:

        if (
            home_ovr is not None
            and away_ovr is not None
        ):
            gap = abs(home_ovr - away_ovr)

            if gap == 0:
                stories.append({
                    "type": "even_matchup",
                    "severity": "medium",
                    "headline": "Evenly matched teams"
                })

            elif gap >= 6:
                stories.append({
                    "type": "major_mismatch",
                    "severity": "high",
                    "headline": "Major overall mismatch"
                })

            elif gap >= 4:
                stories.append({
                    "type": "clear_favorite",
                    "severity": "medium",
                    "headline": "Clear favorite by overall"
                })

        if game.get("isGameOfTheWeek", False):
            stories.append({
                "type": "game_of_the_week",
                "severity": "high",
                "headline": "Game of the Week"
            })

        if home.get("user") or away.get("user"):
            stories.append({
                "type": "user_game",
                "severity": "medium",
                "headline": "User-controlled team involved"
            })

        return result

    # =====================================================
    # POSTGAME
    # =====================================================

    margin = abs(home_score - away_score)

    result["margin"] = margin

    if home_score > away_score:
        winner = home
        loser = away
        winner_location = "home"
        loser_score = away_score

    elif away_score > home_score:
        winner = away
        loser = home
        winner_location = "away"
        loser_score = home_score

    else:
        result["winner"] = "Tie"

        stories.append({
            "type": "tie",
            "severity": "medium",
            "headline": "Game ended in a tie"
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

    result["winner_location"] = winner_location

    winner_ovr = winner.get("overall")
    loser_ovr = loser.get("overall")

    # Blowout
    if margin >= 30:
        stories.append({
            "type": "embarrassing_blowout",
            "severity": "critical",
            "headline": "Embarrassing blowout"
        })

    elif margin >= 20:
        stories.append({
            "type": "blowout",
            "severity": "high",
            "headline": "Lopsided loss"
        })

    elif margin <= 3:
        stories.append({
            "type": "nail_biter",
            "severity": "high",
            "headline": "Nail-biter"
        })

    elif margin <= 7:
        stories.append({
            "type": "close_game",
            "severity": "medium",
            "headline": "One-score game"
        })

    # Road victory
    if winner_location == "away":
        stories.append({
            "type": "road_win",
            "severity": "medium",
            "headline": "Road victory"
        })

    # Upset
    if (
        winner_ovr is not None
        and loser_ovr is not None
        and winner_ovr < loser_ovr
    ):
        difference = loser_ovr - winner_ovr

        if difference >= 8:
            severity = "critical"

        elif difference >= 5:
            severity = "high"

        else:
            severity = "medium"

        stories.append({
            "type": "upset",
            "severity": severity,
            "headline": "Upset victory",
            "overall_difference": difference
        })

    # Favorite got embarrassed
    if (
        winner_ovr is not None
        and loser_ovr is not None
        and loser_ovr > winner_ovr
        and margin >= 14
    ):
        stories.append({
            "type": "favorite_disappointment",
            "severity": "high",
            "headline": "Favorite failed to deliver"
        })

    # Shutout
    if loser_score == 0:
        stories.append({
            "type": "shutout",
            "severity": "high",
            "headline": "Shutout"
        })

    return result


# =========================================================
# ANALYST REACTION GENERATOR
# =========================================================

def generate_reaction(game_data, story):

    away = game_data["away_team"]
    home = game_data["home_team"]

    away_ovr = game_data.get("away_overall")
    home_ovr = game_data.get("home_overall")

    story_type = story.get("type")

    # -----------------------------------------------------
    # PREGAME
    # -----------------------------------------------------

    if story_type == "major_mismatch":

        if away_ovr > home_ovr:
            favorite = away
            underdog = home
        else:
            favorite = home
            underdog = away

        choices = [
            (
                f"I'm looking at {favorite} against {underdog}, "
                f"and there is a serious talent gap here. "
                f"If {favorite} doesn't handle business, "
                f"we are going to have some questions."
            ),

            (
                f"{favorite} has the better roster on paper. "
                f"No excuses. This is the type of matchup "
                f"they are supposed to control."
            )
        ]

        return random.choice(choices)

    if story_type == "clear_favorite":

        if away_ovr > home_ovr:
            favorite = away
        else:
            favorite = home

        return (
            f"I've got my eyes on {favorite}. "
            f"They have the roster advantage, so now "
            f"it's time to prove that rating actually means something."
        )

    if story_type == "even_matchup":

        return (
            f"This is about as even as it gets. "
            f"{away} and {home} are evenly matched on paper, "
            f"so execution is going to decide this one."
        )

    if story_type == "user_game":

        user_team = None

        if game_data.get("away_user"):
            user_team = away

        if game_data.get("home_user"):
            user_team = home

        return (
            f"This is a matchup I'm watching closely. "
            f"{user_team} is user-controlled, so the pressure is on "
            f"to outperform what the roster ratings say on paper."
        )

    if story_type == "game_of_the_week":

        return (
            f"This is the Game of the Week for a reason. "
            f"{away} versus {home} has my attention."
        )

    # -----------------------------------------------------
    # POSTGAME NEGATIVE
    # -----------------------------------------------------

    if story_type == "embarrassing_blowout":

        loser = game_data.get("loser")
        winner = game_data.get("winner")
        margin = game_data.get("margin")

        return (
            f"That was embarrassing. {loser} didn't just lose "
            f"to {winner} — they got beat by {margin}. "
            f"You cannot put that kind of performance on the field "
            f"and pretend everything is fine."
        )

    if story_type == "blowout":

        loser = game_data.get("loser")
        winner = game_data.get("winner")

        return (
            f"{loser} has some explaining to do. "
            f"{winner} controlled this game, and this was not "
            f"the kind of performance you can just brush aside."
        )

    if story_type == "favorite_disappointment":

        loser = game_data.get("loser")

        return (
            f"This is exactly why ratings don't win football games. "
            f"{loser} came in with the talent advantage and still "
            f"failed to deliver. That's unacceptable."
        )

    if story_type == "upset":

        winner = game_data.get("winner")
        loser = game_data.get("loser")

        return (
            f"Now THIS got my attention. {winner} just took down "
            f"a more talented {loser} team on paper. "
            f"That's a statement win."
        )

    if story_type == "shutout":

        loser = game_data.get("loser")

        return (
            f"Zero points? ZERO? {loser} has to go back to the drawing "
            f"board because an offense cannot show up and give you nothing."
        )

    # -----------------------------------------------------
    # POSTGAME POSITIVE / NEUTRAL
    # -----------------------------------------------------

    if story_type == "nail_biter":

        winner = game_data.get("winner")

        return (
            f"That one came down to the wire. "
            f"{winner} found a way to survive when the pressure was highest."
        )

    if story_type == "close_game":

        winner = game_data.get("winner")

        return (
            f"It wasn't easy, but {winner} got the job done "
            f"in a one-score game."
        )

    if story_type == "road_win":

        winner = game_data.get("winner")

        return (
            f"Going on the road and getting a win matters. "
            f"{winner} deserves credit for handling business away from home."
        )

    return (
        f"{away} versus {home} is one of the stories "
        f"to watch around Project Madden."
    )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "Project Madden Analytics",
        "analyst_system": "online"
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
            os.path.join(
                DATA_DIR,
                "leagueteams.json"
            ),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

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
            json.dump(data, f, indent=2)

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
            json.dump(data, f, indent=2)

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
            json.dump(data, f, indent=2)

        return jsonify({
            "success": True,
            "type": "freeagents"
        }), 200

    # Team rosters
    if (
        "team" in parts
        and parts[-1] == "roster"
    ):

        team_index = parts.index("team")
        team_id = parts[team_index + 1]

        with open(
            os.path.join(
                DATA_DIR,
                f"roster_{team_id}.json"
            ),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        return jsonify({
            "success": True,
            "type": "roster",
            "team_id": team_id
        }), 200

    # Weekly stats/schedules
    if "week" in parts:

        try:
            week_index = parts.index("week")

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
                "error": "Invalid weekly export path"
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


# =========================================================
# STORIES
# =========================================================

@app.route(
    "/analytics/stories/<season_type>/<week_number>"
)
def weekly_stories(
    season_type,
    week_number
):

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

    output = []

    for game in games:
        output.append(
            classify_game_story(
                game,
                team_map
            )
        )

    return jsonify({
        "league": "Project Madden",
        "season_type": season_type,
        "week": week_number,
        "games": output
    })


# =========================================================
# ANALYST REACTIONS
# =========================================================

@app.route(
    "/analyst/reactions/<season_type>/<week_number>"
)
def analyst_reactions(
    season_type,
    week_number
):

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

    reactions = []

    for game in games:

        game_data = classify_game_story(
            game,
            team_map
        )

        for story in game_data["stories"]:

            reaction = generate_reaction(
                game_data,
                story
            )

            reactions.append({
                "matchup":
                    f"{game_data['away_team']} @ "
                    f"{game_data['home_team']}",

                "story_type":
                    story["type"],

                "headline":
                    story["headline"],

                "severity":
                    story["severity"],

                "reaction":
                    reaction
            })

    return jsonify({
        "analyst":
            "Project Madden Analyst",

        "note":
            "Generated fictional league commentary",

        "season_type":
            season_type,

        "week":
            week_number,

        "reaction_count":
            len(reactions),

        "reactions":
            reactions
    })


# =========================================================
# DISCOHOOK / DISCORD EMBED PREVIEW
# =========================================================

@app.route(
    "/analyst/embed/<season_type>/<week_number>"
)
def analyst_embed(
    season_type,
    week_number
):

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

    fields = []

    for game in games:

        game_data = classify_game_story(
            game,
            team_map
        )

        if not game_data["stories"]:
            continue

        for story in game_data["stories"]:

            reaction = generate_reaction(
                game_data,
                story
            )

            fields.append({
                "name":
                    f"🏈 {game_data['away_team']} @ "
                    f"{game_data['home_team']}",

                "value":
                    f"**{story['headline']}**\n"
                    f"{reaction}",

                "inline":
                    False
            })

    # Discord allows max 25 embed fields
    fields = fields[:25]

    embed = {
        "embeds": [
            {
                "title":
                    "🎙️ PROJECT MADDEN — AROUND THE LEAGUE",

                "description":
                    (
                        f"Analyst reactions for "
                        f"{season_type.upper()} Week {week_number}"
                    ),

                "fields":
                    fields,

                "footer": {
                    "text":
                        "Project Madden • Fictional Analyst Commentary"
                }
            }
        ]
    }

    return jsonify(embed)


# =========================================================
# MATCHUPS
# =========================================================

@app.route(
    "/analytics/matchups/<season_type>/<week_number>"
)
def weekly_matchups(
    season_type,
    week_number
):

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

        away = team_map.get(
            str(game.get("awayTeamId")),
            {}
        )

        home = team_map.get(
            str(game.get("homeTeamId")),
            {}
        )

        matchups.append({
            "away": {
                "team": away.get(
                    "name",
                    "Unknown"
                ),
                "overall": away.get(
                    "overall"
                ),
                "score": game.get(
                    "awayScore",
                    0
                )
            },

            "home": {
                "team": home.get(
                    "name",
                    "Unknown"
                ),
                "overall": home.get(
                    "overall"
                ),
                "score": game.get(
                    "homeScore",
                    0
                )
            },

            "status":
                game.get("status")
        })

    return jsonify({
        "season_type": season_type,
        "week": week_number,
        "games": matchups
    })


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
