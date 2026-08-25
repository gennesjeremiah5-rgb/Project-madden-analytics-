from flask import Flask, request, jsonify, render_template_string
import json
import os
import hashlib
import uuid
import re
import requests
from datetime import datetime, timezone


# =========================================================
# FLASK APP - MUST STAY AT TOP
# =========================================================

app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

ANALYST_HISTORY_FILE = "analyst_history.json"


# =========================================================
# FILE HELPERS
# =========================================================

def load_json_file(filename):
    path = os.path.join(DATA_DIR, filename)

    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return None


def save_json_file(filename, data):
    path = os.path.join(DATA_DIR, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def stable_choice(options, key):
    if not options:
        return ""

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
# NON-REPETITIVE ANALYST HISTORY
# =========================================================

def load_analyst_history():
    history = load_json_file(
        ANALYST_HISTORY_FILE
    )

    if not isinstance(
        history,
        dict
    ):
        history = {}

    return history


def save_analyst_history(history):
    save_json_file(
        ANALYST_HISTORY_FILE,
        history
    )


def unique_analyst_choice(
    category,
    options,
    key
):
    if not options:
        return ""

    history = load_analyst_history()

    recent = history.get(
        category,
        []
    )

    available = [
        option
        for option in options
        if option not in recent
    ]

    if not available:
        available = options[:]
        recent = []

    digest = hashlib.sha256(
        f"{category}-{key}".encode(
            "utf-8"
        )
    ).hexdigest()

    index = (
        int(digest[:8], 16)
        % len(available)
    )

    selected = available[index]

    recent.append(selected)

    history[category] = (
        recent[-10:]
    )

    save_analyst_history(
        history
    )

    return selected


# =========================================================
# NFL LOGOS
# =========================================================

NFL_LOGO_CODES = {
    "ARI": "ari",
    "ATL": "atl",
    "BAL": "bal",
    "BUF": "buf",
    "CAR": "car",
    "CHI": "chi",
    "CIN": "cin",
    "CLE": "cle",
    "DAL": "dal",
    "DEN": "den",
    "DET": "det",
    "GB": "gb",
    "HOU": "hou",
    "IND": "ind",
    "JAX": "jax",
    "KC": "kc",
    "LV": "lv",
    "LAC": "lac",
    "LAR": "lar",
    "MIA": "mia",
    "MIN": "min",
    "NE": "ne",
    "NO": "no",
    "NYG": "nyg",
    "NYJ": "nyj",
    "PHI": "phi",
    "PIT": "pit",
    "SEA": "sea",
    "SF": "sf",
    "TB": "tb",
    "TEN": "ten",
    "WAS": "wsh",
    "WSH": "wsh"
}


def get_logo_url(abbr):
    code = NFL_LOGO_CODES.get(
        str(abbr or "").upper(),
        str(abbr or "").lower()
    )

    return (
        "https://a.espncdn.com/i/teamlogos/"
        f"nfl/500/{code}.png"
    )


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
        abbr = team.get(
            "abbrName"
        )

        teams[
            str(team.get("teamId"))
        ] = {
            "teamId":
                team.get("teamId"),

            "abbr":
                abbr,

            "city":
                team.get("cityName"),

            "name":
                team.get("displayName"),

            "nickname":
                team.get("nickName"),

            "overall":
                team.get("ovrRating"),

            "user":
                team.get(
                    "userName",
                    ""
                ),

            "logo":
                get_logo_url(
                    abbr
                )
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
                and str(value)
                .strip()
                .lower()
                == target
            ):
                return team

    return None


def team_by_id(team_id):
    return get_team_map().get(
        str(team_id)
    )


def safe_team_name(team_id):
    team = team_by_id(
        team_id
    )

    if not team:
        return f"Team {team_id}"

    return (
        team.get("name")
        or team.get("abbr")
        or f"Team {team_id}"
    )


def safe_team_overall(team_id):
    team = team_by_id(
        team_id
    )

    if not team:
        return None

    try:
        return int(
            team.get("overall")
        )

    except Exception:
        return None


# =========================================================
# ROSTER HELPERS
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
            "overallPlayerRating"
        ]
    )

    try:
        return int(value)

    except (
        TypeError,
        ValueError
    ):
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

    except (
        TypeError,
        ValueError
    ):
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


def get_team_roster(team_name):
    team = find_team(
        team_name
    )

    if not team:
        raise ValueError(
            f"Could not find team "
            f"'{team_name}' in "
            f"Snallabot league data."
        )

    team_id = team.get(
        "teamId"
    )

    roster = load_json_file(
        f"roster_{team_id}.json"
    )

    if not roster:
        raise ValueError(
            f"No Snallabot roster "
            f"found for the "
            f"{team.get('name')}. "
            f"Run the Snallabot "
            f"roster export again."
        )

    return team, roster


def build_roster_index(team_name):
    team, roster = (
        get_team_roster(
            team_name
        )
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

        key = (
            name.lower(),
            position,
            overall
        )

        if key in seen:
            continue

        seen.add(key)

        players.append({
            "name":
                name,

            "position":
                position,

            "overall":
                overall,

            "age":
                age,

            "dev":
                detect_dev(
                    record
                )
        })

    players.sort(
        key=lambda p: (
            -(p.get("overall") or 0),
            p.get("name", "")
        )
    )

    return team, players


def find_player_on_team(
    team_name,
    player_name
):
    team, players = (
        build_roster_index(
            team_name
        )
    )

    target = (
        player_name
        .strip()
        .lower()
    )

    exact = [
        player
        for player in players
        if player[
            "name"
        ].lower() == target
    ]

    if exact:
        player = exact[0]

    else:
        partial = [
            player
            for player in players
            if target
            in player[
                "name"
            ].lower()
        ]

        if len(partial) == 1:
            player = partial[0]

        elif len(partial) > 1:
            names = ", ".join(
                player["name"]
                for player in partial[:8]
            )

            raise ValueError(
                f"'{player_name}' matched "
                f"multiple players: {names}."
            )

        else:
            raise ValueError(
                f"Could not find "
                f"'{player_name}' on the "
                f"{team.get('name')} roster."
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
        "type":
            "player",

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
            )
    }


# =========================================================
# TRADE PARSER
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
        r"(?:round\s*)?"
        r"([1-7])"
        r"(?:st|nd|rd|th)?",
        clean
    )

    if not round_match:
        return None

    round_number = int(
        round_match.group(1)
    )

    years_away = max(
        0,
        year - datetime.now().year
    )

    return {
        "type":
            "pick",

        "year":
            year,

        "round":
            round_number,

        "years_away":
            years_away
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

    if not assets:
        raise ValueError(
            f"{team_name} must send "
            f"at least one asset."
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
            0.90 ** years_away
        )

    return round(
        value,
        2
    )


def calculate_asset_value(asset):
    if asset[
        "type"
    ] == "pick":
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

    return (
        round(total, 2),
        breakdown
    )


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
        "grade":
            grade,

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

    elif gap_percent <= 20:
        decision = "COMMITTEE REVIEW"
        level = "QUESTIONABLE"
        emoji = "🟡"

    elif gap_percent < 35:
        decision = (
            "STRONG COMMITTEE REVIEW"
        )

        level = "BAD"
        emoji = "🟠"

    else:
        decision = "AUTO DENY"
        level = "VERY BAD"
        emoji = "❌"

    return {
        "decision":
            decision,

        "level":
            level,

        "emoji":
            emoji,

        "value_gap_percent":
            gap_percent,

        "advantage_team":
            advantage_team,

        "disadvantage_team":
            disadvantage_team
    }


# =========================================================
# TRADE ANALYST
# =========================================================

TRADE_STEAL_LINES = [
    "Somebody needs to explain this deal because the value is nowhere close.",

    "I would be asking serious questions if my front office agreed to this.",

    "One side of this trade came away looking a whole lot smarter than the other.",

    "This is exactly the kind of deal people bring up months later and ask how it ever got approved.",

    "There is a clear winner here, and pretending otherwise would be ridiculous."
]

TRADE_CLOSE_LINES = [
    "This one is close enough that both teams can defend their thinking.",

    "I can understand the logic from both sides even if I prefer one package.",

    "This is the kind of trade that will really be judged by what happens on the field.",

    "Neither side should be embarrassed by the value in this deal.",

    "This is competitive enough that I can see the argument both ways."
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
        winner = None
        loser = None

    gap = max(
        abs(
            grade_a["percentage"]
        ),
        abs(
            grade_b["percentage"]
        )
    )

    if winner is None:
        return (
            "Even trade",
            unique_analyst_choice(
                "trade_close",
                TRADE_CLOSE_LINES,
                trade_id
            )
        )

    if gap >= 25:
        intro = unique_analyst_choice(
            "trade_steal",
            TRADE_STEAL_LINES,
            trade_id
        )

        return (
            f"{winner} won the trade",
            (
                f"{intro} "
                f"I have {winner} getting "
                f"the better end of this, "
                f"and {loser} has some "
                f"explaining to do."
            )
        )

    return (
        f"Slight edge to {winner}",
        (
            f"I lean toward {winner}, "
            f"but this is not a deal "
            f"I would call completely "
            f"one-sided."
        )
    )


def analyze_trade(data):
    team_a = data["team_a"]
    team_b = data["team_b"]

    value_a_sent, breakdown_a = (
        calculate_package_value(
            data["team_a_sends"]
        )
    )

    value_b_sent, breakdown_b = (
        calculate_package_value(
            data["team_b_sends"]
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
        "trade_id":
            trade_id,

        "team_a":
            team_a,

        "team_b":
            team_b,

        "team_a_mention":
            data[
                "team_a_mention"
            ],

        "team_b_mention":
            data[
                "team_b_mention"
            ],

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

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


def dev_display(dev):
    mapping = {
        "normal": "Normal",
        "star": "Star",
        "superstar": "Superstar",
        "xfactor": "X-Factor"
    }

    return mapping.get(
        str(dev).lower(),
        str(dev)
    )


def summarize_asset(asset):
    if asset[
        "type"
    ] == "pick":
        return (
            f"{asset['year']} "
            f"Round "
            f"{asset['round']} Pick"
        )

    return (
        f"{asset['name']} — "
        f"{asset['overall']} OVR "
        f"{asset['position']} • "
        f"Age {asset['age']} • "
        f"{dev_display(asset.get('dev'))}"
    )


# =========================================================
# DISCORD TRADE POST
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
                (
                    "DISCORD_WEBHOOK_URL "
                    "is not configured."
                )
        }

    team_a_assets = "\n".join(
        f"• {summarize_asset(asset)}"
        for asset
        in analysis[
            "team_a_sends"
        ]
    )

    team_b_assets = "\n".join(
        f"• {summarize_asset(asset)}"
        for asset
        in analysis[
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
                    (
                        "🚨 PROJECT MADDEN "
                        "TRADE PROPOSAL"
                    ),

                "description":
                    (
                        f"**{analysis['team_a']} "
                        f"↔ "
                        f"{analysis['team_b']}**"
                        f"\n\n"
                        f"Trade ID: "
                        f"`{analysis['trade_id']}`"
                    ),

                "fields": [
                    {
                        "name":
                            (
                                f"{analysis['team_a']} "
                                f"Sends"
                            ),
                        "value":
                            team_a_assets,
                        "inline":
                            False
                    },

                    {
                        "name":
                            (
                                f"{analysis['team_b']} "
                                f"Sends"
                            ),
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
                                f"{analysis['team_a_grade']['grade']}"
                                f"\n"
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
                                f"**{committee['decision']}**"
                                f"\n"
                                f"Quality: "
                                f"{committee['level']}"
                                f"\n"
                                f"Value Gap: "
                                f"{committee['value_gap_percent']}%"
                            ),

                        "inline":
                            False
                    },

                    {
                        "name":
                            (
                                "🎙️ Project Madden "
                                "First Take"
                            ),

                        "value":
                            (
                                f"**{analysis['verdict']}**"
                                f"\n\n"
                                f"{analysis['reaction']}"
                            ),

                        "inline":
                            False
                    }
                ]
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
# WEEKLY DATA HELPERS
# =========================================================

def weekly_file(
    season_type,
    week_number,
    stat_type
):
    return os.path.join(
        DATA_DIR,
        "weekly",
        season_type,
        f"week_{week_number}",
        f"{stat_type}.json"
    )


def load_weekly_data(
    season_type,
    week_number,
    stat_type
):
    path = weekly_file(
        season_type,
        week_number,
        stat_type
    )

    if not os.path.exists(path):
        return None

    try:
        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception:
        return None


# =========================================================
# GAME ANALYST BANKS
# =========================================================

ANALYST_OPENINGS = [
    "I need everybody to understand what we just watched.",

    "There is no way I'm brushing this result aside.",

    "We have to talk about what happened in this game.",

    "Forget the excuses. Let's talk about what actually happened on the field.",

    "Somebody needs to explain this performance to me.",

    "This result told us a whole lot about both of these teams.",

    "The scoreboard is one thing, but the way this game happened matters even more.",

    "I watched enough of this game to know exactly where I stand.",

    "This is the kind of result that gets everybody's attention.",

    "There are certain games you cannot just move past without saying something."
]

BLOWOUT_LINES = [
    "{winner} didn't just win. They completely controlled this matchup.",

    "{loser} got overwhelmed, and the score reflects how one-sided this became.",

    "{winner} imposed its will from start to finish.",

    "This looked like a statement from {winner} and a warning sign for {loser}.",

    "The difference in execution between {winner} and {loser} was obvious.",

    "{winner} made this look much easier than anybody should be comfortable with.",

    "There was no point where {loser} looked capable of matching {winner}'s level.",

    "This was domination, not just a normal victory."
]

UPSET_LINES = [
    "{winner} came into this as the lower-rated team and clearly did not care.",

    "Throw the ratings away. {winner} earned this one on the field.",

    "This is exactly why games are not decided by overall ratings.",

    "{loser} had the advantage on paper and still could not finish the job.",

    "{winner} just gave everybody a reason to stop overlooking them.",

    "The roster ratings told one story. The scoreboard told another.",

    "{winner} just proved execution matters more than numbers beside a team name.",

    "If {loser} expected its rating advantage to carry it, {winner} delivered a reality check."
]

CLOSE_LINES = [
    "{winner} made the plays that mattered when the pressure was highest.",

    "There was almost nothing separating these teams, but {winner} finished better.",

    "{loser} had chances to steal this game and could not close it.",

    "This game came down to the smallest details, and {winner} handled them better.",

    "When the margin is this thin, every mistake becomes enormous.",

    "{winner} stayed composed when this game could have gone either direction.",

    "{loser} will look back at several moments and know this game was there for the taking.",

    "This was a real pressure test, and {winner} survived it."
]

NORMAL_WIN_LINES = [
    "{winner} was simply the better team today.",

    "{winner} handled its business and earned the result.",

    "{loser} competed, but {winner} made more winning plays.",

    "{winner} was cleaner in the moments that mattered.",

    "This was not flawless, but {winner} did enough to stay in control.",

    "{winner} consistently found answers whenever {loser} threatened.",

    "The difference was not massive, but {winner} deserved the win.",

    "{winner} played the more complete game."
]

POSITIVE_CLOSERS = [
    "You do not have to like them, but you better respect what they just did.",

    "That is the type of performance that gets everybody's attention.",

    "If they keep playing like this, the rest of the league has a problem.",

    "That looked like a team that knew exactly what it wanted to accomplish.",

    "This is how you make people stop doubting you.",

    "They earned every bit of praise coming their way.",

    "Put this one on the résumé.",

    "That is the standard they should be chasing every week."
]

NEGATIVE_CLOSERS = [
    "They better fix this before another opponent exposes the exact same problem.",

    "That cannot become a habit if this team expects to contend.",

    "The film room is going to be uncomfortable after this.",

    "This team needs answers because that was not good enough.",

    "No excuses. Get back to work and clean it up.",

    "The criticism is only going to get louder if this keeps happening.",

    "Everybody in that building needs to look at what went wrong.",

    "A serious team cannot be satisfied with this."
]


# =========================================================
# GAME STORY LOGIC
# =========================================================

def game_looks_completed(game):
    away_score = int(
        game.get(
            "awayScore",
            0
        )
        or 0
    )

    home_score = int(
        game.get(
            "homeScore",
            0
        )
        or 0
    )

    # Snallabot currently uses 0-0
    # for your unplayed games.
    return (
        away_score != 0
        or home_score != 0
    )


def classify_game_story(game):
    away_id = game.get(
        "awayTeamId"
    )

    home_id = game.get(
        "homeTeamId"
    )

    away_score = int(
        game.get(
            "awayScore",
            0
        )
        or 0
    )

    home_score = int(
        game.get(
            "homeScore",
            0
        )
        or 0
    )

    away_name = safe_team_name(
        away_id
    )

    home_name = safe_team_name(
        home_id
    )

    away_ovr = safe_team_overall(
        away_id
    )

    home_ovr = safe_team_overall(
        home_id
    )

    if away_score == home_score:
        return {
            "story_type":
                "tie",

            "away":
                away_name,

            "home":
                home_name,

            "away_score":
                away_score,

            "home_score":
                home_score
        }

    if away_score > home_score:
        winner = away_name
        loser = home_name
        winner_score = away_score
        loser_score = home_score
        winner_ovr = away_ovr
        loser_ovr = home_ovr

    else:
        winner = home_name
        loser = away_name
        winner_score = home_score
        loser_score = away_score
        winner_ovr = home_ovr
        loser_ovr = away_ovr

    margin = (
        winner_score
        -
        loser_score
    )

    upset = (
        winner_ovr is not None
        and loser_ovr is not None
        and winner_ovr < loser_ovr
    )

    if margin >= 21:
        story_type = "blowout"

    elif upset:
        story_type = "upset"

    elif margin <= 3:
        story_type = "close_game"

    else:
        story_type = "normal_win"

    return {
        "story_type":
            story_type,

        "winner":
            winner,

        "loser":
            loser,

        "margin":
            margin,

        "winner_score":
            winner_score,

        "loser_score":
            loser_score,

        "winner_ovr":
            winner_ovr,

        "loser_ovr":
            loser_ovr,

        "away":
            away_name,

        "home":
            home_name,

        "away_score":
            away_score,

        "home_score":
            home_score,

        "upset":
            upset
    }


def build_game_take(
    story,
    key
):
    winner = story.get(
        "winner"
    )

    loser = story.get(
        "loser"
    )

    opening = unique_analyst_choice(
        "game_opening",
        ANALYST_OPENINGS,
        key
    )

    story_type = story.get(
        "story_type"
    )

    if story_type == "blowout":
        body_template = unique_analyst_choice(
            "blowout_body",
            BLOWOUT_LINES,
            key
        )

        closer = unique_analyst_choice(
            "blowout_closer",
            POSITIVE_CLOSERS,
            key
        )

    elif story_type == "upset":
        body_template = unique_analyst_choice(
            "upset_body",
            UPSET_LINES,
            key
        )

        closer = unique_analyst_choice(
            "upset_closer",
            [
                "The league better remember this result.",

                "Anybody overlooking this team needs to reconsider.",

                "This league just got a lot more interesting.",

                "That is how you earn respect when nobody expects you to win.",

                "The next team on the schedule better be paying attention."
            ],
            key
        )

    elif story_type == "close_game":
        body_template = unique_analyst_choice(
            "close_body",
            CLOSE_LINES,
            key
        )

        closer = unique_analyst_choice(
            "close_closer",
            [
                "Games like this reveal who handles pressure.",

                "Every possession mattered and everybody knows it.",

                "Both teams are going to find plenty to study on film.",

                "A game this close can change confidence in a hurry.",

                "That is why finishing matters."
            ],
            key
        )

    else:
        body_template = unique_analyst_choice(
            "normal_body",
            NORMAL_WIN_LINES,
            key
        )

        closer = unique_analyst_choice(
            "normal_closer",
            POSITIVE_CLOSERS,
            key
        )

    body = body_template.format(
        winner=winner,
        loser=loser
    )

    return (
        f"{opening} "
        f"{body} "
        f"{closer}"
    )


def make_game_headline(
    story,
    key
):
    winner = story.get(
        "winner"
    )

    loser = story.get(
        "loser"
    )

    story_type = story.get(
        "story_type"
    )

    if story_type == "blowout":
        options = [
            f"{winner} sends a message",

            f"{winner} overwhelms {loser}",

            f"{winner} leaves no doubt",

            f"{loser} has no answers",

            f"{winner} dominates the matchup"
        ]

    elif story_type == "upset":
        options = [
            f"{winner} shocks {loser}",

            f"{winner} pulls the upset",

            f"{winner} flips the script",

            f"Ratings mean nothing as {winner} wins",

            f"{loser} stunned by {winner}"
        ]

    elif story_type == "close_game":
        options = [
            f"{winner} survives a thriller",

            f"{winner} escapes against {loser}",

            f"{winner} delivers in the clutch",

            f"{loser} falls just short",

            f"{winner} wins a nail-biter"
        ]

    else:
        options = [
            f"{winner} handles business",

            f"{winner} gets the job done",

            f"{winner} beats {loser}",

            f"{winner} proves to be the better team",

            f"{winner} takes care of business"
        ]

    return unique_analyst_choice(
        "game_headlines",
        options,
        key
    )


# =========================================================
# PLAYER STAT HELPERS
# =========================================================

def stat_value(
    record,
    keys,
    default=0
):
    value = first_value(
        record,
        keys
    )

    if value is None:
        return default

    try:
        return int(value)

    except Exception:
        try:
            return float(value)

        except Exception:
            return default


def extract_stat_records(data):
    if not data:
        return []

    records = recursive_records(
        data
    )

    return [
        record
        for record in records
        if detect_player_name(
            record
        )
    ]


# =========================================================
# PLAYER ANALYST
# =========================================================

QB_ELITE_LINES = [
    "{player} was operating at an elite level. {yards} yards, {tds} touchdowns and only {ints} interceptions is serious quarterback production.",

    "{player} controlled this offense from the quarterback position and never let the defense get comfortable.",

    "That was high-level quarterback play from {player}. The numbers back it up.",

    "{player} was dealing. When the quarterback gives you that kind of production, the offense becomes extremely difficult to stop.",

    "{player} looked completely comfortable running the offense.",

    "Start with {player} when you're explaining why this offense was successful."
]

QB_BAD_LINES = [
    "{player} has to be better than this. {ints} interceptions puts the entire offense in danger.",

    "I am putting a lot of this on {player}. The quarterback cannot repeatedly put the team behind the eight ball.",

    "{player} had a rough day and there is no way around it.",

    "When the quarterback struggles like this, everybody else ends up playing uphill.",

    "This was not good enough from {player}. The decision-making has to improve.",

    "{player} has to own this performance because the quarterback position demands better."
]

RUSH_LINES = [
    "{player} punished the defense on the ground with {yards} rushing yards and {tds} touchdowns.",

    "The defense knew {player} was getting the football and still struggled to stop him.",

    "{player} took over the running game once he found a rhythm.",

    "{player} ran with purpose all game long.",

    "That was a physical rushing performance from {player}.",

    "{player} made the ground game matter, and that changed the whole offense."
]

REC_LINES = [
    "{player} was a nightmare to cover with {yards} receiving yards and {tds} touchdowns.",

    "Every time the offense needed a big play, {player} seemed to be involved.",

    "{player} completely changed the game as a receiver.",

    "The secondary never found a consistent answer for {player}.",

    "That was a takeover game from {player}.",

    "{player} delivered whenever his number was called."
]

DEF_LINES = [
    "{player} was everywhere defensively.",

    "{player} changed possessions and disrupted the offense all game long.",

    "That was a defensive takeover from {player}.",

    "{player} made the offense account for him on every important snap.",

    "Defense is about creating problems, and {player} created plenty of them.",

    "{player} delivered the kind of defensive performance coaches love."
]


def passing_reactions(
    data,
    season_type,
    week_number
):
    results = []

    for record in extract_stat_records(
        data
    ):
        player = detect_player_name(
            record
        )

        yards = stat_value(
            record,
            [
                "passYds",
                "passingYards",
                "passYards",
                "pass_yds"
            ]
        )

        tds = stat_value(
            record,
            [
                "passTDs",
                "passingTDs",
                "passTouchdowns",
                "pass_tds"
            ]
        )

        ints = stat_value(
            record,
            [
                "passInts",
                "passingInts",
                "interceptions",
                "pass_ints"
            ]
        )

        if (
            yards <= 0
            and tds <= 0
            and ints <= 0
        ):
            continue

        key = (
            f"{season_type}-"
            f"{week_number}-"
            f"{player}-passing"
        )

        if (
            yards >= 300
            and tds >= 3
            and ints <= 1
        ):
            story_type = (
                "elite_qb_game"
            )

            template = (
                unique_analyst_choice(
                    "qb_elite",
                    QB_ELITE_LINES,
                    key
                )
            )

        elif (
            ints >= 3
            or (
                ints >= 2
                and tds == 0
            )
        ):
            story_type = (
                "qb_disaster"
            )

            template = (
                unique_analyst_choice(
                    "qb_bad",
                    QB_BAD_LINES,
                    key
                )
            )

        else:
            continue

        results.append({
            "player":
                player,

            "category":
                "passing",

            "story_type":
                story_type,

            "stats": {
                "yards":
                    yards,

                "touchdowns":
                    tds,

                "interceptions":
                    ints
            },

            "analyst_take":
                template.format(
                    player=player,
                    yards=yards,
                    tds=tds,
                    ints=ints
                )
        })

    return results


def rushing_reactions(
    data,
    season_type,
    week_number
):
    results = []

    for record in extract_stat_records(
        data
    ):
        player = detect_player_name(
            record
        )

        yards = stat_value(
            record,
            [
                "rushYds",
                "rushingYards",
                "rushYards",
                "rush_yds"
            ]
        )

        tds = stat_value(
            record,
            [
                "rushTDs",
                "rushingTDs",
                "rushTouchdowns",
                "rush_tds"
            ]
        )

        if (
            yards < 100
            and tds < 2
        ):
            continue

        key = (
            f"{season_type}-"
            f"{week_number}-"
            f"{player}-rushing"
        )

        template = unique_analyst_choice(
            "rush_star",
            RUSH_LINES,
            key
        )

        results.append({
            "player":
                player,

            "category":
                "rushing",

            "story_type":
                "rushing_takeover",

            "stats": {
                "yards":
                    yards,

                "touchdowns":
                    tds
            },

            "analyst_take":
                template.format(
                    player=player,
                    yards=yards,
                    tds=tds
                )
        })

    return results


def receiving_reactions(
    data,
    season_type,
    week_number
):
    results = []

    for record in extract_stat_records(
        data
    ):
        player = detect_player_name(
            record
        )

        yards = stat_value(
            record,
            [
                "recYds",
                "receivingYards",
                "receiveYards",
                "rec_yds"
            ]
        )

        tds = stat_value(
            record,
            [
                "recTDs",
                "receivingTDs",
                "receivingTouchdowns",
                "rec_tds"
            ]
        )

        if (
            yards < 100
            and tds < 2
        ):
            continue

        key = (
            f"{season_type}-"
            f"{week_number}-"
            f"{player}-receiving"
        )

        template = unique_analyst_choice(
            "receiver_star",
            REC_LINES,
            key
        )

        results.append({
            "player":
                player,

            "category":
                "receiving",

            "story_type":
                "receiver_takeover",

            "stats": {
                "yards":
                    yards,

                "touchdowns":
                    tds
            },

            "analyst_take":
                template.format(
                    player=player,
                    yards=yards,
                    tds=tds
                )
        })

    return results


def defense_reactions(
    data,
    season_type,
    week_number
):
    results = []

    for record in extract_stat_records(
        data
    ):
        player = detect_player_name(
            record
        )

        sacks = stat_value(
            record,
            [
                "defSacks",
                "sacks",
                "sackCount",
                "def_sacks"
            ]
        )

        ints = stat_value(
            record,
            [
                "defInts",
                "defensiveInterceptions",
                "def_ints"
            ]
        )

        forced_fumbles = stat_value(
            record,
            [
                "forcedFumbles",
                "fumblesForced",
                "ff"
            ]
        )

        if (
            sacks < 2
            and ints < 1
            and forced_fumbles < 2
        ):
            continue

        key = (
            f"{season_type}-"
            f"{week_number}-"
            f"{player}-defense"
        )

        template = unique_analyst_choice(
            "def_star",
            DEF_LINES,
            key
        )

        results.append({
            "player":
                player,

            "category":
                "defense",

            "story_type":
                "defensive_takeover",

            "stats": {
                "sacks":
                    sacks,

                "interceptions":
                    ints,

                "forced_fumbles":
                    forced_fumbles
            },

            "analyst_take":
                (
                    f"{template} "
                    f"He finished with "
                    f"{sacks} sacks, "
                    f"{ints} interceptions "
                    f"and {forced_fumbles} "
                    f"forced fumbles."
                )
        })

    return results


# =========================================================
# HOME ROUTES
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

        "team_api":
            "/api/teams",

        "player_search":
            "/api/players",

        "game_analyst":
            "/analyst/reactions/pre/1",

        "player_analyst":
            "/analyst/players/pre/1",

        "weekly_show":
            "/analyst/show/pre/1",

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
        "online":
            True,

        "discord_webhook_configured":
            bool(
                os.environ.get(
                    "DISCORD_WEBHOOK_URL"
                )
            )
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

    if parts[-1] == "leagueteams":
        save_json_file(
            "leagueteams.json",
            data
        )

        return jsonify({
            "success":
                True,

            "type":
                "leagueteams"
        })

    if parts[-1] == "standings":
        save_json_file(
            "standings.json",
            data
        )

        return jsonify({
            "success":
                True,

            "type":
                "standings"
        })

    if parts[-1] == "extra":
        save_json_file(
            "extra.json",
            data
        )

        return jsonify({
            "success":
                True,

            "type":
                "extra"
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
            "success":
                True,

            "type":
                "freeagents"
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
            "success":
                True,

            "type":
                "roster",

            "team_id":
                team_id
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
            "w",
            encoding="utf-8"
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
        })

    return jsonify({
        "success":
            True,

        "type":
            "unknown",

        "path":
            subpath
    })


# =========================================================
# TEAM API
# =========================================================

@app.route("/api/teams")
def teams_api():
    teams = list(
        get_team_map().values()
    )

    teams.sort(
        key=lambda t:
            t.get(
                "name",
                ""
            )
    )

    return jsonify({
        "team_count":
            len(teams),

        "teams":
            teams
    })


# =========================================================
# PLAYER API
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
    ).strip().lower()

    try:
        team, players = (
            build_roster_index(
                team_name
            )
        )

    except Exception as e:
        return jsonify({
            "error":
                str(e)
        }), 400

    if query:
        players = [
            player
            for player in players
            if query
            in player[
                "name"
            ].lower()
        ]

    return jsonify({
        "team":
            team.get("name"),

        "team_logo":
            team.get("logo"),

        "player_count":
            len(players),

        "players":
            players[:100]
    })


# =========================================================
# GAME ANALYST API
# =========================================================

@app.route(
    "/analyst/reactions/<season_type>/<int:week_number>"
)
def analyst_game_reactions(
    season_type,
    week_number
):
    schedule_data = load_weekly_data(
        season_type,
        week_number,
        "schedules"
    )

    if not schedule_data:
        return jsonify({
            "season_type":
                season_type,

            "week":
                week_number,

            "status":
                "waiting",

            "message":
                (
                    "No Snallabot schedule "
                    "export received yet."
                ),

            "reactions":
                []
        })

    games = schedule_data.get(
        "gameScheduleInfoList",
        []
    )

    reactions = []

    for game in games:
        if not game_looks_completed(
            game
        ):
            continue

        story = classify_game_story(
            game
        )

        if story.get(
            "story_type"
        ) == "tie":
            continue

        key = (
            f"{season_type}-"
            f"{week_number}-"
            f"{game.get('scheduleId')}"
        )

        reactions.append({
            "schedule_id":
                game.get(
                    "scheduleId"
                ),

            "game":
                (
                    f"{story['away']} "
                    f"{story['away_score']}, "
                    f"{story['home']} "
                    f"{story['home_score']}"
                ),

            "story_type":
                story[
                    "story_type"
                ],

            "headline":
                make_game_headline(
                    story,
                    key
                ),

            "winner":
                story.get(
                    "winner"
                ),

            "loser":
                story.get(
                    "loser"
                ),

            "margin":
                story.get(
                    "margin"
                ),

            "upset":
                story.get(
                    "upset",
                    False
                ),

            "analyst":
                (
                    "Project Madden "
                    "Debate Analyst"
                ),

            "analyst_take":
                build_game_take(
                    story,
                    key
                )
        })

    return jsonify({
        "season_type":
            season_type,

        "week":
            week_number,

        "completed_games_found":
            len(reactions),

        "reactions":
            reactions
    })


# =========================================================
# PLAYER ANALYST API
# =========================================================

@app.route(
    "/analyst/players/<season_type>/<int:week_number>"
)
def analyst_player_reactions(
    season_type,
    week_number
):
    passing_data = load_weekly_data(
        season_type,
        week_number,
        "passing"
    )

    rushing_data = load_weekly_data(
        season_type,
        week_number,
        "rushing"
    )

    receiving_data = load_weekly_data(
        season_type,
        week_number,
        "receiving"
    )

    defense_data = load_weekly_data(
        season_type,
        week_number,
        "defense"
    )

    reactions = []

    if passing_data:
        reactions.extend(
            passing_reactions(
                passing_data,
                season_type,
                week_number
            )
        )

    if rushing_data:
        reactions.extend(
            rushing_reactions(
                rushing_data,
                season_type,
                week_number
            )
        )

    if receiving_data:
        reactions.extend(
            receiving_reactions(
                receiving_data,
                season_type,
                week_number
            )
        )

    if defense_data:
        reactions.extend(
            defense_reactions(
                defense_data,
                season_type,
                week_number
            )
        )

    return jsonify({
        "season_type":
            season_type,

        "week":
            week_number,

        "files_received": {
            "passing":
                passing_data
                is not None,

            "rushing":
                rushing_data
                is not None,

            "receiving":
                receiving_data
                is not None,

            "defense":
                defense_data
                is not None
        },

        "reaction_count":
            len(reactions),

        "status":
            (
                "ready"
                if reactions
                else
                "waiting_for_player_performances"
            ),

        "reactions":
            reactions
    })


# =========================================================
# WEEKLY SHOW
# =========================================================

@app.route(
    "/analyst/show/<season_type>/<int:week_number>"
)
def analyst_weekly_show(
    season_type,
    week_number
):
    schedule_data = load_weekly_data(
        season_type,
        week_number,
        "schedules"
    )

    game_segments = []

    if schedule_data:
        for game in schedule_data.get(
            "gameScheduleInfoList",
            []
        ):
            if not game_looks_completed(
                game
            ):
                continue

            story = classify_game_story(
                game
            )

            if story.get(
                "story_type"
            ) == "tie":
                continue

            key = (
                f"show-"
                f"{season_type}-"
                f"{week_number}-"
                f"{game.get('scheduleId')}"
            )

            game_segments.append({
                "headline":
                    make_game_headline(
                        story,
                        key
                    ),

                "game":
                    (
                        f"{story['away']} "
                        f"{story['away_score']}, "
                        f"{story['home']} "
                        f"{story['home_score']}"
                    ),

                "story_type":
                    story[
                        "story_type"
                    ],

                "script":
                    build_game_take(
                        story,
                        key
                    )
            })

    player_segments = []

    passing_data = load_weekly_data(
        season_type,
        week_number,
        "passing"
    )

    rushing_data = load_weekly_data(
        season_type,
        week_number,
        "rushing"
    )

    receiving_data = load_weekly_data(
        season_type,
        week_number,
        "receiving"
    )

    defense_data = load_weekly_data(
        season_type,
        week_number,
        "defense"
    )

    if passing_data:
        player_segments.extend(
            passing_reactions(
                passing_data,
                season_type,
                week_number
            )
        )

    if rushing_data:
        player_segments.extend(
            rushing_reactions(
                rushing_data,
                season_type,
                week_number
            )
        )

    if receiving_data:
        player_segments.extend(
            receiving_reactions(
                receiving_data,
                season_type,
                week_number
            )
        )

    if defense_data:
        player_segments.extend(
            defense_reactions(
                defense_data,
                season_type,
                week_number
            )
        )

    return jsonify({
        "show":
            "Project Madden First Take",

        "analyst":
            (
                "Project Madden "
                "Debate Analyst"
            ),

        "season_type":
            season_type,

        "week":
            week_number,

        "game_segments":
            game_segments,

        "player_segments":
            player_segments,

        "total_segments":
            (
                len(game_segments)
                +
                len(player_segments)
            )
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

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #0b0c10;
    color: white;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 18px;
}

.title {
    text-align: center;
    font-size: 30px;
    font-weight: 900;
}

.subtitle {
    text-align: center;
    color: #8e94a5;
    margin-bottom: 25px;
}

.card,
.result,
.committee {
    background: #171920;
    border: 1px solid #292d39;
    border-radius: 16px;
    padding: 18px;
    margin-bottom: 18px;
}

input,
select {
    width: 100%;
    padding: 13px;
    margin-top: 7px;
    margin-bottom: 12px;
    background: #0e1016;
    color: white;
    border: 1px solid #363b49;
    border-radius: 11px;
    font-size: 16px;
}

button {
    width: 100%;
    padding: 15px;
    border: 0;
    border-radius: 11px;
    background: #5865f2;
    color: white;
    font-size: 17px;
    font-weight: 800;
}

.team-row {
    display: flex;
    align-items: center;
    gap: 10px;
}

.team-logo {
    width: 42px;
    height: 42px;
    object-fit: contain;
}

.search-results {
    background: #151821;
    border: 1px solid #353a48;
    border-radius: 11px;
    max-height: 280px;
    overflow-y: auto;
    display: none;
}

.search-results.open {
    display: block;
}

.player-option {
    padding: 12px;
    border-bottom: 1px solid #282c35;
}

.player-option:last-child {
    border-bottom: 0;
}

.player-option strong {
    display: block;
}

.small {
    color: #9ba1af;
    font-size: 13px;
    margin-top: 4px;
}

.asset {
    background: #20232c;
    border-radius: 10px;
    padding: 10px;
    margin-top: 8px;
}

.error {
    background: #42191d;
    padding: 14px;
    border-radius: 10px;
    margin-bottom: 15px;
}

.success {
    background: #15361d;
    padding: 14px;
    border-radius: 10px;
    margin-bottom: 15px;
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

<h2>
🚨 TRADE PROPOSED
</h2>

<h3>
{{ analysis.team_a }} Sends
</h3>

{% for asset in analysis.team_a_sends %}
<p>
• {{ summarize(asset) }}
</p>
{% endfor %}

<h3>
{{ analysis.team_b }} Sends
</h3>

{% for asset in analysis.team_b_sends %}
<p>
• {{ summarize(asset) }}
</p>
{% endfor %}

<h3>
📊 Grades
</h3>

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

<h3>
🎙️ Project Madden First Take
</h3>

<p>
<strong>
{{ analysis.verdict }}
</strong>
</p>

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

</div>


{% if discord.sent %}

<div class="success">
✅ Posted to Discord.
</div>

{% else %}

<div class="error">
Discord failed:
{{ discord.error }}
</div>

{% endif %}

<a href="/proposetrade">
<button>
Propose Another Trade
</button>
</a>


{% else %}


<form
method="POST"
id="tradeForm">


<div class="card">

<h2>
TEAM A
</h2>

<label>
Select Team
</label>

<select
name="team_a"
id="teamA"
required>

<option value="">
Select Team A
</option>

</select>

<div
id="teamALogo">
</div>

<label>
Discord @
</label>

<input
name="team_a_mention"
placeholder="@RavensOwner"
required>

<label>
Search Players
</label>

<input
id="playerSearchA"
placeholder="Select a team first"
disabled>

<div
id="resultsA"
class="search-results">
</div>

<div
id="selectedA">
</div>

<label>
Draft Pick
</label>

<select id="pickYearA">
<option>2027</option>
<option>2028</option>
<option>2029</option>
<option>2030</option>
<option>2031</option>
</select>

<select id="pickRoundA">
<option value="1">Round 1</option>
<option value="2">Round 2</option>
<option value="3">Round 3</option>
<option value="4">Round 4</option>
<option value="5">Round 5</option>
<option value="6">Round 6</option>
<option value="7">Round 7</option>
</select>

<button
type="button"
onclick="addPick('A')">
+ Add Pick
</button>

<input
type="hidden"
name="team_a_assets"
id="assetsA">

</div>


<div class="card">

<h2>
TEAM B
</h2>

<label>
Select Team
</label>

<select
name="team_b"
id="teamB"
required>

<option value="">
Select Team B
</option>

</select>

<div
id="teamBLogo">
</div>

<label>
Discord @
</label>

<input
name="team_b_mention"
placeholder="@ChiefsOwner"
required>

<label>
Search Players
</label>

<input
id="playerSearchB"
placeholder="Select a team first"
disabled>

<div
id="resultsB"
class="search-results">
</div>

<div
id="selectedB">
</div>

<label>
Draft Pick
</label>

<select id="pickYearB">
<option>2027</option>
<option>2028</option>
<option>2029</option>
<option>2030</option>
<option>2031</option>
</select>

<select id="pickRoundB">
<option value="1">Round 1</option>
<option value="2">Round 2</option>
<option value="3">Round 3</option>
<option value="4">Round 4</option>
<option value="5">Round 5</option>
<option value="6">Round 6</option>
<option value="7">Round 7</option>
</select>

<button
type="button"
onclick="addPick('B')">
+ Add Pick
</button>

<input
type="hidden"
name="team_b_assets"
id="assetsB">

</div>


<button
type="submit">
🚨 PROPOSE TRADE
</button>

</form>


<script>

const teams = [];

const selected = {
    A: [],
    B: []
};


async function loadTeams() {

    const res =
        await fetch(
            "/api/teams"
        );

    const data =
        await res.json();

    teams.push(
        ...(data.teams || [])
    );

    ["A","B"].forEach(side => {

        const select =
            document.getElementById(
                "team" + side
            );

        teams.forEach(team => {

            const option =
                document.createElement(
                    "option"
                );

            option.value =
                team.name;

            option.textContent =
                `${team.name} (${team.abbr})`;

            option.dataset.logo =
                team.logo;

            select.appendChild(
                option
            );

        });

        select.addEventListener(
            "change",
            () => {

                selected[side] =
                    [];

                syncAssets(side);

                const search =
                    document.getElementById(
                        "playerSearch"
                        + side
                    );

                if (select.value) {
                    search.disabled =
                        false;

                    search.placeholder =
                        `Search ${select.value} players...`;

                    const team =
                        teams.find(
                            t =>
                            t.name
                            ===
                            select.value
                        );

                    document.getElementById(
                        "team"
                        + side
                        + "Logo"
                    ).innerHTML =
                        team
                        ?
                        `<div class="team-row">
                        <img
                        class="team-logo"
                        src="${team.logo}">
                        <strong>
                        ${team.name}
                        </strong>
                        </div>`
                        :
                        "";

                } else {

                    search.disabled =
                        true;
                }

            }
        );

    });
}


async function searchPlayers(
    side
) {

    const team =
        document.getElementById(
            "team" + side
        ).value;

    const query =
        document.getElementById(
            "playerSearch"
            + side
        ).value;

    if (!team) {
        return;
    }

    const res =
        await fetch(
            "/api/players?team="
            +
            encodeURIComponent(team)
            +
            "&q="
            +
            encodeURIComponent(query)
        );

    const data =
        await res.json();

    const box =
        document.getElementById(
            "results" + side
        );

    box.innerHTML = "";

    if (
        !data.players
        ||
        data.players.length
        === 0
    ) {

        box.innerHTML =
            "<div class='player-option'>No players found</div>";

        box.classList.add(
            "open"
        );

        return;
    }

    data.players
    .slice(0,30)
    .forEach(player => {

        const div =
            document.createElement(
                "div"
            );

        div.className =
            "player-option";

        div.innerHTML = `
            <strong>
            ${player.name}
            </strong>

            <div
            class="small">

            ${player.overall}
            OVR
            •
            ${player.position}
            •
            Age
            ${player.age}
            •
            ${player.dev}

            </div>
        `;

        div.onclick = () => {

            if (
                selected[side]
                .some(
                    x =>
                    x.type
                    ===
                    "player"
                    &&
                    x.name
                    ===
                    player.name
                )
            ) {
                return;
            }

            selected[side]
            .push({
                type:
                    "player",

                name:
                    player.name
            });

            document.getElementById(
                "playerSearch"
                + side
            ).value = "";

            box.classList.remove(
                "open"
            );

            syncAssets(side);

        };

        box.appendChild(
            div
        );

    });

    box.classList.add(
        "open"
    );
}


function addPick(side) {

    const year =
        document.getElementById(
            "pickYear"
            + side
        ).value;

    const round =
        document.getElementById(
            "pickRound"
            + side
        ).value;

    selected[side]
    .push({
        type:
            "pick",

        year:
            year,

        round:
            round
    });

    syncAssets(side);
}


function removeAsset(
    side,
    index
) {

    selected[side]
    .splice(
        index,
        1
    );

    syncAssets(side);
}


function syncAssets(side) {

    const box =
        document.getElementById(
            "selected"
            + side
        );

    box.innerHTML = "";

    const lines = [];

    selected[side]
    .forEach(
        (item,index) => {

            let label = "";

            if (
                item.type
                ===
                "player"
            ) {

                label =
                    item.name;

                lines.push(
                    item.name
                );

            } else {

                label =
                    `${item.year}
                    Round
                    ${item.round}`;

                lines.push(
                    `${item.year} Round ${item.round}`
                );
            }

            const div =
                document.createElement(
                    "div"
                );

            div.className =
                "asset";

            div.innerHTML =
                `${label}
                <button
                type="button"
                style="
                width:auto;
                float:right;
                padding:4px 9px;
                "
                onclick="
                removeAsset(
                    '${side}',
                    ${index}
                )">
                ✕
                </button>`;

            box.appendChild(
                div
            );

        }
    );

    document.getElementById(
        "assets"
        + side
    ).value =
        lines.join(
            "\\n"
        );
}


["A","B"].forEach(side => {

    document.getElementById(
        "playerSearch"
        + side
    ).addEventListener(
        "input",
        () => {
            searchPlayers(
                side
            );
        }
    );

});


document.getElementById(
    "tradeForm"
).addEventListener(
    "submit",
    event => {

        syncAssets("A");
        syncAssets("B");

        if (
            selected.A.length
            === 0
            ||
            selected.B.length
            === 0
        ) {

            event.preventDefault();

            alert(
                "Both teams must send at least one asset."
            );

        }

    }
);


loadTeams();

</script>


{% endif %}


</div>

</body>
</html>
"""


# =========================================================
# TRADE ROUTE
# =========================================================

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
            summarize=
                summarize_asset
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

    if not team_a or not team_b:
        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=
                "Select both teams.",
            discord=None,
            summarize=
                summarize_asset
        )

    if (
        team_a.lower()
        ==
        team_b.lower()
    ):
        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=
                (
                    "A team cannot "
                    "trade with itself."
                ),
            discord=None,
            summarize=
                summarize_asset
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
            error=
                str(e),
            discord=None,
            summarize=
                summarize_asset
        )

    analysis = analyze_trade({
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
        analysis=
            analysis,
        error=
            None,
        discord=
            discord_result,
        summarize=
            summarize_asset
    )


# =========================================================
# TRADE PROPOSALS API
# =========================================================

@app.route(
    "/analyst/trade-proposals"
)
def trade_proposals_api():
    proposals = load_json_file(
        "trade_proposals.json"
    )

    if not isinstance(
        proposals,
        list
    ):
        proposals = []

    return jsonify({
        "count":
            len(proposals),

        "proposals":
            proposals
    })


# =========================================================
# START APP
# THIS MUST STAY LAST
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
