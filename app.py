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
# HELPERS
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
# TRADE VALUE SETTINGS
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


# =========================================================
# TRADE VALUE ENGINE
# =========================================================

def calculate_player_value(asset):

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

    value = max(
        1,
        (overall - 60) * 1.8
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

    value *= POSITION_MULTIPLIERS.get(
        position,
        1.0
    )

    return round(
        max(value, 1),
        2
    )


def calculate_pick_value(asset):

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
        return calculate_pick_value(asset)

    return calculate_player_value(asset)


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
        value_received - value_sent
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

    if asset.get("type") == "pick":

        return (
            f"{asset.get('year')} "
            f"Round {asset.get('round')} Pick"
        )

    return (
        f"{asset.get('name')} "
        f"({asset.get('overall')} OVR "
        f"{asset.get('position')})"
    )


# =========================================================
# PARSE BROWSER TRADE ASSETS
# =========================================================

def parse_trade_assets(text):

    assets = []

    lines = text.splitlines()

    for line in lines:

        line = line.strip()

        if not line:
            continue

        parts = [
            p.strip()
            for p in line.split("|")
        ]

        asset_type = (
            parts[0].lower()
            if parts
            else ""
        )

        # player|Name|POS|OVR|AGE|DEV

        if asset_type == "player":

            if len(parts) < 6:
                raise ValueError(
                    "Player format must be: "
                    "player|Name|POS|OVR|Age|Dev"
                )

            assets.append({
                "type": "player",
                "name": parts[1],
                "position": parts[2],
                "overall": int(parts[3]),
                "age": int(parts[4]),
                "dev": parts[5]
            })

        # pick|2027|2|1

        elif asset_type == "pick":

            if len(parts) < 3:
                raise ValueError(
                    "Pick format must be: "
                    "pick|Year|Round|YearsAway"
                )

            years_away = 0

            if len(parts) >= 4:
                years_away = int(
                    parts[3]
                )

            assets.append({
                "type": "pick",
                "year": int(parts[1]),
                "round": int(parts[2]),
                "years_away": years_away
            })

        else:

            raise ValueError(
                "Every asset must start with "
                "'player' or 'pick'."
            )

    return assets


# =========================================================
# TRADE REACTION
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
                "The value is close enough that nobody got robbed here."
            )
        )

    gap = max(
        abs(grade_a["percentage"]),
        abs(grade_b["percentage"])
    )

    if gap >= 35:

        verdict = (
            f"{winner} won — major steal"
        )

        reaction = stable_choice([
            (
                f"Hold on. What are {team_phrase(loser)} doing? "
                f"{team_phrase_start(winner)} just walked away "
                f"with significantly better value. "
                f"I'm calling this a robbery."
            ),

            (
                f"I do not like this deal for {team_phrase(loser)}. "
                f"Not even a little bit. "
                f"{team_phrase_start(winner)} clearly got the better package."
            ),

            (
                f"Somebody needs to explain this one to me. "
                f"{team_phrase_start(winner)} got the better end of this deal "
                f"and it really isn't close."
            )
        ], trade_id)

    elif gap >= 20:

        verdict = (
            f"{winner} won the trade"
        )

        reaction = stable_choice([
            (
                f"I understand the thinking on both sides, "
                f"but I'm giving this deal to {team_phrase(winner)}. "
                f"{team_phrase_start(loser)} gave up more value than I would've liked."
            ),

            (
                f"{team_phrase_start(winner)} came out ahead. "
                f"I wouldn't call it a robbery, but they definitely "
                f"got the better end of the deal."
            )
        ], trade_id)

    elif gap >= 8:

        verdict = (
            f"Slight edge to {winner}"
        )

        reaction = (
            f"This is pretty close. "
            f"If you're forcing me to pick a winner, "
            f"I'm taking {team_phrase(winner)} by a small margin."
        )

    else:

        verdict = "Fair trade"

        reaction = (
            "I don't see a clear loser here. "
            "The value is close enough that this is going to come down "
            "to how these players actually perform."
        )

    return verdict, reaction


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
        "trade_id": trade_id,

        "team_a": team_a,
        "team_b": team_b,

        "team_a_mention":
            data.get(
                "team_a_mention",
                ""
            ),

        "team_b_mention":
            data.get(
                "team_b_mention",
                ""
            ),

        "team_a_sends":
            breakdown_a,

        "team_b_sends":
            breakdown_b,

        "team_a_value_sent":
            value_a_sent,

        "team_a_value_received":
            value_a_received,

        "team_b_value_sent":
            value_b_sent,

        "team_b_value_received":
            value_b_received,

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
# HOME
# =========================================================

@app.route("/")
def home():

    return jsonify({
        "status": "online",

        "service":
            "Project Madden Analytics",

        "snallabot":
            "connected",

        "trade_desk":
            "online",

        "propose_trade_page":
            "/proposetrade"
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

        save_json_file(
            "leagueteams.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "leagueteams"
        }), 200

    # Standings
    if parts[-1] == "standings":

        save_json_file(
            "standings.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "standings"
        }), 200

    # Extra
    if parts[-1] == "extra":

        save_json_file(
            "extra.json",
            data
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

        save_json_file(
            "freeagents_roster.json",
            data
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

        save_json_file(
            f"roster_{team_id}.json",
            data
        )

        return jsonify({
            "success": True,
            "type": "roster",
            "team_id": team_id
        }), 200

    # Weekly Snallabot exports
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
# PROPOSE TRADE WEBSITE
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
    background: #0c0c0f;
    color: white;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 800px;
    margin: auto;
    padding: 20px;
}

.logo {
    text-align: center;
    font-size: 30px;
    font-weight: bold;
    margin-bottom: 5px;
}

.subtitle {
    text-align: center;
    color: #aaa;
    margin-bottom: 30px;
}

.card {
    background: #17171c;
    border: 1px solid #2d2d34;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 20px;
}

h2 {
    margin-top: 0;
}

label {
    display: block;
    font-weight: bold;
    margin-top: 15px;
    margin-bottom: 6px;
}

input,
textarea {
    width: 100%;
    box-sizing: border-box;
    padding: 13px;
    border-radius: 8px;
    border: 1px solid #444;
    background: #0e0e12;
    color: white;
    font-size: 16px;
}

textarea {
    min-height: 140px;
}

button {
    width: 100%;
    margin-top: 20px;
    padding: 16px;
    border: none;
    border-radius: 9px;
    background: #5865F2;
    color: white;
    font-size: 18px;
    font-weight: bold;
}

.help {
    color: #aaa;
    font-size: 13px;
    line-height: 1.5;
}

.error {
    background: #461919;
    border: 1px solid #a33434;
    border-radius: 10px;
    padding: 15px;
    margin-bottom: 20px;
}

.result {
    background: #151a15;
    border: 1px solid #325a32;
    border-radius: 12px;
    padding: 20px;
}

.grade {
    font-size: 24px;
    font-weight: bold;
}

.mention {
    color: #9da7ff;
    font-weight: bold;
}

.assets {
    white-space: pre-line;
}

</style>

</head>

<body>

<div class="container">

<div class="logo">
🏈 PROJECT MADDEN
</div>

<div class="subtitle">
Trade Proposal Center
</div>


{% if error %}

<div class="error">
<strong>Trade could not be submitted.</strong><br><br>
{{ error }}
</div>

{% endif %}


{% if analysis %}

<div class="result">

<h2>🚨 Trade Proposed</h2>

<p>
<span class="mention">
{{ analysis.team_a_mention }}
</span>

and

<span class="mention">
{{ analysis.team_b_mention }}
</span>
</p>

<hr>

<h3>{{ analysis.team_a }} SENDS</h3>

<div class="assets">
{% for asset in analysis.team_a_sends %}
• {{ summarize(asset) }}
{% endfor %}
</div>


<h3>{{ analysis.team_b }} SENDS</h3>

<div class="assets">
{% for asset in analysis.team_b_sends %}
• {{ summarize(asset) }}
{% endfor %}
</div>

<hr>

<h3>📊 Analyst Grades</h3>

<p class="grade">
{{ analysis.team_a }}:
{{ analysis.team_a_grade.grade }}
</p>

<p class="grade">
{{ analysis.team_b }}:
{{ analysis.team_b_grade.grade }}
</p>

<h3>🏆 Verdict</h3>

<p>
<strong>
{{ analysis.verdict }}
</strong>
</p>

<h3>🎙️ Project Madden First Take</h3>

<p>
{{ analysis.reaction }}
</p>

<p>
<strong>Status:</strong>
PROPOSED — waiting for approval
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

<h2>Team A</h2>

<label>Team</label>

<input
name="team_a"
placeholder="Example: Ravens"
required
>

<label>Your Discord @</label>

<input
name="team_a_mention"
placeholder="@EBKJayyyyy"
required
>

<label>Team A Sends</label>

<textarea
name="team_a_assets"
placeholder="player|Example WR|WR|85|24|star
pick|2027|2|1"
required
></textarea>

<p class="help">

Player format:<br>
player|Name|Position|OVR|Age|Dev

<br><br>

Example:<br>
player|Zay Flowers|WR|88|25|superstar

<br><br>

Pick format:<br>
pick|Year|Round|YearsAway

<br><br>

Example:<br>
pick|2027|2|1

</p>

</div>


<div class="card">

<h2>Team B</h2>

<label>Team</label>

<input
name="team_b"
placeholder="Example: Chiefs"
required
>

<label>Other Owner's Discord @</label>

<input
name="team_b_mention"
placeholder="@ChiefsOwner"
required
>

<label>Team B Sends</label>

<textarea
name="team_b_assets"
placeholder="player|Example CB|CB|90|23|superstar
pick|2027|1|1"
required
></textarea>

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

    assets_a_text = request.form.get(
        "team_a_assets",
        ""
    )

    assets_b_text = request.form.get(
        "team_b_assets",
        ""
    )

    # Require @ mentions

    if not mention_a.startswith("@"):

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=(
                "Team A owner must be tagged "
                "with an @ mention."
            ),
            summarize=summarize_asset
        )

    if not mention_b.startswith("@"):

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=(
                "Team B owner must be tagged "
                "with an @ mention."
            ),
            summarize=summarize_asset
        )

    if team_a.lower() == team_b.lower():

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=(
                "A team cannot trade with itself."
            ),
            summarize=summarize_asset
        )

    try:

        team_a_sends = parse_trade_assets(
            assets_a_text
        )

        team_b_sends = parse_trade_assets(
            assets_b_text
        )

    except Exception as e:

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=str(e),
            summarize=summarize_asset
        )

    if not team_a_sends:

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=(
                f"{team_a} must send at least one asset."
            ),
            summarize=summarize_asset
        )

    if not team_b_sends:

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=(
                f"{team_b} must send at least one asset."
            ),
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
            team_a_sends,

        "team_b_sends":
            team_b_sends
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
# TRADE PROPOSALS JSON
# =========================================================

@app.route("/analyst/trade-proposals")
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
# TRADE EMBED JSON
# =========================================================

@app.route(
    "/analyst/trade/<trade_id>/embed"
)
def trade_proposal_embed(
    trade_id
):

    proposals = load_json_file(
        "trade_proposals.json"
    )

    if not isinstance(
        proposals,
        list
    ):
        proposals = []

    trade = None

    for proposal in proposals:

        if (
            proposal.get("trade_id")
            == trade_id
        ):

            trade = proposal
            break

    if trade is None:

        return jsonify({
            "error":
                "Trade proposal not found"
        }), 404

    a_assets = "\n".join(
        f"• {summarize_asset(asset)}"
        for asset in trade[
            "team_a_sends"
        ]
    )

    b_assets = "\n".join(
        f"• {summarize_asset(asset)}"
        for asset in trade[
            "team_b_sends"
        ]
    )

    return jsonify({
        "content":
            (
                f"{trade['team_a_mention']} "
                f"{trade['team_b_mention']}"
            ),

        "embeds": [
            {
                "title":
                    "🚨 PROJECT MADDEN TRADE PROPOSAL",

                "description":
                    (
                        f"**{trade['team_a']} ↔ "
                        f"{trade['team_b']}**\n\n"
                        f"Status: **PROPOSED**"
                    ),

                "fields": [
                    {
                        "name":
                            (
                                f"{trade['team_a']} sends"
                            ),

                        "value":
                            a_assets,

                        "inline":
                            True
                    },

                    {
                        "name":
                            (
                                f"{trade['team_b']} sends"
                            ),

                        "value":
                            b_assets,

                        "inline":
                            True
                    },

                    {
                        "name":
                            "📊 Analyst Grades",

                        "value":
                            (
                                f"**{trade['team_a']}:** "
                                f"{trade['team_a_grade']['grade']}\n"
                                f"**{trade['team_b']}:** "
                                f"{trade['team_b_grade']['grade']}"
                            ),

                        "inline":
                            False
                    },

                    {
                        "name":
                            "🏆 Verdict",

                        "value":
                            trade["verdict"],

                        "inline":
                            False
                    },

                    {
                        "name":
                            "🎙️ Project Madden First Take",

                        "value":
                            trade["reaction"],

                        "inline":
                            False
                    }
                ],

                "footer": {
                    "text":
                        (
                            f"Trade ID: "
                            f"{trade['trade_id']} "
                            f"• Project Madden"
                        )
                }
            }
        ]
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

                        records += len(
                            value
                        )

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
# PLAYER SCHEMA
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

    if not os.path.exists(
        filename
    ):

        return jsonify({
            "error":
                "Stat file not found"
        }), 404

    with open(
        filename,
        "r"
    ) as f:

        data = json.load(f)

    lists_found = {}
    first_record = None
    first_list_name = None

    if isinstance(
        data,
        dict
    ):

        for key, value in data.items():

            if isinstance(
                value,
                list
            ):

                lists_found[key] = {
                    "count":
                        len(value)
                }

                if (
                    value
                    and first_record is None
                ):

                    first_record = (
                        value[0]
                    )

                    first_list_name = (
                        key
                    )

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

    return jsonify(
        response
    )


# =========================================================
# START SERVER
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
