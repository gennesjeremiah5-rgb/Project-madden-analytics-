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
                records.append(
                    item
                )

            records.extend(
                recursive_records(
                    item
                )
            )

    elif isinstance(obj, dict):

        for value in obj.values():

            if isinstance(
                value,
                (list, dict)
            ):
                records.extend(
                    recursive_records(
                        value
                    )
                )

    return records


def first_value(record, keys):

    for key in keys:

        if key in record:

            value = record.get(
                key
            )

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
        return int(
            value
        )

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
        return int(
            value
        )

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

    if isinstance(
        value,
        int
    ):

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


def build_roster_index(
    team_name
):

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

        seen.add(
            key
        )

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
            -(
                p.get(
                    "overall"
                )
                or 0
            ),
            p.get(
                "name",
                ""
            )
        )
    )

    return team, players


# =========================================================
# PLAYER LOOKUP
# =========================================================

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
        for player
        in players
        if player[
            "name"
        ].lower() == target
    ]

    if exact:

        player = exact[0]

    else:

        partial = [
            player
            for player
            in players
            if target
            in player[
                "name"
            ].lower()
        ]

        if len(
            partial
        ) == 1:

            player = partial[0]

        elif len(
            partial
        ) > 1:

            names = ", ".join(
                player["name"]
                for player
                in partial[:8]
            )

            raise ValueError(
                f"'{player_name}' "
                f"matched multiple "
                f"{team.get('name')} "
                f"players: {names}."
            )

        else:

            raise ValueError(
                f"Could not find "
                f"'{player_name}' "
                f"on the "
                f"{team.get('name')} "
                f"roster."
            )

    missing = []

    if player.get(
        "overall"
    ) is None:
        missing.append(
            "OVR"
        )

    if player.get(
        "age"
    ) is None:
        missing.append(
            "age"
        )

    if not player.get(
        "position"
    ):
        missing.append(
            "position"
        )

    if missing:

        raise ValueError(
            f"Found "
            f"{player['name']}, "
            f"but Snallabot did "
            f"not provide: "
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
            ),

        "source":
            "Snallabot roster"
    }


# =========================================================
# PICK PARSER
# =========================================================

def parse_easy_pick(line):

    clean = (
        line.strip()
        .lower()
        .replace(
            ",",
            " "
        )
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

    current_year = (
        datetime.now().year
    )

    years_away = max(
        0,
        year - current_year
    )

    return {
        "type": "pick",
        "year": year,
        "round": round_number,
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

        player = (
            find_player_on_team(
                team_name,
                line
            )
        )

        assets.append(
            player
        )

    if not assets:

        raise ValueError(
            f"{team_name} must "
            f"send at least one asset."
        )

    return assets


# =========================================================
# TRADE VALUE SETTINGS
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
# TRADE VALUE ENGINE
# =========================================================

def calculate_player_value(
    asset
):

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
        max(
            value,
            1
        ),
        2
    )


def calculate_pick_value(
    asset
):

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


def calculate_asset_value(
    asset
):

    if asset[
        "type"
    ] == "pick":

        return (
            calculate_pick_value(
                asset
            )
        )

    return (
        calculate_player_value(
            asset
        )
    )


def calculate_package_value(
    assets
):

    total = 0
    breakdown = []

    for asset in assets:

        value = (
            calculate_asset_value(
                asset
            )
        )

        total += value

        breakdown.append({
            **asset,
            "calculated_value":
                value
        })

    return (
        round(
            total,
            2
        ),
        breakdown
    )


# =========================================================
# TRADE GRADES
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


# =========================================================
# TRADE COMMITTEE
# =========================================================

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
        disadvantage_team = (
            team_b
        )

    elif value_b > value_a:

        advantage_team = team_b
        disadvantage_team = (
            team_a
        )

    else:

        advantage_team = None
        disadvantage_team = None

    if gap_percent <= 10:

        decision = (
            "AUTO APPROVE"
        )

        level = "GOOD"
        emoji = "✅"

        reason = (
            "The trade packages "
            "are close enough in "
            "calculated value for "
            "automatic approval."
        )

    elif gap_percent <= 20:

        decision = (
            "COMMITTEE REVIEW"
        )

        level = (
            "QUESTIONABLE"
        )

        emoji = "🟡"

        reason = (
            f"The value "
            f"difference is "
            f"{gap_percent}%."
        )

    elif gap_percent < 35:

        decision = (
            "STRONG COMMITTEE "
            "REVIEW"
        )

        level = "BAD"
        emoji = "🟠"

        reason = (
            f"The packages have "
            f"a {gap_percent}% "
            f"value difference."
        )

    else:

        decision = (
            "AUTO DENY"
        )

        level = (
            "VERY BAD"
        )

        emoji = "❌"

        reason = (
            f"The trade has a "
            f"{gap_percent}% "
            f"value difference."
        )

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
            disadvantage_team,

        "reason":
            reason
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

    if (
        value_a_received
        > value_b_received
    ):

        winner = team_a
        loser = team_b

    elif (
        value_b_received
        > value_a_received
    ):

        winner = team_b
        loser = team_a

    else:

        return (
            "Even trade",
            (
                "I can understand "
                "this deal from both "
                "sides. The value is "
                "close enough that "
                "neither team looks "
                "like it got taken "
                "advantage of."
            )
        )

    gap = max(
        abs(
            grade_a[
                "percentage"
            ]
        ),
        abs(
            grade_b[
                "percentage"
            ]
        )
    )

    if gap >= 35:

        verdict = (
            f"{winner} won — "
            f"major steal"
        )

        choices = [
            (
                f"Hold on. What are "
                f"the {loser} doing "
                f"here? The {winner} "
                f"are walking away "
                f"with the better "
                f"value and it really "
                f"isn't close. I'm "
                f"calling this a "
                f"robbery."
            ),
            (
                f"I do not like this "
                f"for the {loser}. "
                f"The {winner} "
                f"clearly got the "
                f"better package. "
                f"Somebody has to "
                f"explain this one."
            )
        ]

    elif gap >= 20:

        verdict = (
            f"{winner} won "
            f"the trade"
        )

        choices = [
            (
                f"I understand the "
                f"thinking, but I'm "
                f"giving this deal "
                f"to the {winner}. "
                f"The {loser} gave "
                f"up more value than "
                f"I would've liked."
            )
        ]

    elif gap >= 8:

        verdict = (
            f"Slight edge to "
            f"{winner}"
        )

        choices = [
            (
                f"This one is close, "
                f"but if you're "
                f"forcing me to "
                f"choose a winner, "
                f"I'm taking the "
                f"{winner}."
            )
        ]

    else:

        verdict = (
            "Fair trade"
        )

        choices = [
            (
                "I don't see a clear "
                "loser here. The "
                "value is close "
                "enough that this "
                "trade will "
                "ultimately be "
                "judged by what "
                "happens on the "
                "field."
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

    team_a = data[
        "team_a"
    ]

    team_b = data[
        "team_b"
    ]

    team_a_sends = data[
        "team_a_sends"
    ]

    team_b_sends = data[
        "team_b_sends"
    ]

    (
        value_a_sent,
        breakdown_a
    ) = calculate_package_value(
        team_a_sends
    )

    (
        value_b_sent,
        breakdown_b
    ) = calculate_package_value(
        team_b_sends
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

    committee = (
        committee_review(
            team_a,
            team_b,
            value_a_received,
            value_b_received
        )
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

        "status":
            "PROPOSED",

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


# =========================================================
# ASSET DISPLAY
# =========================================================

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


def summarize_asset(
    asset
):

    if asset[
        "type"
    ] == "pick":

        return (
            f"{asset['year']} "
            f"Round "
            f"{asset['round']} "
            f"Pick"
        )

    return (
        f"{asset['name']} — "
        f"{asset['overall']} OVR "
        f"{asset['position']} • "
        f"Age {asset['age']} • "
        f"{dev_display(asset.get('dev'))}"
    )


# =========================================================
# DISCORD
# =========================================================

def post_trade_to_discord(
    analysis
):

    webhook_url = (
        os.environ.get(
            "DISCORD_WEBHOOK_URL"
        )
    )

    if not webhook_url:

        return {
            "sent": False,
            "error":
                (
                    "DISCORD_WEBHOOK_URL "
                    "is not configured "
                    "in Render."
                )
        }

    team_a_assets = "\n".join(
        (
            f"• "
            f"{summarize_asset(asset)}"
        )
        for asset
        in analysis[
            "team_a_sends"
        ]
    )

    team_b_assets = "\n".join(
        (
            f"• "
            f"{summarize_asset(asset)}"
        )
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
                                f"**"
                                f"{analysis['team_a']}"
                                f":** "
                                f"{analysis['team_a_grade']['grade']}"
                                f"\n"
                                f"**"
                                f"{analysis['team_b']}"
                                f":** "
                                f"{analysis['team_b_grade']['grade']}"
                            ),

                        "inline":
                            False
                    },

                    {
                        "name":
                            (
                                "🏛️ "
                                "Trade Committee"
                            ),

                        "value":
                            (
                                f"{committee['emoji']} "
                                f"**"
                                f"{committee['decision']}"
                                f"**\n"
                                f"Quality: "
                                f"{committee['level']}"
                                f"\n"
                                f"Value Gap: "
                                f"{committee['value_gap_percent']}"
                                f"%\n"
                                f"{committee['reason']}"
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
                                f"**"
                                f"{analysis['verdict']}"
                                f"**"
                                f"\n\n"
                                f"{analysis['reaction']}"
                            ),

                        "inline":
                            False
                    }
                ],

                "footer": {
                    "text":
                        (
                            "Project Madden "
                            "• Trade Center"
                        )
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
                    f"{response.status_code}: "
                    f"{response.text[:200]}"
                )
        }

    except Exception as e:

        return {
            "sent": False,
            "error":
                str(e)
        }


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return jsonify({

        "status":
            "online",

        "service":
            (
                "Project Madden "
                "Analytics"
            ),

        "snallabot":
            "connected",

        "trade_center":
            "/proposetrade",

        "team_api":
            "/api/teams",

        "player_search":
            "/api/players",

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
def snallabot_receiver(
    subpath
):

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

    if parts[-1] == (
        "leagueteams"
    ):

        save_json_file(
            "leagueteams.json",
            data
        )

        return jsonify({
            "success": True,
            "type":
                "leagueteams"
        })

    if parts[-1] == (
        "standings"
    ):

        save_json_file(
            "standings.json",
            data
        )

        return jsonify({
            "success": True,
            "type":
                "standings"
        })

    if parts[-1] == "extra":

        save_json_file(
            "extra.json",
            data
        )

        return jsonify({
            "success": True,
            "type":
                "extra"
        })

    if (
        "freeagents" in parts
        and parts[-1]
        == "roster"
    ):

        save_json_file(
            "freeagents_roster.json",
            data
        )

        return jsonify({
            "success": True,
            "type":
                "freeagents"
        })

    if (
        "team" in parts
        and parts[-1]
        == "roster"
    ):

        team_index = (
            parts.index(
                "team"
            )
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
            "type":
                "roster",
            "team_id":
                team_id
        })

    if "week" in parts:

        try:

            week_index = (
                parts.index(
                    "week"
                )
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
                    (
                        "Invalid weekly "
                        "export path"
                    )
            }), 400

        weekly_dir = (
            os.path.join(
                DATA_DIR,
                "weekly",
                season_type,
                f"week_{week_number}"
            )
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
            (
                t.get(
                    "name"
                )
                or ""
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

    team_name = (
        request.args.get(
            "team",
            ""
        )
    )

    query = (
        request.args.get(
            "q",
            ""
        )
        .strip()
        .lower()
    )

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
            for player
            in players
            if query
            in player[
                "name"
            ].lower()
        ]

    return jsonify({

        "team":
            team.get(
                "name"
            ),

        "team_logo":
            team.get(
                "logo"
            ),

        "player_count":
            len(players),

        "players":
            players[:100]
    })


# =========================================================
# TRADE PAGE
# =========================================================

TRADE_PAGE = """
<!DOCTYPE html>
<html>

<head>

<meta
name="viewport"
content="width=device-width, initial-scale=1, viewport-fit=cover">

<title>
Project Madden Trade Center
</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background:
        linear-gradient(
            180deg,
            #08090d 0%,
            #11131a 100%
        );
    color: white;
    font-family:
        Arial,
        Helvetica,
        sans-serif;
    min-height: 100vh;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 18px;
    padding-bottom: 60px;
}

.header {
    text-align: center;
    margin-top: 8px;
    margin-bottom: 28px;
}

.logo-title {
    font-size: 31px;
    font-weight: 900;
    letter-spacing: -1px;
}

.subtitle {
    color: #8e94a5;
    margin-top: 7px;
    font-size: 15px;
}

.card {
    background: #171920;
    border: 1px solid #292d39;
    border-radius: 18px;
    padding: 18px;
    margin-bottom: 18px;
}

.card-title {
    font-size: 20px;
    font-weight: 850;
    margin-bottom: 15px;
}

label {
    display: block;
    color: #d9dce6;
    font-weight: 750;
    margin-top: 15px;
    margin-bottom: 8px;
}

input,
select {
    width: 100%;
    min-height: 48px;
    background: #0e1016;
    border: 1px solid #363b49;
    color: white;
    border-radius: 12px;
    padding: 12px;
    font-size: 16px;
    outline: none;
}

input:focus,
select:focus {
    border-color: #5865f2;
}

.team-picker {
    position: relative;
}

.team-button {
    width: 100%;
    border: 1px solid #363b49;
    border-radius: 13px;
    background: #0e1016;
    min-height: 62px;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    gap: 12px;
    color: white;
    text-align: left;
    font-size: 17px;
}

.team-button img {
    width: 42px;
    height: 42px;
    object-fit: contain;
}

.team-button .placeholder {
    color: #777e90;
}

.team-dropdown {
    display: none;
    position: absolute;
    top: 68px;
    left: 0;
    right: 0;
    z-index: 1000;
    max-height: 350px;
    overflow-y: auto;
    background: #151821;
    border: 1px solid #3b4150;
    border-radius: 14px;
    box-shadow:
        0 16px 40px
        rgba(0,0,0,.55);
}

.team-dropdown.open {
    display: block;
}

.team-option {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 11px 13px;
    border-bottom: 1px solid #272b35;
    cursor: pointer;
}

.team-option:last-child {
    border-bottom: 0;
}

.team-option img {
    width: 38px;
    height: 38px;
    object-fit: contain;
}

.team-option-name {
    font-size: 16px;
    font-weight: 750;
}

.team-option-small {
    color: #858b9b;
    font-size: 12px;
    margin-top: 3px;
}

.player-search-wrap {
    position: relative;
}

.player-results {
    display: none;
    position: absolute;
    left: 0;
    right: 0;
    top: 54px;
    z-index: 900;
    max-height: 330px;
    overflow-y: auto;
    background: #151821;
    border: 1px solid #3b4150;
    border-radius: 14px;
    box-shadow:
        0 16px 40px
        rgba(0,0,0,.55);
}

.player-results.open {
    display: block;
}

.player-option {
    padding: 13px;
    border-bottom: 1px solid #292d36;
    cursor: pointer;
}

.player-option:last-child {
    border-bottom: 0;
}

.player-name {
    font-weight: 850;
    font-size: 16px;
}

.player-info {
    color: #9ba1af;
    font-size: 13px;
    margin-top: 5px;
}

.overall {
    color: #7d89ff;
    font-weight: 850;
}

.selected-assets {
    margin-top: 12px;
}

.asset-chip {
    background: #20232c;
    border: 1px solid #363c4a;
    border-radius: 12px;
    padding: 11px 12px;
    margin-top: 9px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
}

.asset-main {
    font-weight: 750;
    font-size: 14px;
}

.asset-small {
    color: #9da3b2;
    font-size: 12px;
    margin-top: 4px;
}

.remove-button {
    border: 0;
    background: #482127;
    color: #ff858d;
    border-radius: 9px;
    width: 34px;
    height: 34px;
    font-size: 17px;
    flex-shrink: 0;
}

.pick-grid {
    display: grid;
    grid-template-columns:
        1fr 1fr;
    gap: 10px;
}

.add-pick {
    margin-top: 10px;
    width: 100%;
    min-height: 46px;
    border: 1px solid #414758;
    background: #232732;
    color: white;
    border-radius: 11px;
    font-weight: 750;
    font-size: 15px;
}

.submit {
    width: 100%;
    border: 0;
    border-radius: 14px;
    background:
        linear-gradient(
            90deg,
            #5865f2,
            #6877ff
        );
    color: white;
    font-weight: 900;
    font-size: 18px;
    min-height: 58px;
}

.help {
    color: #858b9b;
    font-size: 13px;
    margin-top: 9px;
    line-height: 1.45;
}

.error {
    background: #42191d;
    border: 1px solid #893239;
    border-radius: 13px;
    padding: 14px;
    margin-bottom: 18px;
}

.success {
    background: #15361d;
    border: 1px solid #347845;
    border-radius: 13px;
    padding: 14px;
    margin-bottom: 18px;
}

.result {
    background: #171920;
    border: 1px solid #292d39;
    border-radius: 18px;
    padding: 18px;
    margin-bottom: 18px;
}

.result h2 {
    margin-top: 0;
}

.result-team {
    font-size: 19px;
    font-weight: 850;
    margin-top: 20px;
}

.result-asset {
    color: #d8dbe4;
    margin-top: 7px;
}

.value {
    color: #99a3ff;
    font-weight: 800;
}

.committee {
    background: #171920;
    border: 1px solid #5865f2;
    border-radius: 18px;
    padding: 18px;
    margin-bottom: 18px;
}

.again {
    display: block;
    text-decoration: none;
}

.again button {
    width: 100%;
    min-height: 54px;
    border: 0;
    border-radius: 14px;
    background: #5865f2;
    color: white;
    font-weight: 900;
    font-size: 17px;
}

.loading {
    padding: 15px;
    color: #9da3b2;
}

.empty {
    padding: 15px;
    color: #9da3b2;
}

@media (max-width: 600px) {

    .container {
        padding:
            15px 13px
            50px;
    }

    .card {
        padding: 16px;
    }

    .logo-title {
        font-size: 27px;
    }
}

</style>

</head>

<body>

<div class="container">

<div class="header">

<div class="logo-title">
🏈 PROJECT MADDEN
</div>

<div class="subtitle">
Trade Proposal & Committee Center
</div>

</div>


{% if error %}

<div class="error">

<strong>
Trade Error
</strong>

<br><br>

{{ error }}

</div>

{% endif %}


{% if analysis %}

<div class="result">

<h2>
🚨 TRADE PROPOSED
</h2>

<p>
{{ analysis.team_a_mention }}
↔
{{ analysis.team_b_mention }}
</p>

<div class="result-team">
{{ analysis.team_a }} Sends
</div>

{% for asset in analysis.team_a_sends %}

<div class="result-asset">
• {{ summarize(asset) }}
</div>

{% endfor %}

<p class="value">
Package Value:
{{ analysis.team_a_value_sent }}
</p>


<div class="result-team">
{{ analysis.team_b }} Sends
</div>

{% for asset in analysis.team_b_sends %}

<div class="result-asset">
• {{ summarize(asset) }}
</div>

{% endfor %}

<p class="value">
Package Value:
{{ analysis.team_b_value_sent }}
</p>

<hr>

<h3>
📊 Trade Grades
</h3>

<p>
<strong>
{{ analysis.team_a }}:
{{ analysis.team_a_grade.grade }}
</strong>
</p>

<p>
<strong>
{{ analysis.team_b }}:
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
<strong>Quality:</strong>
{{ analysis.trade_committee.level }}
</p>

<p>
<strong>Value Gap:</strong>
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

⚠️ Trade was analyzed, but Discord post failed.

<br><br>

{{ discord.error }}

</div>

{% endif %}


<a
class="again"
href="/proposetrade">

<button>
Propose Another Trade
</button>

</a>


{% else %}


<form
method="POST"
id="tradeForm">


<!-- TEAM A -->

<div class="card">

<div class="card-title">
TEAM A
</div>


<label>
Select Team
</label>

<div
class="team-picker"
id="pickerA">

<button
type="button"
class="team-button"
id="teamButtonA">

<span class="placeholder">
Select Team A
</span>

</button>

<div
class="team-dropdown"
id="teamDropdownA">
</div>

</div>

<input
type="hidden"
name="team_a"
id="teamA"
required>


<label>
Discord @
</label>

<input
name="team_a_mention"
placeholder="@RavensOwner"
required>


<label>
Search Team A Players
</label>

<div class="player-search-wrap">

<input
type="text"
id="playerSearchA"
placeholder="Select a team first..."
autocomplete="off"
disabled>

<div
class="player-results"
id="playerResultsA">
</div>

</div>

<div
class="selected-assets"
id="selectedPlayersA">
</div>


<label>
Add Draft Pick
</label>

<div class="pick-grid">

<select id="pickYearA">

<option value="2027">
2027
</option>

<option value="2028">
2028
</option>

<option value="2029">
2029
</option>

<option value="2030">
2030
</option>

<option value="2031">
2031
</option>

</select>


<select id="pickRoundA">

<option value="1">
Round 1
</option>

<option value="2">
Round 2
</option>

<option value="3">
Round 3
</option>

<option value="4">
Round 4
</option>

<option value="5">
Round 5
</option>

<option value="6">
Round 6
</option>

<option value="7">
Round 7
</option>

</select>

</div>

<button
type="button"
class="add-pick"
onclick="addPick('A')">

+ Add Draft Pick

</button>


<div
class="selected-assets"
id="selectedPicksA">
</div>


<input
type="hidden"
name="team_a_assets"
id="assetsA">


<p class="help">
Player ratings, age, position and development trait are pulled automatically from Snallabot.
</p>

</div>


<!-- TEAM B -->

<div class="card">

<div class="card-title">
TEAM B
</div>


<label>
Select Team
</label>

<div
class="team-picker"
id="pickerB">

<button
type="button"
class="team-button"
id="teamButtonB">

<span class="placeholder">
Select Team B
</span>

</button>

<div
class="team-dropdown"
id="teamDropdownB">
</div>

</div>

<input
type="hidden"
name="team_b"
id="teamB"
required>


<label>
Discord @
</label>

<input
name="team_b_mention"
placeholder="@ChiefsOwner"
required>


<label>
Search Team B Players
</label>

<div class="player-search-wrap">

<input
type="text"
id="playerSearchB"
placeholder="Select a team first..."
autocomplete="off"
disabled>

<div
class="player-results"
id="playerResultsB">
</div>

</div>


<div
class="selected-assets"
id="selectedPlayersB">
</div>


<label>
Add Draft Pick
</label>

<div class="pick-grid">

<select id="pickYearB">

<option value="2027">
2027
</option>

<option value="2028">
2028
</option>

<option value="2029">
2029
</option>

<option value="2030">
2030
</option>

<option value="2031">
2031
</option>

</select>


<select id="pickRoundB">

<option value="1">
Round 1
</option>

<option value="2">
Round 2
</option>

<option value="3">
Round 3
</option>

<option value="4">
Round 4
</option>

<option value="5">
Round 5
</option>

<option value="6">
Round 6
</option>

<option value="7">
Round 7
</option>

</select>

</div>

<button
type="button"
class="add-pick"
onclick="addPick('B')">

+ Add Draft Pick

</button>


<div
class="selected-assets"
id="selectedPicksB">
</div>


<input
type="hidden"
name="team_b_assets"
id="assetsB">

</div>


<div class="card">

<div class="card-title">
🏛️ Automatic Trade Committee
</div>

<p class="help">

✅ 0–10% = AUTO APPROVE

<br><br>

🟡 10–20% = COMMITTEE REVIEW

<br><br>

🟠 20–35% = STRONG REVIEW

<br><br>

❌ 35%+ = AUTO DENY

</p>

</div>


<button
class="submit"
type="submit">

🚨 PROPOSE TRADE

</button>

</form>


<script>

const teams = [];

const selected = {
    A: {
        team: null,
        players: [],
        picks: []
    },

    B: {
        team: null,
        players: [],
        picks: []
    }
};


function devName(dev) {

    if (!dev) {
        return "Normal";
    }

    const value =
        dev.toLowerCase();

    if (value === "xfactor") {
        return "X-Factor";
    }

    if (value === "superstar") {
        return "Superstar";
    }

    if (value === "star") {
        return "Star";
    }

    return "Normal";
}


async function loadTeams() {

    const response =
        await fetch("/api/teams");

    const data =
        await response.json();

    teams.push(
        ...(data.teams || [])
    );

    renderTeamDropdown("A");
    renderTeamDropdown("B");
}


function renderTeamDropdown(side) {

    const dropdown =
        document.getElementById(
            "teamDropdown" + side
        );

    dropdown.innerHTML = "";

    teams.forEach(team => {

        const option =
            document.createElement(
                "div"
            );

        option.className =
            "team-option";

        option.innerHTML = `
            <img
            src="${team.logo}"
            alt="">

            <div>

                <div
                class="team-option-name">
                ${team.name}
                </div>

                <div
                class="team-option-small">

                ${team.abbr}
                ${team.overall
                    ? " • " +
                      team.overall +
                      " OVR"
                    : ""}

                </div>

            </div>
        `;

        option.onclick = () => {
            selectTeam(
                side,
                team
            );
        };

        dropdown.appendChild(
            option
        );
    });
}


function selectTeam(
    side,
    team
) {

    const other =
        side === "A"
        ? "B"
        : "A";

    if (
        selected[other].team
        &&
        String(
            selected[other]
                .team.teamId
        )
        ===
        String(team.teamId)
    ) {

        alert(
            "Team A and Team B cannot be the same team."
        );

        return;
    }

    selected[side].team =
        team;

    selected[side].players =
        [];

    selected[side].picks =
        [];

    document.getElementById(
        "team" + side
    ).value = team.name;

    const button =
        document.getElementById(
            "teamButton" + side
        );

    button.innerHTML = `
        <img
        src="${team.logo}"
        alt="">

        <div>

            <div
            style="
            font-weight:850;
            font-size:17px;
            ">
            ${team.name}
            </div>

            <div
            style="
            color:#8e94a5;
            font-size:12px;
            margin-top:3px;
            ">
            ${team.abbr}
            ${team.overall
                ? " • " +
                  team.overall +
                  " OVR"
                : ""}
            </div>

        </div>
    `;

    closeTeamDropdown(
        side
    );

    const search =
        document.getElementById(
            "playerSearch" + side
        );

    search.disabled = false;

    search.value = "";

    search.placeholder =
        `Search ${team.name} players...`;

    renderAssets(
        side
    );
}


function toggleTeamDropdown(
    side
) {

    const dropdown =
        document.getElementById(
            "teamDropdown" + side
        );

    dropdown.classList.toggle(
        "open"
    );

    const other =
        side === "A"
        ? "B"
        : "A";

    closeTeamDropdown(
        other
    );
}


function closeTeamDropdown(
    side
) {

    document.getElementById(
        "teamDropdown" + side
    ).classList.remove(
        "open"
    );
}


document.getElementById(
    "teamButtonA"
).onclick = () =>
    toggleTeamDropdown("A");


document.getElementById(
    "teamButtonB"
).onclick = () =>
    toggleTeamDropdown("B");


let searchTimerA = null;
let searchTimerB = null;


function setupPlayerSearch(
    side
) {

    const input =
        document.getElementById(
            "playerSearch" + side
        );

    input.addEventListener(
        "input",
        () => {

            const timerName =
                side === "A"
                ? "A"
                : "B";

            if (timerName === "A") {

                clearTimeout(
                    searchTimerA
                );

                searchTimerA =
                    setTimeout(
                        () =>
                            searchPlayers(
                                side
                            ),
                        250
                    );

            } else {

                clearTimeout(
                    searchTimerB
                );

                searchTimerB =
                    setTimeout(
                        () =>
                            searchPlayers(
                                side
                            ),
                        250
                    );
            }

        }
    );


    input.addEventListener(
        "focus",
        () => {

            if (
                selected[side].team
            ) {
                searchPlayers(
                    side
                );
            }

        }
    );
}


async function searchPlayers(
    side
) {

    const team =
        selected[side].team;

    if (!team) {
        return;
    }

    const input =
        document.getElementById(
            "playerSearch" + side
        );

    const results =
        document.getElementById(
            "playerResults" + side
        );

    const query =
        input.value.trim();

    results.classList.add(
        "open"
    );

    results.innerHTML =
        '<div class="loading">Loading players...</div>';

    try {

        const response =
            await fetch(
                "/api/players?team="
                +
                encodeURIComponent(
                    team.name
                )
                +
                "&q="
                +
                encodeURIComponent(
                    query
                )
            );

        const data =
            await response.json();

        if (data.error) {

            results.innerHTML =
                `<div class="empty">
                ${data.error}
                </div>`;

            return;
        }

        let players =
            data.players || [];

        players =
            players.filter(
                player =>
                    !selected[
                        side
                    ].players.some(
                        selectedPlayer =>
                            selectedPlayer
                                .name
                            ===
                            player.name
                    )
            );

        if (
            players.length === 0
        ) {

            results.innerHTML =
                '<div class="empty">No players found.</div>';

            return;
        }

        results.innerHTML = "";

        players
            .slice(0, 30)
            .forEach(player => {

                const option =
                    document.createElement(
                        "div"
                    );

                option.className =
                    "player-option";

                option.innerHTML = `
                    <div
                    class="player-name">

                    ${player.name}

                    </div>

                    <div
                    class="player-info">

                    <span
                    class="overall">
                    ${player.overall}
                    OVR
                    </span>

                    •
                    ${player.position}

                    •
                    Age ${player.age}

                    •
                    ${devName(
                        player.dev
                    )}

                    </div>
                `;

                option.onclick = () => {

                    addPlayer(
                        side,
                        player
                    );

                };

                results.appendChild(
                    option
                );
            });

    } catch (error) {

        results.innerHTML =
            '<div class="empty">Player search failed.</div>';
    }
}


function addPlayer(
    side,
    player
) {

    const exists =
        selected[
            side
        ].players.some(
            item =>
                item.name
                ===
                player.name
        );

    if (exists) {
        return;
    }

    selected[
        side
    ].players.push(
        player
    );

    document.getElementById(
        "playerSearch" + side
    ).value = "";

    document.getElementById(
        "playerResults" + side
    ).classList.remove(
        "open"
    );

    renderAssets(
        side
    );
}


function removePlayer(
    side,
    index
) {

    selected[
        side
    ].players.splice(
        index,
        1
    );

    renderAssets(
        side
    );
}


function addPick(
    side
) {

    if (
        !selected[
            side
        ].team
    ) {

        alert(
            "Select a team first."
        );

        return;
    }

    const year =
        document.getElementById(
            "pickYear" + side
        ).value;

    const round =
        document.getElementById(
            "pickRound" + side
        ).value;

    const alreadyExists =
        selected[
            side
        ].picks.some(
            pick =>
                String(
                    pick.year
                )
                ===
                String(year)
                &&
                String(
                    pick.round
                )
                ===
                String(round)
        );

    if (
        alreadyExists
    ) {

        alert(
            "That draft pick is already added."
        );

        return;
    }

    selected[
        side
    ].picks.push({
        year:
            Number(year),

        round:
            Number(round)
    });

    renderAssets(
        side
    );
}


function removePick(
    side,
    index
) {

    selected[
        side
    ].picks.splice(
        index,
        1
    );

    renderAssets(
        side
    );
}


function renderAssets(
    side
) {

    const playerBox =
        document.getElementById(
            "selectedPlayers" + side
        );

    const pickBox =
        document.getElementById(
            "selectedPicks" + side
        );

    playerBox.innerHTML = "";

    pickBox.innerHTML = "";


    selected[
        side
    ].players.forEach(
        (
            player,
            index
        ) => {

            const item =
                document.createElement(
                    "div"
                );

            item.className =
                "asset-chip";

            item.innerHTML = `
                <div>

                    <div
                    class="asset-main">

                    ${player.name}

                    </div>

                    <div
                    class="asset-small">

                    ${player.overall}
                    OVR

                    •
                    ${player.position}

                    •
                    Age ${player.age}

                    •
                    ${devName(
                        player.dev
                    )}

                    </div>

                </div>

                <button
                type="button"
                class="remove-button"
                onclick="
                removePlayer(
                    '${side}',
                    ${index}
                )">
                ✕
                </button>
            `;

            playerBox.appendChild(
                item
            );
        }
    );


    selected[
        side
    ].picks.forEach(
        (
            pick,
            index
        ) => {

            const item =
                document.createElement(
                    "div"
                );

            item.className =
                "asset-chip";

            item.innerHTML = `
                <div>

                    <div
                    class="asset-main">

                    ${pick.year}
                    Round
                    ${pick.round}
                    Pick

                    </div>

                    <div
                    class="asset-small">

                    Draft Pick

                    </div>

                </div>

                <button
                type="button"
                class="remove-button"
                onclick="
                removePick(
                    '${side}',
                    ${index}
                )">
                ✕
                </button>
            `;

            pickBox.appendChild(
                item
            );
        }
    );


    syncHiddenAssets(
        side
    );
}


function syncHiddenAssets(
    side
) {

    const lines = [];

    selected[
        side
    ].players.forEach(
        player => {

            lines.push(
                player.name
            );

        }
    );

    selected[
        side
    ].picks.forEach(
        pick => {

            lines.push(
                `${pick.year} Round ${pick.round}`
            );

        }
    );

    document.getElementById(
        "assets" + side
    ).value =
        lines.join(
            "\\n"
        );
}


document.getElementById(
    "tradeForm"
).addEventListener(
    "submit",
    event => {

        syncHiddenAssets(
            "A"
        );

        syncHiddenAssets(
            "B"
        );

        if (
            !selected.A.team
            ||
            !selected.B.team
        ) {

            event.preventDefault();

            alert(
                "Select both teams."
            );

            return;
        }

        if (
            selected.A.players.length
            +
            selected.A.picks.length
            === 0
        ) {

            event.preventDefault();

            alert(
                "Team A must send at least one asset."
            );

            return;
        }

        if (
            selected.B.players.length
            +
            selected.B.picks.length
            === 0
        ) {

            event.preventDefault();

            alert(
                "Team B must send at least one asset."
            );

            return;
        }

    }
);


setupPlayerSearch(
    "A"
);

setupPlayerSearch(
    "B"
);

loadTeams();


document.addEventListener(
    "click",
    event => {

        if (
            !document
            .getElementById(
                "pickerA"
            )
            .contains(
                event.target
            )
        ) {

            closeTeamDropdown(
                "A"
            );

        }

        if (
            !document
            .getElementById(
                "pickerB"
            )
            .contains(
                event.target
            )
        ) {

            closeTeamDropdown(
                "B"
            );

        }

    }
);

</script>

{% endif %}

</div>

</body>

</html>
"""


# =========================================================
# PROPOSE TRADE
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

    team_a = (
        request.form.get(
            "team_a",
            ""
        ).strip()
    )

    team_b = (
        request.form.get(
            "team_b",
            ""
        ).strip()
    )

    mention_a = (
        request.form.get(
            "team_a_mention",
            ""
        ).strip()
    )

    mention_b = (
        request.form.get(
            "team_b_mention",
            ""
        ).strip()
    )

    if not team_a:

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=
                "Select Team A.",
            discord=None,
            summarize=
                summarize_asset
        )

    if not team_b:

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=
                "Select Team B.",
            discord=None,
            summarize=
                summarize_asset
        )

    if (
        team_a.lower()
        == team_b.lower()
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

    if not mention_a.startswith(
        "@"
    ):

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=
                (
                    "Team A must "
                    "include a "
                    "Discord @."
                ),
            discord=None,
            summarize=
                summarize_asset
        )

    if not mention_b.startswith(
        "@"
    ):

        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error=
                (
                    "Team B must "
                    "include a "
                    "Discord @."
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

    proposals = (
        load_json_file(
            "trade_proposals.json"
        )
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
# START
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
