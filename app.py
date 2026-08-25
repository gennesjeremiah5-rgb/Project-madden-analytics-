from flask import Flask, request, jsonify
import json
import os
import hashlib

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


def team_phrase(team):
    """
    Converts:
    Ravens -> the Ravens
    Texans -> the Texans
    49ers -> the 49ers
    """
    if not team:
        return "the team"

    return f"the {team}"


def stable_choice(options, key):
    """
    Gives the same reaction every time for the same
    matchup/story/week instead of changing on refresh.
    """
    digest = hashlib.sha256(
        key.encode("utf-8")
    ).hexdigest()

    number = int(
        digest[:8],
        16
    )

    return options[
        number % len(options)
    ]


# =========================================================
# GAME STORY DETECTOR
# =========================================================

def classify_game_story(game, team_map):

    home_id = str(
        game.get("homeTeamId")
    )

    away_id = str(
        game.get("awayTeamId")
    )

    home = team_map.get(
        home_id,
        {}
    )

    away = team_map.get(
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

    home_ovr = home.get(
        "overall"
    )

    away_ovr = away.get(
        "overall"
    )

    result = {
        "schedule_id":
            game.get("scheduleId"),

        "away_team":
            away.get(
                "name",
                "Unknown"
            ),

        "home_team":
            home.get(
                "name",
                "Unknown"
            ),

        "away_abbr":
            away.get("abbr"),

        "home_abbr":
            home.get("abbr"),

        "away_score":
            away_score,

        "home_score":
            home_score,

        "away_overall":
            away_ovr,

        "home_overall":
            home_ovr,

        "away_user":
            away.get(
                "user",
                ""
            ),

        "home_user":
            home.get(
                "user",
                ""
            ),

        "stories":
            []
    }

    stories = result[
        "stories"
    ]

    # =====================================================
    # PREGAME
    # =====================================================

    if (
        home_score == 0
        and away_score == 0
    ):

        if (
            home_ovr is not None
            and away_ovr is not None
        ):

            gap = abs(
                home_ovr
                - away_ovr
            )

            if gap == 0:

                stories.append({
                    "type":
                        "even_matchup",

                    "severity":
                        "medium",

                    "headline":
                        "Evenly matched teams"
                })

            elif gap >= 6:

                stories.append({
                    "type":
                        "major_mismatch",

                    "severity":
                        "high",

                    "headline":
                        "Major overall mismatch"
                })

            elif gap >= 4:

                stories.append({
                    "type":
                        "clear_favorite",

                    "severity":
                        "medium",

                    "headline":
                        "Clear favorite by overall"
                })

        if game.get(
            "isGameOfTheWeek",
            False
        ):

            stories.append({
                "type":
                    "game_of_the_week",

                "severity":
                    "high",

                "headline":
                    "Game of the Week"
            })

        if (
            home.get("user")
            or away.get("user")
        ):

            stories.append({
                "type":
                    "user_game",

                "severity":
                    "medium",

                "headline":
                    "User-controlled matchup"
            })

        return result

    # =====================================================
    # POSTGAME
    # =====================================================

    margin = abs(
        home_score
        - away_score
    )

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
            "type":
                "tie",

            "severity":
                "medium",

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

    result[
        "winner_location"
    ] = winner_location

    winner_ovr = winner.get(
        "overall"
    )

    loser_ovr = loser.get(
        "overall"
    )

    # Blowout
    if margin >= 30:

        stories.append({
            "type":
                "embarrassing_blowout",

            "severity":
                "critical",

            "headline":
                "Embarrassing blowout"
        })

    elif margin >= 20:

        stories.append({
            "type":
                "blowout",

            "severity":
                "high",

            "headline":
                "Lopsided loss"
        })

    elif margin <= 3:

        stories.append({
            "type":
                "nail_biter",

            "severity":
                "high",

            "headline":
                "Nail-biter"
        })

    elif margin <= 7:

        stories.append({
            "type":
                "close_game",

            "severity":
                "medium",

            "headline":
                "One-score game"
        })

    # Road victory
    if winner_location == "away":

        stories.append({
            "type":
                "road_win",

            "severity":
                "medium",

            "headline":
                "Road victory"
        })

    # Upset
    if (
        winner_ovr is not None
        and loser_ovr is not None
        and winner_ovr < loser_ovr
    ):

        difference = (
            loser_ovr
            - winner_ovr
        )

        if difference >= 8:
            severity = "critical"

        elif difference >= 5:
            severity = "high"

        else:
            severity = "medium"

        stories.append({
            "type":
                "upset",

            "severity":
                severity,

            "headline":
                "Upset victory",

            "overall_difference":
                difference
        })

    # Favorite failed badly
    if (
        winner_ovr is not None
        and loser_ovr is not None
        and loser_ovr > winner_ovr
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
            "type":
                "shutout",

            "severity":
                "high",

            "headline":
                "Shutout"
        })

    return result


# =========================================================
# HUMAN ANALYST REACTION ENGINE
# =========================================================

def generate_reaction(
    game_data,
    story,
    season_type,
    week_number
):

    away = game_data[
        "away_team"
    ]

    home = game_data[
        "home_team"
    ]

    away_ovr = game_data.get(
        "away_overall"
    )

    home_ovr = game_data.get(
        "home_overall"
    )

    story_type = story.get(
        "type"
    )

    schedule_id = game_data.get(
        "schedule_id"
    )

    stable_key = (
        f"{season_type}-"
        f"{week_number}-"
        f"{schedule_id}-"
        f"{story_type}"
    )

    away_the = team_phrase(
        away
    )

    home_the = team_phrase(
        home
    )

    # =====================================================
    # MAJOR MISMATCH
    # =====================================================

    if story_type == "major_mismatch":

        if away_ovr > home_ovr:
            favorite = away
            underdog = home
        else:
            favorite = home
            underdog = away

        favorite_the = team_phrase(
            favorite
        )

        underdog_the = team_phrase(
            underdog
        )

        choices = [
            (
                f"{favorite_the} should win this game. "
                f"They've got the better roster and the talent edge. "
                f"If they struggle with {underdog_the}, "
                f"we're going to have some serious questions afterward."
            ),

            (
                f"I look at this matchup and I see a clear advantage "
                f"for {favorite_the}. No excuses. "
                f"They're supposed to be the better football team. "
                f"Now go prove it."
            ),

            (
                f"This is one of those games where {favorite_the} "
                f"cannot afford to play around. "
                f"{underdog_the.capitalize()} would love nothing more "
                f"than to embarrass them."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # CLEAR FAVORITE
    # =====================================================

    if story_type == "clear_favorite":

        if away_ovr > home_ovr:
            favorite = away
            underdog = home
        else:
            favorite = home
            underdog = away

        favorite_the = team_phrase(
            favorite
        )

        underdog_the = team_phrase(
            underdog
        )

        choices = [
            (
                f"I'm leaning toward {favorite_the} here. "
                f"They've got the roster edge, but they're still "
                f"going to have to earn it. "
                f"{underdog_the.capitalize()} aren't handing anybody a win."
            ),

            (
                f"{favorite_the.capitalize()} have the advantage on paper. "
                f"That's fine. Now show me it actually matters "
                f"once the game starts."
            ),

            (
                f"This feels like a game {favorite_the} should control. "
                f"But if they come out sloppy, "
                f"{underdog_the} can absolutely make them pay."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # EVEN MATCHUP
    # =====================================================

    if story_type == "even_matchup":

        choices = [
            (
                f"I don't see much separating {away_the} and {home_the}. "
                f"This is going to come down to turnovers, execution "
                f"and who makes the plays late."
            ),

            (
                f"This is a legitimate toss-up. "
                f"{away_the.capitalize()} and {home_the} are basically "
                f"even on paper. Somebody has to separate themselves."
            ),

            (
                f"If you want a matchup that could go either way, "
                f"this is it. I can't give either side a comfortable edge."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # USER-CONTROLLED MATCHUP
    # =====================================================

    if story_type == "user_game":

        if game_data.get(
            "away_user"
        ):
            user_team = away
            opponent = home
            user_name = game_data.get(
                "away_user"
            )

        else:
            user_team = home
            opponent = away
            user_name = game_data.get(
                "home_user"
            )

        user_the = team_phrase(
            user_team
        )

        opponent_the = team_phrase(
            opponent
        )

        choices = [
            (
                f"I'm keeping a close eye on {user_the}. "
                f"{user_name} is on the sticks, so I'm not just looking "
                f"at the ratings screen. A good user can erase a lot "
                f"of advantages on paper."
            ),

            (
                f"Don't dismiss {user_the} because of the ratings. "
                f"{user_name} is controlling Baltimore's side of this matchup, "
                f"and that makes things much more interesting against "
                f"{opponent_the}."
                if user_team == "Ravens"
                else
                f"Don't dismiss {user_the} because of the ratings. "
                f"{user_name} is controlling this team, and that makes "
                f"things much more interesting against {opponent_the}."
            ),

            (
                f"This is where the ratings only tell part of the story. "
                f"{user_the.capitalize()} have an active user behind them. "
                f"Now I want to see whether that translates into an advantage "
                f"once the game starts."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # GAME OF THE WEEK
    # =====================================================

    if story_type == "game_of_the_week":

        choices = [
            (
                f"This is the game I'm circling. "
                f"{away_the.capitalize()} against {home_the} "
                f"has my full attention."
            ),

            (
                f"If you're only watching one Project Madden game this week, "
                f"this is the one I'd keep an eye on."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # EMBARRASSING BLOWOUT
    # =====================================================

    if story_type == "embarrassing_blowout":

        loser = game_data.get(
            "loser"
        )

        winner = game_data.get(
            "winner"
        )

        margin = game_data.get(
            "margin"
        )

        loser_the = team_phrase(
            loser
        )

        winner_the = team_phrase(
            winner
        )

        choices = [
            (
                f"No. Absolutely not. "
                f"{loser_the.capitalize()} just lost by {margin} points. "
                f"That's not one bad bounce. That's a complete breakdown."
            ),

            (
                f"{loser_the.capitalize()} got embarrassed. "
                f"There's no softer way to put it. "
                f"{winner_the.capitalize()} controlled that game from start to finish."
            ),

            (
                f"You can lose a football game. "
                f"What you cannot do is get run out of the building like that. "
                f"{loser_the.capitalize()} have a lot to answer for."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # BLOWOUT
    # =====================================================

    if story_type == "blowout":

        loser = game_data.get(
            "loser"
        )

        winner = game_data.get(
            "winner"
        )

        choices = [
            (
                f"The {loser} got handled. "
                f"This wasn't one or two plays going wrong. "
                f"The {winner} were simply better."
            ),

            (
                f"I expected more from the {loser}. "
                f"That was a one-sided performance, and they have to own it."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # FAVORITE DISAPPOINTMENT
    # =====================================================

    if story_type == "favorite_disappointment":

        loser = game_data.get(
            "loser"
        )

        choices = [
            (
                f"This is exactly the kind of loss that gets people talking. "
                f"The {loser} came in with the talent advantage "
                f"and still couldn't get it done."
            ),

            (
                f"I don't want to hear about ratings after that. "
                f"The {loser} were supposed to be the better team. "
                f"They didn't look like it."
            ),

            (
                f"That's a disappointing loss for the {loser}. "
                f"When you have the better roster, expectations come with it."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # UPSET
    # =====================================================

    if story_type == "upset":

        winner = game_data.get(
            "winner"
        )

        loser = game_data.get(
            "loser"
        )

        choices = [
            (
                f"Now THAT is a statement. "
                f"The {winner} just beat a team everybody thought "
                f"was better on paper."
            ),

            (
                f"Everybody looked at the ratings and leaned toward "
                f"the {loser}. The {winner} didn't care. "
                f"They went out there and took the game."
            ),

            (
                f"The {winner} just flipped the script. "
                f"That's the kind of upset that gets the whole league talking."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # SHUTOUT
    # =====================================================

    if story_type == "shutout":

        loser = game_data.get(
            "loser"
        )

        choices = [
            (
                f"Zero points? That's unacceptable. "
                f"I don't care how good the defense was. "
                f"The {loser} have to find something offensively."
            ),

            (
                f"You cannot walk off the field with a zero "
                f"on the scoreboard and shrug your shoulders. "
                f"Something failed for the {loser}."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # NAIL-BITER
    # =====================================================

    if story_type == "nail_biter":

        winner = game_data.get(
            "winner"
        )

        choices = [
            (
                f"That was a fight. "
                f"The {winner} had to earn every bit of that win."
            ),

            (
                f"That's the kind of close game that tells you something. "
                f"The {winner} found a way when it got tight."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    # =====================================================
    # CLOSE GAME
    # =====================================================

    if story_type == "close_game":

        winner = game_data.get(
            "winner"
        )

        return (
            f"It wasn't pretty, but the {winner} did enough "
            f"to finish the job."
        )

    # =====================================================
    # ROAD WIN
    # =====================================================

    if story_type == "road_win":

        winner = game_data.get(
            "winner"
        )

        choices = [
            (
                f"Winning on the road matters. "
                f"The {winner} went into somebody else's building "
                f"and handled business."
            ),

            (
                f"I give the {winner} credit for this one. "
                f"Road wins are never automatic."
            )
        ]

        return stable_choice(
            choices,
            stable_key
        )

    return (
        f"{away_the.capitalize()} against {home_the} "
        f"is one of the stories I'm watching around Project Madden."
    )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "status":
            "online",

        "service":
            "Project Madden Analytics",

        "analyst_system":
            "online",

        "analyst_version":
            "2.0"
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
            "working":
                True,

            "path":
                subpath
        })

    data = request.get_json(
        silent=True
    )

    if data is None:

        return jsonify({
            "success":
                False,

            "error":
                "No JSON received"
        }), 400

    parts = subpath.split(
        "/"
    )

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
            "success":
                True,

            "type":
                "leagueteams"
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
            "success":
                True,

            "type":
                "standings"
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
            "success":
                True,

            "type":
                "extra"
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
            "success":
                True,

            "type":
                "freeagents"
        }), 200

    # Team roster
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
            "success":
                True,

            "type":
                "roster",

            "team_id":
                team_id
        }), 200

    # Weekly exports
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
                "success":
                    False,

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
            "success":
                True,

            "type":
                "weekly",

            "season_type":
                season_type,

            "week":
                week_number,

            "stat_type":
                stat_type
        }), 200

    return jsonify({
        "success":
            True,

        "type":
            "unknown",

        "path":
            subpath
    }), 200


# =========================================================
# ANALYST REACTIONS
# =========================================================

@app.route(
    "/analyst/reactions/"
    "<season_type>/"
    "<week_number>"
)
def analyst_reactions(
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

    reactions = []

    for game in games:

        game_data = classify_game_story(
            game,
            team_map
        )

        for story in game_data[
            "stories"
        ]:

            reaction = generate_reaction(
                game_data,
                story,
                season_type,
                week_number
            )

            reactions.append({
                "matchup":
                    (
                        f"{game_data['away_team']} "
                        f"@ {game_data['home_team']}"
                    ),

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
    "/analyst/embed/"
    "<season_type>/"
    "<week_number>"
)
def analyst_embed(
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

    fields = []

    for game in games:

        game_data = classify_game_story(
            game,
            team_map
        )

        for story in game_data[
            "stories"
        ]:

            reaction = generate_reaction(
                game_data,
                story,
                season_type,
                week_number
            )

            fields.append({
                "name":
                    (
                        f"🏈 "
                        f"{game_data['away_team']} "
                        f"@ {game_data['home_team']}"
                    ),

                "value":
                    (
                        f"**{story['headline']}**\n"
                        f"{reaction}"
                    ),

                "inline":
                    False
            })

    fields = fields[:25]

    return jsonify({
        "embeds": [
            {
                "title":
                    (
                        "🎙️ PROJECT MADDEN "
                        "— AROUND THE LEAGUE"
                    ),

                "description":
                    (
                        "Project Madden First Take • "
                        f"{season_type.upper()} "
                        f"Week {week_number}"
                    ),

                "fields":
                    fields,

                "footer": {
                    "text":
                        (
                            "Project Madden • "
                            "Around The League"
                        )
                }
            }
        ]
    })


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
