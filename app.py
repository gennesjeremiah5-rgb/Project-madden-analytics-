from flask import Flask, request, jsonify, render_template_string
import json
import os
import hashlib
import uuid
import re
import requests
from datetime import datetime, timezone

app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


# =========================================================
# FILE HELPERS
# =========================================================

def load_json_file(filename):
    path = os.path.join(DATA_DIR, filename)

    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(filename, data):
    path = os.path.join(DATA_DIR, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def stable_choice(options, key):
    digest = hashlib.sha256(
        str(key).encode("utf-8")
    ).hexdigest()

    number = int(
        digest[:8],
        16
    )

    return options[
        number % len(options)
    ]


# =========================================================
# TEAM DATA
# =========================================================

def get_team_map():
    data = load_json_file(
        "leagueteams.json"
    )

    if not data:
        return {}

    teams = {}

    for team in data.get(
        "leagueTeamInfoList",
        []
    ):

        teams[str(team.get("teamId"))] = {
            "teamId": team.get("teamId"),
            "abbr": team.get("abbrName"),
            "city": team.get("cityName"),
            "name": team.get("displayName"),
            "nickname": team.get("nickName"),
            "overall": team.get("ovrRating"),
            "user": team.get("userName", "")
        }

    return teams


def find_team(team_name):
    target = str(
        team_name
    ).strip().lower()

    for team in get_team_map().values():

        options = [
            team.get("name"),
            team.get("nickname"),
            team.get("abbr"),
            team.get("city")
        ]

        for value in options:

            if (
                value
                and str(value).strip().lower()
                == target
            ):
                return team

    return None


# =========================================================
# ROSTER SCANNER
# =========================================================

def recursive_records(obj):
    records = []

    if isinstance(obj, list):

        for item in obj:

            if isinstance(
                item,
                dict
            ):
                records.append(item)

            records.extend(
                recursive_records(item)
            )

    elif isinstance(obj, dict):

        for value in obj.values():

            if isinstance(
                value,
                (list, dict)
            ):
                records.extend(
                    recursive_records(value)
                )

    return records


def first_value(record, keys):
    for key in keys:

        if key in record:

            value = record.get(key)

            if value is not None:
                return value

    return None


def detect_player_name(record):
    full_name = first_value(
        record,
        [
            "fullName",
            "playerName",
            "displayName",
            "name",
            "full_name",
            "player_name"
        ]
    )

    if full_name:
        return str(
            full_name
        ).strip()

    first = first_value(
        record,
        [
            "firstName",
            "first_name",
            "firstname"
        ]
    )

    last = first_value(
        record,
        [
            "lastName",
            "last_name",
            "lastname"
        ]
    )

    if first and last:
        return (
            f"{first} {last}"
        ).strip()

    return None


def detect_position(record):
    value = first_value(
        record,
        [
            "position",
            "positionAbbr",
            "positionName",
            "pos",
            "position_abbr"
        ]
    )

    if value is None:
        return None

    return str(
        value
    ).upper()


def detect_overall(record):
    value = first_value(
        record,
        [
            "playerBestOvr",
            "playerSchemeOvr",
            "teamSchemeOvr",

            "ovrRating",
            "overallRating",
            "overall",
            "ovr",
            "overall_rating",
            "playerOverall",
            "overallPlayerRating",
            "overallPlayer",
            "overallValue",
            "ratingOverall",
            "playerOverallRating",
            "ovr_rating"
        ]
    )

    try:
        return int(value)

    except (TypeError, ValueError):
        return None


def detect_age(record):
    value = first_value(
        record,
        [
            "age",
            "playerAge",
            "player_age"
        ]
    )

    try:
        return int(value)

    except (TypeError, ValueError):
        return None


def detect_dev(record):
    value = first_value(
        record,
        [
            "devTrait",
            "developmentTrait",
            "development",
            "dev",
            "dev_trait"
        ]
    )

    if value is None:
        return "normal"

    if isinstance(value, int):

        mapping = {
            0: "normal",
            1: "star",
            2: "superstar",
            3: "xfactor"
        }

        return mapping.get(
            value,
            "normal"
        )

    text = str(
        value
    ).strip().lower()

    if (
        "factor" in text
        or text == "xf"
    ):
        return "xfactor"

    if "superstar" in text:
        return "superstar"

    if "star" in text:
        return "star"

    return "normal"


# =========================================================
# TEAM ROSTERS
# =========================================================

def get_team_roster(team_name):
    team = find_team(
        team_name
    )

    if not team:
        raise ValueError(
            f"Could not find team '{team_name}' "
            f"in the Snallabot league data."
        )

    team_id = team.get(
        "teamId"
    )

    roster = load_json_file(
        f"roster_{team_id}.json"
    )

    if not roster:
        raise ValueError(
            f"No Snallabot roster found for "
            f"the {team.get('name')}. "
            f"Run the Snallabot roster export again."
        )

    return team, roster


def build_roster_index(team_name):
    team, roster = get_team_roster(
        team_name
    )

    records = recursive_records(
        roster
    )

    players = []
    seen = set()

    for record in records:

        name = detect_player_name(
            record
        )

        overall = detect_overall(
            record
        )

        position = detect_position(
            record
        )

        age = detect_age(
            record
        )

        if not name:
            continue

        if (
            overall is None
            and position is None
            and age is None
        ):
            continue

        unique_key = (
            name.lower(),
            position,
            overall
        )

        if unique_key in seen:
            continue

        seen.add(
            unique_key
        )

        players.append({
            "name": name,
            "position": position,
            "overall": overall,
            "age": age,
            "dev": detect_dev(
                record
            )
        })

    return team, players


def find_player_on_team(
    team_name,
    player_name
):

    team, players = build_roster_index(
        team_name
    )

    target = (
        player_name
        .strip()
        .lower()
    )

    exact = [
        player
        for player in players
        if player["name"].lower()
        == target
    ]

    if len(exact) >= 1:
        player = exact[0]

    else:
        partial = [
            player
            for player in players
            if target
            in player["name"].lower()
        ]

        if len(partial) == 1:
            player = partial[0]

        elif len(partial) > 1:
            names = ", ".join(
                player["name"]
                for player
                in partial[:8]
            )

            raise ValueError(
                f"'{player_name}' matched multiple "
                f"{team.get('name')} players: "
                f"{names}. Enter the full name."
            )

        else:
            raise ValueError(
                f"Could not find '{player_name}' "
                f"on the {team.get('name')} roster."
            )

    missing = []

    if player.get("overall") is None:
        missing.append("OVR")

    if player.get("age") is None:
        missing.append("age")

    if not player.get("position"):
        missing.append("position")

    if missing:
        raise ValueError(
            f"Found {player['name']}, "
            f"but Snallabot did not provide: "
            f"{', '.join(missing)}."
        )

    return {
        "type": "player",
        "name": player["name"],
        "position": player["position"],
        "overall": player["overall"],
        "age": player["age"],
        "dev": player.get(
            "dev",
            "normal"
        ),
        "source": "Snallabot roster"
    }


# =========================================================
# PICK / ASSET PARSER
# =========================================================

def parse_easy_pick(line):
    clean = (
        line.strip()
        .lower()
        .replace(",", " ")
    )

    year_match = re.search(
        r"\b(20\d{2})\b",
        clean
    )

    if not year_match:
        return None

    year = int(
        year_match.group(1)
    )

    round_match = re.search(
        r"(?:round\s*)?([1-7])(?:st|nd|rd|th)?",
        clean
    )

    if not round_match:
        return None

    round_number = int(
        round_match.group(1)
    )

    current_year = datetime.now().year

    years_away = max(
        0,
        year - current_year
    )

    return {
        "type": "pick",
        "year": year,
        "round": round_number,
        "years_away": years_away
    }


def parse_trade_assets(
    text,
    team_name
):

    assets = []

    for line in text.splitlines():

        line = line.strip()

        if not line:
            continue

        pick = parse_easy_pick(
            line
        )

        if pick:
            assets.append(
                pick
            )
            continue

        player = find_player_on_team(
            team_name,
            line
        )

        assets.append(
            player
        )

    return assets


# =========================================================
# VALUE SETTINGS
# =========================================================

DEV_VALUES = {
    "normal": 0,
    "star": 5,
    "superstar": 10,
    "xfactor": 16
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


# =========================================================
# VALUE ENGINE
# =========================================================

def calculate_player_value(asset):
    overall = float(
        asset["overall"]
    )

    age = int(
        asset["age"]
    )

    position = str(
        asset["position"]
    ).upper()

    dev = str(
        asset.get(
            "dev",
            "normal"
        )
    ).lower()

    value = max(
        1,
        (
            overall - 60
        ) * 1.8
    )

    value += DEV_VALUES.get(
        dev,
        0
    )

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

    value *= (
        POSITION_MULTIPLIERS.get(
            position,
            1.0
        )
    )

    return round(
        max(value, 1),
        2
    )


def calculate_pick_value(asset):
    round_number = int(
        asset["round"]
    )

    years_away = int(
        asset.get(
            "years_away",
            0
        )
    )

    value = PICK_VALUES.get(
        round_number,
        1
    )

    if years_away > 0:
        value *= (
            0.90
            ** years_away
        )

    return round(
        value,
        2
    )


def calculate_asset_value(asset):
    if asset["type"] == "pick":
        return calculate_pick_value(
            asset
        )

    return calculate_player_value(
        asset
    )


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

    return (
        round(total, 2),
        breakdown
    )


# =========================================================
# GRADES + COMMITTEE
# =========================================================

def trade_grade(
    received,
    sent
):
    difference = (
        received - sent
    )

    if sent <= 0:
        percentage = 100

    else:
        percentage = (
            difference / sent
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


def committee_review(
    team_a,
    team_b,
    value_a,
    value_b
):
    highest = max(
        value_a,
        value_b
    )

    lowest = min(
        value_a,
        value_b
    )

    if highest <= 0:
        gap_percent = 0

    else:
        gap_percent = (
            (
                highest - lowest
            )
            / highest
        ) * 100

    gap_percent = round(
        gap_percent,
        1
    )

    if value_a > value_b:
        advantage_team = team_a
        disadvantage_team = team_b

    elif value_b > value_a:
        advantage_team = team_b
        disadvantage_team = team_a

    else:
        advantage_team = None
        disadvantage_team = None

    if gap_percent <= 10:
        decision = "AUTO APPROVE"
        level = "GOOD"
        emoji = "✅"

        reason = (
            "The trade packages are close enough "
            "in calculated value for automatic approval."
        )

    elif gap_percent <= 20:
        decision = "COMMITTEE REVIEW"
        level = "QUESTIONABLE"
        emoji = "🟡"

        reason = (
            f"The value difference is {gap_percent}%."
        )

    elif gap_percent < 35:
        decision = "STRONG COMMITTEE REVIEW"
        level = "BAD"
        emoji = "🟠"

        reason = (
            f"The packages have a {gap_percent}% "
            f"value difference."
        )

    else:
        decision = "AUTO DENY"
        level = "VERY BAD"
        emoji = "❌"

        reason = (
            f"The trade has a {gap_percent}% "
            f"value difference."
        )

    return {
        "decision": decision,
        "level": level,
        "emoji": emoji,
        "value_gap_percent": gap_percent,
        "advantage_team": advantage_team,
        "disadvantage_team": disadvantage_team,
        "reason": reason
    }


# =========================================================
# ANALYST REACTION
# =========================================================

def generate_trade_reaction(
    team_a,
    team_b,
    grade_a,
    grade_b,
    value_a_received,
    value_b_received,
    trade_id
):
    if value_a_received > value_b_received:
        winner = team_a
        loser = team_b

    elif value_b_received > value_a_received:
        winner = team_b
        loser = team_a

    else:
        return (
            "Even trade",
            (
                "I can understand this deal from both sides. "
                "The value is close enough that neither team "
                "looks like it got taken advantage of."
            )
        )

    gap = max(
        abs(
            grade_a["percentage"]
        ),
        abs(
            grade_b["percentage"]
        )
    )

    if gap >= 35:
        verdict = (
            f"{winner} won — major steal"
        )

        choices = [
            (
                f"Hold on. What are the {loser} doing here? "
                f"The {winner} are walking away with the better value "
                f"and it really isn't close. I'm calling this a robbery."
            ),
            (
                f"I do not like this for the {loser}. "
                f"The {winner} clearly got the better package. "
                f"Somebody has to explain this one to me."
            )
        ]

    elif gap >= 20:
        verdict = (
            f"{winner} won the trade"
        )

        choices = [
            (
                f"I understand the thinking, but I'm giving "
                f"this deal to the {winner}. "
                f"The {loser} gave up more value than I would've liked."
            )
        ]

    elif gap >= 8:
        verdict = (
            f"Slight edge to {winner}"
        )

        choices = [
            (
                f"This one is close, but if you're forcing me "
                f"to choose a winner, I'm taking the {winner}."
            )
        ]

    else:
        verdict = "Fair trade"

        choices = [
            (
                "I don't see a clear loser here. "
                "The value is close enough that this trade "
                "will ultimately be judged by what happens on the field."
            )
        ]

    return (
        verdict,
        stable_choice(
            choices,
            trade_id
        )
    )


# =========================================================
# ANALYZE TRADE
# =========================================================

def analyze_trade(data):
    team_a = data["team_a"]
    team_b = data["team_b"]

    team_a_sends = data[
        "team_a_sends"
    ]

    team_b_sends = data[
        "team_b_sends"
    ]

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

    value_a_received = value_b_sent
    value_b_received = value_a_sent

    grade_a = trade_grade(
        value_a_received,
        value_a_sent
    )

    grade_b = trade_grade(
        value_b_received,
        value_b_sent
    )

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
            trade_id
        )
    )

    committee = committee_review(
        team_a,
        team_b,
        value_a_received,
        value_b_received
    )

    return {
        "trade_id": trade_id,

        "team_a": team_a,
        "team_b": team_b,

        "team_a_mention":
            data["team_a_mention"],

        "team_b_mention":
            data["team_b_mention"],

        "team_a_sends":
            breakdown_a,

        "team_b_sends":
            breakdown_b,

        "team_a_value_sent":
            value_a_sent,

        "team_b_value_sent":
            value_b_sent,

        "team_a_grade":
            grade_a,

        "team_b_grade":
            grade_b,

        "verdict":
            verdict,

        "reaction":
            reaction,

        "trade_committee":
            committee,

        "status":
            "PROPOSED",

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


# =========================================================
# DISPLAY
# =========================================================

def summarize_asset(asset):
    if asset["type"] == "pick":
        return (
            f"{asset['year']} "
            f"Round {asset['round']} Pick"
        )

    dev = str(
        asset.get(
            "dev",
            "normal"
        )
    ).replace(
        "xfactor",
        "X-Factor"
    ).replace(
        "superstar",
        "Superstar"
    ).replace(
        "star",
        "Star"
    ).replace(
        "normal",
        "Normal"
    )

    return (
        f"{asset['name']} — "
        f"{asset['overall']} OVR "
        f"{asset['position']} • "
        f"Age {asset['age']} • "
        f"{dev}"
    )


# =========================================================
# DISCORD WEBHOOK
# =========================================================

def post_trade_to_discord(
    analysis
):
    webhook_url = os.environ.get(
        "DISCORD_WEBHOOK_URL"
    )

    if not webhook_url:
        return {
            "sent": False,
            "error":
                "DISCORD_WEBHOOK_URL not configured"
        }

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

    committee = analysis[
        "trade_committee"
    ]

    payload = {
        "content":
            (
                f"{analysis['team_a_mention']} "
                f"{analysis['team_b_mention']}"
            ),

        "embeds": [
            {
                "title":
                    "🚨 PROJECT MADDEN TRADE PROPOSAL",

                "description":
                    (
                        f"**{analysis['team_a']} ↔ "
                        f"{analysis['team_b']}**\n\n"
                        f"Trade ID: `{analysis['trade_id']}`"
                    ),

                "fields": [
                    {
                        "name":
                            f"{analysis['team_a']} Sends",

                        "value":
                            team_a_assets,

                        "inline":
                            False
                    },

                    {
                        "name":
                            f"{analysis['team_b']} Sends",

                        "value":
                            team_b_assets,

                        "inline":
                            False
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
                    },

                    {
                        "name":
                            "🏛️ Trade Committee",

                        "value":
                            (
                                f"{committee['emoji']} "
                                f"**{committee['decision']}**\n"
                                f"Quality: {committee['level']}\n"
                                f"Value Gap: "
                                f"{committee['value_gap_percent']}%\n"
                                f"{committee['reason']}"
                            ),

                        "inline":
                            False
                    },

                    {
                        "name":
                            "🎙️ Project Madden First Take",

                        "value":
                            (
                                f"**{analysis['verdict']}**\n\n"
                                f"{analysis['reaction']}"
                            ),

                        "inline":
                            False
                    }
                ],

                "footer": {
                    "text":
                        "Project Madden • Trade Center"
                }
            }
        ]
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10
        )

        if response.status_code in [
            200,
            204
        ]:
            return {
                "sent": True
            }

        return {
            "sent": False,
            "error":
                (
                    f"Discord returned "
                    f"{response.status_code}"
                )
        }

    except Exception as e:
        return {
            "sent": False,
            "error": str(e)
        }


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "Project Madden Analytics",
        "snallabot": "connected",
        "trade_center": "/proposetrade",
        "discord_webhook_configured":
            bool(
                os.environ.get(
                    "DISCORD_WEBHOOK_URL"
                )
            )
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
    methods=[
        "GET",
        "POST",
        "PUT"
    ]
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

    parts = subpath.split(
        "/"
    )

    if parts[-1] == "leagueteams":
        save_json_file(
            "leagueteams.json",
            data
        )

        return jsonify({
            "success": True
        })

    if parts[-1] == "standings":
        save_json_file(
            "standings.json",
            data
        )

        return jsonify({
            "success": True
        })

    if parts[-1] == "extra":
        save_json_file(
            "extra.json",
            data
        )

        return jsonify({
            "success": True
        })

    if (
        "freeagents" in parts
        and parts[-1] == "roster"
    ):
        save_json_file(
            "freeagents_roster.json",
            data
        )

        return jsonify({
            "success": True
        })

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
            "success": True
        })

    if "week" in parts:
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
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                indent=2
            )

        return jsonify({
            "success": True
        })

    return jsonify({
        "success": True
    })


# =========================================================
# TRADE PAGE
# =========================================================

TRADE_PAGE = """
<!DOCTYPE html>
<html>
<head>

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Project Madden Trade Center</title>

<style>

body {
    margin: 0;
    background: #0b0c10;
    color: white;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 850px;
    margin: auto;
    padding: 20px;
}

.title {
    text-align: center;
    font-size: 32px;
    font-weight: 900;
}

.subtitle {
    text-align: center;
    color: #aaa;
    margin-bottom: 25px;
}

.card,
.result,
.committee {
    background: #15171d;
    border: 1px solid #2c303b;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 20px;
}

input,
textarea {
    width: 100%;
    box-sizing: border-box;
    padding: 13px;
    margin-top: 6px;
    margin-bottom: 12px;
    background: #0d0f14;
    color: white;
    border: 1px solid #454853;
    border-radius: 8px;
    font-size: 16px;
}

textarea {
    min-height: 140px;
}

button {
    width: 100%;
    padding: 16px;
    background: #5865F2;
    border: none;
    border-radius: 9px;
    color: white;
    font-size: 18px;
    font-weight: bold;
}

.error {
    background: #43191c;
    padding: 15px;
    border-radius: 10px;
}

.success {
    background: #14351b;
    padding: 15px;
    border-radius: 10px;
}

</style>

</head>

<body>

<div class="container">

<div class="title">
🏈 PROJECT MADDEN
</div>

<div class="subtitle">
Trade Proposal Center
</div>

{% if error %}

<div class="error">
{{ error }}
</div>

{% endif %}


{% if analysis %}

<div class="result">

<h2>🚨 Trade Proposed</h2>

<h3>{{ analysis.team_a }} Sends</h3>

{% for asset in analysis.team_a_sends %}
<p>• {{ summarize(asset) }}</p>
{% endfor %}

<h3>{{ analysis.team_b }} Sends</h3>

{% for asset in analysis.team_b_sends %}
<p>• {{ summarize(asset) }}</p>
{% endfor %}

<h3>📊 Grades</h3>

<p>
{{ analysis.team_a }}:
<strong>
{{ analysis.team_a_grade.grade }}
</strong>
</p>

<p>
{{ analysis.team_b }}:
<strong>
{{ analysis.team_b_grade.grade }}
</strong>
</p>

<h3>🎙️ First Take</h3>

<p>
{{ analysis.reaction }}
</p>

</div>


<div class="committee">

<h2>
🏛️ Trade Committee
</h2>

<h2>
{{ analysis.trade_committee.emoji }}
{{ analysis.trade_committee.decision }}
</h2>

<p>
Value Gap:
{{ analysis.trade_committee.value_gap_percent }}%
</p>

<p>
{{ analysis.trade_committee.reason }}
</p>

</div>


{% if discord.sent %}

<div class="success">
✅ Trade automatically posted to Discord.
</div>

{% else %}

<div class="error">
⚠️ Trade saved, but Discord post failed:
{{ discord.error }}
</div>

{% endif %}

<br>

<a href="/proposetrade">
<button>
Propose Another Trade
</button>
</a>


{% else %}


<form method="POST">

<div class="card">

<h2>TEAM A</h2>

<label>Team</label>

<input
name="team_a"
placeholder="Ravens"
required>

<label>Discord @</label>

<input
name="team_a_mention"
placeholder="@RavensOwner"
required>

<label>Assets</label>

<textarea
name="team_a_assets"
placeholder="Lamar Jackson
Zay Flowers
2027 Round 2"
required></textarea>

</div>


<div class="card">

<h2>TEAM B</h2>

<label>Team</label>

<input
name="team_b"
placeholder="Chiefs"
required>

<label>Discord @</label>

<input
name="team_b_mention"
placeholder="@ChiefsOwner"
required>

<label>Assets</label>

<textarea
name="team_b_assets"
placeholder="Patrick Mahomes
2027 Round 1"
required></textarea>

</div>


<button type="submit">
🚨 PROPOSE TRADE
</button>

</form>

{% endif %}

</div>

</body>
</html>
"""


@app.route(
    "/proposetrade",
    methods=[
        "GET",
        "POST"
    ]
)
def propose_trade():
    if request.method == "GET":
        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=None,
            discord=None,
            summarize=summarize_asset
        )

    team_a = request.form.get(
        "team_a",
        ""
    ).strip()

    team_b = request.form.get(
        "team_b",
        ""
    ).strip()

    mention_a = request.form.get(
        "team_a_mention",
        ""
    ).strip()

    mention_b = request.form.get(
        "team_b_mention",
        ""
    ).strip()

    if not mention_a.startswith("@"):
        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error="Team A must include a Discord @.",
            discord=None,
            summarize=summarize_asset
        )

    if not mention_b.startswith("@"):
        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error="Team B must include a Discord @.",
            discord=None,
            summarize=summarize_asset
        )

    try:
        team_a_assets = parse_trade_assets(
            request.form.get(
                "team_a_assets",
                ""
            ),
            team_a
        )

        team_b_assets = parse_trade_assets(
            request.form.get(
                "team_b_assets",
                ""
            ),
            team_b
        )

    except Exception as e:
        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=str(e),
            discord=None,
            summarize=summarize_asset
        )

    analysis = analyze_trade({
        "team_a": team_a,
        "team_b": team_b,
        "team_a_mention": mention_a,
        "team_b_mention": mention_b,
        "team_a_sends": team_a_assets,
        "team_b_sends": team_b_assets
    })

    proposals = load_json_file(
        "trade_proposals.json"
    )

    if not isinstance(
        proposals,
        list
    ):
        proposals = []

    proposals.append(
        analysis
    )

    save_json_file(
        "trade_proposals.json",
        proposals
    )

    discord_result = (
        post_trade_to_discord(
            analysis
        )
    )

    return render_template_string(
        TRADE_PAGE,
        analysis=analysis,
        error=None,
        discord=discord_result,
        summarize=summarize_asset
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
