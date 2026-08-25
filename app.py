from flask import Flask, request, jsonify, render_template_string
import json
import os
import hashlib
import uuid
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

    team_name = str(
        team_name
    ).strip().lower()

    for team in get_team_map().values():

        possible = [
            team.get("name"),
            team.get("nickname"),
            team.get("abbr"),
            team.get("city")
        ]

        for value in possible:

            if (
                value
                and str(value).strip().lower()
                == team_name
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

            if isinstance(item, dict):
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
        return str(full_name).strip()

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

    return str(value).upper()


def detect_overall(record):

    value = first_value(
        record,
        [
            "ovrRating",
            "overallRating",
            "overall",
            "ovr",
            "overall_rating"
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

    # Some Madden exports use numbers
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

    if "factor" in text:
        return "xfactor"

    if text in [
        "xf",
        "x-factor"
    ]:
        return "xfactor"

    if "superstar" in text:
        return "superstar"

    if "star" in text:
        return "star"

    return "normal"


# =========================================================
# PLAYER LOOKUP FROM SNALLABOT
# =========================================================

def get_team_roster(team_name):

    team = find_team(
        team_name
    )

    if not team:

        raise ValueError(
            f"Could not find team '{team_name}' "
            f"in Snallabot league teams."
        )

    team_id = team.get(
        "teamId"
    )

    filename = (
        f"roster_{team_id}.json"
    )

    roster = load_json_file(
        filename
    )

    if not roster:

        raise ValueError(
            f"No Snallabot roster data found for "
            f"the {team.get('name')}. "
            f"Run the Snallabot roster export first."
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

        # Avoid random nested records that happen to contain "name"
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

        seen.add(unique_key)

        players.append({
            "name": name,
            "position": position,
            "overall": overall,
            "age": age,
            "dev": detect_dev(record)
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

    # Exact match first
    exact = [
        player
        for player in players
        if player["name"].lower()
        == target
    ]

    if len(exact) == 1:
        player = exact[0]

    elif len(exact) > 1:
        player = exact[0]

    else:

        # Allow unique partial match
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
                p["name"]
                for p in partial[:8]
            )

            raise ValueError(
                f"'{player_name}' matched multiple "
                f"{team.get('name')} players: {names}. "
                f"Enter the full player name."
            )

        else:

            raise ValueError(
                f"Could not find '{player_name}' "
                f"on the {team.get('name')} roster."
            )

    missing = []

    if player.get(
        "overall"
    ) is None:
        missing.append("OVR")

    if not player.get(
        "position"
    ):
        missing.append("position")

    if player.get(
        "age"
    ) is None:
        missing.append("age")

    if missing:

        raise ValueError(
            f"Found {player['name']}, but Snallabot "
            f"did not provide: {', '.join(missing)}."
        )

    return {
        "type": "player",

        "name":
            player["name"],

        "position":
            player["position"],

        "overall":
            player["overall"],

        "age":
            player["age"],

        "dev":
            player.get(
                "dev",
                "normal"
            ),

        "source":
            "Snallabot roster"
    }


# =========================================================
# TRADE ASSET PARSER
# =========================================================

def parse_trade_assets(
    text,
    team_name
):

    assets = []

    for line in text.splitlines():

        line = line.strip()

        if not line:
            continue

        pieces = [
            x.strip()
            for x in line.split("|")
        ]

        asset_type = (
            pieces[0].lower()
        )

        # -------------------------------------------------
        # PLAYER
        # player|Lamar Jackson
        # -------------------------------------------------

        if asset_type == "player":

            if len(pieces) < 2:

                raise ValueError(
                    "Player format must be: "
                    "player|Player Name"
                )

            player_name = pieces[1]

            detected_player = (
                find_player_on_team(
                    team_name,
                    player_name
                )
            )

            assets.append(
                detected_player
            )

        # -------------------------------------------------
        # PICK
        # pick|2027|2|1
        # -------------------------------------------------

        elif asset_type == "pick":

            if len(pieces) < 3:

                raise ValueError(
                    "Pick format must be: "
                    "pick|Year|Round|YearsAway"
                )

            year = int(
                pieces[1]
            )

            round_number = int(
                pieces[2]
            )

            years_away = 0

            if len(pieces) >= 4:

                years_away = int(
                    pieces[3]
                )

            assets.append({
                "type": "pick",
                "year": year,
                "round": round_number,
                "years_away": years_away
            })

        else:

            raise ValueError(
                "Asset must start with "
                "'player' or 'pick'."
            )

    return assets


# =========================================================
# TRADE VALUE ENGINE
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
        (overall - 60)
        * 1.8
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
        max(
            value,
            1
        ),
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
            "calculated_value":
                value
        })

    return round(
        total,
        2
    ), breakdown


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
        "difference":
            round(
                difference,
                2
            ),

        "percentage":
            round(
                percentage,
                1
            )
    }


# =========================================================
# ANALYST TRADE REACTION
# =========================================================

def stable_choice(options, key):

    digest = hashlib.sha256(
        str(key).encode(
            "utf-8"
        )
    ).hexdigest()

    number = int(
        digest[:8],
        16
    )

    return options[
        number
        % len(options)
    ]


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
                "I can understand this move "
                "from both sides. The value is "
                "close enough that nobody got "
                "taken advantage of here."
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

        reactions = [
            (
                f"Hold on. What are the {loser} doing here? "
                f"The {winner} just walked away with significantly "
                f"better value. I'm calling this a robbery."
            ),

            (
                f"I do not like this deal for the {loser}. "
                f"Not even a little bit. The {winner} clearly "
                f"got the better package."
            ),

            (
                f"Somebody needs to explain this one to me. "
                f"The {winner} got the better end of this deal "
                f"and it really isn't close."
            )
        ]

    elif gap >= 20:

        verdict = (
            f"{winner} won the trade"
        )

        reactions = [
            (
                f"I understand what both teams are trying to do, "
                f"but I'm giving this deal to the {winner}. "
                f"The {loser} gave up more than I would've liked."
            ),

            (
                f"The {winner} came out ahead here. "
                f"I wouldn't call it robbery, but they definitely "
                f"got the better end of this deal."
            )
        ]

    elif gap >= 8:

        verdict = (
            f"Slight edge to {winner}"
        )

        reactions = [
            (
                f"This one is close, but if you're making me choose, "
                f"I'm giving the edge to the {winner}."
            )
        ]

    else:

        verdict = "Fair trade"

        reactions = [
            (
                "I don't see a clear loser here. "
                "The value is close enough that this deal "
                "is going to be judged by what happens on the field."
            )
        ]

    return (
        verdict,
        stable_choice(
            reactions,
            trade_id
        )
    )


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

    value_a_received = (
        value_b_sent
    )

    value_b_received = (
        value_a_sent
    )

    grade_a = trade_grade(
        value_a_received,
        value_a_sent
    )

    grade_b = trade_grade(
        value_b_received,
        value_b_sent
    )

    trade_id = data.get(
        "trade_id",
        str(uuid.uuid4())[:8]
    )

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

    if value_a_received > value_b_received:
        winner = team_a

    elif value_b_received > value_a_received:
        winner = team_b

    else:
        winner = "Even"

    return {
        "trade_id":
            trade_id,

        "team_a":
            team_a,

        "team_b":
            team_b,

        "team_a_mention":
            data["team_a_mention"],

        "team_b_mention":
            data["team_b_mention"],

        "team_a_sends":
            breakdown_a,

        "team_b_sends":
            breakdown_b,

        "team_a_grade":
            grade_a,

        "team_b_grade":
            grade_b,

        "winner":
            winner,

        "verdict":
            verdict,

        "reaction":
            reaction,

        "status":
            "PROPOSED",

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


# =========================================================
# DISPLAY ASSET
# =========================================================

def summarize_asset(asset):

    if asset["type"] == "pick":

        return (
            f"{asset['year']} "
            f"Round {asset['round']} Pick"
        )

    dev = asset.get(
        "dev",
        "normal"
    ).title()

    return (
        f"{asset['name']} — "
        f"{asset['overall']} OVR "
        f"{asset['position']} • "
        f"Age {asset['age']} • "
        f"{dev}"
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

        "snallabot":
            "connected",

        "trade_center":
            "/proposetrade",

        "automatic_roster_lookup":
            True
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
        })

    if parts[-1] == "standings":

        save_json_file(
            "standings.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "standings"
        })

    if parts[-1] == "extra":

        save_json_file(
            "extra.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "extra"
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
            "success": True,
            "type": "freeagents"
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
            "success": True,
            "type": "roster",
            "team_id": team_id
        })

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
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=2
            )

        return jsonify({
            "success": True,
            "type": "weekly",
            "stat_type": stat_type
        })

    return jsonify({
        "success": True,
        "type": "unknown",
        "path": subpath
    })


# =========================================================
# PLAYER LOOKUP API
# =========================================================

@app.route("/api/players")
def players_api():

    team_name = request.args.get(
        "team",
        ""
    )

    query = request.args.get(
        "q",
        ""
    ).lower()

    try:

        team, players = (
            build_roster_index(
                team_name
            )
        )

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 400

    if query:

        players = [
            p
            for p in players
            if query
            in p["name"].lower()
        ]

    return jsonify({
        "team":
            team.get("name"),

        "player_count":
            len(players),

        "players":
            players[:50]
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
    max-width: 820px;
    margin: auto;
    padding: 20px;
}

.title {
    text-align: center;
    font-size: 30px;
    font-weight: 800;
}

.subtitle {
    text-align: center;
    color: #aaa;
    margin-bottom: 25px;
}

.card {
    background: #15171d;
    border: 1px solid #2b2e38;
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
    border: 1px solid #444;
    border-radius: 8px;
    font-size: 16px;
}

textarea {
    min-height: 135px;
}

label {
    font-weight: bold;
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

.help {
    color: #aaa;
    font-size: 14px;
    line-height: 1.5;
}

.result {
    background: #111a14;
    border: 1px solid #345a3c;
    border-radius: 14px;
    padding: 20px;
}

.asset {
    margin-bottom: 7px;
}

.error {
    background: #41191c;
    border: 1px solid #86353b;
    border-radius: 10px;
    padding: 15px;
    margin-bottom: 20px;
}

.grade {
    font-size: 22px;
    font-weight: bold;
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
<strong>Error</strong><br><br>
{{ error }}
</div>

{% endif %}


{% if analysis %}

<div class="result">

<h2>🚨 TRADE PROPOSED</h2>

<p>
{{ analysis.team_a_mention }}
↔
{{ analysis.team_b_mention }}
</p>


<h3>{{ analysis.team_a }} Sends</h3>

{% for asset in analysis.team_a_sends %}

<div class="asset">
• {{ summarize(asset) }}
</div>

{% endfor %}


<h3>{{ analysis.team_b }} Sends</h3>

{% for asset in analysis.team_b_sends %}

<div class="asset">
• {{ summarize(asset) }}
</div>

{% endfor %}


<hr>


<h3>📊 Trade Grades</h3>

<div class="grade">

{{ analysis.team_a }}:
{{ analysis.team_a_grade.grade }}

<br>

{{ analysis.team_b }}:
{{ analysis.team_b_grade.grade }}

</div>


<h3>🏆 Verdict</h3>

<strong>
{{ analysis.verdict }}
</strong>


<h3>🎙️ Project Madden First Take</h3>

<p>
{{ analysis.reaction }}
</p>


<p>
Status: <strong>PROPOSED</strong>
</p>

</div>

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
placeholder="@EBKJayyyyy"
required>


<label>Assets Being Sent</label>

<textarea
name="team_a_assets"
placeholder="player|Lamar Jackson
player|Zay Flowers
pick|2027|2|1"
required></textarea>


<div class="help">

For players, ONLY enter:

<br><br>

<strong>
player|Player Name
</strong>

<br><br>

OVR, age, position and development trait are automatically pulled from Snallabot.

<br><br>

For picks:

<br><br>

<strong>
pick|Year|Round|YearsAway
</strong>

</div>

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


<label>Assets Being Sent</label>

<textarea
name="team_b_assets"
placeholder="player|Patrick Mahomes
pick|2027|1|1"
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
    methods=["GET", "POST"]
)
def propose_trade():

    if request.method == "GET":

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=None,
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
            summarize=summarize_asset
        )

    if not mention_b.startswith("@"):

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error="Team B must include a Discord @.",
            summarize=summarize_asset
        )

    if team_a.lower() == team_b.lower():

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error="A team cannot trade with itself.",
            summarize=summarize_asset
        )

    try:

        team_a_assets = (
            parse_trade_assets(
                request.form.get(
                    "team_a_assets",
                    ""
                ),
                team_a
            )
        )

        team_b_assets = (
            parse_trade_assets(
                request.form.get(
                    "team_b_assets",
                    ""
                ),
                team_b
            )
        )

    except Exception as e:

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=str(e),
            summarize=summarize_asset
        )

    trade_data = {
        "team_a":
            team_a,

        "team_b":
            team_b,

        "team_a_mention":
            mention_a,

        "team_b_mention":
            mention_b,

        "team_a_sends":
            team_a_assets,

        "team_b_sends":
            team_b_assets
    }

    analysis = analyze_trade(
        trade_data
    )

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

    return render_template_string(
        TRADE_PAGE,
        analysis=analysis,
        error=None,
        summarize=summarize_asset
    )


# =========================================================
# TRADE HISTORY
# =========================================================

@app.route(
    "/analyst/trade-proposals"
)
def trade_proposals():

    proposals = load_json_file(
        "trade_proposals.json"
    )

    if not isinstance(
        proposals,
        list
    ):
        proposals = []

    return jsonify({
        "proposal_count":
            len(proposals),

        "proposals":
            proposals
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

    for stat_type in [
        "passing",
        "rushing",
        "receiving",
        "defense"
    ]:

        path = os.path.join(
            DATA_DIR,
            "weekly",
            season_type,
            f"week_{week_number}",
            f"{stat_type}.json"
        )

        records = 0

        if os.path.exists(path):

            with open(
                path,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)

            if isinstance(
                data,
                dict
            ):

                for value in data.values():

                    if isinstance(
                        value,
                        list
                    ):

                        records += len(
                            value
                        )

        categories[
            stat_type
        ] = {
            "file_received":
                os.path.exists(path),

            "records_found":
                records
        }

    return jsonify({
        "analyst":
            "Project Madden First Take",

        "categories":
            categories
    })


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
