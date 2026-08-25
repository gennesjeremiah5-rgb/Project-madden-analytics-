from flask import Flask, request, jsonify
import json
import os
import hashlib
import uuid
from datetime import datetime, timezone

app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


# =========================================================
# BASIC HELPERS
# =========================================================

def load_json_file(filename):
    path = os.path.join(DATA_DIR, filename)

    if not os.path.exists(path):
        return None

    with open(path, "r") as f:
        return json.load(f)


def save_json_file(filename, data):
    path = os.path.join(DATA_DIR, filename)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_team_map():
    data = load_json_file("leagueteams.json")

    if not data:
        return {}

    team_map = {}

    for team in data.get("leagueTeamInfoList", []):
        team_map[str(team.get("teamId"))] = {
            "teamId": team.get("teamId"),
            "abbr": team.get("abbrName"),
            "city": team.get("cityName"),
            "name": team.get("displayName"),
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


def team_phrase(team):
    if not team:
        return "the team"

    return f"the {team}"


def team_phrase_start(team):
    if not team:
        return "The team"

    return f"The {team}"


def stable_choice(options, key):
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

    home = team_map.get(
        str(game.get("homeTeamId")),
        {}
    )

    away = team_map.get(
        str(game.get("awayTeamId")),
        {}
    )

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

        "away_score": away_score,
        "home_score": home_score,

        "away_overall": away_ovr,
        "home_overall": home_ovr,

        "away_user": away.get(
            "user",
            ""
        ),

        "home_user": home.get(
            "user",
            ""
        ),

        "stories": []
    }

    stories = result["stories"]

    # Pregame
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

        if game.get(
            "isGameOfTheWeek",
            False
        ):
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

    # Postgame
    margin = abs(
        home_score - away_score
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

    result["winner_location"] = (
        winner_location
    )

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
# GAME ANALYST REACTIONS
# =========================================================

def generate_reaction(
    game_data,
    story,
    season_type,
    week_number
):

    away = game_data["away_team"]
    home = game_data["home_team"]

    away_ovr = game_data.get(
        "away_overall"
    )

    home_ovr = game_data.get(
        "home_overall"
    )

    story_type = story.get("type")

    key = (
        f"{season_type}-"
        f"{week_number}-"
        f"{game_data.get('schedule_id')}-"
        f"{story_type}"
    )

    if story_type == "major_mismatch":

        if away_ovr > home_ovr:
            favorite = away
            underdog = home
        else:
            favorite = home
            underdog = away

        return stable_choice([
            (
                f"{team_phrase_start(favorite)} should win this game. "
                f"They've got the better roster and the talent edge. "
                f"If they struggle with {team_phrase(underdog)}, "
                f"we're going to have some serious questions."
            ),

            (
                f"This is one of those games where "
                f"{team_phrase(favorite)} cannot afford to play around. "
                f"{team_phrase_start(underdog)} would love nothing more "
                f"than to embarrass them."
            )
        ], key)

    if story_type == "clear_favorite":

        if away_ovr > home_ovr:
            favorite = away
            underdog = home
        else:
            favorite = home
            underdog = away

        return stable_choice([
            (
                f"I'm leaning toward {team_phrase(favorite)} here. "
                f"They've got the roster edge, but they're still "
                f"going to have to earn it. "
                f"{team_phrase_start(underdog)} aren't handing anybody a win."
            ),

            (
                f"{team_phrase_start(favorite)} have the advantage on paper. "
                f"That's fine. Now show me it actually matters "
                f"once the game starts."
            )
        ], key)

    if story_type == "even_matchup":

        return stable_choice([
            (
                f"I don't see much separating "
                f"{team_phrase(away)} and {team_phrase(home)}. "
                f"Turnovers and late-game execution are going "
                f"to decide this one."
            ),

            (
                f"If you want a matchup that could go either way, "
                f"this is it. I can't give either side a comfortable edge."
            )
        ], key)

    if story_type == "user_game":

        if game_data.get("away_user"):
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

        return stable_choice([
            (
                f"I'm keeping a close eye on {team_phrase(user_team)}. "
                f"{user_name} is on the sticks, so I'm not just looking "
                f"at the ratings screen. A good user can erase a lot "
                f"of advantages on paper."
            ),

            (
                f"Don't dismiss {team_phrase(user_team)} because of the ratings. "
                f"{user_name} is controlling this team, and that changes "
                f"the conversation against {team_phrase(opponent)}."
            )
        ], key)

    if story_type == "embarrassing_blowout":

        loser = game_data.get("loser")
        winner = game_data.get("winner")
        margin = game_data.get("margin")

        return stable_choice([
            (
                f"No. Absolutely not. "
                f"{team_phrase_start(loser)} just lost by {margin} points. "
                f"That's a complete breakdown from top to bottom."
            ),

            (
                f"{team_phrase_start(loser)} got embarrassed. "
                f"There's no softer way to say it. "
                f"{team_phrase_start(winner)} controlled that game."
            )
        ], key)

    if story_type == "blowout":

        loser = game_data.get("loser")
        winner = game_data.get("winner")

        return (
            f"{team_phrase_start(loser)} got handled. "
            f"This wasn't one or two bad plays. "
            f"{team_phrase_start(winner)} were simply better."
        )

    if story_type == "favorite_disappointment":

        loser = game_data.get("loser")

        return (
            f"I don't want to hear about ratings after that. "
            f"{team_phrase_start(loser)} were supposed to be the better team, "
            f"and they didn't look like it."
        )

    if story_type == "upset":

        winner = game_data.get("winner")
        loser = game_data.get("loser")

        return (
            f"Now THAT is a statement. "
            f"{team_phrase_start(winner)} just beat a team "
            f"that was supposed to be better on paper. "
            f"Everybody who picked {team_phrase(loser)} has some explaining to do."
        )

    if story_type == "shutout":

        loser = game_data.get("loser")

        return (
            f"Zero points? That's unacceptable. "
            f"{team_phrase_start(loser)} have to go back "
            f"and figure out what went wrong offensively."
        )

    if story_type == "nail_biter":

        winner = game_data.get("winner")

        return (
            f"That was a fight. "
            f"{team_phrase_start(winner)} had to earn every bit of that win."
        )

    if story_type == "close_game":

        winner = game_data.get("winner")

        return (
            f"It wasn't easy, but {team_phrase(winner)} "
            f"found a way to finish the job."
        )

    if story_type == "road_win":

        winner = game_data.get("winner")

        return (
            f"I give {team_phrase(winner)} credit. "
            f"Going on the road and getting it done matters."
        )

    return (
        f"{team_phrase_start(away)} against {team_phrase(home)} "
        f"is one of the stories I'm watching."
    )


# =========================================================
# TRADE VALUE ENGINE
# =========================================================

DEV_VALUES = {
    "normal": 0,
    "star": 5,
    "superstar": 10,
    "ss": 10,
    "x-factor": 16,
    "xfactor": 16,
    "xf": 16
}


POSITION_MULTIPLIERS = {
    "QB": 1.30,

    "WR": 1.08,
    "TE": 1.02,

    "LT": 1.08,
    "RT": 1.03,
    "LG": 1.00,
    "RG": 1.00,
    "C": 1.00,

    "LE": 1.08,
    "RE": 1.08,
    "EDGE": 1.10,

    "DT": 1.03,

    "LOLB": 1.03,
    "ROLB": 1.03,
    "MLB": 1.00,
    "LB": 1.00,

    "CB": 1.08,
    "FS": 1.03,
    "SS": 1.03,

    "HB": 0.94,
    "RB": 0.94,
    "FB": 0.80,

    "K": 0.72,
    "P": 0.65
}


PICK_VALUES = {
    1: 36,
    2: 24,
    3: 15,
    4: 9,
    5: 5,
    6: 3,
    7: 2
}


def calculate_player_value(asset):

    if asset.get("value_override") is not None:
        return float(
            asset.get("value_override")
        )

    overall = float(
        asset.get("overall", 70)
    )

    age = int(
        asset.get("age", 25)
    )

    position = str(
        asset.get("position", "")
    ).upper()

    dev = str(
        asset.get("dev", "normal")
    ).lower()

    # OVR is the main value
    value = max(
        1,
        (overall - 60) * 1.8
    )

    # Development trait
    value += DEV_VALUES.get(
        dev,
        0
    )

    # Age
    if age <= 22:
        value += 9

    elif age <= 24:
        value += 6

    elif age <= 26:
        value += 3

    elif age >= 33:
        value -= 10

    elif age >= 30:
        value -= 6

    elif age >= 28:
        value -= 3

    # Premium positions
    value *= POSITION_MULTIPLIERS.get(
        position,
        1.0
    )

    return round(
        max(value, 1),
        2
    )


def calculate_pick_value(asset):

    if asset.get("value_override") is not None:
        return float(
            asset.get("value_override")
        )

    round_number = int(
        asset.get("round", 7)
    )

    years_away = int(
        asset.get("years_away", 0)
    )

    value = PICK_VALUES.get(
        round_number,
        1
    )

    # Future picks are worth a little less
    if years_away > 0:
        value *= (
            0.90 ** years_away
        )

    return round(
        value,
        2
    )


def calculate_asset_value(asset):

    asset_type = str(
        asset.get("type", "player")
    ).lower()

    if asset_type == "pick":
        value = calculate_pick_value(
            asset
        )

    else:
        value = calculate_player_value(
            asset
        )

    return value


def calculate_package_value(assets):

    total = 0
    breakdown = []

    for asset in assets:

        value = calculate_asset_value(
            asset
        )

        total += value

        breakdown.append({
            **asset,
            "calculated_value": value
        })

    return round(total, 2), breakdown


def trade_grade(value_received, value_sent):

    difference = (
        value_received
        - value_sent
    )

    if value_sent <= 0:
        percentage = 100
    else:
        percentage = (
            difference / value_sent
        ) * 100

    if percentage >= 40:
        grade = "A+"

    elif percentage >= 25:
        grade = "A"

    elif percentage >= 12:
        grade = "B+"

    elif percentage >= 4:
        grade = "B"

    elif percentage > -4:
        grade = "C+"

    elif percentage > -12:
        grade = "C"

    elif percentage > -25:
        grade = "D"

    else:
        grade = "F"

    return {
        "grade": grade,
        "difference": round(
            difference,
            2
        ),
        "percentage": round(
            percentage,
            1
        )
    }


def summarize_asset(asset):

    asset_type = str(
        asset.get("type", "player")
    ).lower()

    if asset_type == "pick":

        year = asset.get(
            "year",
            "Future"
        )

        round_number = asset.get(
            "round",
            "?"
        )

        return (
            f"{year} Round {round_number} Pick"
        )

    name = asset.get(
        "name",
        "Unknown Player"
    )

    overall = asset.get(
        "overall"
    )

    position = asset.get(
        "position",
        ""
    )

    if overall is not None:
        return (
            f"{name} ({overall} OVR {position})"
        )

    return f"{name} ({position})"


# =========================================================
# TRADE ANALYST REACTION
# =========================================================

def generate_trade_reaction(
    team_a,
    team_b,
    grade_a,
    grade_b,
    value_a_received,
    value_b_received,
    assets_a,
    assets_b,
    trade_id
):

    gap = abs(
        value_a_received
        - value_b_received
    )

    if value_a_received > value_b_received:
        winner = team_a
        loser = team_b
        winner_grade = grade_a["grade"]

    elif value_b_received > value_a_received:
        winner = team_b
        loser = team_a
        winner_grade = grade_b["grade"]

    else:
        winner = None
        loser = None
        winner_grade = "C+"

    if winner is None:

        verdict = "Even trade"

        reaction = stable_choice([
            (
                f"I don't have a problem with this deal. "
                f"{team_phrase_start(team_a)} got something they needed, "
                f"{team_phrase(team_b)} got something they needed, "
                f"and neither side got taken to the cleaners."
            ),

            (
                f"This is one of those trades where I can understand "
                f"the thinking on both sides. I don't see a clear robbery here."
            )
        ], trade_id)

        return verdict, reaction

    percentage_gap = max(
        abs(grade_a["percentage"]),
        abs(grade_b["percentage"])
    )

    if percentage_gap >= 35:

        verdict = (
            f"{winner} won the trade — major steal"
        )

        reaction = stable_choice([
            (
                f"Hold on. What are {team_phrase(loser)} doing here? "
                f"I look at this deal and I see {team_phrase(winner)} "
                f"walking away with the better value by a mile. "
                f"That's not a small difference. That's a robbery."
            ),

            (
                f"I do not like this for {team_phrase(loser)} at all. "
                f"{team_phrase_start(winner)} got the better end of this deal, "
                f"and it isn't particularly close. "
                f"If I'm running {team_phrase(loser)}, somebody has to explain this to me."
            )
        ], trade_id)

    elif percentage_gap >= 20:

        verdict = (
            f"{winner} won the trade"
        )

        reaction = stable_choice([
            (
                f"I understand what both teams were trying to do, "
                f"but I'm giving this one to {team_phrase(winner)}. "
                f"They got the better overall value, and I think "
                f"{team_phrase(loser)} paid too much."
            ),

            (
                f"{team_phrase_start(winner)} came out ahead here. "
                f"I don't necessarily hate the deal for {team_phrase(loser)}, "
                f"but they gave up more than I would've been comfortable giving."
            )
        ], trade_id)

    elif percentage_gap >= 8:

        verdict = (
            f"Slight edge to {winner}"
        )

        reaction = stable_choice([
            (
                f"This is pretty close, but if you're making me pick a winner, "
                f"I'm taking {team_phrase(winner)}. "
                f"They squeezed a little more value out of the deal."
            ),

            (
                f"I can live with this for both teams. "
                f"I just think {team_phrase(winner)} came away "
                f"with the slightly better package."
            )
        ], trade_id)

    else:

        verdict = "Fair trade"

        reaction = (
            f"I don't see a loser here. "
            f"The value is close enough that this is going to come down "
            f"to how these players actually perform."
        )

    return verdict, reaction


def analyze_trade(data):

    team_a = data.get(
        "team_a",
        "Team A"
    )

    team_b = data.get(
        "team_b",
        "Team B"
    )

    team_a_sends = data.get(
        "team_a_sends",
        []
    )

    team_b_sends = data.get(
        "team_b_sends",
        []
    )

    value_a_sent, breakdown_a = (
        calculate_package_value(
            team_a_sends
        )
    )

    value_b_sent, breakdown_b = (
        calculate_package_value(
            team_b_sends
        )
    )

    # Team A receives Team B's package
    value_a_received = value_b_sent

    # Team B receives Team A's package
    value_b_received = value_a_sent

    grade_a = trade_grade(
        value_a_received,
        value_a_sent
    )

    grade_b = trade_grade(
        value_b_received,
        value_b_sent
    )

    trade_id = data.get(
        "trade_id"
    )

    if not trade_id:
        trade_id = str(
            uuid.uuid4()
        )[:8]

    verdict, reaction = (
        generate_trade_reaction(
            team_a,
            team_b,
            grade_a,
            grade_b,
            value_a_received,
            value_b_received,
            breakdown_a,
            breakdown_b,
            trade_id
        )
    )

    if value_a_received > value_b_received:
        winner = team_a

    elif value_b_received > value_a_received:
        winner = team_b

    else:
        winner = "Even"

    return {
        "trade_id": trade_id,

        "team_a": team_a,
        "team_b": team_b,

        "team_a_sends": breakdown_a,
        "team_b_sends": breakdown_b,

        "team_a_value_sent": value_a_sent,
        "team_a_value_received": value_a_received,

        "team_b_value_sent": value_b_sent,
        "team_b_value_received": value_b_received,

        "team_a_grade": grade_a,
        "team_b_grade": grade_b,

        "winner": winner,

        "verdict": verdict,

        "reaction": reaction,

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return jsonify({
        "status": "online",
        "service": "Project Madden Analytics",
        "analyst_system": "online",
        "trade_analyst": "online",
        "player_system":
            "waiting_for_completed_game_data"
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

    if parts[-1] == "leagueteams":

        save_json_file(
            "leagueteams.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "leagueteams"
        }), 200

    if parts[-1] == "standings":

        save_json_file(
            "standings.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "standings"
        }), 200

    if parts[-1] == "extra":

        save_json_file(
            "extra.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "extra"
        }), 200

    if (
        "freeagents" in parts
        and parts[-1] == "roster"
    ):

        save_json_file(
            "freeagents_roster.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "freeagents"
        }), 200

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

        save_json_file(
            f"roster_{team_id}.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "roster",
            "team_id": team_id
        }), 200

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
# GAME ANALYST REACTIONS
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

    reactions = []

    for game in schedule_data.get(
        "gameScheduleInfoList",
        []
    ):

        game_data = classify_game_story(
            game,
            team_map
        )

        for story in game_data["stories"]:

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
                    generate_reaction(
                        game_data,
                        story,
                        season_type,
                        week_number
                    )
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
# TRADE ANALYST
# =========================================================

@app.route(
    "/analyst/trade",
    methods=["POST"]
)
def analyst_trade():

    data = request.get_json(
        silent=True
    )

    if not data:

        return jsonify({
            "error": "Trade JSON required"
        }), 400

    required = [
        "team_a",
        "team_b",
        "team_a_sends",
        "team_b_sends"
    ]

    missing = [
        field
        for field in required
        if field not in data
    ]

    if missing:

        return jsonify({
            "error":
                "Missing required fields",

            "missing":
                missing
        }), 400

    analysis = analyze_trade(
        data
    )

    history = load_json_file(
        "trades.json"
    )

    if not isinstance(
        history,
        list
    ):
        history = []

    history.append(
        analysis
    )

    save_json_file(
        "trades.json",
        history
    )

    return jsonify({
        "analyst":
            "Project Madden First Take",

        "analysis":
            analysis
    })


@app.route(
    "/analyst/trade/embed",
    methods=["POST"]
)
def analyst_trade_embed():

    data = request.get_json(
        silent=True
    )

    if not data:

        return jsonify({
            "error": "Trade JSON required"
        }), 400

    analysis = analyze_trade(
        data
    )

    team_a_assets = "\n".join(
        f"• {summarize_asset(asset)}"
        for asset in analysis[
            "team_a_sends"
        ]
    )

    team_b_assets = "\n".join(
        f"• {summarize_asset(asset)}"
        for asset in analysis[
            "team_b_sends"
        ]
    )

    return jsonify({
        "embeds": [
            {
                "title":
                    "🚨 PROJECT MADDEN TRADE ALERT",

                "description":
                    (
                        f"**{analysis['verdict']}**\n\n"
                        f"🎙️ {analysis['reaction']}"
                    ),

                "fields": [
                    {
                        "name":
                            (
                                f"{analysis['team_a']} sends"
                            ),

                        "value":
                            (
                                team_a_assets
                                if team_a_assets
                                else "Nothing listed"
                            ),

                        "inline":
                            True
                    },

                    {
                        "name":
                            (
                                f"{analysis['team_b']} sends"
                            ),

                        "value":
                            (
                                team_b_assets
                                if team_b_assets
                                else "Nothing listed"
                            ),

                        "inline":
                            True
                    },

                    {
                        "name":
                            "📊 Trade Grades",

                        "value":
                            (
                                f"**{analysis['team_a']}:** "
                                f"{analysis['team_a_grade']['grade']}\n"
                                f"**{analysis['team_b']}:** "
                                f"{analysis['team_b_grade']['grade']}"
                            ),

                        "inline":
                            False
                    }
                ],

                "footer": {
                    "text":
                        (
                            "Project Madden • "
                            "First Take Trade Desk"
                        )
                }
            }
        ]
    })


@app.route(
    "/analyst/trades"
)
def trade_history():

    history = load_json_file(
        "trades.json"
    )

    if not isinstance(
        history,
        list
    ):
        history = []

    return jsonify({
        "trade_count":
            len(history),

        "trades":
            history
    })


# =========================================================
# PLAYER DATA STATUS
# =========================================================

@app.route(
    "/analyst/players/<season_type>/<week_number>"
)
def player_status(
    season_type,
    week_number
):

    categories = {}

    ready = 0

    for stat_type in [
        "passing",
        "rushing",
        "receiving",
        "defense"
    ]:

        filename = get_weekly_file(
            season_type,
            week_number,
            stat_type
        )

        records = 0

        if os.path.exists(filename):

            with open(
                filename,
                "r"
            ) as f:

                data = json.load(f)

            if isinstance(data, dict):

                for value in data.values():

                    if isinstance(
                        value,
                        list
                    ):
                        records += len(value)

        categories[stat_type] = {
            "file_received":
                os.path.exists(filename),

            "records_found":
                records
        }

        if records > 0:
            ready += 1

    return jsonify({
        "analyst":
            "Project Madden First Take",

        "player_reaction_engine":
            (
                "data_available"
                if ready
                else
                "waiting_for_completed_game"
            ),

        "stat_categories_ready":
            ready,

        "categories":
            categories
    })


# =========================================================
# PLAYER SCHEMA INSPECTOR
# =========================================================

@app.route(
    "/analytics/player-schema/"
    "<season_type>/"
    "<week_number>/"
    "<stat_type>"
)
def player_schema(
    season_type,
    week_number,
    stat_type
):

    filename = get_weekly_file(
        season_type,
        week_number,
        stat_type
    )

    if not os.path.exists(filename):

        return jsonify({
            "error":
                "Stat file not found"
        }), 404

    with open(filename, "r") as f:
        data = json.load(f)

    lists_found = {}
    first_record = None
    first_list_name = None

    if isinstance(data, dict):

        for key, value in data.items():

            if isinstance(value, list):

                lists_found[key] = {
                    "count":
                        len(value)
                }

                if (
                    value
                    and first_record is None
                ):

                    first_record = value[0]
                    first_list_name = key

    response = {
        "season_type":
            season_type,

        "week":
            week_number,

        "stat_type":
            stat_type,

        "lists_found":
            lists_found
    }

    if first_record is None:

        response[
            "ready_for_player_mapping"
        ] = False

        response["message"] = (
            "No completed player stat records yet."
        )

    else:

        response[
            "ready_for_player_mapping"
        ] = True

        response[
            "player_list_name"
        ] = first_list_name

        response[
            "first_record_keys"
        ] = list(
            first_record.keys()
        )

        response[
            "first_record"
        ] = first_record

    return jsonify(response)


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

    if not os.path.exists(filename):

        return jsonify({
            "error":
                "Schedule data not found"
        }), 404

    with open(filename, "r") as f:
        schedule_data = json.load(f)

    team_map = get_team_map()

    games = []

    for game in schedule_data.get(
        "gameScheduleInfoList",
        []
    ):

        away = team_map.get(
            str(game.get("awayTeamId")),
            {}
        )

        home = team_map.get(
            str(game.get("homeTeamId")),
            {}
        )

        games.append({
            "schedule_id":
                game.get("scheduleId"),

            "away": {
                "team":
                    away.get(
                        "name",
                        "Unknown"
                    ),

                "overall":
                    away.get("overall"),

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
                    home.get("overall"),

                "score":
                    game.get(
                        "homeScore",
                        0
                    )
            }
        })

    return jsonify({
        "season_type":
            season_type,

        "week":
            week_number,

        "game_count":
            len(games),

        "games":
            games
    })


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
