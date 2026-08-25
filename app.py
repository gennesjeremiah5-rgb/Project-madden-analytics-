from pathlib import Path
from io import BytesIO
from flask import Flask, request, jsonify, render_template_string, send_file
import json
import os
import hashlib
import uuid
import re
import requests
from PIL import Image, ImageDraw, ImageFont
import threading
import time
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from datetime import datetime, timezone


# =========================================================
# FLASK APP
# =========================================================

app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

ANALYST_HISTORY_FILE = "analyst_history.json"
ANALYST_POST_HISTORY_FILE = "analyst_discord_posts.json"
PROJECT_MADDEN_ANALYST = "Marcus Hayes"
DISCORD_DEBUG_FILE = "discord_interaction_debug.json"
TRADE_CARD_DIR = "generated_trade_cards"
STANDINGS_POST_LOCK = threading.Lock()


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

    number = int(digest[:8], 16)

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

    if not isinstance(history, dict):
        history = {}

    return history


def save_analyst_history(history):
    save_json_file(
        ANALYST_HISTORY_FILE,
        history
    )


def unique_analyst_choice(category, options, key):
    if not options:
        return ""

    history = load_analyst_history()
    recent = history.get(category, [])

    available = [
        option
        for option in options
        if option not in recent
    ]

    if not available:
        available = options[:]
        recent = []

    digest = hashlib.sha256(
        f"{category}-{key}".encode("utf-8")
    ).hexdigest()

    index = int(digest[:8], 16) % len(available)
    selected = available[index]

    recent.append(selected)
    history[category] = recent[-10:]

    save_analyst_history(history)

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
    data = load_json_file("leagueteams.json")

    if not data:
        return {}

    teams = {}

    for team in data.get("leagueTeamInfoList", []):
        abbr = team.get("abbrName")

        teams[str(team.get("teamId"))] = {
            "teamId": team.get("teamId"),
            "abbr": abbr,
            "city": team.get("cityName"),
            "name": team.get("displayName"),
            "nickname": team.get("nickName"),
            "overall": team.get("ovrRating"),
            "user": team.get("userName", ""),
            "logo": get_logo_url(abbr)
        }

    return teams


def find_team(team_name):
    target = str(team_name).strip().lower()

    for team in get_team_map().values():
        options = [
            team.get("name"),
            team.get("nickname"),
            team.get("abbr"),
            team.get("city")
        ]

        for value in options:
            if value and str(value).strip().lower() == target:
                return team

    return None


def team_by_id(team_id):
    return get_team_map().get(str(team_id))


def safe_team_name(team_id):
    team = team_by_id(team_id)

    if not team:
        return f"Team {team_id}"

    return (
        team.get("name")
        or team.get("abbr")
        or f"Team {team_id}"
    )


def safe_team_overall(team_id):
    team = team_by_id(team_id)

    if not team:
        return None

    try:
        return int(team.get("overall"))
    except Exception:
        return None


# =========================================================
# ROSTER HELPERS
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
            if isinstance(value, (list, dict)):
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
        return f"{first} {last}".strip()

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

        return mapping.get(value, "normal")

    text = str(value).strip().lower()

    if "factor" in text or text == "xf":
        return "xfactor"

    if "superstar" in text:
        return "superstar"

    if "star" in text:
        return "star"

    return "normal"


def get_team_roster(team_name):
    team = find_team(team_name)

    if not team:
        raise ValueError(
            f"Could not find team '{team_name}' "
            f"in Snallabot league data."
        )

    team_id = team.get("teamId")
    roster = load_json_file(
        f"roster_{team_id}.json"
    )

    if not roster:
        raise ValueError(
            f"No Snallabot roster found for the "
            f"{team.get('name')}. Run the "
            f"Snallabot roster export again."
        )

    return team, roster


def build_roster_index(team_name):
    team, roster = get_team_roster(team_name)
    records = recursive_records(roster)

    players = []
    seen = set()

    for record in records:
        name = detect_player_name(record)
        overall = detect_overall(record)
        position = detect_position(record)
        age = detect_age(record)

        if not name:
            continue

        if overall is None and position is None and age is None:
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
            "name": name,
            "position": position,
            "overall": overall,
            "age": age,
            "dev": detect_dev(record)
        })

    players.sort(
        key=lambda p: (
            -(p.get("overall") or 0),
            p.get("name", "")
        )
    )

    return team, players


def find_player_on_team(team_name, player_name):
    team, players = build_roster_index(team_name)
    target = player_name.strip().lower()

    exact = [
        player
        for player in players
        if player["name"].lower() == target
    ]

    if exact:
        player = exact[0]

    else:
        partial = [
            player
            for player in players
            if target in player["name"].lower()
        ]

        if len(partial) == 1:
            player = partial[0]

        elif len(partial) > 1:
            names = ", ".join(
                player["name"]
                for player in partial[:8]
            )

            raise ValueError(
                f"'{player_name}' matched multiple players: {names}."
            )

        else:
            raise ValueError(
                f"Could not find '{player_name}' on the "
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
            f"Found {player['name']}, but Snallabot did not provide: "
            f"{', '.join(missing)}."
        )

    return {
        "type": "player",
        "name": player["name"],
        "position": player["position"],
        "overall": player["overall"],
        "age": player["age"],
        "dev": player.get("dev", "normal")
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

    # IMPORTANT:
    # Do NOT use a loose single-digit regex here.
    # "2026 Round 1" used to match the first "2" in 2026,
    # causing every 2026 pick to be read as Round 2.
    round_patterns = [
        r"\bround\s*([1-7])\b",
        r"\brd\s*([1-7])\b",
        r"\b([1-7])(?:st|nd|rd|th)\s*(?:round|rd)?\b",
        r"\b(?:r|round)[\s#-]*([1-7])\b",
    ]

    round_number = None

    for pattern in round_patterns:
        match = re.search(
            pattern,
            clean
        )

        if match:
            round_number = int(
                match.group(1)
            )
            break

    if round_number is None:
        return None

    years_away = max(
        0,
        year - datetime.now().year
    )

    return {
        "type": "pick",
        "year": year,
        "round": round_number,
        "years_away": years_away
    }


def parse_trade_assets(text, team_name):
    assets = []

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        pick = parse_easy_pick(line)

        if pick:
            assets.append(pick)
            continue

        player = find_player_on_team(
            team_name,
            line
        )

        assets.append(player)

    if not assets:
        raise ValueError(
            f"{team_name} must send at least one asset."
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
    overall = float(asset["overall"])
    age = int(asset["age"])
    position = str(asset["position"]).upper()
    dev = str(
        asset.get("dev", "normal")
    ).lower()

    value = max(
        1,
        (overall - 60) * 1.8
    )

    value += DEV_VALUES.get(dev, 0)

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
        asset["round"]
    )

    years_away = int(
        asset.get("years_away", 0)
    )

    value = PICK_VALUES.get(
        round_number,
        1
    )

    if years_away > 0:
        value *= 0.90 ** years_away

    return round(value, 2)


def calculate_asset_value(asset):
    if asset["type"] == "pick":
        return calculate_pick_value(asset)

    return calculate_player_value(asset)


def calculate_package_value(assets):
    total = 0
    breakdown = []

    for asset in assets:
        value = calculate_asset_value(asset)
        total += value

        breakdown.append({
            **asset,
            "calculated_value": value
        })

    return (
        round(total, 2),
        breakdown
    )


def trade_grade(received, sent):
    difference = received - sent

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


def league_office_asset_flags(
    assets
):
    flags = {
        "elite_players": [],
        "young_elite_players": [],
        "franchise_qbs": [],
        "first_round_picks": 0,
        "premium_dev_players": [],
        "pick_only_package": True,
        "asset_count": len(assets),
    }

    for asset in assets:
        if asset.get("type") == "pick":
            try:
                round_number = int(
                    asset.get("round", 0)
                )
            except Exception:
                round_number = 0

            if round_number == 1:
                flags[
                    "first_round_picks"
                ] += 1

            continue

        flags["pick_only_package"] = False

        name = str(
            asset.get(
                "name",
                "Player"
            )
        )

        position = str(
            asset.get(
                "position",
                ""
            )
        ).upper()

        overall = int(
            asset.get(
                "overall",
                0
            ) or 0
        )

        age = int(
            asset.get(
                "age",
                99
            ) or 99
        )

        dev = str(
            asset.get(
                "dev",
                ""
            )
        ).lower()

        if overall >= 94:
            flags[
                "elite_players"
            ].append(
                name
            )

        if overall >= 90 and age <= 25:
            flags[
                "young_elite_players"
            ].append(
                name
            )

        if (
            position == "QB"
            and overall >= 90
        ):
            flags[
                "franchise_qbs"
            ].append(
                name
            )

        if (
            dev in [
                "superstar",
                "xfactor"
            ]
            and overall >= 90
        ):
            flags[
                "premium_dev_players"
            ].append(
                name
            )

    return flags


def committee_review(
    team_a,
    team_b,
    value_a,
    value_b,
    team_a_assets=None,
    team_b_assets=None
):
    """
    Project Madden League Office Review V2.

    V2 does not rely on the raw value gap alone. It also checks
    premium/young players, franchise QBs, first-round-pick volume,
    pick-only packages, and package complexity before deciding whether
    a trade can be auto-approved, needs staff review, or should be denied.
    """

    team_a_assets = (
        team_a_assets
        if isinstance(
            team_a_assets,
            list
        )
        else []
    )

    team_b_assets = (
        team_b_assets
        if isinstance(
            team_b_assets,
            list
        )
        else []
    )

    highest = max(
        value_a,
        value_b
    )

    lowest = min(
        value_a,
        value_b
    )

    if highest <= 0:
        gap_percent = 0.0
    else:
        gap_percent = (
            (
                highest
                - lowest
            )
            / highest
            * 100
        )

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

    flags_a = league_office_asset_flags(
        team_a_assets
    )

    flags_b = league_office_asset_flags(
        team_b_assets
    )

    review_points = 0
    critical_points = 0
    reasons = []

    # -------------------------
    # Raw value-gap protection
    # -------------------------
    if gap_percent <= 7:
        gap_bucket = "excellent"
    elif gap_percent <= 12:
        gap_bucket = "good"
        review_points += 1
        reasons.append(
            "Value gap is outside the V2 auto-approve comfort zone."
        )
    elif gap_percent <= 20:
        gap_bucket = "questionable"
        review_points += 2
        reasons.append(
            "Noticeable value difference between the two packages."
        )
    elif gap_percent < 30:
        gap_bucket = "bad"
        review_points += 4
        critical_points += 1
        reasons.append(
            "Large value gap requires strong League Office scrutiny."
        )
    else:
        gap_bucket = "very_bad"
        review_points += 7
        critical_points += 2
        reasons.append(
            "Extreme value gap creates a major competitive-balance concern."
        )

    # -------------------------
    # Franchise-QB protection
    # -------------------------
    franchise_qbs = (
        flags_a[
            "franchise_qbs"
        ]
        + flags_b[
            "franchise_qbs"
        ]
    )

    if franchise_qbs:
        review_points += 2
        reasons.append(
            "Franchise-QB asset involved: "
            + ", ".join(
                franchise_qbs
            )
            + "."
        )

        # A franchise QB for a pick-only return should never
        # quietly auto-approve.
        if (
            (
                flags_a[
                    "franchise_qbs"
                ]
                and flags_b[
                    "pick_only_package"
                ]
            )
            or (
                flags_b[
                    "franchise_qbs"
                ]
                and flags_a[
                    "pick_only_package"
                ]
            )
        ):
            review_points += 2
            critical_points += 1
            reasons.append(
                "Franchise QB is being exchanged for a pick-only package."
            )

    # -------------------------
    # Young cornerstone protection
    # -------------------------
    young_elite = (
        flags_a[
            "young_elite_players"
        ]
        + flags_b[
            "young_elite_players"
        ]
    )

    if young_elite:
        review_points += 1
        reasons.append(
            "Young elite cornerstone involved: "
            + ", ".join(
                young_elite
            )
            + "."
        )

    # -------------------------
    # Elite / premium-dev protection
    # -------------------------
    elite_players = (
        flags_a[
            "elite_players"
        ]
        + flags_b[
            "elite_players"
        ]
    )

    if elite_players:
        review_points += 1
        reasons.append(
            "94+ OVR player involved: "
            + ", ".join(
                elite_players
            )
            + "."
        )

    premium_players = (
        flags_a[
            "premium_dev_players"
        ]
        + flags_b[
            "premium_dev_players"
        ]
    )

    if premium_players:
        review_points += 1

    # -------------------------
    # Draft-capital protection
    # -------------------------
    first_round_total = (
        flags_a[
            "first_round_picks"
        ]
        + flags_b[
            "first_round_picks"
        ]
    )

    if first_round_total >= 3:
        review_points += 2
        reasons.append(
            "Three or more first-round picks are involved."
        )
    elif first_round_total >= 2:
        review_points += 1
        reasons.append(
            "Multiple first-round picks are involved."
        )

    # -------------------------
    # Package-size protection
    # -------------------------
    count_a = flags_a[
        "asset_count"
    ]
    count_b = flags_b[
        "asset_count"
    ]

    count_gap = abs(
        count_a - count_b
    )

    if count_gap >= 3:
        review_points += 1
        reasons.append(
            "Large difference in number of assets between packages."
        )

    # -------------------------
    # Premium-player-for-low-return protection
    # -------------------------
    def package_has_premium(
        flags
    ):
        return bool(
            flags[
                "franchise_qbs"
            ]
            or flags[
                "young_elite_players"
            ]
            or flags[
                "elite_players"
            ]
        )

    if (
        package_has_premium(
            flags_a
        )
        and value_b
        < (
            value_a
            * 0.75
        )
    ):
        critical_points += 1
        reasons.append(
            f"{team_a} is sending premium talent without enough return value."
        )

    if (
        package_has_premium(
            flags_b
        )
        and value_a
        < (
            value_b
            * 0.75
        )
    ):
        critical_points += 1
        reasons.append(
            f"{team_b} is sending premium talent without enough return value."
        )

    # -------------------------
    # V2 decision
    # -------------------------
    # Extreme raw imbalance is still an automatic denial.
    if gap_percent >= 35:
        decision = "AUTO DENY"
        level = "VERY BAD"
        emoji = "❌"

    # Critical premium-asset concerns + meaningful imbalance.
    elif (
        critical_points >= 2
        or (
            critical_points >= 1
            and gap_percent >= 24
        )
    ):
        decision = "AUTO DENY"
        level = "VERY BAD"
        emoji = "❌"

    # Strong manual-review zone.
    elif (
        gap_percent >= 20
        or review_points >= 5
    ):
        decision = (
            "STRONG LEAGUE OFFICE REVIEW"
        )
        level = "BAD"
        emoji = "🟠"

    # Normal manual-review zone.
    elif (
        gap_percent > 7
        or review_points >= 2
    ):
        decision = (
            "LEAGUE OFFICE REVIEW V2"
        )
        level = "QUESTIONABLE"
        emoji = "🟡"

    else:
        decision = "AUTO APPROVE"
        level = "GOOD"
        emoji = "✅"

    # Convert review points into a simple confidence/fairness score.
    fairness_score = max(
        0,
        min(
            100,
            round(
                100
                - (
                    gap_percent
                    * 1.8
                )
                - (
                    review_points
                    * 3.5
                )
                - (
                    critical_points
                    * 7
                )
            )
        )
    )

    if not reasons:
        reasons.append(
            "Packages are close in value with no major V2 risk flags."
        )

    return {
        "version":
            "League Office Review V2",
        "decision":
            decision,
        "level":
            level,
        "emoji":
            emoji,
        "value_gap_percent":
            gap_percent,
        "fairness_score":
            fairness_score,
        "review_points":
            review_points,
        "critical_points":
            critical_points,
        "advantage_team":
            advantage_team,
        "disadvantage_team":
            disadvantage_team,
        "reasons":
            reasons[:6],
        "checks": {
            "team_a":
                flags_a,
            "team_b":
                flags_b,
            "gap_bucket":
                gap_bucket
        }
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
        abs(grade_a["percentage"]),
        abs(grade_b["percentage"])
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
                f"I have {winner} getting the better end of this, "
                f"and {loser} has some explaining to do."
            )
        )

    return (
        f"Slight edge to {winner}",
        (
            f"I lean toward {winner}, but this is not a deal "
            f"I would call completely one-sided."
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

    verdict, reaction = generate_trade_reaction(
        team_a,
        team_b,
        grade_a,
        grade_b,
        value_a_received,
        value_b_received,
        trade_id
    )

    review = committee_review(
        team_a,
        team_b,
        value_a_received,
        value_b_received,
        breakdown_a,
        breakdown_b
    )

    return {
        "trade_id": trade_id,
        "team_a": team_a,
        "team_b": team_b,
        "team_a_mention": data["team_a_mention"],
        "team_b_mention": data["team_b_mention"],
        "team_a_sends": breakdown_a,
        "team_b_sends": breakdown_b,
        "team_a_value_sent": value_a_sent,
        "team_b_value_sent": value_b_sent,
        "team_a_grade": grade_a,
        "team_b_grade": grade_b,
        "verdict": verdict,
        "reaction": reaction,
        "trade_committee": review,
        "created_at": datetime.now(
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
    if asset["type"] == "pick":
        return (
            f"{asset['year']} "
            f"Round {asset['round']} Pick"
        )

    return (
        f"{asset['name']} — "
        f"{asset['overall']} OVR "
        f"{asset['position']} • "
        f"Age {asset['age']} • "
        f"{dev_display(asset.get('dev'))}"
    )


# =========================================================
# DISCORD - TRADE APPROVAL ONLY
# =========================================================


def team_logo_url_from_name(team_name):
    team = find_team(team_name)

    if not team:
        return ""

    abbr = (
        team.get("abbrName")
        or team.get("abbr")
        or ""
    ).lower()

    if not abbr:
        return ""

    return (
        "https://a.espncdn.com/i/teamlogos/"
        f"nfl/500/{abbr}.png"
    )


def fetch_image_for_card(url):
    if not url:
        return None

    try:
        response = requests.get(
            url,
            timeout=10
        )
        response.raise_for_status()

        image = Image.open(
            BytesIO(response.content)
        ).convert("RGBA")

        return image

    except Exception:
        return None


def trade_card_font(size, bold=False):
    candidates = []

    if bold:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ])
    else:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ])

    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(
                    candidate,
                    size
                )
            except Exception:
                pass

    return ImageFont.load_default()


def format_trade_card_asset(asset):
    if isinstance(asset, str):
        return asset

    if not isinstance(asset, dict):
        return str(asset)

    if asset.get("type") == "pick":
        year = asset.get("year", "")
        round_number = asset.get(
            "round",
            ""
        )
        return (
            f"{year} Round {round_number}"
        ).strip()

    name = (
        asset.get("name")
        or asset.get("player")
        or "Player"
    )

    position = (
        asset.get("position")
        or ""
    )

    overall = (
        asset.get("overall")
        or asset.get("ovr")
    )

    dev = (
        asset.get("dev")
        or ""
    )

    pieces = [str(name)]

    meta = []

    if position:
        meta.append(str(position))

    if overall is not None:
        meta.append(
            f"{overall} OVR"
        )

    if dev:
        meta.append(
            str(dev).replace(
                "_",
                " "
            ).title()
        )

    if meta:
        pieces.append(
            " • ".join(meta)
        )

    return " — ".join(pieces)


def get_trade_side_assets(
    analysis,
    side
):
    keys = []

    if side == "a":
        keys = [
            "team_a_sends",
            "team_a_assets",
            "team_a_trade_assets"
        ]
    else:
        keys = [
            "team_b_sends",
            "team_b_assets",
            "team_b_trade_assets"
        ]

    for key in keys:
        value = analysis.get(key)

        if isinstance(value, list):
            return value

    return []


def wrap_card_text(
    draw,
    text,
    font,
    max_width
):
    words = str(text).split()
    lines = []
    current = ""

    for word in words:
        test = (
            word
            if not current
            else f"{current} {word}"
        )

        bbox = draw.textbbox(
            (0, 0),
            test,
            font=font
        )

        width = (
            bbox[2] - bbox[0]
        )

        if width <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines or [""]


def draw_trade_side(
    canvas,
    draw,
    x,
    y,
    width,
    height,
    team_name,
    assets,
    grade,
    logo
):
    # Franchise-mode inspired presentation without copying the exact game UI.
    panel_fill = (8, 17, 30)
    header_fill = (16, 74, 118)
    slot_fill = (13, 23, 36)
    slot_border = (55, 83, 109)
    white = (245, 247, 250)
    muted = (174, 189, 203)
    accent = (74, 192, 255)
    grade_bg = (18, 30, 44)

    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=18,
        fill=panel_fill,
        outline=(38, 112, 164),
        width=3
    )

    draw.rectangle(
        (x, y, x + width, y + 108),
        fill=header_fill
    )

    title_font = trade_card_font(
        31,
        bold=True
    )

    label_font = trade_card_font(
        18,
        bold=True
    )

    asset_font = trade_card_font(
        23,
        bold=True
    )

    meta_font = trade_card_font(
        17,
        bold=False
    )

    ovr_font = trade_card_font(
        27,
        bold=True
    )

    grade_font = trade_card_font(
        38,
        bold=True
    )

    if logo:
        logo_copy = logo.copy()
        logo_copy.thumbnail(
            (76, 76)
        )

        canvas.alpha_composite(
            logo_copy,
            (
                x + 18,
                y + 15
            )
        )

        title_x = x + 108
    else:
        title_x = x + 24

    draw.text(
        (title_x, y + 23),
        str(team_name).upper(),
        font=title_font,
        fill=white
    )

    draw.text(
        (title_x, y + 66),
        "TRADE ASSETS",
        font=label_font,
        fill=(205, 230, 245)
    )

    slot_y = y + 132
    slot_h = 78
    slot_gap = 12

    for slot_index in range(5):
        sy = (
            slot_y
            + slot_index
            * (slot_h + slot_gap)
        )

        draw.rounded_rectangle(
            (
                x + 22,
                sy,
                x + width - 22,
                sy + slot_h
            ),
            radius=10,
            fill=slot_fill,
            outline=slot_border,
            width=2
        )

        if slot_index < len(assets):
            asset = assets[
                slot_index
            ]

            formatted = (
                format_trade_card_asset(
                    asset
                )
            )

            if isinstance(
                asset,
                dict
            ):
                overall = (
                    asset.get("overall")
                    or asset.get("ovr")
                )

                name = (
                    asset.get("name")
                    or asset.get("player")
                    or formatted
                )

                position = (
                    asset.get("position")
                    or ""
                )

                if asset.get(
                    "type"
                ) == "pick":
                    draw.text(
                        (
                            x + 42,
                            sy + 18
                        ),
                        "NFL DRAFT",
                        font=label_font,
                        fill=accent
                    )

                    draw.text(
                        (
                            x + 165,
                            sy + 16
                        ),
                        formatted,
                        font=asset_font,
                        fill=white
                    )
                else:
                    draw.text(
                        (
                            x + 42,
                            sy + 14
                        ),
                        str(name),
                        font=asset_font,
                        fill=white
                    )

                    meta = (
                        str(position)
                        if position
                        else "PLAYER"
                    )

                    draw.text(
                        (
                            x + 42,
                            sy + 47
                        ),
                        meta,
                        font=meta_font,
                        fill=muted
                    )

                    if overall is not None:
                        badge_x = (
                            x + width - 88
                        )

                        draw.ellipse(
                            (
                                badge_x,
                                sy + 11,
                                badge_x + 54,
                                sy + 65
                            ),
                            fill=(18, 85, 49),
                            outline=(115, 242, 155),
                            width=2
                        )

                        draw.text(
                            (
                                badge_x + 27,
                                sy + 37
                            ),
                            str(overall),
                            font=ovr_font,
                            fill=white,
                            anchor="mm"
                        )
            else:
                draw.text(
                    (
                        x + 42,
                        sy + 24
                    ),
                    formatted,
                    font=asset_font,
                    fill=white
                )

        else:
            draw.text(
                (
                    x + width / 2,
                    sy + slot_h / 2
                ),
                "ADD PLAYER OR DRAFT PICK",
                font=label_font,
                fill=(90, 113, 133),
                anchor="mm"
            )

    grade_y = (
        y + height - 88
    )

    draw.rounded_rectangle(
        (
            x + 22,
            grade_y,
            x + width - 22,
            y + height - 20
        ),
        radius=10,
        fill=grade_bg
    )

    draw.text(
        (
            x + 42,
            grade_y + 18
        ),
        "TRADE GRADE",
        font=label_font,
        fill=muted
    )

    draw.text(
        (
            x + width - 58,
            grade_y + 34
        ),
        str(grade or "—"),
        font=grade_font,
        fill=white,
        anchor="mm"
    )


def generate_trade_card(
    analysis
):
    width = 1600
    height = 1000

    canvas = Image.new(
        "RGBA",
        (width, height),
        (4, 11, 19, 255)
    )

    draw = ImageDraw.Draw(
        canvas
    )

    white = (245, 247, 250)
    muted = (167, 184, 198)
    accent = (58, 169, 234)

    # Blue franchise-mode style backdrop.
    for stripe_x in range(
        -200,
        width + 200,
        90
    ):
        draw.polygon(
            [
                (
                    stripe_x,
                    0
                ),
                (
                    stripe_x + 50,
                    0
                ),
                (
                    stripe_x + 420,
                    height
                ),
                (
                    stripe_x + 360,
                    height
                )
            ],
            fill=(5, 20, 34)
        )

    header_font = trade_card_font(
        42,
        bold=True
    )

    sub_font = trade_card_font(
        20,
        bold=True
    )

    small_font = trade_card_font(
        18,
        bold=False
    )

    review_font = trade_card_font(
        29,
        bold=True
    )

    # Top title bar.
    draw.rectangle(
        (
            0,
            0,
            width,
            118
        ),
        fill=(6, 13, 22)
    )

    draw.text(
        (
            55,
            30
        ),
        "REQUEST A TRADE",
        font=header_font,
        fill=white
    )

    draw.text(
        (
            55,
            82
        ),
        "PROJECT MADDEN • LEAGUE OFFICE TRADE CENTER V2",
        font=sub_font,
        fill=accent
    )

    trade_id = str(
        analysis.get(
            "trade_id",
            ""
        )
    )

    if trade_id:
        draw.text(
            (
                width - 55,
                50
            ),
            f"TRADE ID {trade_id}",
            font=small_font,
            fill=muted,
            anchor="ra"
        )

    team_a = analysis.get(
        "team_a",
        "TEAM A"
    )

    team_b = analysis.get(
        "team_b",
        "TEAM B"
    )

    assets_a = get_trade_side_assets(
        analysis,
        "a"
    )

    assets_b = get_trade_side_assets(
        analysis,
        "b"
    )

    grade_a = (
        analysis.get(
            "team_a_grade",
            {}
        ).get(
            "grade",
            "—"
        )
        if isinstance(
            analysis.get(
                "team_a_grade"
            ),
            dict
        )
        else "—"
    )

    grade_b = (
        analysis.get(
            "team_b_grade",
            {}
        ).get(
            "grade",
            "—"
        )
        if isinstance(
            analysis.get(
                "team_b_grade"
            ),
            dict
        )
        else "—"
    )

    logo_a = fetch_image_for_card(
        team_logo_url_from_name(
            team_a
        )
    )

    logo_b = fetch_image_for_card(
        team_logo_url_from_name(
            team_b
        )
    )

    panel_y = 150
    panel_h = 690
    panel_w = 675

    draw_trade_side(
        canvas,
        draw,
        55,
        panel_y,
        panel_w,
        panel_h,
        team_a,
        assets_a,
        grade_a,
        logo_a
    )

    draw_trade_side(
        canvas,
        draw,
        870,
        panel_y,
        panel_w,
        panel_h,
        team_b,
        assets_b,
        grade_b,
        logo_b
    )

    # Center exchange indicator.
    arrow_font = trade_card_font(
        62,
        bold=True
    )

    draw.text(
        (
            800,
            470
        ),
        "⇄",
        font=arrow_font,
        fill=accent,
        anchor="mm"
    )

    draw.text(
        (
            800,
            535
        ),
        "TRADE",
        font=sub_font,
        fill=muted,
        anchor="mm"
    )

    review = analysis.get(
        "trade_committee",
        {}
    )

    decision = (
        review.get(
            "decision",
            "LEAGUE OFFICE REVIEW"
        )
        if isinstance(
            review,
            dict
        )
        else "LEAGUE OFFICE REVIEW"
    )

    gap = (
        review.get(
            "gap_percentage"
        )
        if isinstance(
            review,
            dict
        )
        else None
    )

    # Bottom command/review bar inspired by franchise UI.
    draw.rectangle(
        (
            0,
            865,
            width,
            height
        ),
        fill=(7, 14, 22)
    )

    draw.text(
        (
            55,
            890
        ),
        "LEAGUE OFFICE REVIEW",
        font=sub_font,
        fill=muted
    )

    draw.text(
        (
            55,
            925
        ),
        str(decision),
        font=review_font,
        fill=white
    )

    if gap is not None:
        draw.text(
            (
                width - 55,
                925
            ),
            f"VALUE GAP {gap}%",
            font=sub_font,
            fill=muted,
            anchor="ra"
        )

    draw.text(
        (
            width / 2,
            975
        ),
        "Project Madden • Trade Center V2",
        font=small_font,
        fill=(100, 120, 137),
        anchor="mm"
    )

    out_dir = (
        Path(__file__).resolve().parent
        / TRADE_CARD_DIR
    )

    out_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    filename = (
        f"trade_{trade_id or 'preview'}.png"
    )

    output_path = (
        out_dir / filename
    )

    canvas.convert(
        "RGB"
    ).save(
        output_path,
        "PNG",
        optimize=True
    )

    return output_path


@app.route(
    "/trade-card/<trade_id>.png",
    methods=["GET"]
)
def trade_card_image(
    trade_id
):
    filepath = (
        Path(__file__).resolve().parent
        / TRADE_CARD_DIR
        / f"trade_{trade_id}.png"
    )

    if not filepath.exists():
        return jsonify({
            "error":
                "trade card not found"
        }), 404

    return send_file(
        filepath,
        mimetype="image/png"
    )



# =========================================================
# MARCUS HAYES - TRADE REACTIONS
# =========================================================

MARCUS_TRADE_OPENERS = [
    "We have a trade proposal on the table, and there is plenty to unpack here.",
    "This one immediately caught my attention because the value is not landing evenly.",
    "A proposal just hit the League Office, and this is exactly the kind of move that starts arguments.",
    "Now this is interesting. Two teams are trying to change their direction with one deal.",
    "The League Office has a new proposal, and the numbers are already telling a story.",
    "This is the kind of trade where both sides need to be very clear about what they are trying to accomplish.",
]

MARCUS_TRADE_BALANCED = [
    "I can understand the logic for both sides. Nobody is obviously getting robbed here, and that matters.",
    "This is close enough that the fit and roster plan matter more than the raw value gap.",
    "Both teams have a case. I may prefer one side, but this is a real negotiation rather than a giveaway.",
]

MARCUS_TRADE_QUESTIONABLE = [
    "I see the idea, but one side is clearly paying a premium. That needs to be justified by team need and roster direction.",
    "There is enough of a gap here that I would want the League Office to look closely before calling it clean.",
    "This is not automatically a terrible deal, but the side giving up more value needs a very strong reason.",
]

MARCUS_TRADE_BAD = [
    "I have a problem with this value. One side is giving up too much, and the grades are reflecting that.",
    "This is where a proposal starts looking less like roster building and more like one team bailing the other out.",
    "The value gap is too large to ignore. If this goes through, the team losing value needs to explain the plan.",
]

MARCUS_TRADE_DENY = [
    "No. The gap is too large. The League Office is right to deny this unless the package changes significantly.",
    "This proposal needs to go back to the negotiating table. The value simply is not close enough right now.",
    "I would not approve this as submitted. One side is giving away far too much value.",
]


def load_marcus_trade_reaction_history():
    history = load_json_file(
        MARCUS_TRADE_REACTION_HISTORY_FILE
    )

    if not isinstance(history, list):
        history = []

    return history


def save_marcus_trade_reaction_history(history):
    save_json_file(
        MARCUS_TRADE_REACTION_HISTORY_FILE,
        history[-300:]
    )


def marcus_trade_reaction_key(analysis):
    return str(
        analysis.get(
            "trade_id",
            ""
        )
    ).strip()


def build_marcus_trade_reaction(analysis):
    team_a = analysis.get(
        "team_a",
        "Team A"
    )

    team_b = analysis.get(
        "team_b",
        "Team B"
    )

    review = analysis.get(
        "trade_committee",
        {}
    )

    decision = str(
        review.get(
            "decision",
            ""
        )
    ).upper()

    gap = review.get(
        "gap_percentage"
    )

    grade_a = (
        analysis.get(
            "team_a_grade",
            {}
        ).get(
            "grade",
            "—"
        )
        if isinstance(
            analysis.get("team_a_grade"),
            dict
        )
        else "—"
    )

    grade_b = (
        analysis.get(
            "team_b_grade",
            {}
        ).get(
            "grade",
            "—"
        )
        if isinstance(
            analysis.get("team_b_grade"),
            dict
        )
        else "—"
    )

    key = (
        f"{analysis.get('trade_id')}|"
        f"{team_a}|{team_b}|"
        f"{decision}|{gap}"
    )

    opener = stable_choice(
        MARCUS_TRADE_OPENERS,
        "trade-open-" + key
    )

    if "AUTO DENY" in decision:
        body = stable_choice(
            MARCUS_TRADE_DENY,
            "trade-body-" + key
        )
    elif "STRONG" in decision:
        body = stable_choice(
            MARCUS_TRADE_BAD,
            "trade-body-" + key
        )
    elif "REVIEW" in decision:
        body = stable_choice(
            MARCUS_TRADE_QUESTIONABLE,
            "trade-body-" + key
        )
    else:
        body = stable_choice(
            MARCUS_TRADE_BALANCED,
            "trade-body-" + key
        )

    return {
        "headline":
            f"{team_a} ↔ {team_b}",
        "take":
            f"{opener} {body}",
        "team_a_grade":
            grade_a,
        "team_b_grade":
            grade_b,
        "decision":
            decision,
        "value_gap":
            gap
    }


def post_marcus_trade_reaction(analysis):
    if not analyst_webhook_configured():
        return {
            "sent": False,
            "error": (
                "ANALYST_DISCORD_WEBHOOK_URL "
                "is not configured."
            )
        }

    trade_key = marcus_trade_reaction_key(
        analysis
    )

    if not trade_key:
        return {
            "sent": False,
            "error":
                "Trade ID missing."
        }

    history = load_marcus_trade_reaction_history()

    if trade_key in history:
        return {
            "sent": False,
            "skipped": True,
            "reason":
                "already_posted"
        }

    reaction = build_marcus_trade_reaction(
        analysis
    )

    description = (
        f"## {reaction['headline']}\n"
        f"{reaction['take']}\n\n"
        f"**Trade Grades**\n"
        f"{analysis.get('team_a')}: "
        f"**{reaction['team_a_grade']}**\n"
        f"{analysis.get('team_b')}: "
        f"**{reaction['team_b_grade']}**\n\n"
        f"🏛️ **League Office Review:** "
        f"{reaction['decision']}"
    )

    if reaction.get(
        "value_gap"
    ) is not None:
        description += (
            f"\n**Value Gap:** "
            f"{reaction['value_gap']}%"
        )

    result = send_analyst_embed(
        "💬 TRADE REACTION • Marcus Hayes",
        description
    )

    if result.get("sent"):
        history.append(
            trade_key
        )

        save_marcus_trade_reaction_history(
            history
        )

    return result


def post_trade_to_discord(analysis):
    webhook_url = os.environ.get(
        "DISCORD_WEBHOOK_URL"
    )

    if not webhook_url:
        return {
            "sent": False,
            "error": "DISCORD_WEBHOOK_URL is not configured."
        }

    team_a_assets = "\n".join(
        f"• {summarize_asset(asset)}"
        for asset in analysis["team_a_sends"]
    )

    team_b_assets = "\n".join(
        f"• {summarize_asset(asset)}"
        for asset in analysis["team_b_sends"]
    )

    review = analysis["trade_committee"]

    mention_ids = extract_discord_user_ids(
        analysis.get("team_a_mention"),
        analysis.get("team_b_mention")
    )

    screenshot_url = str(
        analysis.get(
            "trade_screenshot_url",
            ""
        )
    ).strip()

    review = analysis.get(
        "trade_committee",
        {}
    )

    decision = str(
        review.get(
            "decision",
            ""
        )
    ).upper()

    committee_role = (
        trade_committee_role_id()
    )

    committee_role_mention = ""

    if committee_role and (
        "LEAGUE OFFICE REVIEW" in decision
        or "STRONG LEAGUE OFFICE REVIEW" in decision
    ):
        committee_role_mention = (
            f"<@&{committee_role}>"
        )

    payload = {
        "username": "Project Madden League Office",
        "avatar_url": (
            "https://project-madden-analytics.onrender.com/"
            "assets/project-madden-league-office.jpeg"
        ),
        "content": (
            f"{analysis['team_a_mention']} "
            f"{analysis['team_b_mention']}"
            + (
                f" {committee_role_mention}"
                if committee_role_mention
                else ""
            )
        ),

        "embeds": [
            {
                "title": "🚨 PROJECT MADDEN TRADE PROPOSAL",

                "description": (
                    f"**{analysis['team_a']} ↔ {analysis['team_b']}**"
                    f"\n\nTrade ID: `{analysis['trade_id']}`"
                ),

                "fields": [
                    {
                        "name": f"{analysis['team_a']} Sends",
                        "value": team_a_assets,
                        "inline": False
                    },
                    {
                        "name": f"{analysis['team_b']} Sends",
                        "value": team_b_assets,
                        "inline": False
                    },
                    {
                        "name": "📊 Trade Grades",
                        "value": (
                            f"**{analysis['team_a']}:** "
                            f"{analysis['team_a_grade']['grade']}\n"
                            f"**{analysis['team_b']}:** "
                            f"{analysis['team_b_grade']['grade']}"
                        ),
                        "inline": False
                    },
                    {
                        "name": "🏛️ League Office Review V2",
                        "value": (
                            f"{review['emoji']} "
                            f"**{review['decision']}**\n"
                            f"Quality: {review['level']}\n"
                            f"Fairness Score: "
                            f"**{review.get('fairness_score', '—')}/100**\n"
                            f"Value Gap: "
                            f"{review['value_gap_percent']}%\n"
                            + (
                                "**Why:**\n• "
                                + "\n• ".join(
                                    review.get(
                                        "reasons",
                                        []
                                    )[:4]
                                )
                                if review.get(
                                    "reasons"
                                )
                                else ""
                            )
                        )[:1024],
                        "inline": False
                    }
                ],

                "footer": {
                    "text": "Project Madden • League Office"
                }
            }
        ]
    }

    trade_card_url = ""

    try:
        trade_card_path = generate_trade_card(
            analysis
        )

        trade_card_url = (
            "https://project-madden-analytics.onrender.com/"
            f"trade-card/{analysis.get('trade_id')}.png"
        )
    except Exception as e:
        print(
            "TRADE CARD ERROR:",
            str(e)
        )

    # The generated Project Madden card is the main visual.
    if trade_card_url:
        try:
            payload["embeds"][0]["image"] = {
                "url": trade_card_url
            }
        except Exception:
            pass

    # If a user also uploaded the Madden trade screen, include it as
    # a second proof embed instead of replacing the generated card.
    if screenshot_url:
        payload["embeds"].append({
            "title":
                "📸 Madden Trade Screen • Proof",
            "image": {
                "url": screenshot_url
            }
        })

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10
        )

        if response.status_code in [200, 204]:
            return {"sent": True}

        return {
            "sent": False,
            "error": (
                f"Discord returned "
                f"{response.status_code}: "
                f"{response.text[:200]}"
            )
        }

    except Exception as e:
        return {
            "sent": False,
            "error": str(e)
        }


# =========================================================
# DISCORD - MARCUS HAYES MEDIA ONLY
# =========================================================

def get_analyst_webhook():
    return os.environ.get(
        "ANALYST_DISCORD_WEBHOOK_URL"
    )


def analyst_webhook_configured():
    return bool(
        get_analyst_webhook()
    )


def get_weekly_show_webhook():
    return os.environ.get(
        "WEEKLY_SHOW_DISCORD_WEBHOOK_URL",
        ""
    ).strip()


def weekly_show_webhook_configured():
    return bool(
        get_weekly_show_webhook()
    )




def send_analyst_embed(
    title,
    description,
    fields=None
):
    webhook_url = get_analyst_webhook()

    if not webhook_url:
        return {
            "sent": False,
            "error": (
                "ANALYST_DISCORD_WEBHOOK_URL "
                "is not configured."
            )
        }

    embed = {
        "title": title,
        "description": description,
        "footer": {
            "text": (
                "Marcus Hayes • "
                "Project Madden Media"
            )
        }
    }

    if fields:
        embed["fields"] = fields

    marcus_avatar_url = (
        "https://project-madden-analytics.onrender.com/"
        "assets/marcus-hayes.png"
    )

    embed["thumbnail"] = {
        "url": marcus_avatar_url
    }

    payload = {
        "username": "Marcus Hayes | Project Madden",
        "avatar_url": marcus_avatar_url,
        "embeds": [embed]
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10
        )

        if response.status_code in [200, 204]:
            return {"sent": True}

        return {
            "sent": False,
            "error": (
                f"Discord returned "
                f"{response.status_code}: "
                f"{response.text[:200]}"
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
# ANALYST BANKS
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


# =========================================================
# GAME STORY LOGIC
# =========================================================

def game_looks_completed(game):
    away_score = int(
        game.get("awayScore", 0) or 0
    )

    home_score = int(
        game.get("homeScore", 0) or 0
    )

    # Current Snallabot unplayed games are 0-0.
    return (
        away_score != 0
        or home_score != 0
    )


def classify_game_story(game):
    away_id = game.get("awayTeamId")
    home_id = game.get("homeTeamId")

    away_score = int(
        game.get("awayScore", 0) or 0
    )

    home_score = int(
        game.get("homeScore", 0) or 0
    )

    away_name = safe_team_name(away_id)
    home_name = safe_team_name(home_id)

    away_ovr = safe_team_overall(away_id)
    home_ovr = safe_team_overall(home_id)

    if away_score == home_score:
        return {
            "story_type": "tie",
            "away": away_name,
            "home": home_name,
            "away_score": away_score,
            "home_score": home_score
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

    margin = winner_score - loser_score

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
        "story_type": story_type,
        "winner": winner,
        "loser": loser,
        "margin": margin,
        "winner_score": winner_score,
        "loser_score": loser_score,
        "winner_ovr": winner_ovr,
        "loser_ovr": loser_ovr,
        "away": away_name,
        "home": home_name,
        "away_score": away_score,
        "home_score": home_score,
        "upset": upset
    }


def build_game_take(story, key):
    winner = story.get("winner")
    loser = story.get("loser")

    opening = unique_analyst_choice(
        "game_opening",
        ANALYST_OPENINGS,
        key
    )

    story_type = story.get("story_type")

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

    return f"{opening} {body} {closer}"


def make_game_headline(story, key):
    winner = story.get("winner")
    loser = story.get("loser")
    story_type = story.get("story_type")

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

def stat_value(record, keys, default=0):
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

    records = recursive_records(data)

    return [
        record
        for record in records
        if detect_player_name(record)
    ]


# =========================================================
# PLAYER ANALYST
# =========================================================

def passing_reactions(
    data,
    season_type,
    week_number
):
    results = []

    for record in extract_stat_records(data):
        player = detect_player_name(record)

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

        if yards <= 0 and tds <= 0 and ints <= 0:
            continue

        key = (
            f"{season_type}-"
            f"{week_number}-"
            f"{player}-passing"
        )

        if yards >= 300 and tds >= 3 and ints <= 1:
            story_type = "elite_qb_game"

            template = unique_analyst_choice(
                "qb_elite",
                QB_ELITE_LINES,
                key
            )

        elif ints >= 3 or (
            ints >= 2 and tds == 0
        ):
            story_type = "qb_disaster"

            template = unique_analyst_choice(
                "qb_bad",
                QB_BAD_LINES,
                key
            )

        else:
            continue

        results.append({
            "player": player,
            "category": "passing",
            "story_type": story_type,
            "stats": {
                "yards": yards,
                "touchdowns": tds,
                "interceptions": ints
            },
            "analyst_take": template.format(
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

    for record in extract_stat_records(data):
        player = detect_player_name(record)

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

        if yards < 100 and tds < 2:
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
            "player": player,
            "category": "rushing",
            "story_type": "rushing_takeover",
            "stats": {
                "yards": yards,
                "touchdowns": tds
            },
            "analyst_take": template.format(
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

    for record in extract_stat_records(data):
        player = detect_player_name(record)

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

        if yards < 100 and tds < 2:
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
            "player": player,
            "category": "receiving",
            "story_type": "receiver_takeover",
            "stats": {
                "yards": yards,
                "touchdowns": tds
            },
            "analyst_take": template.format(
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

    for record in extract_stat_records(data):
        player = detect_player_name(record)

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

        if sacks < 2 and ints < 1 and forced_fumbles < 2:
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
            "player": player,
            "category": "defense",
            "story_type": "defensive_takeover",
            "stats": {
                "sacks": sacks,
                "interceptions": ints,
                "forced_fumbles": forced_fumbles
            },
            "analyst_take": (
                f"{template} "
                f"He finished with {sacks} sacks, "
                f"{ints} interceptions and "
                f"{forced_fumbles} forced fumbles."
            )
        })

    return results


# =========================================================
# MARCUS HAYES DISCORD POST HELPERS
# =========================================================

def post_game_reaction_to_discord(reaction):
    story_type = reaction.get(
        "story_type",
        "game_reaction"
    )

    story_labels = {
        "blowout": "🔥 BLOWOUT",
        "upset": "🚨 UPSET ALERT",
        "close_game": "😮 THRILLER",
        "normal_win": "🏈 GAME REACTION"
    }

    label = story_labels.get(
        story_type,
        "🏈 GAME REACTION"
    )

    headline = reaction.get(
        "headline",
        "Marcus Hayes reacts"
    )

    game = reaction.get("game", "")
    take = reaction.get(
        "analyst_take",
        ""
    )

    fields = []

    if reaction.get("winner"):
        fields.append({
            "name": "Winner",
            "value": str(
                reaction["winner"]
            ),
            "inline": True
        })

    if reaction.get("loser"):
        fields.append({
            "name": "Loser",
            "value": str(
                reaction["loser"]
            ),
            "inline": True
        })

    if reaction.get("margin") is not None:
        fields.append({
            "name": "Margin",
            "value": str(
                reaction["margin"]
            ),
            "inline": True
        })

    if reaction.get("upset"):
        fields.append({
            "name": "Marcus Says",
            "value": (
                "The lower-rated team won this game "
                "on the field."
            ),
            "inline": False
        })

    return send_analyst_embed(
        f"{label} • {headline}",
        (
            f"**{game}**\n\n"
            f"🎙️ **Marcus Hayes:**\n"
            f"{take}"
        ),
        fields
    )


def post_player_reaction_to_discord(reaction):
    player = reaction.get(
        "player",
        "Unknown Player"
    )

    category = reaction.get(
        "category",
        "performance"
    )

    story_type = reaction.get(
        "story_type",
        ""
    )

    take = reaction.get(
        "analyst_take",
        ""
    )

    stats = reaction.get(
        "stats",
        {}
    )

    category_labels = {
        "passing": "🎯 QB REPORT",
        "rushing": "💨 RUSHING REPORT",
        "receiving": "🔥 RECEIVER REPORT",
        "defense": "🛡️ DEFENSIVE REPORT"
    }

    label = category_labels.get(
        category,
        "⭐ PLAYER REPORT"
    )

    stat_lines = []

    for key, value in stats.items():
        pretty_key = (
            str(key)
            .replace("_", " ")
            .title()
        )

        stat_lines.append(
            f"**{pretty_key}:** {value}"
        )

    fields = []

    if stat_lines:
        fields.append({
            "name": "Stat Line",
            "value": "\n".join(
                stat_lines
            ),
            "inline": False
        })

    fields.append({
        "name": "Story",
        "value": (
            str(story_type)
            .replace("_", " ")
            .title()
        ),
        "inline": False
    })

    return send_analyst_embed(
        f"{label} • {player}",
        (
            f"🎙️ **Marcus Hayes:**\n"
            f"{take}"
        ),
        fields
    )


def build_week_game_reactions(
    season_type,
    week_number
):
    schedule_data = load_weekly_data(
        season_type,
        week_number,
        "schedules"
    )

    if not schedule_data:
        return []

    reactions = []

    for game in schedule_data.get(
        "gameScheduleInfoList",
        []
    ):
        if not game_looks_completed(game):
            continue

        story = classify_game_story(game)

        if story.get("story_type") == "tie":
            continue

        key = (
            f"discord-{season_type}-"
            f"{week_number}-"
            f"{game.get('scheduleId')}"
        )

        reactions.append({
            "schedule_id": game.get("scheduleId"),
            "game": (
                f"{story['away']} {story['away_score']}, "
                f"{story['home']} {story['home_score']}"
            ),
            "story_type": story["story_type"],
            "headline": make_game_headline(
                story,
                key
            ),
            "winner": story.get("winner"),
            "loser": story.get("loser"),
            "margin": story.get("margin"),
            "upset": story.get("upset", False),
            "analyst_take": build_game_take(
                story,
                key
            )
        })

    return reactions


def build_week_player_reactions(
    season_type,
    week_number
):
    results = []

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
        results.extend(
            passing_reactions(
                passing_data,
                season_type,
                week_number
            )
        )

    if rushing_data:
        results.extend(
            rushing_reactions(
                rushing_data,
                season_type,
                week_number
            )
        )

    if receiving_data:
        results.extend(
            receiving_reactions(
                receiving_data,
                season_type,
                week_number
            )
        )

    if defense_data:
        results.extend(
            defense_reactions(
                defense_data,
                season_type,
                week_number
            )
        )

    return results


def load_analyst_post_history():
    history = load_json_file(
        ANALYST_POST_HISTORY_FILE
    )

    if not isinstance(history, list):
        history = []

    return history


def analyst_post_key(
    season_type,
    week_number,
    item_type,
    identifier
):
    return (
        f"{season_type}:"
        f"{week_number}:"
        f"{item_type}:"
        f"{identifier}"
    )





# =========================================================
# PROJECT MADDEN WEEKLY SHOW
# =========================================================

WEEKLY_SHOW_OPENERS = [
    "Welcome to the Project Madden Weekly Show. We are breaking down the biggest games, the loudest performances, and the stories that matter.",
    "This is the Project Madden Weekly Show, and there is no shortage of things to talk about after this week.",
    "Another week is in the books, and now it is time to sort out who helped themselves, who hurt themselves, and what the league should be watching next.",
    "The games are over, the numbers are in, and the Project Madden Weekly Show is live.",
]

WEEKLY_SHOW_CLOSERS = [
    "That is the week. The next slate will tell us whether these storylines are real or temporary.",
    "That wraps the Project Madden Weekly Show. Now the pressure shifts to next week.",
    "The league gave us plenty to debate. We will see which teams back it up next week.",
    "That is all for this edition. The standings and the film will keep talking for us.",
]


def load_weekly_show_history():
    history = load_json_file(
        WEEKLY_SHOW_HISTORY_FILE
    )

    if not isinstance(history, list):
        history = []

    return history


def save_weekly_show_history(history):
    save_json_file(
        WEEKLY_SHOW_HISTORY_FILE,
        history[-200:]
    )


def weekly_show_post_key(
    season_type,
    week_number
):
    return (
        f"{season_type}:{week_number}"
    )



def build_weekly_game_predictions(
    season_type,
    week_number
):
    schedule_data = load_weekly_data(
        season_type,
        week_number,
        "schedules"
    )

    if not schedule_data:
        return []

    predictions = []

    for game in schedule_data.get(
        "gameScheduleInfoList",
        []
    ):
        if game_looks_completed(game):
            continue

        away_id = game.get(
            "awayTeamId"
        )
        home_id = game.get(
            "homeTeamId"
        )

        away = safe_team_name(
            away_id
        )
        home = safe_team_name(
            home_id
        )

        away_ovr = safe_team_overall(
            away_id
        )
        home_ovr = safe_team_overall(
            home_id
        )

        if (
            away_ovr is None
            or home_ovr is None
        ):
            favorite = None
            underdog = None
            edge = None
        elif away_ovr > home_ovr:
            favorite = away
            underdog = home
            edge = away_ovr - home_ovr
        elif home_ovr > away_ovr:
            favorite = home
            underdog = away
            edge = home_ovr - away_ovr
        else:
            favorite = "TOSS-UP"
            underdog = None
            edge = 0

        key = (
            f"weekly-pick-{season_type}-"
            f"{week_number}-"
            f"{game.get('scheduleId')}"
        )

        if favorite == "TOSS-UP":
            confidence = "TOSS-UP"
            reason = (
                "The teams are even by current OVR, "
                "so execution and user play should decide it."
            )
        elif edge is not None and edge >= 5:
            confidence = "STRONG LEAN"
            reason = (
                f"{favorite} has the larger roster-rating edge "
                f"by {edge} OVR points."
            )
        elif edge is not None and edge >= 2:
            confidence = "LEAN"
            reason = (
                f"{favorite} is higher-rated by {edge} OVR points, "
                "but this is still very playable."
            )
        else:
            confidence = "SLIGHT LEAN"
            reason = (
                f"{favorite} has only a small OVR advantage. "
                "This matchup can swing on turnovers and execution."
            )

        predictions.append({
            "schedule_id":
                game.get("scheduleId"),
            "away":
                away,
            "home":
                home,
            "away_ovr":
                away_ovr,
            "home_ovr":
                home_ovr,
            "favorite":
                favorite,
            "underdog":
                underdog,
            "ovr_edge":
                edge,
            "confidence":
                confidence,
            "reason":
                reason,
            "matchup":
                f"{away} @ {home}",
            "key":
                key
        })

    return predictions


def weekly_trade_proposals():
    proposals = load_json_file(
        "trade_proposals.json"
    )

    if not isinstance(
        proposals,
        list
    ):
        return []

    # The trade file currently has timestamps, but not Madden week metadata.
    # Use the most recent proposals as the weekly trade-desk segment.
    def sort_key(item):
        return str(
            item.get(
                "created_at",
                ""
            )
        )

    proposals = sorted(
        proposals,
        key=sort_key,
        reverse=True
    )

    return proposals[:5]


def format_trade_show_line(
    trade
):
    team_a = trade.get(
        "team_a",
        "Team A"
    )
    team_b = trade.get(
        "team_b",
        "Team B"
    )

    grade_a = (
        trade.get(
            "team_a_grade",
            {}
        ).get(
            "grade",
            "—"
        )
        if isinstance(
            trade.get("team_a_grade"),
            dict
        )
        else "—"
    )

    grade_b = (
        trade.get(
            "team_b_grade",
            {}
        ).get(
            "grade",
            "—"
        )
        if isinstance(
            trade.get("team_b_grade"),
            dict
        )
        else "—"
    )

    decision = (
        trade.get(
            "trade_committee",
            {}
        ).get(
            "decision",
            "LEAGUE OFFICE REVIEW"
        )
        if isinstance(
            trade.get("trade_committee"),
            dict
        )
        else "LEAGUE OFFICE REVIEW"
    )

    return (
        f"**{team_a} ↔ {team_b}** — "
        f"{team_a}: {grade_a} | "
        f"{team_b}: {grade_b} | "
        f"{decision}"
    )


def build_weekly_panel_takes(
    show,
    season_type,
    week_number
):
    completed = show.get(
        "top_games",
        []
    )

    players = show.get(
        "top_players",
        []
    )

    predictions = show.get(
        "game_predictions",
        []
    )

    trades = show.get(
        "trade_proposals",
        []
    )

    key = (
        f"weekly-panel-{season_type}-"
        f"{week_number}"
    )

    marcus_parts = []
    stephen_parts = []
    pat_parts = []

    if completed:
        game = completed[0]

        winner = game.get(
            "winner",
            "the winner"
        )
        loser = game.get(
            "loser",
            "the loser"
        )

        marcus_parts.append(
            f"{winner} earned the result. "
            f"{loser} has to explain what failed."
        )

        stephen_parts.append(
            f"I am looking directly at {loser}. "
            "A bad result is one thing; repeating the same mistakes "
            "is where I start questioning the entire approach."
        )

        pat_parts.append(
            f"{winner} made the winning plays. "
            "That is the stuff the locker room can build on."
        )

    if players:
        player = players[0]

        player_name = player.get(
            "player",
            "the standout player"
        )

        stats = player.get(
            "stats",
            {}
        )

        stat_text = ", ".join(
            f"{str(k).replace('_', ' ').title()}: {v}"
            for k, v in stats.items()
        )

        marcus_parts.append(
            f"{player_name} deserves the spotlight: {stat_text}."
        )

        stephen_parts.append(
            f"If {player_name} is producing like that, "
            "the opponent has no excuse for failing to adjust."
        )

        pat_parts.append(
            f"{player_name} was a dude this week. "
            "Production like that changes how the next defense prepares."
        )

    if trades:
        trade = trades[0]

        team_a = trade.get(
            "team_a",
            "Team A"
        )
        team_b = trade.get(
            "team_b",
            "Team B"
        )

        decision = (
            trade.get(
                "trade_committee",
                {}
            ).get(
                "decision",
                ""
            )
            if isinstance(
                trade.get("trade_committee"),
                dict
            )
            else ""
        )

        marcus_parts.append(
            f"Trade desk: {team_a} and {team_b} put a deal on the table. "
            f"The League Office call is {decision}."
        )

        stephen_parts.append(
            f"I do not care how exciting a trade looks. "
            f"If {team_a} or {team_b} is giving away too much value, "
            "I am going to say it."
        )

        pat_parts.append(
            "Trades are about fit as much as ratings. "
            "The question is whether the move actually fixes a weakness."
        )

    if predictions:
        pick = predictions[0]

        favorite = pick.get(
            "favorite"
        )

        matchup = pick.get(
            "matchup"
        )

        reason = pick.get(
            "reason",
            ""
        )

        if favorite == "TOSS-UP":
            marcus_parts.append(
                f"Game pick: {matchup} is a toss-up for me. {reason}"
            )
            stephen_parts.append(
                f"I am not giving either side a pass in {matchup}. "
                "The team that protects the football should win."
            )
            pat_parts.append(
                f"{matchup} feels like the game where one weird turnover "
                "or special-teams play can flip everything."
            )
        else:
            marcus_parts.append(
                f"My early favorite in {matchup}: **{favorite}**. {reason}"
            )
            stephen_parts.append(
                f"I have **{favorite}** in {matchup}, "
                "but if the higher-rated roster plays sloppy, "
                "I will be the first one criticizing them afterward."
            )
            pat_parts.append(
                f"I lean **{favorite}** in {matchup}. "
                "But this is Madden — user execution can erase an OVR edge fast."
            )

    if not marcus_parts:
        marcus_parts.append(
            "There is not enough completed league data yet for me to fake a take. "
            "Once the games and stats hit Snallabot, we will break them down."
        )

    if not stephen_parts:
        stephen_parts.append(
            "No fake outrage from me. Give me actual results, stats, "
            "or a real matchup and then we can debate it."
        )

    if not pat_parts:
        pat_parts.append(
            "We are waiting on real league data. Once it lands, "
            "we will have plenty to talk about."
        )

    return {
        "marcus":
            " ".join(
                marcus_parts[:4]
            ),
        "stephen":
            " ".join(
                stephen_parts[:4]
            ),
        "pat":
            " ".join(
                pat_parts[:4]
            )
    }




def build_hot_seat_rankings():
    standings = normalize_standings()

    if not standings:
        return []

    rankings = []

    for team in standings:
        games = int(
            team.get("games", 0) or 0
        )

        wins = int(
            team.get("wins", 0) or 0
        )

        losses = int(
            team.get("losses", 0) or 0
        )

        point_diff = float(
            team.get("point_diff", 0) or 0
        )

        overall = int(
            team.get("overall", 80) or 80
        )

        streak = str(
            team.get("streak", "") or ""
        ).upper()

        team_info = team_by_id(
            team.get("team_id")
        ) or {}

        user_name = (
            team_info.get("user")
            or "CPU / Unassigned"
        )

        if games == 0:
            continue

        loss_pct = (
            losses / games
            if games
            else 0
        )

        # Pressure rises when a strong roster underperforms,
        # loses repeatedly, or has a poor point differential.
        pressure = (
            loss_pct * 50
            + max(
                0,
                overall - 80
            ) * 2.0
            + max(
                0,
                -point_diff / max(
                    games,
                    1
                )
            ) * 2.25
        )

        if streak.startswith("L"):
            try:
                streak_count = int(
                    streak[1:]
                )
            except Exception:
                streak_count = 0

            pressure += min(
                streak_count,
                6
            ) * 4.5

        # Winning teams should almost never appear.
        if wins > losses:
            pressure -= 20

        if wins >= losses and point_diff >= 0:
            pressure -= 15

        reasons = []

        if overall >= 84 and losses > wins:
            reasons.append(
                f"{overall} OVR roster is under .500"
            )

        if point_diff <= -25:
            reasons.append(
                f"{int(point_diff)} point differential"
            )

        if streak.startswith("L"):
            reasons.append(
                f"current {streak} losing streak"
            )

        if loss_pct >= 0.65:
            reasons.append(
                "losing most of their games"
            )

        if not reasons:
            reasons.append(
                "results are not matching expectations"
            )

        rankings.append({
            "team":
                team.get("team"),
            "team_id":
                team.get("team_id"),
            "user":
                user_name,
            "record":
                f"{wins}-{losses}",
            "wins":
                wins,
            "losses":
                losses,
            "games":
                games,
            "overall":
                overall,
            "point_diff":
                int(point_diff),
            "streak":
                streak,
            "pressure_score":
                round(
                    pressure,
                    1
                ),
            "reasons":
                reasons[:4]
        })

    rankings.sort(
        key=lambda item:
            item["pressure_score"],
        reverse=True
    )

    # Only surface teams that actually have meaningful pressure.
    return [
        item
        for item in rankings
        if item["pressure_score"] >= 32
    ][:5]


def build_hot_seat_panel_take(
    hot_seat
):
    if not hot_seat:
        return {}

    top = hot_seat[0]

    team = top["team"]
    user = top["user"]
    record = top["record"]
    overall = top["overall"]

    return {
        "marcus": (
            f"**{user} ({team})** is sitting on my hottest seat right now. "
            f"A {record} record with a {overall} OVR roster means the results "
            "are not matching the talent. At some point, execution has to improve."
        ),
        "stephen": (
            f"**{team}** cannot hide behind roster talent. "
            f"If you are rated {overall} OVR and still sitting at {record}, "
            "I am questioning the decisions, the adjustments, and the consistency."
        ),
        "pat": (
            f"**{team}** needs a get-right week. When the losses start stacking, "
            "every turnover, fourth-down call, and clock decision gets magnified."
        )
    }


def build_super_bowl_favorites():
    standings = normalize_standings()

    if not standings:
        return []

    candidates = []

    # Normalize all available league teams into a projection score.
    for team in standings:
        games = int(
            team.get(
                "games",
                0
            ) or 0
        )

        wins = int(
            team.get(
                "wins",
                0
            ) or 0
        )

        losses = int(
            team.get(
                "losses",
                0
            ) or 0
        )

        win_pct = (
            wins / games
            if games > 0
            else 0.0
        )

        point_diff = float(
            team.get(
                "point_diff",
                0
            ) or 0
        )

        overall = float(
            team.get(
                "overall",
                80
            ) or 80
        )

        streak = str(
            team.get(
                "streak",
                ""
            )
        ).upper()

        seed = int(
            team.get(
                "playoff_seed",
                0
            ) or 0
        )

        streak_bonus = 0.0

        if streak.startswith("W"):
            try:
                streak_count = int(
                    streak[1:]
                )
            except Exception:
                streak_count = 0

            streak_bonus = min(
                streak_count,
                5
            ) * 1.75

        elif streak.startswith("L"):
            try:
                streak_count = int(
                    streak[1:]
                )
            except Exception:
                streak_count = 0

            streak_bonus = -min(
                streak_count,
                5
            ) * 1.5

        # Before games are played, roster OVR matters more.
        if games == 0:
            score = (
                (overall - 75) * 3.0
            )
        else:
            score = (
                win_pct * 70
                + max(
                    -25,
                    min(
                        25,
                        point_diff / max(
                            games,
                            1
                        )
                    )
                )
                + (overall - 80) * 1.7
                + streak_bonus
            )

            if 1 <= seed <= 7:
                score += (
                    8 - seed
                ) * 1.5

        candidates.append({
            "team":
                team.get(
                    "team",
                    "Unknown"
                ),
            "wins":
                wins,
            "losses":
                losses,
            "games":
                games,
            "win_pct":
                round(
                    win_pct,
                    3
                ),
            "point_diff":
                point_diff,
            "overall":
                overall,
            "streak":
                streak,
            "playoff_seed":
                seed,
            "projection_score":
                score
        })

    candidates.sort(
        key=lambda item:
            item["projection_score"],
        reverse=True
    )

    top = candidates[:8]

    if not top:
        return []

    # Softmax-style normalization into a clean "Project Madden projection"
    # percentage. This is not betting odds.
    max_score = max(
        item[
            "projection_score"
        ]
        for item in top
    )

    weights = []

    for item in top:
        weight = math.exp(
            (
                item[
                    "projection_score"
                ]
                - max_score
            ) / 12.0
        )

        weights.append(
            weight
        )

    weight_total = sum(
        weights
    ) or 1.0

    favorites = []

    for index, (
        item,
        weight
    ) in enumerate(
        zip(
            top,
            weights
        ),
        start=1
    ):
        chance = (
            weight
            / weight_total
            * 100
        )

        reason_parts = []

        if item["games"] == 0:
            reason_parts.append(
                f"{int(item['overall'])} OVR roster"
            )
        else:
            reason_parts.append(
                f"{item['wins']}-{item['losses']} record"
            )

            if item["point_diff"] > 0:
                reason_parts.append(
                    f"+{int(item['point_diff'])} point differential"
                )

            if item["streak"].startswith(
                "W"
            ):
                reason_parts.append(
                    f"{item['streak']} streak"
                )

            if 1 <= item[
                "playoff_seed"
            ] <= 7:
                reason_parts.append(
                    f"current #{item['playoff_seed']} seed"
                )

            reason_parts.append(
                f"{int(item['overall'])} OVR"
            )

        favorites.append({
            "rank":
                index,
            "team":
                item["team"],
            "projected_chance":
                round(
                    chance,
                    1
                ),
            "record":
                (
                    f"{item['wins']}-"
                    f"{item['losses']}"
                ),
            "overall":
                int(
                    item["overall"]
                ),
            "streak":
                item["streak"],
            "playoff_seed":
                item["playoff_seed"],
            "reason":
                ", ".join(
                    reason_parts
                )
        })

    return favorites


def build_super_bowl_panel_picks(
    favorites,
    season_type,
    week_number
):
    if not favorites:
        return {}

    top = favorites[:5]

    # Marcus leans toward the current #1 projection.
    marcus_pick = top[0]

    # Stephen A. parody favors the strongest blend of record/OVR,
    # usually the current top projection but not always.
    stephen_pick = max(
        top,
        key=lambda item: (
            item.get(
                "overall",
                0
            ),
            item.get(
                "projected_chance",
                0
            )
        )
    )

    # Pat parody gets a slightly different angle:
    # among the top 5, prefer a team on the best win streak,
    # otherwise the highest projection.
    def streak_value(item):
        streak = str(
            item.get(
                "streak",
                ""
            )
        ).upper()

        if streak.startswith(
            "W"
        ):
            try:
                return int(
                    streak[1:]
                )
            except Exception:
                return 0

        return 0

    pat_pick = max(
        top,
        key=lambda item: (
            streak_value(
                item
            ),
            item.get(
                "projected_chance",
                0
            )
        )
    )

    return {
        "marcus": {
            "team":
                marcus_pick[
                    "team"
                ],
            "take": (
                f"My Super Bowl favorite right now is "
                f"**{marcus_pick['team']}**. "
                f"They lead the Project Madden projection at "
                f"{marcus_pick['projected_chance']}%, and the case is "
                f"{marcus_pick['reason']}."
            )
        },
        "stephen": {
            "team":
                stephen_pick[
                    "team"
                ],
            "take": (
                f"I am putting **{stephen_pick['team']}** at the top of my list. "
                "If you have that kind of roster and you are producing, "
                "I expect you to look like a championship team every single week."
            )
        },
        "pat": {
            "team":
                pat_pick[
                    "team"
                ],
            "take": (
                f"I am riding with **{pat_pick['team']}** right now. "
                "Momentum matters, roster talent matters, and if they keep "
                "stacking good weeks they are going to be a problem in the postseason."
            )
        }
    }


def build_weekly_show_summary(
    season_type,
    week_number
):
    game_reactions = build_week_game_reactions(
        season_type,
        week_number
    )

    player_reactions = build_week_player_reactions(
        season_type,
        week_number
    )

    rankings = build_power_rankings()

    game_predictions = (
        build_weekly_game_predictions(
            season_type,
            week_number
        )
    )

    trade_proposals = (
        weekly_trade_proposals()
    )

    super_bowl_favorites = (
        build_super_bowl_favorites()
    )

    hot_seat = (
        build_hot_seat_rankings()
    )

    top_games = sorted(
        game_reactions,
        key=lambda item: (
            1 if item.get("upset") else 0,
            int(item.get("margin", 0) or 0)
        ),
        reverse=True
    )[:3]

    def player_score(item):
        stats = item.get(
            "stats",
            {}
        )

        category = item.get(
            "category",
            ""
        )

        if category == "passing":
            return (
                int(stats.get("touchdowns", 0) or 0) * 120
                + int(stats.get("yards", 0) or 0)
                - int(stats.get("interceptions", 0) or 0) * 70
            )

        if category in [
            "rushing",
            "receiving"
        ]:
            return (
                int(stats.get("touchdowns", 0) or 0) * 110
                + int(stats.get("yards", 0) or 0)
            )

        if category == "defense":
            return (
                int(stats.get("sacks", 0) or 0) * 140
                + int(stats.get("interceptions", 0) or 0) * 180
                + int(stats.get("forced_fumbles", 0) or 0) * 120
            )

        return 0

    top_players = sorted(
        player_reactions,
        key=player_score,
        reverse=True
    )[:5]

    key = (
        f"weekly-show-{season_type}-{week_number}"
    )

    opener = stable_choice(
        WEEKLY_SHOW_OPENERS,
        key + "-open"
    )

    closer = stable_choice(
        WEEKLY_SHOW_CLOSERS,
        key + "-close"
    )

    stephen_segment = (
        build_stephen_a_parody_segment(
            season_type,
            week_number
        )
    )

    pat_segment = (
        build_pat_mcafee_parody_segment(
            season_type,
            week_number
        )
    )

    show = {
        "season_type":
            season_type,
        "week":
            week_number,
        "opener":
            opener,
        "top_games":
            top_games,
        "top_players":
            top_players,
        "power_rankings":
            rankings[:5],
        "game_predictions":
            game_predictions,
        "trade_proposals":
            trade_proposals,
        "super_bowl_favorites":
            super_bowl_favorites,
        "hot_seat":
            hot_seat,
        "hot_seat_panel_take":
            build_hot_seat_panel_take(
                hot_seat
            ),
        "stephen_a_parody_segment":
            stephen_segment[:2],
        "pat_mcafee_parody_segment":
            pat_segment[:2],
        "closer":
            closer
    }

    show["panel_takes"] = (
        build_weekly_panel_takes(
            show,
            season_type,
            week_number
        )
    )

    show["super_bowl_panel_picks"] = (
        build_super_bowl_panel_picks(
            super_bowl_favorites,
            season_type,
            week_number
        )
    )

    return show


def weekly_show_embed_fields(
    show
):
    fields = []

    top_games = show.get(
        "top_games",
        []
    )

    if top_games:
        lines = []

        for game in top_games[:3]:
            lines.append(
                (
                    f"**{game.get('game', '')}**\n"
                    f"{game.get('analyst_take', '')}"
                )
            )

        fields.append({
            "name":
                "🏈 Game Reactions",
            "value":
                "\n\n".join(lines)[:1024],
            "inline":
                False
        })

    top_players = show.get(
        "top_players",
        []
    )

    if top_players:
        lines = []

        for player in top_players[:5]:
            name = player.get(
                "player",
                "Player"
            )

            stats = player.get(
                "stats",
                {}
            )

            stat_parts = [
                f"{str(key).replace('_', ' ').title()}: {value}"
                for key, value in stats.items()
            ]

            lines.append(
                f"**{name}** — "
                + ", ".join(stat_parts)
            )

        fields.append({
            "name":
                "📊 Stat Leaders & Performances",
            "value":
                "\n".join(lines)[:1024],
            "inline":
                False
        })

    trades = show.get(
        "trade_proposals",
        []
    )

    if trades:
        lines = [
            format_trade_show_line(
                trade
            )
            for trade in trades[:4]
        ]

        fields.append({
            "name":
                "🔄 Trade Desk",
            "value":
                "\n".join(lines)[:1024],
            "inline":
                False
        })

    predictions = show.get(
        "game_predictions",
        []
    )

    if predictions:
        lines = []

        for pick in predictions[:8]:
            favorite = pick.get(
                "favorite"
            )

            if favorite == "TOSS-UP":
                pick_text = "TOSS-UP"
            else:
                pick_text = (
                    f"{favorite} "
                    f"({pick.get('confidence')})"
                )

            lines.append(
                f"**{pick.get('matchup')}**\n"
                f"Pick: **{pick_text}** — "
                f"{pick.get('reason')}"
            )

        fields.append({
            "name":
                "🎯 Weekly Picks & Favorites",
            "value":
                "\n\n".join(lines)[:1024],
            "inline":
                False
        })

    hot_seat = show.get(
        "hot_seat",
        []
    )

    if hot_seat:
        lines = []

        for index, item in enumerate(
            hot_seat,
            start=1
        ):
            reasons = "; ".join(
                item.get(
                    "reasons",
                    []
                )
            )

            lines.append(
                f"{index}. **{item.get('user')} — {item.get('team')}**\n"
                f"{item.get('record')} | {item.get('overall')} OVR | "
                f"Point Diff {item.get('point_diff')}\n"
                f"{reasons}"
            )

        fields.append({
            "name":
                "🔥 Hot Seat",
            "value":
                "\n\n".join(lines)[:1024],
            "inline":
                False
        })

    hot_takes = show.get(
        "hot_seat_panel_take",
        {}
    )

    if hot_takes:
        fields.append({
            "name":
                "🔥 Hot Seat — Panel Reaction",
            "value": (
                f"**Marcus Hayes:** {hot_takes.get('marcus', '')}\n\n"
                f"**Stephen A. Smith — AI Parody:** {hot_takes.get('stephen', '')}\n\n"
                f"**Pat McAfee — AI Parody:** {hot_takes.get('pat', '')}\n\n"
                "*Stephen A. Smith and Pat McAfee content is fictional AI parody "
                "and not real statements from either person.*"
            )[:1024],
            "inline":
                False
        })

    favorites = show.get(
        "super_bowl_favorites",
        []
    )

    if favorites:
        lines = []

        for item in favorites[:5]:
            lines.append(
                f"{item.get('rank')}. "
                f"**{item.get('team')}** — "
                f"{item.get('projected_chance')}%\n"
                f"{item.get('reason')}"
            )

        fields.append({
            "name":
                "🏆 Super Bowl Favorites",
            "value":
                "\n\n".join(lines)[:1024],
            "inline":
                False
        })

    sb_picks = show.get(
        "super_bowl_panel_picks",
        {}
    )

    if sb_picks:
        fields.append({
            "name":
                "🏆 Championship Picks — Panel",
            "value": (
                f"**Marcus Hayes:** "
                f"{sb_picks.get('marcus', {}).get('take', '')}\n\n"
                f"**Stephen A. Smith — AI Parody:** "
                f"{sb_picks.get('stephen', {}).get('take', '')}\n\n"
                f"**Pat McAfee — AI Parody:** "
                f"{sb_picks.get('pat', {}).get('take', '')}\n\n"
                "*Stephen A. Smith and Pat McAfee content is fictional "
                "AI parody and not real statements from either person. "
                "Percentages are Project Madden projections, not betting odds.*"
            )[:1024],
            "inline":
                False
        })

    panel = show.get(
        "panel_takes",
        {}
    )

    if panel:
        fields.append({
            "name":
                "🎙️ Marcus Hayes",
            "value":
                panel.get(
                    "marcus",
                    ""
                )[:1024],
            "inline":
                False
        })

        fields.append({
            "name":
                "🎙️ Stephen A. Smith — AI Parody",
            "value": (
                panel.get(
                    "stephen",
                    ""
                )
                + "\n\n*Fictional AI parody — not a real "
                "Stephen A. Smith statement.*"
            )[:1024],
            "inline":
                False
        })

        fields.append({
            "name":
                "🎙️ Pat McAfee — AI Parody",
            "value": (
                panel.get(
                    "pat",
                    ""
                )
                + "\n\n*Fictional AI parody — not a real "
                "Pat McAfee statement.*"
            )[:1024],
            "inline":
                False
        })

    rankings = show.get(
        "power_rankings",
        []
    )

    if rankings:
        lines = []

        for index, team in enumerate(
            rankings[:5],
            start=1
        ):
            lines.append(
                f"{index}. **{team.get('team')}** "
                f"({team.get('wins', 0)}-"
                f"{team.get('losses', 0)})"
            )

        fields.append({
            "name":
                "📈 Top 5 Power Rankings",
            "value":
                "\n".join(lines),
            "inline":
                False
        })

    return fields


def send_weekly_show_embed(
    title,
    description,
    fields=None
):
    webhook_url = get_weekly_show_webhook()

    if not webhook_url:
        return {
            "sent": False,
            "error": (
                "WEEKLY_SHOW_DISCORD_WEBHOOK_URL "
                "is not configured."
            )
        }

    weekly_show_logo_url = (
        "https://project-madden-analytics.onrender.com/"
        "assets/weekly-show-logo.jpg"
    )

    embed = {
        "title": title,
        "description": description,
        "thumbnail": {
            "url": weekly_show_logo_url
        },
        "image": {
            "url": weekly_show_logo_url
        },
        "footer": {
            "text":
                "Project Madden Weekly Show"
        }
    }

    if fields:
        embed["fields"] = fields

    payload = {
        "username":
            "Project Madden Weekly Show",
        "avatar_url":
            weekly_show_logo_url,
        "embeds": [embed]
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=15
        )

        if response.status_code not in [200, 204]:
            return {
                "sent": False,
                "error": (
                    f"Discord returned "
                    f"{response.status_code}: "
                    f"{response.text[:500]}"
                )
            }

        return {"sent": True}

    except Exception as e:
        return {
            "sent": False,
            "error": str(e)
        }


def send_weekly_show_to_discord(
    season_type,
    week_number
):
    if not weekly_show_webhook_configured():
        return {
            "success": False,
            "error": (
                "WEEKLY_SHOW_DISCORD_WEBHOOK_URL "
                "is not configured."
            )
        }

    show = build_weekly_show_summary(
        season_type,
        week_number
    )

    history = load_weekly_show_history()
    key = weekly_show_post_key(
        season_type,
        week_number
    )

    if key in history:
        return {
            "success": True,
            "skipped": True,
            "reason":
                "weekly_show_already_posted"
        }

    description = (
        f"{show['opener']}\n\n"
        "This week's desk covers completed games, player stats, "
        "recent trade proposals, power rankings, and picks for "
        "the unplayed matchups on the schedule.\n\n"
        f"**Marcus Hayes closes:** {show['closer']}\n\n"
        "*Stephen A. Smith and Pat McAfee content in this show is fictional AI parody "
        "and not real statements from either person. Picks are Project Madden analysis "
        "based on available league data and current OVR, not betting odds.*"
    )

    result = send_weekly_show_embed(
        (
            f"📺 PROJECT MADDEN WEEKLY SHOW • "
            f"{season_type.upper()} WEEK {week_number}"
        ),
        description,
        weekly_show_embed_fields(
            show
        )
    )

    if result.get("sent"):
        history.append(key)
        save_weekly_show_history(
            history
        )

    return {
        "success":
            bool(result.get("sent")),
        "sent":
            bool(result.get("sent")),
        "result":
            result,
        "show":
            show
    }


@app.route(
    "/analyst/hot-seat"
)
def analyst_hot_seat():
    hot_seat = build_hot_seat_rankings()

    return jsonify({
        "brand":
            "Project Madden Media",
        "hot_seat":
            hot_seat,
        "panel_take":
            build_hot_seat_panel_take(
                hot_seat
            )
    })


@app.route(
    "/analyst/super-bowl-favorites"
)
def analyst_super_bowl_favorites():
    favorites = build_super_bowl_favorites()

    return jsonify({
        "brand":
            "Project Madden Media",
        "projection_type":
            "Project Madden championship projection",
        "not_betting_odds":
            True,
        "favorites":
            favorites,
        "panel_picks":
            build_super_bowl_panel_picks(
                favorites,
                "reg",
                0
            )
    })


@app.route(
    "/weekly-show/debug/"
    "<season_type>/<int:week_number>"
)
def weekly_show_debug(
    season_type,
    week_number
):
    try:
        show = build_weekly_show_summary(
            season_type,
            week_number
        )

        return jsonify({
            "success": True,
            "season_type": season_type,
            "week": week_number,
            "has_games": bool(
                show.get("top_games")
            ),
            "has_players": bool(
                show.get("top_players")
            ),
            "has_predictions": bool(
                show.get("game_predictions")
            ),
            "has_trades": bool(
                show.get("trade_proposals")
            ),
            "has_super_bowl_favorites": bool(
                show.get("super_bowl_favorites")
            ),
            "has_hot_seat": bool(
                show.get("hot_seat")
            ),
            "weekly_show_webhook_configured": (
                weekly_show_webhook_configured()
            ),
            "show": show
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }), 500


@app.route(
    "/analyst/weekly-show/"
    "<season_type>/<int:week_number>"
)
def analyst_weekly_show_summary(
    season_type,
    week_number
):
    return jsonify(
        build_weekly_show_summary(
            season_type,
            week_number
        )
    )


@app.route(
    "/analyst/post-weekly-show/"
    "<season_type>/<int:week_number>",
    methods=["GET", "POST"]
)
def analyst_post_weekly_show(
    season_type,
    week_number
):
    result = send_weekly_show_to_discord(
        season_type,
        week_number
    )

    return jsonify(result), (
        200
        if result.get("success")
        else 400
    )


# =========================================================
# STEPHEN A. SMITH - AI PARODY SPECIAL SEGMENT
# =========================================================

STEPHEN_A_PARODY_OPENERS = [
    "Now hold on! We need to talk about what just happened here.",
    "Ladies and gentlemen, this cannot simply be ignored.",
    "I have seen enough. Somebody needs to explain this immediately.",
    "Excuse me, but are we really going to act like that performance was normal?",
    "This is exactly the kind of result that gets everybody in the league talking.",
    "I am not letting this one slide. There is too much to discuss.",
]

STEPHEN_A_PARODY_GAME_LINES = [
    "{winner} handled business, and {loser} has to answer for it. You can dress it up however you want, but the scoreboard is the scoreboard.",
    "{winner} made the statement. {loser} now has to prove this was an exception and not the beginning of a problem.",
    "When {winner} walks away with that result, the conversation changes immediately. {loser} cannot just shrug this off.",
    "There are wins, and then there are wins that put pressure on everybody else. {winner} just delivered one of those.",
]

STEPHEN_A_PARODY_PLAYER_LINES = [
    "{player} put up a performance that demands attention. If you are building a game plan next week, that name is now circled.",
    "I do not care what anybody expected coming in — {player} showed up and made the entire league notice.",
    "{player} just gave us the kind of performance that changes how opponents prepare.",
    "That was not background production from {player}. That was a headline performance.",
]


def get_stephen_a_parody_webhook():
    return os.environ.get(
        "STEPHEN_A_PARODY_WEBHOOK_URL",
        ""
    ).strip()


def stephen_a_parody_webhook_configured():
    return bool(
        get_stephen_a_parody_webhook()
    )


def load_stephen_a_parody_history():
    history = load_json_file(
        STEPHEN_A_PARODY_HISTORY_FILE
    )

    if not isinstance(history, list):
        history = []

    return history


def save_stephen_a_parody_history(history):
    save_json_file(
        STEPHEN_A_PARODY_HISTORY_FILE,
        history[-300:]
    )


def stephen_a_parody_post_key(
    season_type,
    week_number,
    story
):
    raw = (
        f"{season_type}|{week_number}|"
        f"{story.get('story_type')}|"
        f"{story.get('source_key')}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:20]


def build_stephen_a_parody_segment(
    season_type,
    week_number
):
    stories = []

    game_reactions = build_week_game_reactions(
        season_type,
        week_number
    )

    player_reactions = build_week_player_reactions(
        season_type,
        week_number
    )

    # Pick the single strongest game story to keep this segment special.
    if game_reactions:
        def game_priority(item):
            return (
                3 if item.get("upset") else 0,
                int(item.get("margin", 0) or 0),
            )

        top_game = sorted(
            game_reactions,
            key=game_priority,
            reverse=True
        )[0]

        winner = top_game.get(
            "winner",
            "the winner"
        )

        loser = top_game.get(
            "loser",
            "the opponent"
        )

        source_key = str(
            top_game.get(
                "schedule_id",
                top_game.get("game", "")
            )
        )

        opener = stable_choice(
            STEPHEN_A_PARODY_OPENERS,
            f"stephen-game-open-{season_type}-{week_number}-{source_key}"
        )

        body = stable_choice(
            STEPHEN_A_PARODY_GAME_LINES,
            f"stephen-game-body-{season_type}-{week_number}-{source_key}"
        ).format(
            winner=winner,
            loser=loser
        )

        stories.append({
            "story_type": "game",
            "source_key": source_key,
            "headline": (
                f"{winner} vs {loser}"
            ),
            "take": (
                f"{opener} {body}"
            ),
            "details": top_game
        })

    # Pick one standout player story.
    if player_reactions:
        def player_priority(item):
            stats = item.get("stats", {})
            category = item.get("category", "")

            if category == "passing":
                return (
                    int(stats.get("touchdowns", 0) or 0) * 100
                    + int(stats.get("yards", 0) or 0)
                )

            if category in [
                "rushing",
                "receiving"
            ]:
                return (
                    int(stats.get("touchdowns", 0) or 0) * 100
                    + int(stats.get("yards", 0) or 0)
                )

            if category == "defense":
                return (
                    int(stats.get("sacks", 0) or 0) * 120
                    + int(stats.get("interceptions", 0) or 0) * 150
                    + int(stats.get("forced_fumbles", 0) or 0) * 100
                )

            return 0

        top_player = sorted(
            player_reactions,
            key=player_priority,
            reverse=True
        )[0]

        player = top_player.get(
            "player",
            "This player"
        )

        source_key = (
            f"{player}-"
            f"{top_player.get('category', '')}-"
            f"{top_player.get('story_type', '')}"
        )

        opener = stable_choice(
            STEPHEN_A_PARODY_OPENERS,
            f"stephen-player-open-{season_type}-{week_number}-{source_key}"
        )

        body = stable_choice(
            STEPHEN_A_PARODY_PLAYER_LINES,
            f"stephen-player-body-{season_type}-{week_number}-{source_key}"
        ).format(
            player=player
        )

        stories.append({
            "story_type": "player",
            "source_key": source_key,
            "headline": player,
            "take": (
                f"{opener} {body}"
            ),
            "details": top_player
        })

    return stories


def send_stephen_a_parody_embed(
    title,
    description
):
    webhook_url = (
        get_stephen_a_parody_webhook()
    )

    if not webhook_url:
        return {
            "sent": False,
            "error": (
                "STEPHEN_A_PARODY_WEBHOOK_URL "
                "is not configured."
            )
        }

    stephen_avatar_url = (
        "https://project-madden-analytics.onrender.com/"
        "assets/stephen-a-smith.png"
    )

    payload = {
        "username":
            "Stephen A. Smith | AI Parody",
        "avatar_url":
            stephen_avatar_url,
        "embeds": [
            {
                "title": title,
                "description": description,
                "thumbnail": {
                    "url": stephen_avatar_url
                },
                "footer": {
                    "text": (
                        "AI parody segment • "
                        "Not real Stephen A. Smith statements"
                    )
                }
            }
        ]
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=15
        )

        if response.status_code not in [
            200,
            204
        ]:
            return {
                "sent": False,
                "error": (
                    f"Discord returned "
                    f"{response.status_code}: "
                    f"{response.text[:500]}"
                )
            }

        return {"sent": True}

    except Exception as e:
        return {
            "sent": False,
            "error": str(e)
        }


def process_stephen_a_parody_posts(
    season_type,
    week_number
):
    if not stephen_a_parody_webhook_configured():
        return {
            "success": False,
            "error": (
                "STEPHEN_A_PARODY_WEBHOOK_URL "
                "is not configured."
            ),
            "sent_count": 0
        }

    stories = build_stephen_a_parody_segment(
        season_type,
        week_number
    )

    history = load_stephen_a_parody_history()

    sent = []
    skipped = []
    failed = []

    for story in stories:
        key = stephen_a_parody_post_key(
            season_type,
            week_number,
            story
        )

        if key in history:
            skipped.append(
                story.get("headline")
            )
            continue

        result = send_stephen_a_parody_embed(
            (
                "🎙️ STEPHEN A. SMITH "
                "— AI PARODY SEGMENT"
            ),
            (
                f"## {story.get('headline')}\n"
                f"{story.get('take')}\n\n"
                "⚠️ *Fictional AI parody for Project Madden. "
                "This is not a real Stephen A. Smith quote or statement.*"
            )
        )

        if result.get("sent"):
            history.append(key)
            sent.append(
                story.get("headline")
            )
        else:
            failed.append({
                "headline":
                    story.get("headline"),
                "error":
                    result.get("error")
            })

    save_stephen_a_parody_history(
        history
    )

    return {
        "success": len(failed) == 0,
        "segment":
            "Stephen A. Smith — AI Parody",
        "season_type":
            season_type,
        "week":
            week_number,
        "stories_found":
            len(stories),
        "sent_count":
            len(sent),
        "skipped_count":
            len(skipped),
        "failed_count":
            len(failed),
        "sent":
            sent,
        "failed":
            failed
    }


@app.route(
    "/analyst/stephen-a/"
    "<season_type>/<int:week_number>"
)
def analyst_stephen_a(
    season_type,
    week_number
):
    return jsonify({
        "segment":
            "Stephen A. Smith — AI Parody",
        "disclaimer": (
            "Fictional AI parody. "
            "Not real Stephen A. Smith statements."
        ),
        "season_type":
            season_type,
        "week":
            week_number,
        "stories":
            build_stephen_a_parody_segment(
                season_type,
                week_number
            )
    })


@app.route(
    "/analyst/post-stephen-a/"
    "<season_type>/<int:week_number>",
    methods=["GET", "POST"]
)
def analyst_post_stephen_a(
    season_type,
    week_number
):
    result = process_stephen_a_parody_posts(
        season_type,
        week_number
    )

    return jsonify(result), (
        200
        if result.get("success")
        else 400
    )



# =========================================================
# PAT MCAFEE - AI PARODY SPECIAL SEGMENT
# =========================================================

PAT_MCAFEE_PARODY_OPENERS = [
    "Alright, this is the kind of week that gives everybody something to argue about.",
    "There is a lot happening around Project Madden right now, and this one deserves some extra attention.",
    "This league just gave us another wild storyline to break down.",
    "Now that is the kind of result that gets the whole room talking.",
    "There is no shortage of energy around this one. Let us get into what actually happened.",
]

PAT_MCAFEE_PARODY_GAME_LINES = [
    "{winner} came out of this looking like the team with the answers, while {loser} has some work to do before the next one.",
    "{winner} made the bigger plays when it mattered, and that is what everybody is going to remember from this matchup.",
    "The scoreboard says {winner}, but the bigger story is how much pressure this puts on {loser} going forward.",
    "That result from {winner} is going to have people around the league looking at this team differently.",
]

PAT_MCAFEE_PARODY_PLAYER_LINES = [
    "{player} was everywhere this week. That is the kind of performance that gets teammates and opponents talking.",
    "{player} gave this team exactly the kind of impact you want from a difference-maker.",
    "You cannot watch that performance from {player} and pretend it was ordinary. That was a major week.",
    "{player} just gave the league another reason to pay attention heading into the next matchup.",
]


def build_pat_mcafee_parody_segment(
    season_type,
    week_number
):
    stories = []

    game_reactions = build_week_game_reactions(
        season_type,
        week_number
    )

    player_reactions = build_week_player_reactions(
        season_type,
        week_number
    )

    if game_reactions:
        top_game = sorted(
            game_reactions,
            key=lambda item: (
                1 if item.get("upset") else 0,
                int(item.get("margin", 0) or 0)
            ),
            reverse=True
        )[0]

        winner = top_game.get(
            "winner",
            "the winner"
        )

        loser = top_game.get(
            "loser",
            "the opponent"
        )

        source_key = str(
            top_game.get(
                "schedule_id",
                top_game.get("game", "")
            )
        )

        opener = stable_choice(
            PAT_MCAFEE_PARODY_OPENERS,
            f"pat-game-open-{season_type}-{week_number}-{source_key}"
        )

        body = stable_choice(
            PAT_MCAFEE_PARODY_GAME_LINES,
            f"pat-game-body-{season_type}-{week_number}-{source_key}"
        ).format(
            winner=winner,
            loser=loser
        )

        stories.append({
            "story_type": "game",
            "headline": (
                f"{winner} vs {loser}"
            ),
            "take": (
                f"{opener} {body}"
            )
        })

    if player_reactions:
        def player_priority(item):
            stats = item.get("stats", {})
            category = item.get("category", "")

            if category == "passing":
                return (
                    int(stats.get("touchdowns", 0) or 0) * 120
                    + int(stats.get("yards", 0) or 0)
                    - int(stats.get("interceptions", 0) or 0) * 50
                )

            if category in [
                "rushing",
                "receiving"
            ]:
                return (
                    int(stats.get("touchdowns", 0) or 0) * 110
                    + int(stats.get("yards", 0) or 0)
                )

            if category == "defense":
                return (
                    int(stats.get("sacks", 0) or 0) * 130
                    + int(stats.get("interceptions", 0) or 0) * 160
                    + int(stats.get("forced_fumbles", 0) or 0) * 100
                )

            return 0

        top_player = sorted(
            player_reactions,
            key=player_priority,
            reverse=True
        )[0]

        player = top_player.get(
            "player",
            "This player"
        )

        source_key = (
            f"{player}-"
            f"{top_player.get('category', '')}-"
            f"{top_player.get('story_type', '')}"
        )

        opener = stable_choice(
            PAT_MCAFEE_PARODY_OPENERS,
            f"pat-player-open-{season_type}-{week_number}-{source_key}"
        )

        body = stable_choice(
            PAT_MCAFEE_PARODY_PLAYER_LINES,
            f"pat-player-body-{season_type}-{week_number}-{source_key}"
        ).format(
            player=player
        )

        stories.append({
            "story_type": "player",
            "headline": player,
            "take": (
                f"{opener} {body}"
            )
        })

    return stories


# =========================================================
# MARCUS HAYES - STANDINGS / POWER RANKINGS / STORYLINES
# =========================================================

STANDINGS_STORY_HISTORY_FILE = "standings_story_posts.json"
STEPHEN_A_PARODY_HISTORY_FILE = "stephen_a_parody_posts.json"
MARCUS_TRADE_REACTION_HISTORY_FILE = "marcus_trade_reaction_posts.json"
WEEKLY_SHOW_HISTORY_FILE = "weekly_show_posts.json"


def standing_records():
    data = load_json_file("standings.json")

    if not data:
        return []

    records = recursive_records(data)
    useful = []

    for record in records:
        if not isinstance(record, dict):
            continue

        team_id = first_value(
            record,
            [
                "teamId",
                "teamID",
                "team_id",
                "clubId",
                "clubID"
            ]
        )

        team_name = first_value(
            record,
            [
                "teamName",
                "displayName",
                "name",
                "team"
            ]
        )

        wins = first_value(
            record,
            [
                "wins",
                "win",
                "totalWins",
                "seasonWins",
                "w"
            ]
        )

        losses = first_value(
            record,
            [
                "losses",
                "loss",
                "totalLosses",
                "seasonLosses",
                "l"
            ]
        )

        # Only treat a record as a standings record if it looks team-based
        # and has at least wins/losses information.
        if team_id is None and not team_name:
            continue

        if wins is None and losses is None:
            continue

        useful.append(record)

    return useful


def standing_int(record, keys, default=None):
    value = first_value(record, keys)

    if value is None:
        return default

    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return default


def standing_float(record, keys, default=None):
    value = first_value(record, keys)

    if value is None:
        return default

    try:
        return float(value)
    except Exception:
        return default


def standing_team_info(record):
    team_id = first_value(
        record,
        [
            "teamId",
            "teamID",
            "team_id",
            "clubId",
            "clubID"
        ]
    )

    team = team_by_id(team_id) if team_id is not None else None

    name = (
        (team or {}).get("name")
        or first_value(
            record,
            [
                "teamName",
                "displayName",
                "name",
                "team"
            ]
        )
        or (
            f"Team {team_id}"
            if team_id is not None
            else "Unknown Team"
        )
    )

    return {
        "team_id": team_id,
        "name": str(name),
        "abbr": (team or {}).get("abbr"),
        "overall": (team or {}).get("overall"),
        "logo": (team or {}).get("logo")
    }


def normalize_standings():
    standings = []

    for record in standing_records():
        info = standing_team_info(record)

        wins = standing_int(
            record,
            [
                "wins",
                "win",
                "totalWins",
                "seasonWins",
                "w"
            ],
            0
        ) or 0

        losses = standing_int(
            record,
            [
                "losses",
                "loss",
                "totalLosses",
                "seasonLosses",
                "l"
            ],
            0
        ) or 0

        ties = standing_int(
            record,
            [
                "ties",
                "tie",
                "totalTies",
                "seasonTies",
                "t"
            ],
            0
        ) or 0

        points_for = standing_int(
            record,
            [
                "ptsFor",
                "pointsFor",
                "pf",
                "scoreFor",
                "totalPointsFor"
            ],
            None
        )

        points_against = standing_int(
            record,
            [
                "ptsAgainst",
                "pointsAgainst",
                "pa",
                "scoreAgainst",
                "totalPointsAgainst"
            ],
            None
        )

        playoff_seed = standing_int(
            record,
            [
                "playoffSeed",
                "seed",
                "conferenceSeed",
                "playoffRank"
            ],
            None
        )

        division_rank = standing_int(
            record,
            [
                "divisionRank",
                "divRank",
                "divisionStanding"
            ],
            None
        )

        conference_rank = standing_int(
            record,
            [
                "conferenceRank",
                "confRank",
                "conferenceStanding"
            ],
            None
        )

        streak_raw = first_value(
            record,
            [
                "streak",
                "winLossStreak",
                "currentStreak",
                "streakType"
            ]
        )

        games = wins + losses + ties

        if games > 0:
            win_pct = (
                wins + (ties * 0.5)
            ) / games
        else:
            win_pct = 0.0

        point_diff = None

        if (
            points_for is not None
            and points_against is not None
        ):
            point_diff = points_for - points_against

        standings.append({
            "team_id": info["team_id"],
            "team": info["name"],
            "abbr": info["abbr"],
            "logo": info["logo"],
            "overall": info["overall"],
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "games": games,
            "win_pct": round(win_pct, 4),
            "points_for": points_for,
            "points_against": points_against,
            "point_diff": point_diff,
            "playoff_seed": playoff_seed,
            "division_rank": division_rank,
            "conference_rank": conference_rank,
            "streak": (
                str(streak_raw)
                if streak_raw is not None
                else None
            )
        })

    # De-duplicate by team id/name because recursive scans may encounter
    # the same standings object through nested structures.
    deduped = {}
    for team in standings:
        key = (
            str(team.get("team_id"))
            if team.get("team_id") is not None
            else team.get("team", "").lower()
        )
        deduped[key] = team

    standings = list(deduped.values())

    standings.sort(
        key=lambda x: (
            -x.get("win_pct", 0),
            -(x.get("wins", 0)),
            -(x.get("point_diff") or -9999)
        )
    )

    return standings


def parse_streak(streak):
    if not streak:
        return None, 0

    text = str(streak).strip().upper()

    # Supports strings like W5, W 5, WIN5, L3, LOSS 3.
    match = re.search(
        r"\b(W|L|WIN|LOSS)[\s\-:]*(\d+)\b",
        text
    )

    if not match:
        return None, 0

    raw_type = match.group(1)
    count = int(match.group(2))

    streak_type = (
        "W"
        if raw_type in ["W", "WIN"]
        else "L"
    )

    return streak_type, count


POWER_RANKING_INTROS = [
    "The standings changed, so the conversation changes with them. I am looking at who earned respect this week.",
    "Forget the names on the jerseys for a minute. The teams producing on the field are the teams getting my attention.",
    "We have movement in this league, and some teams are making a much stronger case than they were a week ago.",
    "This ranking is not about reputation. It is about what the league has actually shown us right now.",
    "Some teams are climbing, some are slipping, and the standings are starting to expose the difference.",
    "I am not rewarding hype. Wins, losses, point differential, and how you are playing right now decide this conversation.",
    "There is a new league snapshot in front of us, and a few teams have completely changed how I look at them.",
    "The hierarchy is moving. If you want to stay near the top, your production has to keep matching the name on your roster.",
    "Every new result gives us more evidence. Right now, the teams earning their spot are separating themselves.",
    "This league is starting to develop an identity, and the latest standings tell us exactly who is trending the right way.",
    "I want results, not excuses. The newest league table gives us a better picture of who is actually delivering.",
    "The latest games gave us something new to debate, because this league order is not standing still."
]

HOT_STREAK_LINES = [
    "{team} is rolling right now. A {count}-game winning streak gets my attention.",
    "Do not look now, but {team} has won {count} straight and confidence is building.",
    "{team} has stacked {count} consecutive wins. That is how you create momentum.",
    "A {count}-game heater from {team} means the rest of the league better stop sleeping.",
    "{team} keeps winning, and after {count} straight victories this is no longer a fluke."
]

COLD_STREAK_LINES = [
    "{team} has dropped {count} straight, and at some point the excuses have to stop.",
    "A {count}-game losing streak is a problem. {team} needs answers immediately.",
    "{team} is going the wrong direction with {count} consecutive losses.",
    "When you lose {count} in a row, everybody gets questioned. {team} is officially under pressure.",
    "{team} has lost {count} straight and the margin for error is disappearing fast."
]

FRAUD_WATCH_LINES = [
    "{team} is officially on fraud watch. The rating says one thing, but a {wins}-{losses} record says something else.",
    "I am looking at {team} and asking a very simple question: where are the results? This roster is too talented to be {wins}-{losses}.",
    "{team} has the talent on paper, but the record is not matching the reputation. That is fraud-watch territory.",
    "At {wins}-{losses}, {team} cannot keep hiding behind overall rating and preseason expectations.",
    "The name and the rating might scare people, but {team}'s {wins}-{losses} record does not."
]

OVERACHIEVER_LINES = [
    "{team} deserves credit. They are winning more than the roster rating suggested they would.",
    "{team} is outperforming the numbers beside its name, and that deserves respect.",
    "The ratings did not hand {team} anything. They are earning their record on the field.",
    "{team} is one of the best examples of coaching and execution beating raw roster rating.",
    "If you only looked at overall rating, you would have missed what {team} is doing."
]

PLAYOFF_RACE_LINES = [
    "{team} is sitting in a playoff position, but nothing about this race looks comfortable yet.",
    "{team} has itself in the postseason picture. Now the challenge is staying there.",
    "The playoff race is tightening, and {team} currently owns one of those valuable spots.",
    "{team} has a seat at the playoff table right now. Every game from here gets bigger.",
    "A playoff seed is nice, but {team} still has work to do before anybody should feel safe."
]


def build_power_rankings():
    standings = normalize_standings()

    rankings = []

    for index, team in enumerate(standings, start=1):
        score = team["win_pct"] * 100

        if team.get("point_diff") is not None:
            score += max(
                -20,
                min(
                    20,
                    team["point_diff"] / 10
                )
            )

        overall = team.get("overall")
        if overall is not None:
            try:
                score += (
                    int(overall) - 80
                ) * 0.35
            except Exception:
                pass

        rankings.append({
            **team,
            "power_score": round(score, 2),
            "standing_rank": index
        })

    rankings.sort(
        key=lambda x: (
            -x["power_score"],
            -x["wins"],
            x["losses"]
        )
    )

    for index, team in enumerate(rankings, start=1):
        team["power_rank"] = index

    return rankings


def build_standings_storylines():
    rankings = build_power_rankings()
    stories = []

    if not rankings:
        return stories

    intro_key = "-".join(
        f"{x['team']}:{x['wins']}-{x['losses']}"
        for x in rankings[:8]
    )

    total_games = sum(
        int(team.get("games") or 0)
        for team in rankings
    )

    stories.append({
        "story_type": "power_rankings_intro",
        "headline": (
            "Marcus Hayes updates the league hierarchy"
            if total_games > 0
            else "Marcus Hayes sets the preseason hierarchy"
        ),
        "state_key": intro_key,
        "analyst_take": stable_choice(
            POWER_RANKING_INTROS,
            intro_key
        )
    })

    for team in rankings:
        streak_type, streak_count = parse_streak(
            team.get("streak")
        )

        key = (
            f"{team['team']}-"
            f"{team['wins']}-"
            f"{team['losses']}-"
            f"{team.get('streak')}-"
            f"{team.get('overall')}"
        )

        if streak_type == "W" and streak_count >= 3:
            template = unique_analyst_choice(
                "hot_streak",
                HOT_STREAK_LINES,
                key
            )

            stories.append({
                "story_type": "hot_streak",
                "state_key": key,
                "team": team["team"],
                "headline": (
                    f"{team['team']} is heating up"
                ),
                "analyst_take": template.format(
                    team=team["team"],
                    count=streak_count
                )
            })

        if streak_type == "L" and streak_count >= 3:
            template = unique_analyst_choice(
                "cold_streak",
                COLD_STREAK_LINES,
                key
            )

            stories.append({
                "story_type": "cold_streak",
                "state_key": key,
                "team": team["team"],
                "headline": (
                    f"Pressure rising on {team['team']}"
                ),
                "analyst_take": template.format(
                    team=team["team"],
                    count=streak_count
                )
            })

        overall = team.get("overall")

        try:
            overall_num = int(overall)
        except Exception:
            overall_num = None

        # Fraud watch:
        # high-rated roster + enough games played + losing record.
        if (
            overall_num is not None
            and overall_num >= 84
            and team["games"] >= 4
            and team["wins"] < team["losses"]
        ):
            template = unique_analyst_choice(
                "fraud_watch",
                FRAUD_WATCH_LINES,
                key
            )

            stories.append({
                "story_type": "fraud_watch",
                "state_key": key,
                "team": team["team"],
                "headline": (
                    f"Fraud Watch: {team['team']}"
                ),
                "analyst_take": template.format(
                    team=team["team"],
                    wins=team["wins"],
                    losses=team["losses"]
                )
            })

        # Overachiever:
        # lower-rated team with at least 4 games and a .667+ record.
        if (
            overall_num is not None
            and overall_num <= 81
            and team["games"] >= 4
            and team["win_pct"] >= 0.667
        ):
            template = unique_analyst_choice(
                "overachiever",
                OVERACHIEVER_LINES,
                key
            )

            stories.append({
                "story_type": "overachiever",
                "state_key": key,
                "team": team["team"],
                "headline": (
                    f"{team['team']} is outperforming expectations"
                ),
                "analyst_take": template.format(
                    team=team["team"]
                )
            })

        # Playoff race:
        # only when Snallabot actually provides a seed.
        playoff_seed = team.get("playoff_seed")

        if (
            playoff_seed is not None
            and 1 <= playoff_seed <= 7
        ):
            template = unique_analyst_choice(
                "playoff_race",
                PLAYOFF_RACE_LINES,
                key
            )

            stories.append({
                "story_type": "playoff_race",
                "state_key": key,
                "team": team["team"],
                "seed": playoff_seed,
                "headline": (
                    f"{team['team']} holds the No. {playoff_seed} seed"
                ),
                "analyst_take": template.format(
                    team=team["team"]
                )
            })

    return stories


def standings_post_key(story):
    state_key = story.get("state_key")

    if state_key:
        raw_key = (
            f"{story.get('story_type', 'standings')}|"
            f"{state_key}"
        )
    else:
        raw_key = json.dumps(
            story,
            sort_keys=True
        )

    return hashlib.sha256(
        raw_key.encode("utf-8")
    ).hexdigest()[:16]


def load_standings_story_history():
    history = load_json_file(
        STANDINGS_STORY_HISTORY_FILE
    )

    if not isinstance(history, list):
        history = []

    return history


def post_standings_storyline_to_discord(story):
    story_type = story.get(
        "story_type",
        "standings"
    )

    labels = {
        "power_rankings_intro": "📊 LEAGUE CHECK",
        "hot_streak": "🔥 HOT STREAK",
        "cold_streak": "🧊 COLD STREAK",
        "fraud_watch": "🚨 FRAUD WATCH",
        "overachiever": "👀 OVERACHIEVER",
        "playoff_race": "🏆 PLAYOFF RACE"
    }

    label = labels.get(
        story_type,
        "📊 STANDINGS"
    )

    return send_analyst_embed(
        (
            f"{label} • "
            f"{story.get('headline', 'Marcus Hayes reacts')}"
        ),
        (
            f"🎙️ **Marcus Hayes:**\n"
            f"{story.get('analyst_take', '')}"
        )
    )


def _process_standings_posts_unlocked():
    if not analyst_webhook_configured():
        return {
            "success": False,
            "error": (
                "ANALYST_DISCORD_WEBHOOK_URL "
                "is not configured in Render."
            ),
            "sent_count": 0,
            "skipped_count": 0,
            "failed_count": 0
        }

    stories = build_standings_storylines()
    history = load_standings_story_history()

    sent = []
    skipped = []
    failed = []

    for story in stories:
        key = standings_post_key(story)

        if key in history:
            skipped.append({
                "headline": story.get("headline"),
                "reason": "already_posted"
            })
            continue

        result = post_standings_storyline_to_discord(
            story
        )

        if result.get("sent"):
            history.append(key)

            sent.append({
                "headline": story.get("headline"),
                "story_type": story.get(
                    "story_type"
                )
            })
        else:
            failed.append({
                "headline": story.get("headline"),
                "error": result.get("error")
            })

    save_json_file(
        STANDINGS_STORY_HISTORY_FILE,
        history[-500:]
    )

    return {
        "success": len(failed) == 0,
        "analyst": PROJECT_MADDEN_ANALYST,
        "destination": "Project Madden Media",
        "story_count": len(stories),
        "sent_count": len(sent),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "sent": sent,
        "skipped": skipped,
        "failed": failed
    }


def process_standings_posts():
    # Snallabot can deliver closely-timed exports. Only one standings
    # posting pass may run at a time so Discord never gets duplicate cards.
    with STANDINGS_POST_LOCK:
        return _process_standings_posts_unlocked()


@app.route("/analyst/standings")
def analyst_standings():
    standings = normalize_standings()

    return jsonify({
        "analyst": PROJECT_MADDEN_ANALYST,
        "team_count": len(standings),
        "standings": standings
    })


@app.route("/analyst/power-rankings")
def analyst_power_rankings():
    rankings = build_power_rankings()

    return jsonify({
        "analyst": PROJECT_MADDEN_ANALYST,
        "ranking_count": len(rankings),
        "rankings": rankings
    })


@app.route("/analyst/storylines")
def analyst_storylines():
    stories = build_standings_storylines()

    return jsonify({
        "analyst": PROJECT_MADDEN_ANALYST,
        "story_count": len(stories),
        "storylines": stories
    })


@app.route(
    "/analyst/post-standings",
    methods=["GET", "POST"]
)
def analyst_post_standings():
    result = process_standings_posts()

    status_code = 200

    if (
        not result.get("success")
        and result.get("error")
    ):
        status_code = 400

    return jsonify(result), status_code



# =========================================================
# DISCORD SLASH COMMAND - /trade
# =========================================================

DISCORD_API_BASE = "https://discord.com/api/v10"

DISCORD_TEAM_CHOICES = [
    {"name": "49ers", "value": "49ers"},
    {"name": "Bears", "value": "Bears"},
    {"name": "Bengals", "value": "Bengals"},
    {"name": "Bills", "value": "Bills"},
    {"name": "Broncos", "value": "Broncos"},
    {"name": "Browns", "value": "Browns"},
    {"name": "Buccaneers", "value": "Buccaneers"},
    {"name": "Cardinals", "value": "Cardinals"},
    {"name": "Chargers", "value": "Chargers"},
    {"name": "Chiefs", "value": "Chiefs"},
    {"name": "Colts", "value": "Colts"},
    {"name": "Commanders", "value": "Commanders"},
    {"name": "Cowboys", "value": "Cowboys"},
    {"name": "Dolphins", "value": "Dolphins"},
    {"name": "Eagles", "value": "Eagles"},
    {"name": "Falcons", "value": "Falcons"},
    {"name": "Giants", "value": "Giants"},
    {"name": "Jaguars", "value": "Jaguars"},
    {"name": "Jets", "value": "Jets"},
    {"name": "Lions", "value": "Lions"},
    {"name": "Packers", "value": "Packers"},
    {"name": "Panthers", "value": "Panthers"},
    {"name": "Patriots", "value": "Patriots"},
    {"name": "Raiders", "value": "Raiders"},
    {"name": "Rams", "value": "Rams"},
    {"name": "Ravens", "value": "Ravens"},
    {"name": "Saints", "value": "Saints"},
    {"name": "Seahawks", "value": "Seahawks"},
    {"name": "Steelers", "value": "Steelers"},
    {"name": "Texans", "value": "Texans"},
    {"name": "Titans", "value": "Titans"},
    {"name": "Vikings", "value": "Vikings"}
]


def discord_application_id():
    return os.environ.get(
        "DISCORD_APPLICATION_ID",
        ""
    ).strip()


def discord_public_key():
    return os.environ.get(
        "DISCORD_PUBLIC_KEY",
        ""
    ).strip()


def discord_bot_token():
    return os.environ.get(
        "DISCORD_BOT_TOKEN",
        ""
    ).strip()


def discord_guild_id():
    return os.environ.get(
        "DISCORD_GUILD_ID",
        ""
    ).strip()


def trade_committee_role_id():
    return os.environ.get(
        "TRADE_COMMITTEE_ROLE_ID",
        ""
    ).strip()


def discord_bot_configured():
    return bool(
        discord_application_id()
        and discord_public_key()
        and discord_bot_token()
    )


def discord_interactions_url():
    return (
        "https://project-madden-analytics.onrender.com"
        "/discord/interactions"
    )


def verify_discord_request(raw_body):
    public_key = discord_public_key()

    if not public_key:
        return False

    signature = request.headers.get(
        "X-Signature-Ed25519",
        ""
    )

    timestamp = request.headers.get(
        "X-Signature-Timestamp",
        ""
    )

    if not signature or not timestamp:
        return False

    try:
        verify_key = VerifyKey(
            bytes.fromhex(public_key)
        )

        verify_key.verify(
            timestamp.encode("utf-8")
            + raw_body,
            bytes.fromhex(signature)
        )

        return True

    except (
        BadSignatureError,
        ValueError
    ):
        return False


def discord_option_map(interaction):
    options = (
        interaction
        .get("data", {})
        .get("options", [])
    )

    return {
        option.get("name"):
            option.get("value")
        for option in options
    }


def resolved_user(interaction, user_id):
    resolved = (
        interaction
        .get("data", {})
        .get("resolved", {})
        .get("users", {})
    )

    return resolved.get(
        str(user_id),
        {}
    )


def discord_user_label(interaction, user_id):
    user = resolved_user(
        interaction,
        user_id
    )

    username = (
        user.get("global_name")
        or user.get("username")
        or str(user_id)
    )

    return (
        f"{username} (<@{user_id}>)"
    )


def extract_discord_user_ids(*mentions):
    ids = []

    for mention in mentions:
        match = re.search(
            r"<@!?(\d+)>",
            str(mention or "")
        )

        if match:
            ids.append(
                match.group(1)
            )

    return ids


def save_trade_proposal(analysis):
    proposals = load_json_file(
        "trade_proposals.json"
    )

    if not isinstance(
        proposals,
        list
    ):
        proposals = []

    proposals.append(analysis)

    save_json_file(
        "trade_proposals.json",
        proposals
    )


def register_trade_slash_command():
    app_id = discord_application_id()
    token = discord_bot_token()
    guild_id = discord_guild_id()

    if not app_id or not token:
        return {
            "success": False,
            "error": (
                "DISCORD_APPLICATION_ID or "
                "DISCORD_BOT_TOKEN is missing."
            )
        }

    def asset_option(name, description, required=False):
        return {
            "type": 3,
            "name": name,
            "description": description,
            "required": required,
            "autocomplete": True
        }

    command = {
        "name": "trade",
        "description": (
            "Submit a Project Madden trade "
            "for League Office Review"
        ),
        "options": [
            # Required options must come first.
            {
                "type": 3,
                "name": "team_a",
                "description": "First team",
                "required": True,
                "autocomplete": True
            },
            {
                "type": 6,
                "name": "team_a_owner",
                "description": "Discord owner of Team A",
                "required": True
            },
            asset_option(
                "team_a_asset_1",
                "Team A player or draft pick #1",
                True
            ),
            {
                "type": 3,
                "name": "team_b",
                "description": "Second team",
                "required": True,
                "autocomplete": True
            },
            {
                "type": 6,
                "name": "team_b_owner",
                "description": "Discord owner of Team B",
                "required": True
            },
            asset_option(
                "team_b_asset_1",
                "Team B player or draft pick #1",
                True
            ),
            {
                "type": 11,
                "name": "trade_screenshot",
                "description": (
                    "Optional Madden trade-screen screenshot"
                ),
                "required": False
            },

            # Optional extra assets.
            asset_option(
                "team_a_asset_2",
                "Team A player or draft pick #2"
            ),
            asset_option(
                "team_a_asset_3",
                "Team A player or draft pick #3"
            ),
            asset_option(
                "team_a_asset_4",
                "Team A player or draft pick #4"
            ),
            asset_option(
                "team_a_asset_5",
                "Team A player or draft pick #5"
            ),
            asset_option(
                "team_b_asset_2",
                "Team B player or draft pick #2"
            ),
            asset_option(
                "team_b_asset_3",
                "Team B player or draft pick #3"
            ),
            asset_option(
                "team_b_asset_4",
                "Team B player or draft pick #4"
            ),
            asset_option(
                "team_b_asset_5",
                "Team B player or draft pick #5"
            )
        ]
    }

    test_marcus_command = {
        "name": "testmarcus",
        "description": "Send a Marcus Hayes test post to Project Madden Media",
        "options": [
            {
                "type": 3,
                "name": "headline",
                "description": "Test headline",
                "required": True,
                "max_length": 100
            },
            {
                "type": 3,
                "name": "take",
                "description": "Marcus Hayes test commentary",
                "required": True,
                "max_length": 1000
            }
        ]
    }

    test_stephen_command = {
        "name": "teststephena",
        "description": "Send a Stephen A. Smith AI parody test segment",
        "options": [
            {
                "type": 3,
                "name": "headline",
                "description": "Test headline",
                "required": True,
                "max_length": 100
            },
            {
                "type": 3,
                "name": "take",
                "description": "AI parody test commentary",
                "required": True,
                "max_length": 1000
            }
        ]
    }

    weekly_show_command = {
        "name": "weeklyshow",
        "description": "Post Weekly Show with Marcus + Stephen A. + Pat McAfee parody",
        "options": [
            {
                "type": 3,
                "name": "season_type",
                "description": "pre or reg",
                "required": True,
                "choices": [
                    {
                        "name": "Preseason",
                        "value": "pre"
                    },
                    {
                        "name": "Regular Season",
                        "value": "reg"
                    }
                ]
            },
            {
                "type": 4,
                "name": "week",
                "description": "Week number",
                "required": True,
                "min_value": 1,
                "max_value": 25
            }
        ]
    }

    test_weekly_show_command = {
        "name": "testweeklyshow",
        "description": "Send a Project Madden Weekly Show test post",
        "options": [
            {
                "type": 3,
                "name": "headline",
                "description": "Optional test headline",
                "required": False,
                "max_length": 100
            }
        ]
    }

    commands = [
        command,
        test_marcus_command,
        test_stephen_command,
        weekly_show_command,
        test_weekly_show_command
    ]

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

    if guild_id:
        # Remove any older GLOBAL command first. Discord can otherwise
        # keep showing the stale global /trade UI alongside the new
        # guild-specific command.
        global_url = (
            f"{DISCORD_API_BASE}/applications/"
            f"{app_id}/commands"
        )

        global_cleanup = requests.put(
            global_url,
            headers=headers,
            json=[],
            timeout=15
        )

        if global_cleanup.status_code not in [
            200,
            201
        ]:
            return {
                "success": False,
                "status_code":
                    global_cleanup.status_code,
                "scope": "global_cleanup",
                "error":
                    global_cleanup.text[:500]
            }

        url = (
            f"{DISCORD_API_BASE}/applications/"
            f"{app_id}/guilds/{guild_id}/commands"
        )
        scope = "guild"
    else:
        url = (
            f"{DISCORD_API_BASE}/applications/"
            f"{app_id}/commands"
        )
        scope = "global"

    response = requests.put(
        url,
        headers=headers,
        json=commands,
        timeout=15
    )

    if response.status_code not in [200, 201]:
        return {
            "success": False,
            "status_code": response.status_code,
            "scope": scope,
            "error": response.text[:500]
        }

    try:
        body = response.json()
    except Exception:
        body = []

    return {
        "success": True,
        "registered": [
            item.get("name")
            for item in body
        ],
        "scope": scope,
        "guild_id_configured": bool(guild_id),
        "old_global_command_removed": bool(guild_id),
        "trade_ui": (
            "5 clean player/pick asset slots per team "
            "+ optional Madden trade screenshot"
        ),
        "note": (
            "Guild command updates are nearly instant."
            if guild_id
            else
            "Global slash commands can take time to refresh in Discord."
        )
    }


def parse_slash_assets(text):
    # Discord slash fields are single-line strings.
    # Accept comma, semicolon, or newline separators.
    parts = re.split(
        r"[\n,;]+",
        str(text or "")
    )

    cleaned = [
        part.strip()
        for part in parts
        if part.strip()
    ]

    return "\n".join(cleaned)


def discord_ephemeral(content):
    return jsonify({
        "type": 4,
        "data": {
            "content": content,
            "flags": 64
        }
    })


def handle_trade_autocomplete(interaction):
    options = (
        interaction
        .get("data", {})
        .get("options", [])
    )

    focused = None

    for option in options:
        if option.get("focused"):
            focused = option
            break

    if not focused:
        return jsonify({
            "type": 8,
            "data": {"choices": []}
        })

    focused_name = focused.get("name", "")
    raw_value = str(
        focused.get("value", "")
    ).strip()

    option_map = {
        option.get("name"):
            option.get("value")
        for option in options
    }

    # TEAM DROPDOWNS
    if focused_name in ["team_a", "team_b"]:
        query = raw_value.lower()

        names = [
            team["value"]
            for team in DISCORD_TEAM_CHOICES
        ]

        filtered = [
            name
            for name in names
            if query in name.lower()
        ][:25]

        return jsonify({
            "type": 8,
            "data": {
                "choices": [
                    {
                        "name": name,
                        "value": name
                    }
                    for name in filtered
                ]
            }
        })

    # COMBINED PLAYER + DRAFT PICK ASSET DROPDOWNS
    if "_asset_" in focused_name:
        side = (
            "team_a"
            if focused_name.startswith("team_a")
            else "team_b"
        )

        team_name = str(
            option_map.get(
                side,
                ""
            )
        ).strip()

        if not team_name:
            return jsonify({
                "type": 8,
                "data": {
                    "choices": [
                        {
                            "name":
                                "Select the team first",
                            "value":
                                raw_value[:100]
                        }
                    ]
                }
            })

        # Hide assets already chosen in another slot on the same side.
        selected_assets = set()

        for key, value in option_map.items():
            if (
                key.startswith(
                    f"{side}_asset_"
                )
                and key != focused_name
                and value
            ):
                selected_assets.add(
                    str(value).lower()
                )

        query = raw_value.lower()
        choices = []

        # Player choices from Snallabot roster.
        try:
            team, players = build_roster_index(
                team_name
            )

            for player in players:
                player_name = str(
                    player.get(
                        "name",
                        ""
                    )
                ).strip()

                if not player_name:
                    continue

                if (
                    player_name.lower()
                    in selected_assets
                ):
                    continue

                position = str(
                    player.get(
                        "position",
                        ""
                    )
                )

                overall = player.get(
                    "overall"
                )

                if (
                    query
                    and query
                    not in player_name.lower()
                    and query
                    not in position.lower()
                    and "round" not in query
                ):
                    continue

                label = (
                    f"👤 {position or 'PLAYER'} • "
                    f"{player_name}"
                )

                if overall is not None:
                    label += (
                        f" • {overall} OVR"
                    )

                choices.append({
                    "name": label[:100],
                    "value": player_name[:100]
                })

                if len(choices) >= 18:
                    break

        except Exception:
            pass

        # Draft pick choices.
        current_year = datetime.now().year

        for year in range(
            current_year,
            current_year + 4
        ):
            for round_number in range(
                1,
                8
            ):
                pick_value = (
                    f"{year} Round "
                    f"{round_number}"
                )

                if (
                    pick_value.lower()
                    in selected_assets
                ):
                    continue

                if (
                    query
                    and query
                    not in pick_value.lower()
                    and query not in [
                        "pick",
                        "picks",
                        "draft"
                    ]
                ):
                    continue

                choices.append({
                    "name":
                        f"🏈 Draft Pick • {pick_value}",
                    "value":
                        pick_value
                })

                if len(choices) >= 25:
                    break

            if len(choices) >= 25:
                break

        return jsonify({
            "type": 8,
            "data": {
                "choices": choices[:25]
            }
        })

    return jsonify({
        "type": 8,
        "data": {"choices": []}
    })


def build_discord_trade_result_text(interaction):
    options = discord_option_map(
        interaction
    )

    team_a = str(
        options.get(
            "team_a",
            ""
        )
    ).strip()

    team_b = str(
        options.get(
            "team_b",
            ""
        )
    ).strip()

    owner_a_id = str(
        options.get(
            "team_a_owner",
            ""
        )
    ).strip()

    owner_b_id = str(
        options.get(
            "team_b_owner",
            ""
        )
    ).strip()

    screenshot_attachment_id = str(
        options.get(
            "trade_screenshot",
            ""
        )
    ).strip()

    screenshot_url = ""

    if screenshot_attachment_id:
        attachment = (
            interaction
            .get("data", {})
            .get("resolved", {})
            .get("attachments", {})
            .get(
                screenshot_attachment_id,
                {}
            )
        )

        screenshot_url = str(
            attachment.get(
                "url",
                ""
            )
        ).strip()

    team_a_assets_list = [
        str(
            options.get(
                f"team_a_asset_{slot}",
                ""
            )
        ).strip()
        for slot in range(1, 6)
        if str(
            options.get(
                f"team_a_asset_{slot}",
                ""
            )
        ).strip()
    ]

    team_b_assets_list = [
        str(
            options.get(
                f"team_b_asset_{slot}",
                ""
            )
        ).strip()
        for slot in range(1, 6)
        if str(
            options.get(
                f"team_b_asset_{slot}",
                ""
            )
        ).strip()
    ]

    assets_a_text = "\n".join(
        team_a_assets_list
    )

    assets_b_text = "\n".join(
        team_b_assets_list
    )

    if not team_a or not team_b:
        return "❌ Select both teams."

    if team_a.lower() == team_b.lower():
        return "❌ A team cannot trade with itself."

    if not team_a_assets_list:
        return (
            "❌ Team A must include at least "
            "one player or draft pick."
        )

    if not team_b_assets_list:
        return (
            "❌ Team B must include at least "
            "one player or draft pick."
        )

    if not find_team(team_a):
        return (
            f"❌ I could not find {team_a} "
            f"in the current Snallabot league export."
        )

    if not find_team(team_b):
        return (
            f"❌ I could not find {team_b} "
            f"in the current Snallabot league export."
        )

    try:
        team_a_assets = parse_trade_assets(
            assets_a_text,
            team_a
        )

        team_b_assets = parse_trade_assets(
            assets_b_text,
            team_b
        )

    except Exception as e:
        return (
            "❌ Trade could not be processed:\n"
            f"{str(e)[:1500]}"
        )

    mention_a = f"<@{owner_a_id}>"
    mention_b = f"<@{owner_b_id}>"

    analysis = analyze_trade({
        "team_a": team_a,
        "team_b": team_b,
        "team_a_mention": mention_a,
        "team_b_mention": mention_b,
        "team_a_sends": team_a_assets,
        "team_b_sends": team_b_assets
    })

    invoking_user = (
        interaction.get("member", {})
        .get("user", {})
    )

    invoking_id = (
        invoking_user.get("id")
        or interaction.get(
            "user",
            {}
        ).get("id")
    )

    analysis["submission_source"] = (
        "Discord /trade"
    )

    analysis["submitted_by_discord_id"] = (
        invoking_id
    )

    if screenshot_url:
        analysis["trade_screenshot_url"] = (
            screenshot_url
        )

    save_trade_proposal(
        analysis
    )

    discord_result = post_trade_to_discord(
        analysis
    )

    try:
        post_marcus_trade_reaction(
            analysis
        )
    except Exception as e:
        print(
            "MARCUS TRADE REACTION ERROR:",
            str(e)
        )

    if not discord_result.get("sent"):
        return (
            "⚠️ The trade was analyzed and saved, "
            "but the #trade-approval post failed.\n"
            f"{discord_result.get('error', 'Unknown error')[:1000]}"
        )

    # Marcus Hayes reacts in Project Madden Media after the
    # League Office proposal has been posted successfully.
    try:
        post_marcus_trade_reaction(
            analysis
        )
    except Exception as e:
        print(
            "MARCUS TRADE REACTION ERROR:",
            str(e)
        )

    review = analysis[
        "trade_committee"
    ]

    return (
        "✅ **Trade submitted successfully.**\n"
        f"**{team_a} ↔ {team_b}**\n"
        f"Trade ID: `{analysis['trade_id']}`\n"
        f"{team_a} grade: "
        f"**{analysis['team_a_grade']['grade']}**\n"
        f"{team_b} grade: "
        f"**{analysis['team_b_grade']['grade']}**\n"
        f"🏛️ League Office Review V2: "
        f"**{review['decision']}**\n"
        f"Fairness Score: "
        f"**{review.get('fairness_score', '—')}/100**\n"
        "The full proposal was posted in trade approval."
    )


def edit_discord_deferred_response(
    application_id,
    interaction_token,
    content
):
    url = (
        f"{DISCORD_API_BASE}/webhooks/"
        f"{application_id}/"
        f"{interaction_token}/messages/@original"
    )

    try:
        requests.patch(
            url,
            json={
                "content": content
            },
            timeout=15
        )
    except Exception as e:
        print(
            "DISCORD FOLLOWUP ERROR:",
            str(e)
        )


def process_trade_interaction_background(
    interaction
):
    try:
        content = (
            build_discord_trade_result_text(
                interaction
            )
        )
    except Exception as e:
        content = (
            "❌ Project Madden hit an internal error "
            f"while processing the trade: {str(e)[:1200]}"
        )

    edit_discord_deferred_response(
        str(
            interaction.get(
                "application_id",
                discord_application_id()
            )
        ),
        str(
            interaction.get(
                "token",
                ""
            )
        ),
        content
    )



def save_discord_debug(data):
    try:
        save_json_file(
            DISCORD_DEBUG_FILE,
            data
        )
    except Exception as e:
        print(
            "DISCORD DEBUG SAVE ERROR:",
            str(e)
        )


def get_discord_debug():
    data = load_json_file(
        DISCORD_DEBUG_FILE
    )

    if not isinstance(data, dict):
        data = {
            "status": "no_interaction_received_yet"
        }

    return data


@app.route(
    "/discord/debug",
    methods=["GET"]
)
def discord_debug():
    return jsonify({
        "configured": {
            "application_id":
                bool(discord_application_id()),
            "public_key":
                bool(discord_public_key()),
            "bot_token":
                bool(discord_bot_token()),
            "trade_webhook":
                bool(
                    os.environ.get(
                        "DISCORD_WEBHOOK_URL"
                    )
                )
        },
        "interactions_endpoint":
            discord_interactions_url(),
        "last_interaction":
            get_discord_debug()
    })



def process_test_weekly_show_background(
    interaction
):
    options = discord_option_map(
        interaction
    )

    headline = str(
        options.get(
            "headline",
            "Project Madden Weekly Show Test"
        )
    ).strip()

    if not headline:
        headline = (
            "Project Madden Weekly Show Test"
        )

    panel = build_weekly_show_test_panel(
        headline
    )

    result = send_weekly_show_embed(
        "📺 PROJECT MADDEN WEEKLY SHOW • TEST",
        (
            f"## {headline}\n"
            "This is a studio test of the Weekly Show panel. "
            "No game results or player stats are being invented."
        ),
        [
            {
                "name": "🎙️ Marcus Hayes",
                "value": panel["marcus"],
                "inline": False
            },
            {
                "name": (
                    "🎙️ Stephen A. Smith — AI Parody"
                ),
                "value": (
                    panel["stephen"]
                    + "\n\n*Fictional AI parody — not a real "
                    "Stephen A. Smith statement.*"
                ),
                "inline": False
            },
            {
                "name": (
                    "🎙️ Pat McAfee — AI Parody"
                ),
                "value": (
                    panel["pat"]
                    + "\n\n*Fictional AI parody — not a real "
                    "Pat McAfee statement.*"
                ),
                "inline": False
            }
        ]
    )

    if result.get("sent"):
        content = (
            "✅ Weekly Show panel test sent to the "
            "dedicated Weekly Show channel."
        )
    else:
        content = (
            "❌ Weekly Show test failed: "
            + str(
                result.get(
                    "error",
                    "Unknown error"
                )
            )[:1000]
        )

    edit_discord_deferred_response(
        str(
            interaction.get(
                "application_id",
                discord_application_id()
            )
        ),
        str(
            interaction.get(
                "token",
                ""
            )
        ),
        content
    )


def process_weekly_show_background(
    interaction
):
    try:
        options = discord_option_map(
            interaction
        )

        season_type = str(
            options.get(
                "season_type",
                "reg"
            )
        ).strip().lower()

        week_number = int(
            options.get(
                "week",
                1
            )
        )

        result = send_weekly_show_to_discord(
            season_type,
            week_number
        )

        if result.get("skipped"):
            content = (
                "ℹ️ That weekly show was already posted."
            )
        elif result.get("success"):
            content = (
                "✅ Project Madden Weekly Show posted "
                f"for {season_type.upper()} Week {week_number}."
            )
        else:
            content = (
                "❌ Weekly Show failed: "
                + str(
                    result.get(
                        "error",
                        result.get(
                            "result",
                            {}
                        ).get(
                            "error",
                            "Unknown error"
                        )
                    )
                )[:1000]
            )

    except Exception as e:
        print(
            "WEEKLY SHOW BACKGROUND ERROR:",
            repr(e)
        )
        content = (
            "❌ Weekly Show crashed while building the show: "
            f"{str(e)[:1000]}"
        )

    edit_discord_deferred_response(
        str(
            interaction.get(
                "application_id",
                discord_application_id()
            )
        ),
        str(
            interaction.get(
                "token",
                ""
            )
        ),
        content
    )


@app.route(
    "/discord/interactions",
    methods=["POST"]
)
def discord_interactions():
    raw_body = request.get_data()

    interaction = request.get_json(
        silent=True
    ) or {}

    interaction_type = interaction.get(
        "type"
    )

    command_name = (
        interaction
        .get("data", {})
        .get("name")
    )

    verified = verify_discord_request(
        raw_body
    )

    save_discord_debug({
        "received_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),
        "verified":
            verified,
        "interaction_type":
            interaction_type,
        "command_name":
            command_name,
        "has_signature_header":
            bool(
                request.headers.get(
                    "X-Signature-Ed25519"
                )
            ),
        "has_timestamp_header":
            bool(
                request.headers.get(
                    "X-Signature-Timestamp"
                )
            ),
        "content_type":
            request.headers.get(
                "Content-Type"
            )
    })

    print(
        "DISCORD INTERACTION:",
        {
            "verified": verified,
            "type": interaction_type,
            "command": command_name
        }
    )

    if not verified:
        return jsonify({
            "error":
                "invalid request signature"
        }), 401

    # Discord endpoint validation / ping.
    if interaction_type == 1:
        return jsonify({
            "type": 1
        })

    # Application command autocomplete.
    if interaction_type == 4:
        return handle_trade_autocomplete(
            interaction
        )

    # Slash command.
    if interaction_type == 2:
        command_name = (
            interaction
            .get("data", {})
            .get("name")
        )

        if command_name == "trade":
            # Discord requires the first response in about 3 seconds.
            # Defer immediately, then process the Snallabot/trade work
            # in the background and edit the original private response.
            worker = threading.Thread(
                target=process_trade_interaction_background,
                args=(interaction,),
                daemon=True
            )
            worker.start()

            return jsonify({
                "type": 5,
                "data": {
                    "flags": 64
                }
            })

        if command_name == "testmarcus":
            options = discord_option_map(
                interaction
            )

            headline = str(
                options.get(
                    "headline",
                    "League Test Segment"
                )
            ).strip()

            take = str(
                options.get(
                    "take",
                    "Marcus Hayes test."
                )
            ).strip()

            result = send_analyst_embed(
                "🧪 TEST • Marcus Hayes",
                (
                    f"## {headline}\n"
                    f"{take}\n\n"
                    "*Test message from Discord.*"
                )
            )

            if result.get("sent"):
                return discord_ephemeral(
                    "✅ Marcus Hayes test post sent to Project Madden Media."
                )

            return discord_ephemeral(
                "❌ Marcus Hayes test failed: "
                + str(
                    result.get(
                        "error",
                        "Unknown error"
                    )
                )[:1000]
            )

        if command_name == "testweeklyshow":
            worker = threading.Thread(
                target=process_test_weekly_show_background,
                args=(interaction,),
                daemon=True
            )
            worker.start()

            return jsonify({
                "type": 5,
                "data": {
                    "flags": 64
                }
            })

        if command_name == "weeklyshow":
            worker = threading.Thread(
                target=process_weekly_show_background,
                args=(interaction,),
                daemon=True
            )
            worker.start()

            return jsonify({
                "type": 5,
                "data": {
                    "flags": 64
                }
            })

        if command_name == "teststephena":
            options = discord_option_map(
                interaction
            )

            headline = str(
                options.get(
                    "headline",
                    "Project Madden Test Debate"
                )
            ).strip()

            take = str(
                options.get(
                    "take",
                    "AI parody test."
                )
            ).strip()

            result = send_stephen_a_parody_embed(
                "🧪 TEST • Stephen A. Smith — AI Parody",
                (
                    f"## {headline}\n"
                    f"{take}\n\n"
                    "⚠️ *Fictional AI parody for Project Madden. "
                    "This is not a real Stephen A. Smith quote or statement.*"
                )
            )

            if result.get("sent"):
                return discord_ephemeral(
                    "✅ Stephen A. Smith AI parody test sent."
                )

            return discord_ephemeral(
                "❌ Stephen A. parody test failed: "
                + str(
                    result.get(
                        "error",
                        "Unknown error"
                    )
                )[:1000]
            )

        return jsonify({
            "type": 4,
            "data": {
                "content":
                    "❌ Unknown Project Madden command.",
                "flags": 64
            }
        })

    return jsonify({
        "type": 4,
        "data": {
            "content":
                "Unsupported interaction.",
            "flags": 64
        }
    })


@app.route(
    "/discord/register",
    methods=["GET"]
)
def discord_register():
    result = register_trade_slash_command()

    status_code = (
        200
        if result.get("success")
        else 400
    )

    return jsonify(
        result
    ), status_code


@app.route(
    "/discord/status",
    methods=["GET"]
)
def discord_status():
    return jsonify({
        "discord_bot_configured":
            discord_bot_configured(),
        "application_id_configured":
            bool(discord_application_id()),
        "public_key_configured":
            bool(discord_public_key()),
        "bot_token_configured":
            bool(discord_bot_token()),
        "guild_id_configured":
            bool(discord_guild_id()),
        "interactions_endpoint":
            discord_interactions_url(),
        "slash_command":
            "/trade",
        "test_commands": [
            "/testmarcus",
            "/teststephena",
            "/testweeklyshow"
        ],
        "weekly_show_command":
            "/weeklyshow",
        "weekly_show_discord_webhook_configured": (
            weekly_show_webhook_configured()
        ),
        "weekly_show_destination":
            "Dedicated Weekly Show channel",
        "trade_webhook_configured":
            bool(
                os.environ.get(
                    "DISCORD_WEBHOOK_URL"
                )
            ),
        "trade_committee_role_configured":
            bool(
                trade_committee_role_id()
            )
    })



@app.route(
    "/assets/project-madden-league-office.jpeg",
    methods=["GET"]
)
def project_madden_league_office_avatar():
    return send_file(
        Path(__file__).resolve().parent
        / "project_madden_league_office.jpeg",
        mimetype="image/jpeg"
    )


@app.route(
    "/assets/marcus-hayes.png",
    methods=["GET"]
)
def marcus_hayes_avatar():
    return send_file(
        Path(__file__).resolve().parent
        / "marcus_hayes.png",
        mimetype="image/png"
    )


@app.route(
    "/assets/stephen-a-smith.png",
    methods=["GET"]
)
def stephen_a_smith_parody_avatar():
    return send_file(
        Path(__file__).resolve().parent
        / "stephen_a_smith.png",
        mimetype="image/png"
    )


@app.route(
    "/assets/weekly-show-logo.jpg",
    methods=["GET"]
)
def weekly_show_logo():
    return send_file(
        Path(__file__).resolve().parent
        / "weekly_show_logo.jpg",
        mimetype="image/jpeg"
    )



# =========================================================
# PROJECT MADDEN TEST CENTER
# =========================================================

TEST_CENTER_HTML = r"""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Project Madden Test Center</title>
<style>
body {
    margin: 0;
    background: #0d0f14;
    color: #f4f4f6;
    font-family: Arial, Helvetica, sans-serif;
}
.wrap {
    max-width: 900px;
    margin: 0 auto;
    padding: 18px;
}
h1 {
    margin: 0 0 6px;
    font-size: 30px;
}
.sub {
    color: #b9bbc5;
    margin-bottom: 22px;
}
.card {
    background: #171922;
    border: 1px solid #353847;
    border-radius: 18px;
    padding: 18px;
    margin-bottom: 18px;
}
.card h2 {
    margin-top: 0;
}
label {
    display: block;
    font-size: 13px;
    color: #c9cad1;
    margin: 12px 0 6px;
}
input, textarea, select {
    width: 100%;
    box-sizing: border-box;
    background: #0f1118;
    color: white;
    border: 1px solid #444758;
    border-radius: 10px;
    padding: 12px;
    font-size: 16px;
}
textarea {
    min-height: 90px;
}
.row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
}
button {
    width: 100%;
    margin-top: 14px;
    border: 0;
    border-radius: 10px;
    padding: 13px;
    font-size: 16px;
    font-weight: 700;
    background: #7b4dff;
    color: white;
}
.secondary {
    background: #303441;
}
.result {
    margin-top: 12px;
    padding: 12px;
    border-radius: 10px;
    background: #0f1118;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    color: #d7d8de;
}
.note {
    color: #aeb0ba;
    font-size: 13px;
}
.badge {
    display: inline-block;
    background: #2a2442;
    color: #c9b8ff;
    border-radius: 999px;
    padding: 5px 10px;
    font-size: 12px;
    margin-bottom: 10px;
}
@media (max-width: 650px) {
    .row {
        grid-template-columns: 1fr;
    }
}
</style>
</head>
<body>
<div class="wrap">
    <h1>🧪 Project Madden Test Center</h1>
    <div class="sub">
        Test trades, Marcus Hayes, and the Stephen A. Smith AI parody segment
        without waiting for a real game.
    </div>

    <div class="card">
        <div class="badge">TRADE ENGINE</div>
        <h2>Test a Trade</h2>
        <div class="row">
            <div>
                <label>Team A</label>
                <select id="team_a"></select>
            </div>
            <div>
                <label>Team B</label>
                <select id="team_b"></select>
            </div>
        </div>

        <label>Team A Assets</label>
        <textarea id="team_a_assets"
        placeholder="Lamar Jackson&#10;2027 Round 2"></textarea>

        <label>Team B Assets</label>
        <textarea id="team_b_assets"
        placeholder="Joe Burrow&#10;2027 Round 3"></textarea>

        <button onclick="testTrade(false)">Preview Trade</button>
        <button class="secondary" onclick="testTrade(true)">
            Send Test Trade to Discord
        </button>
        <div id="trade_result" class="result">Ready.</div>
    </div>

    <div class="card">
        <div class="badge">PROJECT MADDEN MEDIA</div>
        <h2>Test Marcus Hayes</h2>

        <label>Headline</label>
        <input id="marcus_headline"
        value="League Test Segment">

        <label>Marcus Test Take</label>
        <textarea id="marcus_take">This is a Project Madden Media test. Marcus Hayes is live and the analyst webhook is working.</textarea>

        <button onclick="testMarcus()">Send Marcus Test</button>
        <div id="marcus_result" class="result">Ready.</div>
    </div>

    <div class="card">
        <div class="badge">AI PARODY SEGMENT</div>
        <h2>Test Stephen A. Smith Segment</h2>

        <p class="note">
            This is always labeled as fictional AI parody and is not presented
            as a real Stephen A. Smith statement.
        </p>

        <label>Headline</label>
        <input id="stephen_headline"
        value="Project Madden Test Debate">

        <label>Parody Test Take</label>
        <textarea id="stephen_take">Ladies and gentlemen, this is a Project Madden test segment. The parody webhook is connected and ready for debate.</textarea>

        <button onclick="testStephen()">Send Parody Test</button>
        <div id="stephen_result" class="result">Ready.</div>
    </div>
</div>

<script>
async function loadTeams() {
    const res = await fetch('/api/teams');
    const data = await res.json();
    const teams = data.teams || [];
    const a = document.getElementById('team_a');
    const b = document.getElementById('team_b');

    teams.forEach((team, i) => {
        const name = team.displayName || team.name || team.abbrName;
        const oa = document.createElement('option');
        oa.value = name;
        oa.textContent = name;
        a.appendChild(oa);

        const ob = document.createElement('option');
        ob.value = name;
        ob.textContent = name;
        b.appendChild(ob);
    });

    if (b.options.length > 1) {
        b.selectedIndex = 1;
    }
}

async function testTrade(sendDiscord) {
    const out = document.getElementById('trade_result');
    out.textContent = 'Testing...';

    const body = {
        team_a: document.getElementById('team_a').value,
        team_b: document.getElementById('team_b').value,
        team_a_assets: document.getElementById('team_a_assets').value,
        team_b_assets: document.getElementById('team_b_assets').value,
        send_discord: sendDiscord
    };

    const res = await fetch('/test-center/trade', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    });

    const data = await res.json();
    out.textContent = JSON.stringify(data, null, 2);
}

async function testMarcus() {
    const out = document.getElementById('marcus_result');
    out.textContent = 'Sending...';

    const res = await fetch('/test-center/marcus', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            headline: document.getElementById('marcus_headline').value,
            take: document.getElementById('marcus_take').value
        })
    });

    const data = await res.json();
    out.textContent = JSON.stringify(data, null, 2);
}

async function testStephen() {
    const out = document.getElementById('stephen_result');
    out.textContent = 'Sending...';

    const res = await fetch('/test-center/stephen-a', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            headline: document.getElementById('stephen_headline').value,
            take: document.getElementById('stephen_take').value
        })
    });

    const data = await res.json();
    out.textContent = JSON.stringify(data, null, 2);
}

loadTeams();
</script>
</body>
</html>
"""


@app.route(
    "/test-center",
    methods=["GET"]
)
def test_center():
    return render_template_string(
        TEST_CENTER_HTML
    )


@app.route(
    "/test-center/trade",
    methods=["POST"]
)
def test_center_trade():
    data = request.get_json(
        silent=True
    ) or {}

    team_a = str(
        data.get(
            "team_a",
            ""
        )
    ).strip()

    team_b = str(
        data.get(
            "team_b",
            ""
        )
    ).strip()

    if not team_a or not team_b:
        return jsonify({
            "success": False,
            "error":
                "Select both teams."
        }), 400

    if team_a.lower() == team_b.lower():
        return jsonify({
            "success": False,
            "error":
                "Choose two different teams."
        }), 400

    try:
        team_a_sends = parse_trade_assets(
            str(
                data.get(
                    "team_a_assets",
                    ""
                )
            ),
            team_a
        )

        team_b_sends = parse_trade_assets(
            str(
                data.get(
                    "team_b_assets",
                    ""
                )
            ),
            team_b
        )

        if not team_a_sends:
            return jsonify({
                "success": False,
                "error":
                    "Team A needs at least one asset."
            }), 400

        if not team_b_sends:
            return jsonify({
                "success": False,
                "error":
                    "Team B needs at least one asset."
            }), 400

        analysis = analyze_trade({
            "team_a": team_a,
            "team_b": team_b,
            "team_a_mention":
                "TEST TEAM A",
            "team_b_mention":
                "TEST TEAM B",
            "team_a_sends":
                team_a_sends,
            "team_b_sends":
                team_b_sends
        })

        trade_card_url = None

        try:
            generate_trade_card(
                analysis
            )

            trade_card_url = (
                request.host_url.rstrip("/")
                + "/trade-card/"
                + analysis["trade_id"]
                + ".png"
            )
        except Exception as e:
            print(
                "TEST TRADE CARD ERROR:",
                str(e)
            )

        result = {
            "success": True,
            "mode": "preview",
            "analysis": analysis,
            "trade_card_url":
                trade_card_url
        }

        if bool(
            data.get(
                "send_discord"
            )
        ):
            discord_result = (
                post_trade_to_discord(
                    analysis
                )
            )

            result[
                "mode"
            ] = "discord_test"

            result[
                "trade_discord"
            ] = discord_result

            try:
                result[
                    "marcus_trade_reaction"
                ] = (
                    post_marcus_trade_reaction(
                        analysis
                    )
                )
            except Exception as e:
                result[
                    "marcus_trade_reaction"
                ] = {
                    "sent": False,
                    "error": str(e)
                }

        return jsonify(result)

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400


@app.route(
    "/test-center/marcus",
    methods=["POST"]
)
def test_center_marcus():
    data = request.get_json(
        silent=True
    ) or {}

    headline = str(
        data.get(
            "headline",
            "League Test Segment"
        )
    ).strip()

    take = str(
        data.get(
            "take",
            "Project Madden Media test."
        )
    ).strip()

    result = send_analyst_embed(
        "🧪 TEST • Marcus Hayes",
        (
            f"## {headline}\n"
            f"{take}\n\n"
            "*Test message from the Project Madden Test Center.*"
        )
    )

    return jsonify({
        "success":
            bool(result.get("sent")),
        "result":
            result
    }), (
        200
        if result.get("sent")
        else 400
    )


@app.route(
    "/test-center/stephen-a",
    methods=["POST"]
)
def test_center_stephen_a():
    data = request.get_json(
        silent=True
    ) or {}

    headline = str(
        data.get(
            "headline",
            "Project Madden Test Debate"
        )
    ).strip()

    take = str(
        data.get(
            "take",
            "Project Madden AI parody test."
        )
    ).strip()

    result = send_stephen_a_parody_embed(
        "🧪 TEST • Stephen A. Smith — AI Parody",
        (
            f"## {headline}\n"
            f"{take}\n\n"
            "⚠️ *Fictional AI parody for Project Madden. "
            "This is not a real Stephen A. Smith quote or statement.*"
        )
    )

    return jsonify({
        "success":
            bool(result.get("sent")),
        "result":
            result
    }), (
        200
        if result.get("sent")
        else 400
    )


# =========================================================
# HOME / HEALTH
# =========================================================

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "Project Madden Analytics",
        "snallabot": "connected",
        "trade_center": "/proposetrade",
        "test_center": "/test-center",
        "team_api": "/api/teams",
        "player_search": "/api/players",
        "game_analyst": "/analyst/reactions/pre/1",
        "player_analyst": "/analyst/players/pre/1",
        "weekly_show": "/analyst/show/pre/1",
        "analyst_status": "/analyst/status",
        "analyst_discord_post": "/analyst/post/pre/1",
        "trade_discord_webhook_configured": bool(
            os.environ.get(
                "DISCORD_WEBHOOK_URL"
            )
        ),
        "analyst_discord_webhook_configured": (
            analyst_webhook_configured()
        ),
        "discord_bot_configured": (
            discord_bot_configured()
        ),
        "discord_slash_command": "/trade",
        "discord_status": "/discord/status"
    })


@app.route("/health")
def health():
    return jsonify({
        "online": True,
        "trade_discord_webhook_configured": bool(
            os.environ.get(
                "DISCORD_WEBHOOK_URL"
            )
        ),
        "analyst_discord_webhook_configured": (
            analyst_webhook_configured()
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

        marcus_standings = None

        try:
            marcus_standings = (
                process_standings_posts()
            )
        except Exception as e:
            # Do not fail a valid Snallabot standings export
            # just because Discord/storyline processing had an issue.
            marcus_standings = {
                "success": False,
                "error": str(e)
            }

        return jsonify({
            "success": True,
            "type": "standings",
            "marcus_auto_post": (
                marcus_standings
            )
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
        team_index = parts.index("team")
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
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                indent=2
            )

        auto_post = None

        # Marcus checks for new material whenever one of the
        # analyst-relevant weekly exports arrives.
        #
        # This safely works even when Snallabot sends the files
        # one at a time:
        # - schedules can trigger the game reaction
        # - passing/rushing/receiving/defense can trigger player reactions
        # - duplicate history prevents the same segment from reposting
        if stat_type in [
            "schedules",
            "passing",
            "rushing",
            "receiving",
            "defense"
        ]:
            try:
                auto_post = process_analyst_week_posts(
                    season_type,
                    int(week_number)
                )
            except Exception as e:
                # Never reject a valid Snallabot export just because
                # Discord or the analyst post step had a problem.
                auto_post = {
                    "success": False,
                    "error": str(e)
                }

        return jsonify({
            "success": True,
            "type": "weekly",
            "season_type": season_type,
            "week": week_number,
            "stat_type": stat_type,
            "marcus_auto_post": auto_post
        })

    return jsonify({
        "success": True,
        "type": "unknown",
        "path": subpath
    })


# =========================================================
# TEAM / PLAYER API
# =========================================================

@app.route("/api/teams")
def teams_api():
    teams = list(
        get_team_map().values()
    )

    teams.sort(
        key=lambda t:
            t.get("name", "")
    )

    return jsonify({
        "team_count": len(teams),
        "teams": teams
    })


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
        team, players = build_roster_index(
            team_name
        )
    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 400

    if query:
        players = [
            player
            for player in players
            if query in player[
                "name"
            ].lower()
        ]

    return jsonify({
        "team": team.get("name"),
        "team_logo": team.get("logo"),
        "player_count": len(players),
        "players": players[:100]
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
            "season_type": season_type,
            "week": week_number,
            "status": "waiting",
            "message": (
                "No Snallabot schedule export received yet."
            ),
            "reactions": []
        })

    reactions = build_week_game_reactions(
        season_type,
        week_number
    )

    return jsonify({
        "season_type": season_type,
        "week": week_number,
        "completed_games_found": len(
            reactions
        ),
        "reactions": reactions
    })


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

    reactions = build_week_player_reactions(
        season_type,
        week_number
    )

    return jsonify({
        "season_type": season_type,
        "week": week_number,
        "files_received": {
            "passing": passing_data is not None,
            "rushing": rushing_data is not None,
            "receiving": receiving_data is not None,
            "defense": defense_data is not None
        },
        "reaction_count": len(
            reactions
        ),
        "status": (
            "ready"
            if reactions
            else "waiting_for_player_performances"
        ),
        "reactions": reactions
    })


@app.route(
    "/analyst/show/<season_type>/<int:week_number>"
)
def analyst_weekly_show(
    season_type,
    week_number
):
    game_segments = []

    for reaction in build_week_game_reactions(
        season_type,
        week_number
    ):
        game_segments.append({
            "headline": reaction.get(
                "headline"
            ),
            "game": reaction.get(
                "game"
            ),
            "story_type": reaction.get(
                "story_type"
            ),
            "script": reaction.get(
                "analyst_take"
            )
        })

    player_segments = build_week_player_reactions(
        season_type,
        week_number
    )

    return jsonify({
        "show": "Project Madden First Take",
        "analyst": PROJECT_MADDEN_ANALYST,
        "season_type": season_type,
        "week": week_number,
        "game_segments": game_segments,
        "player_segments": player_segments,
        "total_segments": (
            len(game_segments)
            + len(player_segments)
        )
    })


# =========================================================
# AUTOMATIC MARCUS HAYES DISCORD PROCESSOR
# =========================================================

def process_analyst_week_posts(
    season_type,
    week_number
):
    if not analyst_webhook_configured():
        return {
            "success": False,
            "error": (
                "ANALYST_DISCORD_WEBHOOK_URL "
                "is not configured in Render."
            ),
            "sent_count": 0,
            "skipped_count": 0,
            "failed_count": 0
        }

    post_history = load_analyst_post_history()


    game_reactions = build_week_game_reactions(
        season_type,
        week_number
    )

    player_reactions = build_week_player_reactions(
        season_type,
        week_number
    )

    sent = []
    skipped = []
    failed = []

    # -------------------------
    # GAME REACTIONS
    # -------------------------

    for reaction in game_reactions:
        identifier = reaction.get(
            "schedule_id"
        )

        key = analyst_post_key(
            season_type,
            week_number,
            "game",
            identifier
        )

        if key in post_history:
            skipped.append({
                "type": "game",
                "id": identifier,
                "reason": "already_posted"
            })
            continue

        result = post_game_reaction_to_discord(
            reaction
        )

        if result.get("sent"):
            post_history.append(key)

            sent.append({
                "type": "game",
                "id": identifier,
                "headline": reaction.get(
                    "headline"
                )
            })
        else:
            failed.append({
                "type": "game",
                "id": identifier,
                "error": result.get(
                    "error"
                )
            })

    # -------------------------
    # PLAYER REACTIONS
    # -------------------------

    for index, reaction in enumerate(
        player_reactions
    ):
        identifier = (
            f"{reaction.get('player')}-"
            f"{reaction.get('category')}-"
            f"{index}"
        )

        key = analyst_post_key(
            season_type,
            week_number,
            "player",
            identifier
        )

        if key in post_history:
            skipped.append({
                "type": "player",
                "id": identifier,
                "reason": "already_posted"
            })
            continue

        result = post_player_reaction_to_discord(
            reaction
        )

        if result.get("sent"):
            post_history.append(key)

            sent.append({
                "type": "player",
                "player": reaction.get(
                    "player"
                ),
                "category": reaction.get(
                    "category"
                )
            })
        else:
            failed.append({
                "type": "player",
                "player": reaction.get(
                    "player"
                ),
                "error": result.get(
                    "error"
                )
            })

    save_json_file(
        ANALYST_POST_HISTORY_FILE,
        post_history
    )

    return {
        "success": len(failed) == 0,
        "analyst": PROJECT_MADDEN_ANALYST,
        "destination": "Project Madden Media",
        "season_type": season_type,
        "week": week_number,
        "game_reactions_found": len(
            game_reactions
        ),
        "player_reactions_found": len(
            player_reactions
        ),
        "sent_count": len(sent),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "sent": sent,
        "skipped": skipped,
        "failed": failed
    }


# =========================================================
# MARCUS HAYES STATUS / DISCORD POST
# =========================================================

@app.route("/analyst/status")
def analyst_status():
    return jsonify({
        "analyst": PROJECT_MADDEN_ANALYST,
        "brand": "Project Madden Media",
        "show": "Project Madden First Take",
        "discord_webhook_configured": (
            analyst_webhook_configured()
        ),
        "game_reactions": (
            "/analyst/reactions/pre/1"
        ),
        "player_reactions": (
            "/analyst/players/pre/1"
        ),
        "weekly_show": (
            "/analyst/show/pre/1"
        ),
        "post_to_discord": (
            "/analyst/post/pre/1"
        ),
        "automatic_posting": True,
        "automatic_trigger": (
            "Snallabot weekly schedules, passing, "
            "rushing, receiving, defense, or standings export"
        ),
        "standings": "/analyst/standings",
        "power_rankings": "/analyst/power-rankings",
        "storylines": "/analyst/storylines",
        "post_standings_to_discord": "/analyst/post-standings"
    })


@app.route(
    "/analyst/post/<season_type>/<int:week_number>",
    methods=["GET", "POST"]
)
def post_analyst_week(
    season_type,
    week_number
):
    result = process_analyst_week_posts(
        season_type,
        week_number
    )

    status_code = 200

    if (
        not result.get("success")
        and result.get("error")
    ):
        status_code = 400

    return jsonify(result), status_code


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
* { box-sizing: border-box; }

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
.review {
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
<p>• {{ summarize(asset) }}</p>
{% endfor %}

<h3>
{{ analysis.team_b }} Sends
</h3>

{% for asset in analysis.team_b_sends %}
<p>• {{ summarize(asset) }}</p>
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

</div>

<div class="review">

<h2>
🏛️ League Office Review
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
✅ Posted to trade approval.
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

<form method="POST" id="tradeForm">

<div class="card">

<h2>TEAM A</h2>

<label>Select Team</label>

<select
name="team_a"
id="teamA"
required>

<option value="">
Select Team A
</option>

</select>

<div id="teamALogo"></div>

<label>Discord @</label>

<input
name="team_a_mention"
placeholder="@RavensOwner"
required>

<label>Search Players</label>

<input
id="playerSearchA"
placeholder="Select a team first"
disabled>

<div
id="resultsA"
class="search-results">
</div>

<div id="selectedA"></div>

<label>Draft Pick</label>

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

<h2>TEAM B</h2>

<label>Select Team</label>

<select
name="team_b"
id="teamB"
required>

<option value="">
Select Team B
</option>

</select>

<div id="teamBLogo"></div>

<label>Discord @</label>

<input
name="team_b_mention"
placeholder="@ChiefsOwner"
required>

<label>Search Players</label>

<input
id="playerSearchB"
placeholder="Select a team first"
disabled>

<div
id="resultsB"
class="search-results">
</div>

<div id="selectedB"></div>

<label>Draft Pick</label>

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

<button type="submit">
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
    const res = await fetch("/api/teams");
    const data = await res.json();

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

            select.appendChild(option);
        });

        select.addEventListener(
            "change",
            () => {
                selected[side] = [];
                syncAssets(side);

                const search =
                    document.getElementById(
                        "playerSearch" + side
                    );

                if (select.value) {
                    search.disabled = false;

                    search.placeholder =
                        `Search ${select.value} players...`;

                    const team =
                        teams.find(
                            t =>
                            t.name === select.value
                        );

                    document.getElementById(
                        "team" + side + "Logo"
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
                    search.disabled = true;
                }
            }
        );
    });
}


async function searchPlayers(side) {
    const team =
        document.getElementById(
            "team" + side
        ).value;

    const query =
        document.getElementById(
            "playerSearch" + side
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

    const data = await res.json();

    const box =
        document.getElementById(
            "results" + side
        );

    box.innerHTML = "";

    if (
        !data.players
        ||
        data.players.length === 0
    ) {
        box.innerHTML =
            "<div class='player-option'>No players found</div>";

        box.classList.add("open");
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

            <div class="small">
            ${player.overall} OVR
            • ${player.position}
            • Age ${player.age}
            • ${player.dev}
            </div>
        `;

        div.onclick = () => {
            if (
                selected[side]
                .some(
                    x =>
                    x.type === "player"
                    &&
                    x.name === player.name
                )
            ) {
                return;
            }

            selected[side].push({
                type: "player",
                name: player.name
            });

            document.getElementById(
                "playerSearch" + side
            ).value = "";

            box.classList.remove(
                "open"
            );

            syncAssets(side);
        };

        box.appendChild(div);
    });

    box.classList.add("open");
}


function addPick(side) {
    const year =
        document.getElementById(
            "pickYear" + side
        ).value;

    const round =
        document.getElementById(
            "pickRound" + side
        ).value;

    selected[side].push({
        type: "pick",
        year: year,
        round: round
    });

    syncAssets(side);
}


function removeAsset(
    side,
    index
) {
    selected[side].splice(
        index,
        1
    );

    syncAssets(side);
}


function syncAssets(side) {
    const box =
        document.getElementById(
            "selected" + side
        );

    box.innerHTML = "";

    const lines = [];

    selected[side].forEach(
        (item,index) => {
            let label = "";

            if (item.type === "player") {
                label = item.name;
                lines.push(item.name);
            } else {
                label =
                    `${item.year} Round ${item.round}`;

                lines.push(
                    `${item.year} Round ${item.round}`
                );
            }

            const div =
                document.createElement(
                    "div"
                );

            div.className = "asset";

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

            box.appendChild(div);
        }
    );

    document.getElementById(
        "assets" + side
    ).value =
        lines.join("\\n");
}


["A","B"].forEach(side => {
    document.getElementById(
        "playerSearch" + side
    ).addEventListener(
        "input",
        () => {
            searchPlayers(side);
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
            selected.A.length === 0
            ||
            selected.B.length === 0
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
    methods=["GET", "POST"]
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

    if not team_a or not team_b:
        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error="Select both teams.",
            discord=None,
            summarize=summarize_asset
        )

    if team_a.lower() == team_b.lower():
        return render_template_string(
            TRADE_PAGE,
            analysis=None,
            error="A team cannot trade with itself.",
            discord=None,
            summarize=summarize_asset
        )

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

    save_trade_proposal(
        analysis
    )

    discord_result = post_trade_to_discord(
        analysis
    )

    return render_template_string(
        TRADE_PAGE,
        analysis=analysis,
        error=None,
        discord=discord_result,
        summarize=summarize_asset
    )


@app.route(
    "/analyst/trade-proposals"
)
def trade_proposals_api():
    proposals = load_json_file(
        "trade_proposals.json"
    )

    if not isinstance(proposals, list):
        proposals = []

    return jsonify({
        "count": len(proposals),
        "proposals": proposals
    })


# =========================================================
# START APP - MUST STAY LAST
# =========================================================


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
