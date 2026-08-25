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
        "away_team": away.get("name", "Unknown"),
        "home_team": home.get("name", "Unknown"),
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

    # -----------------------------------------------------
    # PREGAME
    # -----------------------------------------------------

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
                "headline": "User-controlled matchup"
            })

        return result

    # -----------------------------------------------------
    # POSTGAME
    # -----------------------------------------------------

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

    result["winner"] = winner.get("name", "Unknown")
    result["loser"] = loser.get("name", "Unknown")
    result["winner_location"] = winner_location

    winner_ovr = winner.get("overall")
    loser_ovr = loser.get("overall")

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

    if winner_location == "away":
        stories.append({
            "type": "road_win",
            "severity": "medium",
            "headline": "Road victory"
        })

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

    if loser_score == 0:
        stories.append({
            "type": "shutout",
            "severity": "high",
            "headline": "Shutout"
        })

    return result


# =========================================================
# HUMAN-STYLE ANALYST REACTIONS
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
                f"{favorite} should win this game. "
                f"They've got the better roster, they've got the talent edge, "
                f"and if they struggle with {underdog}, we're going to have "
                f"a very different conversation afterward."
            ),
            (
                f"I look at this matchup and I see one team with a clear advantage. "
                f"{favorite} has more talent on paper. No excuses here. "
                f"Go out there and handle your business."
            ),
            (
                f"This is one of those games where {favorite} cannot afford "
                f"to play around. You're supposed to be the better team. "
                f"Now go prove it."
            )
        ]

        return random.choice(choices)

    if story_type == "clear_favorite":

        if away_ovr > home_ovr:
            favorite = away
            underdog = home
        else:
            favorite = home
            underdog = away

        choices = [
            (
                f"I lean {favorite} here. They've got the roster edge, "
                f"but I still want to see them earn it. {underdog} isn't "
                f"just going to hand them this game."
            ),
            (
                f"{favorite} has the advantage on paper, but that only means "
                f"something if they show it once the game starts."
            ),
            (
                f"This feels like a game {favorite} should control, "
                f"but if they come out sloppy, {underdog} can absolutely make them pay."
            )
        ]

        return random.choice(choices)

    if story_type == "even_matchup":

        choices = [
            (
                f"I really don't see much separating {away} and {home}. "
                f"This one is going to come down to who protects the football "
                f"and who executes late."
            ),
            (
                f"This is a true toss-up. The ratings are basically even, "
                f"so I'm not giving either team an easy edge."
            ),
            (
                f"If you like close games, this is one to watch. "
                f"{away} and {home} are about as evenly matched as it gets."
            )
        ]

        return random.choice(choices)

    if story_type == "user_game":

        if game_data.get("away_user"):
            user_team = away
            opponent = home
        else:
            user_team = home
            opponent = away

        choices = [
            (
                f"I'm watching {user_team} closely here. "
                f"There's a live user on the sticks, so I'm not just staring "
                f"at roster ratings. Execution can change everything."
            ),
            (
                f"{user_team} makes this matchup interesting because somebody "
                f"is actually controlling that team. Against {opponent}, "
                f"that can completely change how this game plays out."
            ),
            (
                f"Don't get caught staring at the ratings. "
                f"{user_team} has somebody on the sticks, and that makes "
                f"this matchup a lot more interesting than it looks on paper."
            )
        ]

        return random.choice(choices)

    if story_type == "game_of_the_week":

        choices = [
            (
                f"This is the one I'm circling. {away} against {home} "
                f"has the kind of matchup that can tell us a lot about both teams."
            ),
            (
                f"If you're only watching one game this week, "
                f"{away} versus {home} deserves your attention."
            )
        ]

        return random.choice(choices)

    # -----------------------------------------------------
    # POSTGAME NEGATIVE
    # -----------------------------------------------------

    if story_type == "embarrassing_blowout":

        loser = game_data.get("loser")
        winner = game_data.get("winner")
        margin = game_data.get("margin")

        choices = [
            (
                f"No. Absolutely not. {loser} just lost by {margin} points. "
                f"That's not one bad bounce or one mistake. That's a complete "
                f"breakdown from start to finish."
            ),
            (
                f"{loser} got embarrassed. There's really no softer way to say it. "
                f"{winner} controlled that game, and by the end it looked "
                f"like one team belonged and the other one didn't."
            ),
            (
                f"You can lose a football game. What you cannot do is get "
                f"run out of the building like that. {loser} has a lot to answer for."
            )
        ]

        return random.choice(choices)

    if story_type == "blowout":

        loser = game_data.get("loser")
        winner = game_data.get("winner")

        choices = [
            (
                f"{loser} got handled. This wasn't one or two plays going wrong. "
                f"{winner} was simply better for most of that game."
            ),
            (
                f"I expected more from {loser}. That was a one-sided performance, "
                f"and they've got to own it."
            )
        ]

        return random.choice(choices)

    if story_type == "favorite_disappointment":

        loser = game_data.get("loser")

        choices = [
            (
                f"This is exactly the kind of loss that gets people talking. "
                f"{loser} came in with the talent advantage and still couldn't get it done."
            ),
            (
                f"I don't want to hear about ratings after that. "
                f"{loser} was supposed to be the better team, and they didn't look like it."
            ),
            (
                f"That's a disappointing loss for {loser}. "
                f"When you have the better roster, expectations come with it."
            )
        ]

        return random.choice(choices)

    if story_type == "upset":

        winner = game_data.get("winner")
        loser = game_data.get("loser")

        choices = [
            (
                f"Now that's a statement. {winner} just beat a team "
                f"that was supposed to be better on paper."
            ),
            (
                f"I love this kind of result. Everybody looked at the ratings, "
                f"everybody leaned {loser}, and {winner} went out there and took the game."
            ),
            (
                f"{winner} just flipped the script. That's the kind of upset "
                f"that gets the whole league's attention."
            )
        ]

        return random.choice(choices)

    if story_type == "shutout":

        loser = game_data.get("loser")

        choices = [
            (
                f"Zero points is unacceptable. I don't care how good the defense was. "
                f"{loser} has to find something offensively."
            ),
            (
                f"You cannot walk off the field with a zero on the scoreboard "
                f"and act like it was just one of those days. Something failed."
            )
        ]

        return random.choice(choices)

    # -----------------------------------------------------
    # POSTGAME POSITIVE / CLOSE
    # -----------------------------------------------------

    if story_type == "nail_biter":

        winner = game_data.get("winner")

        choices = [
            (
                f"That was a fight. {winner} had to earn every bit of that win."
            ),
            (
                f"That's the kind of close game that tells you something. "
                f"{winner} found a way when things got tight."
            )
        ]

        return random.choice(choices)

    if story_type == "close_game":

        winner = game_data.get("winner")

        return (
            f"It wasn't pretty, but {winner} did enough to finish the job."
        )

    if story_type == "road_win":

        winner = game_data.get("winner")

        choices = [
            (
                f"Winning on the road matters. {winner} went into somebody else's place "
                f"and handled business."
            ),
            (
                f"I give {winner} credit for this one. Road wins are never automatic."
            )
        ]

        return random.choice(choices)

    return (
        f"{away} versus {home} is one of the matchups "
        f"worth watching around Project Madden."
    )


# =========================================================
# HOME / HEALTH
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

    if parts[-1] == "leagueteams":

        with open(
            os.path.join(DATA_DIR, "leagueteams.json"),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        return jsonify({
            "success": True,
            "type": "leagueteams"
        }), 200

    if parts[-1] == "standings":

        with open(
            os.path.join(DATA_DIR, "standings.json"),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        return jsonify({
            "success": True,
            "type": "standings"
        }), 200

    if parts[-1] == "extra":

        with open(
            os.path.join(DATA_DIR, "extra.json"),
            "w"
        ) as f:
            json.dump(data, f, indent=2)

        return jsonify({
            "success": True,
            "type": "extra"
        }), 200

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
            "Project Madden First Take",

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
# DISCORD / DISCOHOOK EMBED
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

    fields = fields[:25]

    return jsonify({
        "embeds": [
            {
                "title":
                    "🎙️ PROJECT MADDEN — AROUND THE LEAGUE",

                "description":
                    (
                        f"Project Madden First Take reactions • "
                        f"{season_type.upper()} Week {week_number}"
                    ),

                "fields":
                    fields,

                "footer": {
                    "text":
                        "Project Madden • Around The League"
                }
            }
        ]
    })


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
                "overall": away.get("overall"),
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
                "overall": home.get("overall"),
                "score": game.get(
                    "homeScore",
                    0
                )
            },

            "status": game.get("status")
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
