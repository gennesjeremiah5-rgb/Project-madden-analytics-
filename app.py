from pathlib import Path
import math
from io import BytesIO
from flask import Flask, request, jsonify, render_template_string, send_file, redirect
import json
import os
import io
import hashlib
import hmac
import uuid
import re
import requests
from PIL import Image, ImageDraw, ImageFont
import threading
import time
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from datetime import datetime, timezone
from urllib.parse import urlencode

try:
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:
    psycopg = None
    Jsonb = None


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

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    ""
).strip()

PERSISTENT_JSON_FILES = {
    "trade_history.json",
    "project_madden_record_book.json",
    "project_madden_hall_of_fame.json",
    "analyst_receipts.json",
    "rivalry_history.json",
    "gotw_poll_history.json",
    "injury_history.json",
    "project_madden_guilds.json",
}

PERSISTENT_DB_LOCK = threading.Lock()
PERSISTENT_DB_READY = False
PERSISTENT_DB_LAST_ERROR = None

# Only members with this Discord role may use commands beginning with /test.
LEAGUE_OWNER_TEST_ROLE_ID = "1538749830111694910"

GOTW_POLL_HISTORY_FILE = "gotw_poll_history.json"
GOTW_POLL_CLOSE_SECONDS = 300

INJURY_HISTORY_FILE = "injury_history.json"
INJURY_MAJOR_OVR = 85

PROJECT_MADDEN_APP_VERSION = "v31-snallabot-official-source"



# =========================================================
# MULTI-SERVER / MULTI-LEAGUE FOUNDATION
# =========================================================

MULTI_SERVER_DB_READY = False
MULTI_SERVER_DB_LOCK = threading.Lock()

PROJECT_MADDEN_BASE_URL = os.environ.get(
    "PROJECT_MADDEN_BASE_URL",
    "https://project-madden-analytics.onrender.com"
).strip().rstrip("/")

# Current production data source.
# Project Madden can manage/display league data, but until the direct EA
# connector is completed the official live data pipeline still requires
# Snallabot exports.
PROJECT_MADDEN_DATA_SOURCE = "snallabot"
DIRECT_EA_CONNECTOR_STATUS = "research"


def ensure_multi_server_db():
    global MULTI_SERVER_DB_READY

    if MULTI_SERVER_DB_READY:
        return True

    if (
        not DATABASE_URL
        or psycopg is None
    ):
        return False

    with MULTI_SERVER_DB_LOCK:
        if MULTI_SERVER_DB_READY:
            return True

        try:
            with psycopg.connect(
                DATABASE_URL,
                connect_timeout=8
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS
                        project_madden_guilds (
                            guild_id TEXT PRIMARY KEY,
                            guild_name TEXT,
                            setup_token TEXT UNIQUE,
                            league_name TEXT,
                            snallabot_league_id TEXT,
                            platform TEXT,
                            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )

                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS
                        project_madden_guilds_snallabot_idx
                        ON project_madden_guilds (
                            snallabot_league_id
                        )
                        """
                    )

            MULTI_SERVER_DB_READY = True
            return True

        except Exception as e:
            print(
                "MULTI SERVER DB ERROR:",
                repr(
                    e
                )
            )
            return False



def fetch_discord_guild_channels(
    guild_id
):
    token = discord_bot_token()

    if (
        not token
        or not guild_id
    ):
        return []

    try:
        response = requests.get(
            (
                "https://discord.com/api/v10/"
                f"guilds/{guild_id}/channels"
            ),
            headers=discord_api_headers(),
            timeout=10
        )

        if response.status_code != 200:
            print(
                "DISCORD CHANNEL FETCH ERROR:",
                response.status_code,
                response.text[:300]
            )
            return []

        payload = response.json()

        return (
            payload
            if isinstance(
                payload,
                list
            )
            else []
        )

    except Exception as e:
        print(
            "DISCORD CHANNEL FETCH EXCEPTION:",
            repr(
                e
            )
        )
        return []


def normalize_discord_channel_name(
    value
):
    text = str(
        value
        or ""
    ).strip().lower()

    text = re.sub(
        r"[^a-z0-9]+",
        "-",
        text
    )

    return re.sub(
        r"-+",
        "-",
        text
    ).strip("-")


def best_discord_channel_match(
    channels,
    aliases,
    allowed_types
):
    aliases = [
        normalize_discord_channel_name(
            alias
        )
        for alias in aliases
    ]

    candidates = [
        item
        for item in channels
        if (
            isinstance(
                item,
                dict
            )
            and int(
                item.get(
                    "type",
                    -1
                )
            )
            in allowed_types
        )
    ]

    # Exact normalized-name match first.
    for alias in aliases:
        for item in candidates:
            if (
                normalize_discord_channel_name(
                    item.get(
                        "name"
                    )
                )
                == alias
            ):
                return item

    # Then a contains match.
    for alias in aliases:
        for item in candidates:
            name = normalize_discord_channel_name(
                item.get(
                    "name"
                )
            )

            if (
                alias
                and (
                    alias in name
                    or name in alias
                )
            ):
                return item

    return None



def create_discord_channel(
    guild_id,
    name,
    channel_type=0,
    parent_id=None,
    topic=None
):
    token = discord_bot_token()

    if (
        not token
        or not guild_id
    ):
        return {
            "success":
                False,
            "error":
                "DISCORD_BOT_TOKEN or guild ID is missing."
        }

    payload = {
        "name":
            safe_discord_channel_name(
                name
            ),
        "type":
            int(
                channel_type
            )
    }

    if (
        parent_id
        and re.fullmatch(
            r"\d{15,22}",
            str(
                parent_id
            )
        )
    ):
        payload[
            "parent_id"
        ] = str(
            parent_id
        )

    if topic:
        payload[
            "topic"
        ] = str(
            topic
        )[:1024]

    try:
        response = requests.post(
            (
                "https://discord.com/api/v10/"
                f"guilds/{guild_id}/channels"
            ),
            headers={
                "Authorization":
                    f"Bot {token}",
                "Content-Type":
                    "application/json"
            },
            json=payload,
            timeout=15
        )

        if response.status_code not in [
            200,
            201
        ]:
            return {
                "success":
                    False,
                "status_code":
                    response.status_code,
                "error":
                    response.text[:500]
            }

        data = response.json()

        return {
            "success":
                True,
            "channel": {
                "id":
                    str(
                        data.get(
                            "id"
                        )
                    ),
                "name":
                    data.get(
                        "name"
                    ),
                "type":
                    data.get(
                        "type"
                    ),
                "parent_id":
                    data.get(
                        "parent_id"
                    )
            }
        }

    except Exception as e:
        return {
            "success":
                False,
            "error":
                str(
                    e
                )
        }


def create_project_madden_channel_bundle(
    guild_id,
    create_category=True,
    create_gotw=True,
    create_hof=True,
    create_injuries=True,
    create_weekly_show=True
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    if not guild_id:
        return {
            "success":
                False,
            "error":
                "Missing Discord server ID."
        }

    results = {}
    category_id = None

    if create_category:
        category_result = create_discord_channel(
            guild_id,
            "project-madden",
            channel_type=4
        )

        results[
            "category"
        ] = category_result

        if category_result.get(
            "success"
        ):
            category_id = (
                category_result
                .get(
                    "channel",
                    {}
                )
                .get(
                    "id"
                )
            )

    specs = []

    if create_gotw:
        specs.append(
            (
                "gotw",
                "gotw",
                "Project Madden Game of the Week voting and announcements."
            )
        )

    if create_hof:
        specs.append(
            (
                "hall_of_fame",
                "hall-of-fame",
                "Project Madden Hall of Fame announcements and profiles."
            )
        )

    if create_injuries:
        specs.append(
            (
                "injuries",
                "injuries",
                "Project Madden league-wide injury reports and updates."
            )
        )

    if create_weekly_show:
        specs.append(
            (
                "weekly_show",
                "weekly-show",
                "Project Madden Weekly Show, analyst reactions, and media."
            )
        )

    for key, name, topic in specs:
        results[
            key
        ] = create_discord_channel(
            guild_id,
            name,
            channel_type=0,
            parent_id=category_id,
            topic=topic
        )

    created = {
        key:
            value.get(
                "channel"
            )
        for key, value in results.items()
        if (
            isinstance(
                value,
                dict
            )
            and value.get(
                "success"
            )
        )
    }

    failures = {
        key:
            value
        for key, value in results.items()
        if (
            isinstance(
                value,
                dict
            )
            and not value.get(
                "success"
            )
        )
    }

    return {
        "success":
            len(
                failures
            )
            == 0,
        "created":
            created,
        "failures":
            failures,
        "results":
            results
    }


def detect_discord_setup(
    guild_id
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    channels = fetch_discord_guild_channels(
        guild_id
    )

    guild_name = fetch_discord_guild_name(
        guild_id
    )

    text_channels = [
        {
            "id":
                str(
                    item.get(
                        "id"
                    )
                ),
            "name":
                str(
                    item.get(
                        "name"
                    )
                    or "unnamed-channel"
                ),
            "type":
                int(
                    item.get(
                        "type",
                        0
                    )
                )
        }
        for item in channels
        if (
            isinstance(
                item,
                dict
            )
            and int(
                item.get(
                    "type",
                    -1
                )
            )
            in [
                0,   # guild text
                5,   # announcement
                15   # forum
            ]
        )
    ]

    categories = [
        {
            "id":
                str(
                    item.get(
                        "id"
                    )
                ),
            "name":
                str(
                    item.get(
                        "name"
                    )
                    or "unnamed-category"
                ),
            "type":
                4
        }
        for item in channels
        if (
            isinstance(
                item,
                dict
            )
            and int(
                item.get(
                    "type",
                    -1
                )
            )
            == 4
        )
    ]

    suggestions = {}

    match_specs = {
        "gotw_channel_id": (
            [
                "gotw",
                "game-of-the-week",
                "gameoftheweek",
                "game-week"
            ],
            [
                0,
                5,
                15
            ]
        ),
        "hall_of_fame_channel_id": (
            [
                "hall-of-fame",
                "halloffame",
                "hof"
            ],
            [
                0,
                5,
                15
            ]
        ),
        "injury_channel_id": (
            [
                "injuries",
                "injury",
                "injury-report",
                "injury-updates"
            ],
            [
                0,
                5,
                15
            ]
        ),
        "weekly_show_channel_id": (
            [
                "weekly-show",
                "weeklyshow",
                "weekly",
                "media"
            ],
            [
                0,
                5,
                15
            ]
        ),
        "hall_of_fame_category_id": (
            [
                "hall-of-fame",
                "halloffame",
                "hof",
                "league-office"
            ],
            [
                4
            ]
        )
    }

    for key, (
        aliases,
        types
    ) in match_specs.items():
        match = best_discord_channel_match(
            channels,
            aliases,
            types
        )

        if match:
            suggestions[
                key
            ] = str(
                match.get(
                    "id"
                )
            )

    return {
        "success":
            bool(
                channels
            ),
        "guild_id":
            guild_id,
        "guild_name":
            guild_name,
        "text_channels":
            text_channels,
        "categories":
            categories,
        "suggestions":
            suggestions,
        "channel_count":
            len(
                text_channels
            ),
        "category_count":
            len(
                categories
            )
    }


def fetch_discord_guild_name(
    guild_id
):
    token = discord_bot_token()

    if (
        not token
        or not guild_id
    ):
        return None

    try:
        response = requests.get(
            (
                "https://discord.com/api/v10/"
                f"guilds/{guild_id}"
            ),
            headers=discord_api_headers(),
            timeout=8
        )

        if response.status_code != 200:
            return None

        payload = response.json()

        return (
            payload.get(
                "name"
            )
            or None
        )

    except Exception:
        return None


def ensure_guild_config(
    guild_id,
    guild_name=None
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    if not guild_id:
        return None

    if not ensure_multi_server_db():
        return None

    if not guild_name:
        guild_name = fetch_discord_guild_name(
            guild_id
        )

    setup_token = uuid.uuid4().hex

    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=8
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO project_madden_guilds (
                        guild_id,
                        guild_name,
                        setup_token,
                        settings,
                        updated_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        '{}'::jsonb,
                        NOW()
                    )
                    ON CONFLICT (guild_id)
                    DO UPDATE SET
                        guild_name = COALESCE(
                            EXCLUDED.guild_name,
                            project_madden_guilds.guild_name
                        ),
                        updated_at = NOW()
                    RETURNING
                        guild_id,
                        guild_name,
                        setup_token,
                        league_name,
                        snallabot_league_id,
                        platform,
                        settings,
                        created_at,
                        updated_at
                    """,
                    (
                        guild_id,
                        guild_name,
                        setup_token
                    )
                )

                row = cur.fetchone()

        if not row:
            return None

        return {
            "guild_id":
                row[0],
            "guild_name":
                row[1],
            "setup_token":
                row[2],
            "league_name":
                row[3],
            "snallabot_league_id":
                row[4],
            "platform":
                row[5],
            "settings":
                row[6]
                if isinstance(
                    row[6],
                    dict
                )
                else {},
            "created_at":
                row[7],
            "updated_at":
                row[8]
        }

    except Exception as e:
        print(
            "ENSURE GUILD CONFIG ERROR:",
            repr(
                e
            )
        )
        return None


def get_guild_config(
    guild_id
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    if (
        not guild_id
        or not ensure_multi_server_db()
    ):
        return None

    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=8
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        guild_id,
                        guild_name,
                        setup_token,
                        league_name,
                        snallabot_league_id,
                        platform,
                        settings,
                        created_at,
                        updated_at
                    FROM project_madden_guilds
                    WHERE guild_id = %s
                    """,
                    (
                        guild_id,
                    )
                )

                row = cur.fetchone()

        if not row:
            return None

        return {
            "guild_id":
                row[0],
            "guild_name":
                row[1],
            "setup_token":
                row[2],
            "league_name":
                row[3],
            "snallabot_league_id":
                row[4],
            "platform":
                row[5],
            "settings":
                row[6]
                if isinstance(
                    row[6],
                    dict
                )
                else {},
            "created_at":
                row[7],
            "updated_at":
                row[8]
        }

    except Exception:
        return None


def get_guild_config_by_token(
    setup_token
):
    token = str(
        setup_token
        or ""
    ).strip()

    if (
        not token
        or not ensure_multi_server_db()
    ):
        return None

    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=8
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        guild_id,
                        guild_name,
                        setup_token,
                        league_name,
                        snallabot_league_id,
                        platform,
                        settings,
                        created_at,
                        updated_at
                    FROM project_madden_guilds
                    WHERE setup_token = %s
                    """,
                    (
                        token,
                    )
                )

                row = cur.fetchone()

        if not row:
            return None

        return {
            "guild_id":
                row[0],
            "guild_name":
                row[1],
            "setup_token":
                row[2],
            "league_name":
                row[3],
            "snallabot_league_id":
                row[4],
            "platform":
                row[5],
            "settings":
                row[6]
                if isinstance(
                    row[6],
                    dict
                )
                else {},
            "created_at":
                row[7],
            "updated_at":
                row[8]
        }

    except Exception:
        return None


def list_guild_configs():
    if not ensure_multi_server_db():
        return []

    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=8
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        guild_id,
                        guild_name,
                        league_name,
                        snallabot_league_id,
                        platform,
                        settings,
                        created_at,
                        updated_at
                    FROM project_madden_guilds
                    ORDER BY
                        COALESCE(
                            guild_name,
                            league_name,
                            guild_id
                        )
                    """
                )

                rows = cur.fetchall()

        return [
            {
                "guild_id":
                    row[0],
                "guild_name":
                    row[1],
                "league_name":
                    row[2],
                "snallabot_league_id":
                    row[3],
                "platform":
                    row[4],
                "settings":
                    row[5]
                    if isinstance(
                        row[5],
                        dict
                    )
                    else {},
                "created_at":
                    row[6],
                "updated_at":
                    row[7]
            }
            for row in rows
        ]

    except Exception:
        return []


def save_guild_setup(
    guild_id,
    league_name,
    snallabot_league_id,
    platform,
    settings
):
    if not ensure_multi_server_db():
        return False

    guild_id = str(
        guild_id
        or ""
    ).strip()

    if not guild_id:
        return False

    payload = (
        Jsonb(
            settings
            if isinstance(
                settings,
                dict
            )
            else {}
        )
        if Jsonb is not None
        else json.dumps(
            settings
            if isinstance(
                settings,
                dict
            )
            else {}
        )
    )

    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=8
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE project_madden_guilds
                    SET
                        league_name = %s,
                        snallabot_league_id = %s,
                        platform = %s,
                        settings = %s,
                        updated_at = NOW()
                    WHERE guild_id = %s
                    """,
                    (
                        str(
                            league_name
                            or ""
                        ).strip(),
                        str(
                            snallabot_league_id
                            or ""
                        ).strip(),
                        str(
                            platform
                            or ""
                        ).strip(),
                        payload,
                        guild_id
                    )
                )

        return True

    except Exception as e:
        print(
            "SAVE GUILD SETUP ERROR:",
            repr(
                e
            )
        )
        return False


def rotate_guild_setup_token(
    guild_id
):
    if not ensure_multi_server_db():
        return None

    new_token = uuid.uuid4().hex

    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=8
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE project_madden_guilds
                    SET
                        setup_token = %s,
                        updated_at = NOW()
                    WHERE guild_id = %s
                    RETURNING setup_token
                    """,
                    (
                        new_token,
                        str(
                            guild_id
                        )
                    )
                )

                row = cur.fetchone()

        return (
            row[0]
            if row
            else None
        )

    except Exception:
        return None


def guild_setup_url(
    setup_token
):
    return (
        f"{PROJECT_MADDEN_BASE_URL}/dashboard/setup/"
        f"{setup_token}"
    )


def setup_link_secret():
    """
    Fast local signing secret for Discord /setup links.
    No database or external request is needed to generate the link,
    so Discord receives its response immediately.
    """
    explicit = os.environ.get(
        "PROJECT_MADDEN_SETUP_SECRET",
        ""
    ).strip()

    if explicit:
        return explicit

    return (
        discord_bot_token()
        or discord_public_key()
        or "project-madden-setup-fallback"
    )


def setup_link_signature(
    guild_id
):
    message = str(
        guild_id
        or ""
    ).encode(
        "utf-8"
    )

    secret = setup_link_secret().encode(
        "utf-8"
    )

    return hmac.new(
        secret,
        message,
        hashlib.sha256
    ).hexdigest()


def setup_start_url(
    guild_id
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    signature = setup_link_signature(
        guild_id
    )

    return (
        f"{PROJECT_MADDEN_BASE_URL}/dashboard/setup/start/"
        f"{guild_id}/{signature}"
    )


def valid_setup_start_signature(
    guild_id,
    signature
):
    expected = setup_link_signature(
        guild_id
    )

    return hmac.compare_digest(
        str(
            signature
            or ""
        ),
        expected
    )


def discord_required_permissions():
    return 117776


def discord_install_url(
    guild_id=None
):
    app_id = discord_application_id()

    if not app_id:
        return ""

    params = {
        "client_id": app_id,
        "permissions": discord_required_permissions(),
        "integration_type": 0,
        "scope": "bot applications.commands"
    }

    guild_id = str(
        guild_id
        or ""
    ).strip()

    if guild_id:
        params["guild_id"] = guild_id
        params["disable_guild_select"] = "true"

    return (
        "https://discord.com/oauth2/authorize?"
        + urlencode(
            params
        )
    )


def discord_api_headers():
    return {
        "Authorization":
            f"Bot {discord_bot_token()}",
        "Content-Type":
            "application/json",
        "User-Agent":
            "ProjectMaddenAnalytics/1.0"
    }


def discord_guild_api_diagnostic(
    guild_id
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    result = {
        "success": False,
        "bot_token_configured": bool(
            discord_bot_token()
        ),
        "guild_id": guild_id,
        "guild_access": False,
        "channels_access": False,
        "guild_status_code": None,
        "channels_status_code": None,
        "guild_name": None,
        "channel_count": 0,
        "error": None
    }

    if not discord_bot_token():
        result["error"] = (
            "DISCORD_BOT_TOKEN is not configured."
        )
        return result

    try:
        r = requests.get(
            f"https://discord.com/api/v10/guilds/{guild_id}",
            headers=discord_api_headers(),
            timeout=10
        )

        result[
            "guild_status_code"
        ] = r.status_code

        if r.status_code == 200:
            result[
                "guild_access"
            ] = True

            payload = r.json()

            result[
                "guild_name"
            ] = payload.get(
                "name"
            )

    except Exception as e:
        result[
            "guild_error"
        ] = str(
            e
        )

    try:
        r = requests.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=discord_api_headers(),
            timeout=10
        )

        result[
            "channels_status_code"
        ] = r.status_code

        if r.status_code == 200:
            result[
                "channels_access"
            ] = True

            payload = r.json()

            if isinstance(
                payload,
                list
            ):
                result[
                    "channel_count"
                ] = len(
                    payload
                )

    except Exception as e:
        result[
            "channels_error"
        ] = str(
            e
        )

    result[
        "success"
    ] = bool(
        result[
            "guild_access"
        ]
        and result[
            "channels_access"
        ]
    )

    if not result[
        "success"
    ]:
        status = (
            result.get(
                "guild_status_code"
            )
            or result.get(
                "channels_status_code"
            )
        )

        if status in [
            403,
            404
        ]:
            result[
                "error"
            ] = (
                "Project Madden's bot is not installed in this "
                "Discord server or cannot access it."
            )

        elif status == 401:
            result[
                "error"
            ] = (
                "Discord rejected the bot token."
            )

        else:
            result[
                "error"
            ] = (
                "Project Madden could not read this Discord server."
            )

    return result


def discord_member_can_manage_guild(
    interaction
):
    member = interaction.get(
        "member",
        {}
    )

    raw = str(
        member.get(
            "permissions",
            "0"
        )
        or "0"
    )

    try:
        permissions = int(
            raw
        )
    except Exception:
        permissions = 0

    administrator = bool(
        permissions
        & 8
    )

    manage_guild = bool(
        permissions
        & 32
    )

    return (
        administrator
        or manage_guild
        or discord_member_has_league_owner_role(
            interaction
        )
    )


def guild_config_summary(
    config
):
    if not config:
        return (
            "Project Madden has not been set up "
            "for this Discord server yet."
        )

    league_name = (
        config.get(
            "league_name"
        )
        or "Not connected"
    )

    league_id = (
        config.get(
            "snallabot_league_id"
        )
        or "Not set"
    )

    platform = (
        config.get(
            "platform"
        )
        or "Not set"
    )

    return (
        "🏈 **PROJECT MADDEN SERVER CONNECTION**\n"
        f"Server: **{config.get('guild_name') or config.get('guild_id')}**\n"
        f"League: **{league_name}**\n"
        f"Snallabot League ID: **{league_id}**\n"
        f"Platform: **{platform}**"
    )




# =========================================================
# PROJECT MADDEN DATA SOURCE STATUS
# =========================================================

PROJECT_MADDEN_DATA_SOURCE = "snallabot"
PROJECT_MADDEN_DIRECT_EA_STATUS = "coming_soon"


def ea_connector_configured():
    """
    Direct EA authentication is intentionally disabled.
    Project Madden currently uses Snallabot as its official Madden
    data source until a legitimate independent EA connector is available.
    """
    return False


def project_madden_data_source_status():
    return {
        "official_source":
            PROJECT_MADDEN_DATA_SOURCE,
        "snallabot_required":
            True,
        "direct_ea_connector":
            PROJECT_MADDEN_DIRECT_EA_STATUS,
        "direct_ea_enabled":
            False,
        "message":
            (
                "Snallabot is currently required for official Madden "
                "league data. Direct EA connection is coming soon."
            )
    }


# =========================================================
# FILE HELPERS
# =========================================================

# =========================================================
# V26 MULTI-SERVER STORAGE OVERRIDE
# Uses the app's already-proven persistent JSON layer instead
# of requiring a separate project_madden_guilds SQL table.
# =========================================================

GUILD_CONFIG_FILE = "project_madden_guilds.json"


def _load_guild_store():
    data = load_json_file(
        GUILD_CONFIG_FILE
    )

    if not isinstance(
        data,
        dict
    ):
        data = {}

    guilds = data.get(
        "guilds"
    )

    if not isinstance(
        guilds,
        dict
    ):
        guilds = {}

    data[
        "guilds"
    ] = guilds

    return data


def _save_guild_store(
    data
):
    save_json_file(
        GUILD_CONFIG_FILE,
        data
    )

    return True


def ensure_multi_server_db():
    """
    Compatibility name retained for older routes.
    Multi-server setup now uses the same persistent JSON backend as
    trades/HOF/receipts. This removes the fragile extra SQL table.
    """
    try:
        data = _load_guild_store()

        _save_guild_store(
            data
        )

        return True

    except Exception as e:
        print(
            "GUILD STORAGE ERROR:",
            repr(
                e
            )
        )

        return False


def ensure_guild_config(
    guild_id,
    guild_name=None
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    if not guild_id:
        return None

    try:
        store = _load_guild_store()

        guilds = store[
            "guilds"
        ]

        existing = guilds.get(
            guild_id
        )

        if not isinstance(
            existing,
            dict
        ):
            existing = {
                "guild_id":
                    guild_id,
                "guild_name":
                    guild_name,
                "setup_token":
                    uuid.uuid4().hex,
                "league_name":
                    "",
                "snallabot_league_id":
                    "",
                "platform":
                    "",
                "settings":
                    {},
                "created_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
                "updated_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat()
            }

        if (
            not guild_name
            and not existing.get(
                "guild_name"
            )
        ):
            # Fetching the Discord name is optional. Setup must still work
            # if Discord's guild endpoint is unavailable.
            try:
                guild_name = fetch_discord_guild_name(
                    guild_id
                )
            except Exception:
                guild_name = None

        if guild_name:
            existing[
                "guild_name"
            ] = guild_name

        if not existing.get(
            "setup_token"
        ):
            existing[
                "setup_token"
            ] = uuid.uuid4().hex

        if not isinstance(
            existing.get(
                "settings"
            ),
            dict
        ):
            existing[
                "settings"
            ] = {}

        existing[
            "updated_at"
        ] = datetime.now(
            timezone.utc
        ).isoformat()

        guilds[
            guild_id
        ] = existing

        _save_guild_store(
            store
        )

        return existing

    except Exception as e:
        print(
            "ENSURE GUILD CONFIG V26 ERROR:",
            repr(
                e
            )
        )

        return None


def get_guild_config(
    guild_id
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    if not guild_id:
        return None

    try:
        store = _load_guild_store()

        config = (
            store
            .get(
                "guilds",
                {}
            )
            .get(
                guild_id
            )
        )

        return (
            config
            if isinstance(
                config,
                dict
            )
            else None
        )

    except Exception:
        return None


def get_guild_config_by_token(
    setup_token
):
    setup_token = str(
        setup_token
        or ""
    ).strip()

    if not setup_token:
        return None

    try:
        store = _load_guild_store()

        for config in (
            store
            .get(
                "guilds",
                {}
            )
            .values()
        ):
            if (
                isinstance(
                    config,
                    dict
                )
                and str(
                    config.get(
                        "setup_token",
                        ""
                    )
                ) == setup_token
            ):
                return config

        return None

    except Exception:
        return None


def list_guild_configs():
    try:
        store = _load_guild_store()

        configs = [
            config
            for config in (
                store
                .get(
                    "guilds",
                    {}
                )
                .values()
            )
            if isinstance(
                config,
                dict
            )
        ]

        configs.sort(
            key=lambda item: str(
                item.get(
                    "guild_name"
                )
                or item.get(
                    "league_name"
                )
                or item.get(
                    "guild_id",
                    ""
                )
            ).lower()
        )

        return configs

    except Exception:
        return []


def save_guild_setup(
    guild_id,
    league_name,
    snallabot_league_id,
    platform,
    settings
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    if not guild_id:
        return False

    try:
        store = _load_guild_store()

        guilds = store[
            "guilds"
        ]

        config = guilds.get(
            guild_id
        )

        if not isinstance(
            config,
            dict
        ):
            config = ensure_guild_config(
                guild_id
            )

            if not config:
                return False

            store = _load_guild_store()

            guilds = store[
                "guilds"
            ]

            config = guilds.get(
                guild_id,
                {}
            )

        config[
            "league_name"
        ] = str(
            league_name
            or ""
        ).strip()

        config[
            "snallabot_league_id"
        ] = str(
            snallabot_league_id
            or ""
        ).strip()

        config[
            "platform"
        ] = str(
            platform
            or ""
        ).strip()

        config[
            "settings"
        ] = (
            dict(
                settings
            )
            if isinstance(
                settings,
                dict
            )
            else {}
        )

        config[
            "updated_at"
        ] = datetime.now(
            timezone.utc
        ).isoformat()

        guilds[
            guild_id
        ] = config

        _save_guild_store(
            store
        )

        return True

    except Exception as e:
        print(
            "SAVE GUILD SETUP V26 ERROR:",
            repr(
                e
            )
        )

        return False


def rotate_guild_setup_token(
    guild_id
):
    guild_id = str(
        guild_id
        or ""
    ).strip()

    if not guild_id:
        return None

    try:
        store = _load_guild_store()

        config = (
            store
            .get(
                "guilds",
                {}
            )
            .get(
                guild_id
            )
        )

        if not isinstance(
            config,
            dict
        ):
            config = ensure_guild_config(
                guild_id
            )

            if not config:
                return None

            store = _load_guild_store()

            config = (
                store
                .get(
                    "guilds",
                    {}
                )
                .get(
                    guild_id
                )
            )

        new_token = uuid.uuid4().hex

        config[
            "setup_token"
        ] = new_token

        config[
            "updated_at"
        ] = datetime.now(
            timezone.utc
        ).isoformat()

        store[
            "guilds"
        ][
            guild_id
        ] = config

        _save_guild_store(
            store
        )

        return new_token

    except Exception:
        return None



def local_json_path(filename):
    return os.path.join(
        DATA_DIR,
        filename
    )


def load_local_json_file(filename):
    path = local_json_path(
        filename
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


def save_local_json_file(
    filename,
    data
):
    path = local_json_path(
        filename
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            indent=2
        )


def persistent_storage_configured():
    return bool(
        DATABASE_URL
    )


def ensure_persistent_db():
    global PERSISTENT_DB_READY
    global PERSISTENT_DB_LAST_ERROR

    if PERSISTENT_DB_READY:
        return True

    if not DATABASE_URL:
        PERSISTENT_DB_LAST_ERROR = (
            "DATABASE_URL is not configured."
        )
        return False

    if psycopg is None:
        PERSISTENT_DB_LAST_ERROR = (
            "psycopg is not installed."
        )
        return False

    with PERSISTENT_DB_LOCK:
        if PERSISTENT_DB_READY:
            return True

        try:
            with psycopg.connect(
                DATABASE_URL,
                connect_timeout=8
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS
                        project_madden_persistent_json (
                            storage_key TEXT PRIMARY KEY,
                            payload JSONB NOT NULL,
                            updated_at TIMESTAMPTZ
                                NOT NULL
                                DEFAULT NOW()
                        )
                        """
                    )

            PERSISTENT_DB_READY = True
            PERSISTENT_DB_LAST_ERROR = None
            return True

        except Exception as e:
            PERSISTENT_DB_LAST_ERROR = str(e)
            print(
                "PERSISTENT DB INIT ERROR:",
                str(e)
            )
            return False


def load_persistent_json(
    filename
):
    global PERSISTENT_DB_LAST_ERROR

    if not ensure_persistent_db():
        return None

    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=8
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT payload
                    FROM project_madden_persistent_json
                    WHERE storage_key = %s
                    """,
                    (
                        filename,
                    )
                )

                row = cur.fetchone()

        if not row:
            return None

        return row[0]

    except Exception as e:
        PERSISTENT_DB_LAST_ERROR = str(e)
        print(
            "PERSISTENT DB READ ERROR:",
            filename,
            str(e)
        )
        return None


def save_persistent_json(
    filename,
    data
):
    global PERSISTENT_DB_LAST_ERROR

    if not ensure_persistent_db():
        return False

    try:
        payload = (
            Jsonb(data)
            if Jsonb is not None
            else json.dumps(data)
        )

        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=8
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO
                    project_madden_persistent_json (
                        storage_key,
                        payload,
                        updated_at
                    )
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (storage_key)
                    DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    """,
                    (
                        filename,
                        payload
                    )
                )

        PERSISTENT_DB_LAST_ERROR = None
        return True

    except Exception as e:
        PERSISTENT_DB_LAST_ERROR = str(e)
        print(
            "PERSISTENT DB WRITE ERROR:",
            filename,
            str(e)
        )
        return False


def migrate_local_file_to_db(
    filename,
    overwrite=False
):
    if (
        filename
        not in PERSISTENT_JSON_FILES
    ):
        return {
            "filename":
                filename,
            "migrated":
                False,
            "reason":
                "not_persistent_file"
        }

    if not ensure_persistent_db():
        return {
            "filename":
                filename,
            "migrated":
                False,
            "reason":
                "database_unavailable",
            "error":
                PERSISTENT_DB_LAST_ERROR
        }

    local_data = load_local_json_file(
        filename
    )

    if local_data is None:
        return {
            "filename":
                filename,
            "migrated":
                False,
            "reason":
                "no_local_data"
        }

    existing = load_persistent_json(
        filename
    )

    if (
        existing is not None
        and not overwrite
    ):
        return {
            "filename":
                filename,
            "migrated":
                False,
            "reason":
                "database_already_has_data"
        }

    ok = save_persistent_json(
        filename,
        local_data
    )

    return {
        "filename":
            filename,
        "migrated":
            bool(ok),
        "reason":
            (
                "migrated"
                if ok
                else "write_failed"
            )
    }


def load_json_file(filename):
    # These four data sets survive Render redeploys
    # when DATABASE_URL is configured.
    if (
        filename
        in PERSISTENT_JSON_FILES
    ):
        db_data = load_persistent_json(
            filename
        )

        if db_data is not None:
            return db_data

        # First deployment migration:
        # if a local JSON copy still exists and the DB
        # has no value yet, move it into Postgres.
        local_data = load_local_json_file(
            filename
        )

        if (
            local_data is not None
            and persistent_storage_configured()
        ):
            if save_persistent_json(
                filename,
                local_data
            ):
                return local_data

        return local_data

    return load_local_json_file(
        filename
    )


def save_json_file(
    filename,
    data
):
    if (
        filename
        in PERSISTENT_JSON_FILES
    ):
        if persistent_storage_configured():
            saved = save_persistent_json(
                filename,
                data
            )

            if saved:
                # Keep a local cache too. The database is
                # the permanent source of truth.
                try:
                    save_local_json_file(
                        filename,
                        data
                    )
                except Exception:
                    pass

                return

            print(
                "WARNING: Permanent DB write failed. "
                "Using temporary local fallback for",
                filename
            )

        # Temporary fallback keeps the app operating
        # if the DB has not been configured yet.
        save_local_json_file(
            filename,
            data
        )
        return

    save_local_json_file(
        filename,
        data
    )


def persistent_storage_status():
    db_ok = ensure_persistent_db()

    datasets = {}

    for filename in sorted(
        PERSISTENT_JSON_FILES
    ):
        local_exists = os.path.exists(
            local_json_path(
                filename
            )
        )

        db_exists = False

        if db_ok:
            try:
                db_exists = (
                    load_persistent_json(
                        filename
                    )
                    is not None
                )
            except Exception:
                db_exists = False

        datasets[
            filename
        ] = {
            "database":
                db_exists,
            "local_cache":
                local_exists
        }

    return {
        "configured":
            persistent_storage_configured(),
        "database_ready":
            db_ok,
        "driver_available":
            psycopg is not None,
        "last_error":
            PERSISTENT_DB_LAST_ERROR,
        "table":
            (
                "project_madden_persistent_json"
            ),
        "datasets":
            datasets
    }



@app.route(
    "/storage/status"
)
def storage_status_route():
    status = persistent_storage_status()

    return jsonify(
        status
    ), (
        200
        if status.get(
            "database_ready"
        )
        else 503
    )


@app.route(
    "/storage/migrate",
    methods=[
        "GET",
        "POST"
    ]
)
def storage_migrate_route():
    overwrite = str(
        request.args.get(
            "overwrite",
            ""
        )
    ).lower() in [
        "1",
        "true",
        "yes"
    ]

    results = [
        migrate_local_file_to_db(
            filename,
            overwrite=overwrite
        )
        for filename
        in sorted(
            PERSISTENT_JSON_FILES
        )
    ]

    return jsonify({
        "database_ready":
            ensure_persistent_db(),
        "overwrite":
            overwrite,
        "results":
            results
    })


@app.route(
    "/storage/backup"
)
def storage_backup_route():
    backup = {}

    for filename in sorted(
        PERSISTENT_JSON_FILES
    ):
        backup[
            filename
        ] = load_json_file(
            filename
        )

    return jsonify({
        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),
        "storage":
            "postgres"
            if persistent_storage_configured()
            else "temporary_local_fallback",
        "data":
            backup
    })


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



def detect_injury_info(record):
    if not isinstance(record, dict):
        return {"injured": False}

    def val(keys):
        return first_value(record, keys)

    injury = val([
        "injuryType","injuryName","injury","injury_type",
        "injury_name","injuryDescription","injuryDesc"
    ])
    status = val([
        "injuryStatus","injury_status","healthStatus",
        "playerStatus","player_status"
    ])
    weeks = val([
        "injuryLength","injuryWeeks","weeksRemaining",
        "injuryWeeksRemaining","weeksOut","injury_length",
        "injury_weeks","weeks_remaining"
    ])
    reserve = val([
        "injuryReserve","injuredReserve","isOnIR","onIR",
        "injury_reserve","injured_reserve"
    ])
    flag = val([
        "isInjured","injured","hasInjury","is_injured","has_injury"
    ])

    def truthy(x):
        if isinstance(x, bool):
            return x
        if isinstance(x, (int,float)):
            return x != 0
        return str(x or "").strip().lower() in {
            "1","true","yes","injured","out","ir",
            "injury reserve","injured reserve"
        }

    try:
        weeks_i = int(float(weeks)) if weeks is not None else None
    except Exception:
        weeks_i = None

    injury_text = str(injury or "").strip()
    status_text = str(status or "").strip()
    healthy_words = {"","none","healthy","uninjured","no injury","n/a","na","0"}

    injured = (
        truthy(flag)
        or truthy(reserve)
        or injury_text.lower() not in healthy_words
        or status_text.lower() in {
            "injured","out","ir","injury reserve",
            "injured reserve","questionable","doubtful"
        }
        or (weeks_i is not None and weeks_i > 0)
    )

    return {
        "injured": bool(injured),
        "injury": injury_text if injury_text.lower() not in healthy_words else None,
        "status": status_text or None,
        "weeks_remaining": weeks_i,
        "reserve": truthy(reserve),
        "source_fields": {
            str(k): v for k,v in record.items()
            if "injur" in str(k).lower()
        }
    }


def injury_webhook_url():
    return os.environ.get("INJURY_DISCORD_WEBHOOK_URL","").strip()


def load_injury_history():
    data=load_json_file(INJURY_HISTORY_FILE)
    if not isinstance(data,dict):
        data={}
    data.setdefault("current_by_team",{})
    data.setdefault("events",[])
    return data


def save_injury_history(data):
    data["events"]=list(data.get("events",[]))[-1000:]
    save_json_file(INJURY_HISTORY_FILE,data)


def build_injuries_from_roster_data(team_id, roster_data):
    team=team_by_id(team_id) or {}
    team_name=team.get("name") or team.get("displayName") or str(team_id)
    items=[]
    seen=set()

    for record in recursive_records(roster_data):
        name=detect_player_name(record)
        if not name:
            continue
        info=detect_injury_info(record)
        if not info.get("injured"):
            continue
        pos=detect_position(record)
        key=(name.lower(),str(pos or ""))
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "team_id":str(team_id),
            "team":team_name,
            "player":name,
            "position":pos,
            "overall":detect_overall(record),
            "injury":info.get("injury"),
            "status":info.get("status"),
            "weeks_remaining":info.get("weeks_remaining"),
            "reserve":info.get("reserve"),
            "source_fields":info.get("source_fields",{})
        })

    items.sort(key=lambda x:(-(x.get("overall") or 0),x.get("player","")))
    return items


def injury_identity(item):
    return f"{str(item.get('player','')).lower()}|{str(item.get('position','')).upper()}"


def injury_summary_label(item):
    detail=item.get("injury") or item.get("status") or "Injured"
    weeks=item.get("weeks_remaining")
    if weeks is not None and int(weeks)>0:
        detail += f" • {weeks} week" + ("" if int(weeks)==1 else "s")
    if item.get("reserve"):
        detail += " • IR"
    return detail


def send_injury_event(event):
    webhook=injury_webhook_url()
    if not webhook:
        return {"sent":False,"error":"INJURY_DISCORD_WEBHOOK_URL is not configured."}

    p=event.get("player",{})
    kind=event.get("event_type")
    title={
        "new":"🚑 PROJECT MADDEN INJURY REPORT",
        "updated":"🩺 PROJECT MADDEN INJURY UPDATE",
        "recovered":"✅ PROJECT MADDEN HEALTH UPDATE"
    }.get(kind,"🩺 PROJECT MADDEN INJURY UPDATE")

    desc=(
        f"**{p.get('player')}** ({p.get('team')}) is now listed as injured."
        if kind=="new" else
        f"**{p.get('player')}** ({p.get('team')}) is no longer listed as injured."
        if kind=="recovered" else
        f"**{p.get('player')}** ({p.get('team')}) has an updated injury status."
    )

    payload={
        "username":"Project Madden Injury Report",
        "embeds":[{
            "title":title,
            "description":desc,
            "fields":[
                {"name":"Player","value":f"{p.get('position') or '—'} {p.get('player')}","inline":True},
                {"name":"OVR","value":str(p.get("overall") or "—"),"inline":True},
                {"name":"Status","value":injury_summary_label(p),"inline":False}
            ],
            "footer":{"text":"Detected from the latest Snallabot/Madden roster export"}
        }]
    }
    try:
        r=requests.post(webhook,json=payload,timeout=15)
        return {"sent":r.status_code in [200,204],"status_code":r.status_code,
                "error":"" if r.status_code in [200,204] else r.text[:500]}
    except Exception as e:
        return {"sent":False,"error":str(e)}


def process_team_injury_export(team_id, roster_data):
    history=load_injury_history()
    previous=history["current_by_team"].get(str(team_id),[])
    current=build_injuries_from_roster_data(team_id,roster_data)
    old_map={injury_identity(x):x for x in previous if isinstance(x,dict)}
    new_map={injury_identity(x):x for x in current if isinstance(x,dict)}
    events=[]

    for key,item in new_map.items():
        old=old_map.get(key)
        if old is None:
            kind="new"
        else:
            old_sig=(old.get("injury"),old.get("status"),old.get("weeks_remaining"),bool(old.get("reserve")))
            new_sig=(item.get("injury"),item.get("status"),item.get("weeks_remaining"),bool(item.get("reserve")))
            if old_sig==new_sig:
                continue
            kind="updated"
        events.append({
            "event_type":kind,
            "detected_at":datetime.now(timezone.utc).isoformat(),
            "team_id":str(team_id),
            "player":item
        })

    for key,item in old_map.items():
        if key not in new_map:
            events.append({
                "event_type":"recovered",
                "detected_at":datetime.now(timezone.utc).isoformat(),
                "team_id":str(team_id),
                "player":item
            })

    history["current_by_team"][str(team_id)]=current
    history["events"].extend(events)
    save_injury_history(history)

    notices=[]
    for event in events:
        notices.append(send_injury_event(event))
        p=event.get("player",{})
        if event.get("event_type")=="new" and int(p.get("overall") or 0)>=INJURY_MAJOR_OVR:
            try:
                send_analyst_embed(
                    "🚑 INJURY REACTION",
                    f"**{p.get('player')}** ({p.get('team')}) is listed with **{injury_summary_label(p)}**. "
                    f"At {p.get('overall')} OVR, that absence can change the entire game plan."
                )
            except Exception:
                pass

    return {"team_id":str(team_id),"current_injuries":current,"events":events,"notifications":notices}


def all_current_injuries():
    data=load_injury_history()
    items=[]
    for group in data.get("current_by_team",{}).values():
        if isinstance(group,list):
            items.extend(x for x in group if isinstance(x,dict))
    items.sort(key=lambda x:(-(x.get("overall") or 0),x.get("team",""),x.get("player","")))
    return items


def injury_report_text(limit=25):
    items=all_current_injuries()
    if not items:
        return "🚑 **PROJECT MADDEN INJURY REPORT**\nNo injuries are currently detected from saved roster exports."
    lines=["🚑 **PROJECT MADDEN INJURY REPORT**"]
    for x in items[:limit]:
        lines.append(
            f"**{x.get('team')} — {x.get('player')}** "
            f"({x.get('position') or '—'}, {x.get('overall') or '—'} OVR) • {injury_summary_label(x)}"
        )
    return "\n".join(lines)


@app.route("/injuries")
def injuries_route():
    data=load_injury_history()
    return jsonify({
        "app_version":PROJECT_MADDEN_APP_VERSION,
        "injury_count":len(all_current_injuries()),
        "injuries":all_current_injuries(),
        "recent_events":list(reversed(data.get("events",[])))[:50]
    })


@app.route("/injuries/status")
def injuries_status_route():
    return jsonify({
        "webhook_configured":bool(injury_webhook_url()),
        "persistent_storage":INJURY_HISTORY_FILE in PERSISTENT_JSON_FILES,
        "major_injury_ovr":INJURY_MAJOR_OVR,
        "current_injury_count":len(all_current_injuries())
    })


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

        injury_info = detect_injury_info(record)

        players.append({
            "name": name,
            "position": position,
            "overall": overall,
            "age": age,
            "dev": detect_dev(record),
            "injured": injury_info.get("injured"),
            "injury": injury_info.get("injury"),
            "injury_status": injury_info.get("status"),
            "injury_weeks_remaining": injury_info.get("weeks_remaining"),
            "injury_reserve": injury_info.get("reserve")
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



def load_trade_history():
    history = load_json_file(TRADE_HISTORY_FILE)
    return history if isinstance(history, list) else []


def save_trade_history(history):
    save_json_file(
        TRADE_HISTORY_FILE,
        history[-1000:]
    )


def grade_trade_winner(trade):
    grade_order = {
        "A+": 7, "A": 6, "B+": 5, "B": 4,
        "C+": 3, "C": 2, "D": 1, "F": 0,
    }

    grade_a = (
        trade.get("team_a_grade", {}).get("grade", "C")
        if isinstance(trade.get("team_a_grade"), dict)
        else "C"
    )
    grade_b = (
        trade.get("team_b_grade", {}).get("grade", "C")
        if isinstance(trade.get("team_b_grade"), dict)
        else "C"
    )

    score_a = grade_order.get(grade_a, 2)
    score_b = grade_order.get(grade_b, 2)

    if score_a > score_b:
        winner = trade.get("team_a")
    elif score_b > score_a:
        winner = trade.get("team_b")
    else:
        winner = "EVEN"

    return {
        "winner": winner,
        "team_a_grade": grade_a,
        "team_b_grade": grade_b,
        "method": "initial League Office trade grades",
        "note": (
            "This first version tracks the winner from the original trade grades. "
            "Later we can re-grade old trades using post-trade player production "
            "once enough historical stat snapshots are stored."
        ),
    }


def trade_history_upsert(analysis, status=None):
    history = load_trade_history()

    trade_id = str(
        analysis.get("trade_id", "")
    ).strip()

    if not trade_id:
        return

    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "trade_id": trade_id,
        "created_at": analysis.get("created_at", now),
        "updated_at": now,
        "team_a": analysis.get("team_a"),
        "team_b": analysis.get("team_b"),
        "team_a_sends": analysis.get("team_a_sends", []),
        "team_b_sends": analysis.get("team_b_sends", []),
        "team_a_grade": analysis.get("team_a_grade", {}),
        "team_b_grade": analysis.get("team_b_grade", {}),
        "league_office": analysis.get("trade_committee", {}),
        "status": status or (
            analysis.get("trade_committee", {}).get("decision", "PROPOSED")
            if isinstance(analysis.get("trade_committee"), dict)
            else "PROPOSED"
        ),
    }

    entry["winner_tracker"] = grade_trade_winner(entry)

    replaced = False
    for index, old in enumerate(history):
        if str(old.get("trade_id", "")) == trade_id:
            history[index] = entry
            replaced = True
            break

    if not replaced:
        history.append(entry)

    save_trade_history(history)


def refresh_trade_winner_tracker():
    history = load_trade_history()

    for trade in history:
        trade["winner_tracker"] = grade_trade_winner(trade)

    save_trade_history(history)
    return history


@app.route("/analyst/trade-history")
def analyst_trade_history():
    history = refresh_trade_winner_tracker()

    return jsonify({
        "trade_count": len(history),
        "trades": list(reversed(history)),
    })



def post_trade_to_logs(analysis):
    webhook_url = get_trade_logs_webhook()

    if not webhook_url:
        return {
            "sent": False,
            "skipped": True,
            "reason": "TRADE_LOGS_DISCORD_WEBHOOK_URL not configured"
        }

    review = analysis.get("trade_committee", {})
    team_a = analysis.get("team_a", "Team A")
    team_b = analysis.get("team_b", "Team B")
    trade_id = analysis.get("trade_id", "Unknown")

    def format_assets(assets):
        if not assets:
            return "None"

        return "\n".join(
            "• " + format_trade_card_asset(asset)
            for asset in assets
        )[:1024]

    grade_a = (
        analysis.get("team_a_grade", {}).get("grade", "—")
        if isinstance(analysis.get("team_a_grade"), dict)
        else "—"
    )

    grade_b = (
        analysis.get("team_b_grade", {}).get("grade", "—")
        if isinstance(analysis.get("team_b_grade"), dict)
        else "—"
    )

    avatar_url = (
        "https://project-madden-analytics.onrender.com/"
        "assets/project-madden-league-office.jpeg"
    )

    payload = {
        "username": "Project Madden Trade Logs",
        "avatar_url": avatar_url,
        "embeds": [
            {
                "title": "📚 TRADE LOG",
                "description": (
                    f"**{team_a} ↔ {team_b}**\n"
                    f"Trade ID: `{trade_id}`"
                ),
                "fields": [
                    {
                        "name": f"{team_a} Sends",
                        "value": format_assets(
                            analysis.get("team_a_sends", [])
                        ),
                        "inline": False
                    },
                    {
                        "name": f"{team_b} Sends",
                        "value": format_assets(
                            analysis.get("team_b_sends", [])
                        ),
                        "inline": False
                    },
                    {
                        "name": "📊 Trade Grades",
                        "value": (
                            f"**{team_a}:** {grade_a}\n"
                            f"**{team_b}:** {grade_b}"
                        ),
                        "inline": False
                    },
                    {
                        "name": "🏛️ League Office Review V2",
                        "value": (
                            f"**{review.get('decision', 'UNKNOWN')}**\n"
                            f"Fairness Score: "
                            f"{review.get('fairness_score', '—')}/100\n"
                            f"Value Gap: "
                            f"{review.get('value_gap_percent', '—')}%"
                        ),
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "Project Madden • Permanent Trade Log"
                }
            }
        ]
    }

    screenshot_url = str(
        analysis.get("trade_screenshot_url", "")
    ).strip()

    if screenshot_url:
        payload["embeds"].append({
            "title": "📸 Madden Trade Screen",
            "image": {"url": screenshot_url}
        })

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
                    f"Discord returned {response.status_code}: "
                    f"{response.text[:500]}"
                )
            }

        return {"sent": True}

    except Exception as e:
        return {
            "sent": False,
            "error": str(e)
        }


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
# PLAYOFF RACE / CLINCHING SCENARIOS
# =========================================================

NFL_ALIGNMENT = {
    "AFC": {
        "East": ["BUF", "MIA", "NE", "NYJ"],
        "North": ["BAL", "CIN", "CLE", "PIT"],
        "South": ["HOU", "IND", "JAX", "TEN"],
        "West": ["DEN", "KC", "LV", "LAC"],
    },
    "NFC": {
        "East": ["DAL", "NYG", "PHI", "WAS"],
        "North": ["CHI", "DET", "GB", "MIN"],
        "South": ["ATL", "CAR", "NO", "TB"],
        "West": ["ARI", "LAR", "SF", "SEA"],
    },
}


def canonical_team_abbr(team):
    value = str(
        team.get(
            "abbr",
            ""
        )
        or ""
    ).upper().strip()

    aliases = {
        "ARZ": "ARI",
        "JAC": "JAX",
        "LVR": "LV",
        "SD": "LAC",
        "STL": "LAR",
        "OAK": "LV",
    }

    return aliases.get(
        value,
        value
    )


def team_alignment(team):
    abbr = canonical_team_abbr(
        team
    )

    for conference, divisions in NFL_ALIGNMENT.items():
        for division, teams in divisions.items():
            if abbr in teams:
                return {
                    "conference":
                        conference,
                    "division":
                        division
                }

    return {
        "conference":
            None,
        "division":
            None
    }


def playoff_sort_key(team):
    return (
        -float(
            team.get(
                "win_pct",
                0
            )
            or 0
        ),
        -int(
            team.get(
                "wins",
                0
            )
            or 0
        ),
        -float(
            team.get(
                "point_diff",
                0
            )
            or 0
        ),
        -int(
            team.get(
                "overall",
                0
            )
            or 0
        ),
    )


def build_conference_playoff_picture(
    conference
):
    standings = normalize_standings()

    teams = []

    for team in standings:
        alignment = team_alignment(
            team
        )

        if (
            alignment.get(
                "conference"
            )
            != conference
        ):
            continue

        item = dict(
            team
        )

        item[
            "conference"
        ] = conference

        item[
            "division"
        ] = alignment.get(
            "division"
        )

        teams.append(
            item
        )

    if not teams:
        return {
            "conference":
                conference,
            "seeds":
                [],
            "division_leaders":
                [],
            "wild_cards":
                [],
            "bubble":
                [],
            "elimination_danger":
                [],
        }

    # If Snallabot exposes seeds, respect them first.
    seeded = [
        team
        for team in teams
        if (
            team.get(
                "playoff_seed"
            )
            is not None
            and 1
            <= int(
                team.get(
                    "playoff_seed"
                )
            )
            <= 7
        )
    ]

    division_leaders = []

    for division in NFL_ALIGNMENT.get(
        conference,
        {}
    ):
        division_teams = [
            team
            for team in teams
            if team.get(
                "division"
            ) == division
        ]

        if not division_teams:
            continue

        ranked = sorted(
            division_teams,
            key=playoff_sort_key
        )

        leader = dict(
            ranked[0]
        )
        leader[
            "division_leader"
        ] = True

        division_leaders.append(
            leader
        )

    if len(seeded) >= 7:
        seeds = sorted(
            seeded,
            key=lambda team: int(
                team.get(
                    "playoff_seed"
                )
            )
        )[:7]
    else:
        # NFL-style approximation:
        # four division leaders first, then three best remaining teams.
        div_ids = {
            str(
                team.get(
                    "team_id"
                )
            )
            for team in division_leaders
        }

        division_leaders = sorted(
            division_leaders,
            key=playoff_sort_key
        )

        remaining = [
            team
            for team in teams
            if str(
                team.get(
                    "team_id"
                )
            )
            not in div_ids
        ]

        remaining = sorted(
            remaining,
            key=playoff_sort_key
        )

        seeds = (
            division_leaders[:4]
            + remaining[:3]
        )

        for index, team in enumerate(
            seeds,
            start=1
        ):
            team[
                "projected_seed"
            ] = index

    seed_ids = {
        str(
            team.get(
                "team_id"
            )
        )
        for team in seeds
    }

    remaining = [
        team
        for team in sorted(
            teams,
            key=playoff_sort_key
        )
        if str(
            team.get(
                "team_id"
            )
        )
        not in seed_ids
    ]

    bubble = remaining[:4]

    elimination_danger = []

    if teams:
        max_games = max(
            int(
                team.get(
                    "games",
                    0
                )
                or 0
            )
            for team in teams
        )

        # Only make stronger elimination language later in the season.
        if max_games >= 10:
            cutoff_wins = (
                int(
                    seeds[-1].get(
                        "wins",
                        0
                    )
                    or 0
                )
                if len(
                    seeds
                ) >= 7
                else 0
            )

            for team in remaining:
                games = int(
                    team.get(
                        "games",
                        0
                    )
                    or 0
                )
                wins = int(
                    team.get(
                        "wins",
                        0
                    )
                    or 0
                )

                remaining_games = max(
                    0,
                    17 - games
                )

                max_possible = (
                    wins
                    + remaining_games
                )

                danger = None

                if (
                    max_possible
                    < cutoff_wins
                ):
                    danger = (
                        "mathematically eliminated by current "
                        "win-count projection"
                    )
                elif (
                    max_possible
                    <= cutoff_wins + 1
                ):
                    danger = (
                        "near elimination — almost no margin for error"
                    )
                elif (
                    wins
                    <= cutoff_wins - 2
                    and games >= 12
                ):
                    danger = (
                        "must stack wins and needs help"
                    )

                if danger:
                    elimination_danger.append({
                        "team":
                            team.get(
                                "team"
                            ),
                        "record":
                            (
                                f"{team.get('wins', 0)}-"
                                f"{team.get('losses', 0)}"
                            ),
                        "status":
                            danger
                    })

    return {
        "conference":
            conference,
        "seeds":
            seeds,
        "division_leaders":
            division_leaders,
        "wild_cards":
            seeds[4:7]
            if len(
                seeds
            ) >= 5
            else [],
        "bubble":
            bubble,
        "elimination_danger":
            elimination_danger[:5],
    }


def build_projected_playoff_matchups():
    results = {}

    for conference in [
        "AFC",
        "NFC"
    ]:
        picture = (
            build_conference_playoff_picture(
                conference
            )
        )

        seeds = picture.get(
            "seeds",
            []
        )

        if len(seeds) < 7:
            results[
                conference
            ] = []
            continue

        def seed_num(team, fallback):
            return int(
                team.get(
                    "playoff_seed",
                    team.get(
                        "projected_seed",
                        fallback
                    )
                )
                or fallback
            )

        by_seed = {
            seed_num(
                team,
                index
            ):
                team
            for index, team in enumerate(
                seeds,
                start=1
            )
        }

        matchups = []

        for high, low in [
            (2, 7),
            (3, 6),
            (4, 5),
        ]:
            a = by_seed.get(
                high
            )
            b = by_seed.get(
                low
            )

            if a and b:
                matchups.append({
                    "higher_seed":
                        high,
                    "lower_seed":
                        low,
                    "matchup":
                        (
                            f"#{low} {b.get('team')} @ "
                            f"#{high} {a.get('team')}"
                        )
                })

        bye = by_seed.get(
            1
        )

        if bye:
            matchups.insert(
                0,
                {
                    "higher_seed":
                        1,
                    "lower_seed":
                        None,
                    "matchup":
                        (
                            f"#1 {bye.get('team')} — FIRST-ROUND BYE"
                        )
                }
            )

        results[
            conference
        ] = matchups

    return results


def build_clinching_scenarios():
    scenarios = []

    for conference in [
        "AFC",
        "NFC"
    ]:
        picture = (
            build_conference_playoff_picture(
                conference
            )
        )

        seeds = picture.get(
            "seeds",
            []
        )

        bubble = picture.get(
            "bubble",
            []
        )

        if not seeds:
            continue

        max_games = max(
            [
                int(
                    team.get(
                        "games",
                        0
                    )
                    or 0
                )
                for team in (
                    seeds + bubble
                )
            ]
            or [0]
        )

        # Avoid pretending exact tiebreaker clinches early.
        if max_games < 10:
            continue

        cutoff = (
            seeds[-1]
            if len(
                seeds
            ) >= 7
            else None
        )

        cutoff_wins = (
            int(
                cutoff.get(
                    "wins",
                    0
                )
                or 0
            )
            if cutoff
            else 0
        )

        for team in seeds:
            games = int(
                team.get(
                    "games",
                    0
                )
                or 0
            )

            wins = int(
                team.get(
                    "wins",
                    0
                )
                or 0
            )

            seed = int(
                team.get(
                    "playoff_seed",
                    team.get(
                        "projected_seed",
                        0
                    )
                )
                or 0
            )

            status = None

            if (
                games >= 15
                and seed <= 4
                and wins >= cutoff_wins + 2
            ):
                status = (
                    "can put itself in strong division-clinch position "
                    "with another win"
                )
            elif (
                games >= 14
                and seed <= 7
            ):
                status = (
                    "controls its own path: keep winning and protect the seed"
                )
            elif (
                games >= 12
                and seed >= 6
            ):
                status = (
                    "must-win territory — a loss could drop it below the line"
                )

            if status:
                scenarios.append({
                    "conference":
                        conference,
                    "team":
                        team.get(
                            "team"
                        ),
                    "seed":
                        seed,
                    "record":
                        (
                            f"{team.get('wins', 0)}-"
                            f"{team.get('losses', 0)}"
                        ),
                    "scenario":
                        status,
                    "note":
                        (
                            "Projection based on current standings. "
                            "Exact NFL tiebreaker clinches require full head-to-head "
                            "and conference/division tiebreaker data."
                        )
                })

        for team in bubble[:2]:
            games = int(
                team.get(
                    "games",
                    0
                )
                or 0
            )

            if games >= 12:
                scenarios.append({
                    "conference":
                        conference,
                    "team":
                        team.get(
                            "team"
                        ),
                    "seed":
                        None,
                    "record":
                        (
                            f"{team.get('wins', 0)}-"
                            f"{team.get('losses', 0)}"
                        ),
                    "scenario":
                        (
                            "needs wins plus help from teams currently "
                            "holding wild-card spots"
                        ),
                    "note":
                        "Bubble projection based on current standings."
                })

    return scenarios


def build_playoff_game_of_week(
    season_type,
    week_number
):
    predictions = (
        build_weekly_game_predictions(
            season_type,
            week_number
        )
    )

    if not predictions:
        return None

    standings = normalize_standings()

    by_team = {
        team.get(
            "team"
        ):
            team
        for team in standings
    }

    best = None
    best_score = -1

    for game in predictions:
        away = by_team.get(
            game.get(
                "away"
            ),
            {}
        )
        home = by_team.get(
            game.get(
                "home"
            ),
            {}
        )

        score = 0
        reasons = []

        for team in [
            away,
            home
        ]:
            seed = team.get(
                "playoff_seed"
            )

            conf_rank = team.get(
                "conference_rank"
            )

            if (
                seed is not None
                and int(
                    seed
                )
                <= 7
            ):
                score += 5
                reasons.append(
                    f"{team.get('team')} currently in playoff position"
                )
            elif (
                conf_rank is not None
                and int(
                    conf_rank
                )
                <= 10
            ):
                score += 3
                reasons.append(
                    f"{team.get('team')} is in the conference race"
                )

            games = int(
                team.get(
                    "games",
                    0
                )
                or 0
            )

            if games >= 12:
                score += 2

        away_align = team_alignment(
            away
        )
        home_align = team_alignment(
            home
        )

        if (
            away_align.get(
                "division"
            )
            and away_align
            == home_align
        ):
            score += 4
            reasons.append(
                "division matchup"
            )

        if score > best_score:
            best_score = score
            best = {
                "matchup":
                    game.get(
                        "matchup"
                    ),
                "score":
                    score,
                "reasons":
                    reasons[:4],
                "favorite":
                    game.get(
                        "favorite"
                    ),
                "confidence":
                    game.get(
                        "confidence"
                    )
            }

    if (
        best
        and best_score
        >= 4
    ):
        return best

    return None


def build_playoff_race(
    season_type=None,
    week_number=None
):
    return {
        "AFC":
            build_conference_playoff_picture(
                "AFC"
            ),
        "NFC":
            build_conference_playoff_picture(
                "NFC"
            ),
        "projected_matchups":
            build_projected_playoff_matchups(),
        "clinching_scenarios":
            build_clinching_scenarios(),
        "game_of_the_week":
            (
                build_playoff_game_of_week(
                    season_type,
                    week_number
                )
                if (
                    season_type is not None
                    and week_number is not None
                )
                else None
            )
    }


@app.route(
    "/analyst/playoff-race"
)
def playoff_race_route():
    season_type = request.args.get(
        "season_type",
        "reg"
    )

    try:
        week_number = int(
            request.args.get(
                "week",
                1
            )
        )
    except Exception:
        week_number = 1

    return jsonify(
        build_playoff_race(
            season_type,
            week_number
        )
    )


# =========================================================
# RIVALRY TRACKER / RIVALRY WEEK
# =========================================================

RIVALRY_HISTORY_FILE = "rivalry_history.json"


def team_owner_name(
    team_id
):
    team = team_by_id(
        team_id
    )

    if not team:
        return None

    return (
        team.get(
            "userName"
        )
        or team.get(
            "username"
        )
        or team.get(
            "user"
        )
        or team.get(
            "owner"
        )
        or team.get(
            "coach"
        )
    )


def load_rivalry_history():
    data = load_json_file(
        RIVALRY_HISTORY_FILE
    )

    if not isinstance(
        data,
        dict
    ):
        data = {}

    data.setdefault(
        "games",
        []
    )

    data.setdefault(
        "custom_names",
        {}
    )

    return data


def save_rivalry_history(
    data
):
    games = data.get(
        "games",
        []
    )

    if not isinstance(
        games,
        list
    ):
        games = []

    data[
        "games"
    ] = games[-5000:]

    save_json_file(
        RIVALRY_HISTORY_FILE,
        data
    )


def rivalry_pair_key(
    owner_a,
    owner_b
):
    values = sorted([
        str(
            owner_a
        ),
        str(
            owner_b
        )
    ])

    return (
        values[0]
        + "||"
        + values[1]
    )


def record_rivalry_week(
    season_type,
    week_number
):
    schedule_data = load_weekly_data(
        season_type,
        week_number,
        "schedules"
    )

    if not schedule_data:
        return 0

    data = load_rivalry_history()
    games = data.get(
        "games",
        []
    )

    existing = {
        (
            str(
                item.get(
                    "season_type"
                )
            ),
            int(
                item.get(
                    "week",
                    0
                )
                or 0
            ),
            str(
                item.get(
                    "schedule_id"
                )
            )
        )
        for item in games
        if isinstance(
            item,
            dict
        )
    }

    created = 0

    for game in schedule_data.get(
        "gameScheduleInfoList",
        []
    ):
        if not game_looks_completed(
            game
        ):
            continue

        schedule_id = game.get(
            "scheduleId"
        )

        key = (
            str(
                season_type
            ),
            int(
                week_number
            ),
            str(
                schedule_id
            )
        )

        if key in existing:
            continue

        away_id = game.get(
            "awayTeamId"
        )
        home_id = game.get(
            "homeTeamId"
        )

        away_owner = team_owner_name(
            away_id
        )
        home_owner = team_owner_name(
            home_id
        )

        # CPU games are not useful for user rivalries.
        if (
            not away_owner
            or not home_owner
        ):
            continue

        if (
            str(
                away_owner
            ).strip().lower()
            == str(
                home_owner
            ).strip().lower()
        ):
            continue

        away_team = safe_team_name(
            away_id
        )
        home_team = safe_team_name(
            home_id
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

        if away_score > home_score:
            winner_owner = away_owner
            loser_owner = home_owner
            winner_team = away_team
            margin = (
                away_score
                - home_score
            )
        elif home_score > away_score:
            winner_owner = home_owner
            loser_owner = away_owner
            winner_team = home_team
            margin = (
                home_score
                - away_score
            )
        else:
            winner_owner = None
            loser_owner = None
            winner_team = None
            margin = 0

        games.append({
            "season_type":
                season_type,
            "week":
                week_number,
            "schedule_id":
                schedule_id,
            "away_owner":
                away_owner,
            "home_owner":
                home_owner,
            "away_team":
                away_team,
            "home_team":
                home_team,
            "away_score":
                away_score,
            "home_score":
                home_score,
            "winner_owner":
                winner_owner,
            "loser_owner":
                loser_owner,
            "winner_team":
                winner_team,
            "margin":
                margin,
            "pair_key":
                rivalry_pair_key(
                    away_owner,
                    home_owner
                ),
            "recorded_at":
                datetime.now(
                    timezone.utc
                ).isoformat()
        })

        existing.add(
            key
        )

        created += 1

    data[
        "games"
    ] = games

    save_rivalry_history(
        data
    )

    return created


def build_rivalry_summary(
    owner_a,
    owner_b
):
    data = load_rivalry_history()

    pair_key = rivalry_pair_key(
        owner_a,
        owner_b
    )

    games = [
        item
        for item in data.get(
            "games",
            []
        )
        if item.get(
            "pair_key"
        ) == pair_key
    ]

    games.sort(
        key=lambda item: (
            str(
                item.get(
                    "season_type",
                    ""
                )
            ),
            int(
                item.get(
                    "week",
                    0
                )
                or 0
            )
        )
    )

    wins = {
        str(
            owner_a
        ):
            0,
        str(
            owner_b
        ):
            0
    }

    total_points = {
        str(
            owner_a
        ):
            0,
        str(
            owner_b
        ):
            0
    }

    memorable = []
    biggest = None

    for item in games:
        away_owner = str(
            item.get(
                "away_owner"
            )
        )
        home_owner = str(
            item.get(
                "home_owner"
            )
        )

        total_points[
            away_owner
        ] = (
            total_points.get(
                away_owner,
                0
            )
            + int(
                item.get(
                    "away_score",
                    0
                )
                or 0
            )
        )

        total_points[
            home_owner
        ] = (
            total_points.get(
                home_owner,
                0
            )
            + int(
                item.get(
                    "home_score",
                    0
                )
                or 0
            )
        )

        winner = item.get(
            "winner_owner"
        )

        if winner:
            wins[
                str(
                    winner
                )
            ] = (
                wins.get(
                    str(
                        winner
                    ),
                    0
                )
                + 1
            )

        margin = int(
            item.get(
                "margin",
                0
            )
            or 0
        )

        if (
            biggest is None
            or margin
            > int(
                biggest.get(
                    "margin",
                    -1
                )
            )
        ):
            biggest = item

        if (
            margin <= 3
            or margin >= 21
        ):
            memorable.append(
                item
            )

    current_streak = None

    if games:
        latest_winner = games[
            -1
        ].get(
            "winner_owner"
        )

        if latest_winner:
            count = 0

            for item in reversed(
                games
            ):
                if (
                    item.get(
                        "winner_owner"
                    )
                    == latest_winner
                ):
                    count += 1
                else:
                    break

            current_streak = {
                "owner":
                    latest_winner,
                "wins":
                    count
            }

    custom_name = (
        data.get(
            "custom_names",
            {}
        ).get(
            pair_key
        )
    )

    return {
        "pair_key":
            pair_key,
        "name":
            custom_name,
        "owner_a":
            owner_a,
        "owner_b":
            owner_b,
        "meetings":
            len(
                games
            ),
        "wins":
            wins,
        "total_points":
            total_points,
        "current_streak":
            current_streak,
        "biggest_win":
            biggest,
        "latest_meeting":
            (
                games[-1]
                if games
                else None
            ),
        "memorable_games":
            memorable[-5:],
        "games":
            games[-20:]
    }


def build_top_rivalries(
    limit=8
):
    data = load_rivalry_history()

    pairs = {}

    for item in data.get(
        "games",
        []
    ):
        pair_key = item.get(
            "pair_key"
        )

        if not pair_key:
            continue

        pairs.setdefault(
            pair_key,
            {
                "owners": (
                    item.get(
                        "away_owner"
                    ),
                    item.get(
                        "home_owner"
                    )
                ),
                "meetings":
                    0,
                "close_games":
                    0,
                "total_margin":
                    0,
            }
        )

        pairs[
            pair_key
        ][
            "meetings"
        ] += 1

        margin = int(
            item.get(
                "margin",
                0
            )
            or 0
        )

        pairs[
            pair_key
        ][
            "total_margin"
        ] += margin

        if margin <= 7:
            pairs[
                pair_key
            ][
                "close_games"
            ] += 1

    ranked = []

    for pair_key, info in pairs.items():
        owner_a, owner_b = info[
            "owners"
        ]

        summary = build_rivalry_summary(
            owner_a,
            owner_b
        )

        meetings = info[
            "meetings"
        ]

        score = (
            meetings * 6
            + info[
                "close_games"
            ] * 4
        )

        if summary.get(
            "current_streak"
        ):
            score += min(
                10,
                int(
                    summary[
                        "current_streak"
                    ].get(
                        "wins",
                        0
                    )
                )
                * 2
            )

        ranked.append({
            **summary,
            "rivalry_score":
                score
        })

    ranked.sort(
        key=lambda item: (
            item.get(
                "rivalry_score",
                0
            ),
            item.get(
                "meetings",
                0
            )
        ),
        reverse=True
    )

    return ranked[:limit]


def rivalry_week_spotlight(
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

    spotlights = []

    for game in schedule_data.get(
        "gameScheduleInfoList",
        []
    ):
        away_owner = team_owner_name(
            game.get(
                "awayTeamId"
            )
        )

        home_owner = team_owner_name(
            game.get(
                "homeTeamId"
            )
        )

        if (
            not away_owner
            or not home_owner
        ):
            continue

        summary = build_rivalry_summary(
            away_owner,
            home_owner
        )

        if (
            summary.get(
                "meetings",
                0
            )
            < 2
        ):
            continue

        spotlights.append({
            "matchup":
                (
                    f"{safe_team_name(game.get('awayTeamId'))} @ "
                    f"{safe_team_name(game.get('homeTeamId'))}"
                ),
            "away_owner":
                away_owner,
            "home_owner":
                home_owner,
            "rivalry":
                summary
        })

    spotlights.sort(
        key=lambda item: item[
            "rivalry"
        ].get(
            "meetings",
            0
        ),
        reverse=True
    )

    return spotlights[:3]


@app.route(
    "/analyst/rivalries"
)
def rivalries_route():
    return jsonify({
        "top_rivalries":
            build_top_rivalries(
                20
            ),
        "stored_games":
            len(
                load_rivalry_history().get(
                    "games",
                    []
                )
            )
    })


@app.route(
    "/analyst/rivalries/update/"
    "<season_type>/<int:week_number>",
    methods=["GET", "POST"]
)
def rivalry_update_route(
    season_type,
    week_number
):
    created = record_rivalry_week(
        season_type,
        week_number
    )

    return jsonify({
        "created":
            created,
        "top_rivalries":
            build_top_rivalries(
                10
            )
    })


@app.route(
    "/analyst/rivalry-week/"
    "<season_type>/<int:week_number>"
)
def rivalry_week_route(
    season_type,
    week_number
):
    return jsonify({
        "spotlights":
            rivalry_week_spotlight(
                season_type,
                week_number
            )
    })


# =========================================================
# ANALYST ACCURACY BY CATEGORY
# =========================================================

def analyst_pick_category(
    item
):
    away_ovr = item.get(
        "away_ovr"
    )
    home_ovr = item.get(
        "home_ovr"
    )
    picked = item.get(
        "pick"
    )
    actual = item.get(
        "actual_winner"
    )

    try:
        edge = abs(
            int(
                away_ovr
            )
            - int(
                home_ovr
            )
        )
    except Exception:
        edge = None

    favorite = None
    underdog = None

    if (
        away_ovr is not None
        and home_ovr is not None
    ):
        if int(
            away_ovr
        ) > int(
            home_ovr
        ):
            favorite = item.get(
                "away"
            )
            underdog = item.get(
                "home"
            )
        elif int(
            home_ovr
        ) > int(
            away_ovr
        ):
            favorite = item.get(
                "home"
            )
            underdog = item.get(
                "away"
            )

    categories = [
        "overall"
    ]

    if (
        favorite
        and picked
        == favorite
    ):
        categories.append(
            "favorite_picks"
        )

    if (
        underdog
        and picked
        == underdog
    ):
        categories.append(
            "upset_picks"
        )

    if (
        edge is not None
        and edge >= 5
    ):
        categories.append(
            "strong_edges"
        )

    if (
        edge is not None
        and edge <= 1
    ):
        categories.append(
            "toss_up_games"
        )

    if (
        underdog
        and actual
        == underdog
    ):
        categories.append(
            "actual_upsets"
        )

    return categories


def analyst_accuracy_by_category():
    data = load_analyst_receipts()

    category_names = {
        "overall":
            "Overall",
        "favorite_picks":
            "Favorite Picks",
        "upset_picks":
            "Upset Calls",
        "strong_edges":
            "Strong OVR Edges",
        "toss_up_games":
            "Near Toss-Ups",
        "actual_upsets":
            "Games That Became Upsets",
    }

    result = {}

    for analyst, name in ANALYST_DISPLAY_NAMES.items():
        result[
            analyst
        ] = {
            "name":
                name,
            "categories":
                {
                    key: {
                        "label":
                            label,
                        "wins":
                            0,
                        "losses":
                            0,
                        "pushes":
                            0,
                        "win_pct":
                            0.0,
                    }
                    for key, label
                    in category_names.items()
                }
        }

    for item in data.get(
        "picks",
        []
    ):
        if not isinstance(
            item,
            dict
        ):
            continue

        analyst = item.get(
            "analyst"
        )

        if analyst not in result:
            continue

        status = item.get(
            "status"
        )

        if status not in [
            "win",
            "loss",
            "push"
        ]:
            continue

        for category in analyst_pick_category(
            item
        ):
            bucket = (
                result[
                    analyst
                ][
                    "categories"
                ][
                    category
                ]
            )

            if status == "win":
                bucket[
                    "wins"
                ] += 1
            elif status == "loss":
                bucket[
                    "losses"
                ] += 1
            else:
                bucket[
                    "pushes"
                ] += 1

    for analyst in result.values():
        for bucket in analyst[
            "categories"
        ].values():
            total = (
                bucket[
                    "wins"
                ]
                + bucket[
                    "losses"
                ]
            )

            bucket[
                "win_pct"
            ] = round(
                (
                    bucket[
                        "wins"
                    ]
                    / total
                    * 100
                )
                if total
                else 0.0,
                1
            )

    return result


def analyst_category_leaders():
    accuracy = (
        analyst_accuracy_by_category()
    )

    categories = [
        "overall",
        "favorite_picks",
        "upset_picks",
        "strong_edges",
        "toss_up_games",
        "actual_upsets",
    ]

    leaders = {}

    for category in categories:
        candidates = []

        for analyst, data in accuracy.items():
            bucket = data[
                "categories"
            ][
                category
            ]

            total = (
                bucket[
                    "wins"
                ]
                + bucket[
                    "losses"
                ]
            )

            candidates.append({
                "analyst":
                    analyst,
                "name":
                    data[
                        "name"
                    ],
                "wins":
                    bucket[
                        "wins"
                    ],
                "losses":
                    bucket[
                        "losses"
                    ],
                "win_pct":
                    bucket[
                        "win_pct"
                    ],
                "sample":
                    total
            })

        candidates.sort(
            key=lambda item: (
                item[
                    "win_pct"
                ],
                item[
                    "wins"
                ],
                item[
                    "sample"
                ]
            ),
            reverse=True
        )

        leaders[
            category
        ] = candidates

    return leaders


@app.route(
    "/analyst/accuracy"
)
def analyst_accuracy_route():
    return jsonify({
        "accuracy":
            analyst_accuracy_by_category(),
        "leaders":
            analyst_category_leaders()
    })



# =========================================================
# FAN POLLS / GAME OF THE WEEK VOTING
# =========================================================

def gotw_channel_id():
    return os.environ.get(
        "GOTW_CHANNEL_ID",
        ""
    ).strip()


def gotw_poll_configured():
    return bool(
        discord_bot_token()
        and gotw_channel_id()
    )


def load_gotw_poll_history():
    data = load_json_file(
        GOTW_POLL_HISTORY_FILE
    )

    if not isinstance(
        data,
        dict
    ):
        data = {}

    data.setdefault(
        "polls",
        []
    )

    return data


def save_gotw_poll_history(
    data
):
    polls = data.get(
        "polls",
        []
    )

    if not isinstance(
        polls,
        list
    ):
        polls = []

    data[
        "polls"
    ] = polls[-500:]

    save_json_file(
        GOTW_POLL_HISTORY_FILE,
        data
    )


def gotw_poll_key(
    season_type,
    week_number,
    test=False
):
    return (
        f"{'test:' if test else ''}"
        f"{season_type}:{week_number}"
    )


def get_gotw_poll_record(
    season_type,
    week_number,
    test=False
):
    key = gotw_poll_key(
        season_type,
        week_number,
        test=test
    )

    for item in reversed(
        load_gotw_poll_history().get(
            "polls",
            []
        )
    ):
        if item.get(
            "key"
        ) == key:
            return item

    return None


def gotw_team_story_score(
    team_name,
    team_id,
    opponent_name,
    opponent_id,
    game,
    standings_map
):
    team_ovr = safe_team_overall(
        team_id
    )
    opp_ovr = safe_team_overall(
        opponent_id
    )

    standing = standings_map.get(
        str(
            team_name
        ).lower(),
        {}
    )

    opponent_standing = standings_map.get(
        str(
            opponent_name
        ).lower(),
        {}
    )

    score = 0.0
    reasons = []

    if team_ovr is not None:
        score += max(
            0,
            team_ovr - 78
        ) * 1.6

    games = int(
        standing.get(
            "games",
            0
        )
        or 0
    )

    wins = int(
        standing.get(
            "wins",
            0
        )
        or 0
    )

    if games:
        win_pct = float(
            standing.get(
                "win_pct",
                0
            )
            or 0
        )

        score += (
            win_pct
            * 18
        )

        if win_pct >= 0.650:
            reasons.append(
                "strong record"
            )

    seed = standing.get(
        "playoff_seed"
    )

    if (
        seed is not None
        and 1
        <= int(
            seed
        )
        <= 7
    ):
        score += 12
        reasons.append(
            f"playoff seed #{seed}"
        )

    conference_rank = (
        standing.get(
            "conference_rank"
        )
    )

    if (
        conference_rank is not None
        and int(
            conference_rank
        )
        <= 10
    ):
        score += 6
        reasons.append(
            "conference race"
        )

    if (
        team_ovr is not None
        and opp_ovr is not None
    ):
        ovr_gap = abs(
            team_ovr
            - opp_ovr
        )

        if ovr_gap <= 2:
            score += 10
            reasons.append(
                "close OVR matchup"
            )
        elif ovr_gap <= 4:
            score += 6

    try:
        team_align = team_alignment(
            standing
        )

        opp_align = team_alignment(
            opponent_standing
        )

        if (
            team_align.get(
                "division"
            )
            and team_align
            == opp_align
        ):
            score += 8
            reasons.append(
                "division matchup"
            )
    except Exception:
        pass

    try:
        owner_a = team_owner_name(
            team_id
        )

        owner_b = team_owner_name(
            opponent_id
        )

        if (
            owner_a
            and owner_b
        ):
            rivalry = (
                build_rivalry_summary(
                    owner_a,
                    owner_b
                )
            )

            meetings = int(
                rivalry.get(
                    "meetings",
                    0
                )
                or 0
            )

            if meetings >= 2:
                score += min(
                    12,
                    meetings * 2
                )

                reasons.append(
                    f"{meetings}-game user rivalry"
                )
    except Exception:
        pass

    if games >= 10:
        score += (
            wins
            * 0.7
        )

    return {
        "team":
            team_name,
        "team_id":
            team_id,
        "opponent":
            opponent_name,
        "opponent_id":
            opponent_id,
        "score":
            round(
                score,
                2
            ),
        "reasons":
            reasons[:4],
        "matchup":
            (
                f"{safe_team_name(game.get('awayTeamId'))} @ "
                f"{safe_team_name(game.get('homeTeamId'))}"
            )
    }


def choose_gotw_poll_candidates(
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

    standings_map = {
        str(
            team.get(
                "team",
                ""
            )
        ).lower():
            team
        for team in normalize_standings()
    }

    matchup_candidates = []

    for game in schedule_data.get(
        "gameScheduleInfoList",
        []
    ):
        if game_looks_completed(
            game
        ):
            continue

        away_id = game.get(
            "awayTeamId"
        )

        home_id = game.get(
            "homeTeamId"
        )

        away_name = safe_team_name(
            away_id
        )

        home_name = safe_team_name(
            home_id
        )

        away = gotw_team_story_score(
            away_name,
            away_id,
            home_name,
            home_id,
            game,
            standings_map
        )

        home = gotw_team_story_score(
            home_name,
            home_id,
            away_name,
            away_id,
            game,
            standings_map
        )

        if away.get(
            "score",
            0
        ) > home.get(
            "score",
            0
        ):
            headline_team = away
        elif home.get(
            "score",
            0
        ) > away.get(
            "score",
            0
        ):
            headline_team = home
        else:
            headline_team = stable_choice(
                [
                    away,
                    home
                ],
                (
                    f"gotw-headline-{season_type}-"
                    f"{week_number}-"
                    f"{game.get('scheduleId')}"
                )
            )

        headline_team = dict(
            headline_team
        )

        headline_team[
            "matchup_score"
        ] = round(
            away.get(
                "score",
                0
            )
            + home.get(
                "score",
                0
            ),
            2
        )

        headline_team[
            "schedule_id"
        ] = game.get(
            "scheduleId"
        )

        matchup_candidates.append(
            headline_team
        )

    matchup_candidates.sort(
        key=lambda item: (
            item.get(
                "matchup_score",
                0
            ),
            item.get(
                "score",
                0
            )
        ),
        reverse=True
    )

    selected = []
    seen_teams = set()
    seen_games = set()

    for item in matchup_candidates:
        team_key = str(
            item.get(
                "team"
            )
        ).lower()

        game_key = str(
            item.get(
                "schedule_id"
            )
        )

        if (
            team_key in seen_teams
            or game_key in seen_games
        ):
            continue

        selected.append(
            item
        )

        seen_teams.add(
            team_key
        )

        seen_games.add(
            game_key
        )

        if len(
            selected
        ) >= 3:
            break

    return selected


def create_discord_gotw_poll(
    season_type,
    week_number,
    test=False,
    force=False
):
    if not gotw_poll_configured():
        return {
            "success":
                False,
            "error": (
                "GOTW requires DISCORD_BOT_TOKEN "
                "and GOTW_CHANNEL_ID."
            )
        }

    existing = get_gotw_poll_record(
        season_type,
        week_number,
        test=test
    )

    if (
        existing
        and not force
    ):
        return {
            "success":
                True,
            "skipped":
                True,
            "reason":
                "poll_already_created",
            "poll":
                existing
        }

    candidates = (
        choose_gotw_poll_candidates(
            season_type,
            week_number
        )
    )

    if len(
        candidates
    ) < 3:
        return {
            "success":
                False,
            "error": (
                "Need at least three unplayed "
                "matchups to create the GOTW vote."
            ),
            "candidates":
                candidates
        }

    channel_id = (
        gotw_channel_id()
    )

    token = discord_bot_token()

    question = (
        f"{'TEST • ' if test else ''}"
        f"PROJECT MADDEN GOTW • "
        f"{season_type.upper()} WEEK {week_number} — "
        "Which team should headline Game of the Week?"
    )

    payload = {
        "content": (
            "🏆 **GAME OF THE WEEK FAN VOTE**\n"
            "Project Madden Analytics selected the **3 strongest "
            "team candidates** from three different scheduled games.\n\n"
            + "\n".join(
                (
                    f"**{index}. {item.get('team')}** — "
                    f"{item.get('matchup')}\n"
                    f"Why selected: "
                    + (
                        ", ".join(
                            item.get(
                                "reasons",
                                []
                            )
                        )
                        or "high-value weekly matchup"
                    )
                )
                for index, item in enumerate(
                    candidates[:3],
                    start=1
                )
            )
            + "\n\n⏱️ **Voting closes in 5 minutes.** "
            "One vote per person."
        ),
        "poll": {
            "question": {
                "text":
                    question[:300]
            },
            "answers": [
                {
                    "poll_media": {
                        "text":
                            str(
                                item.get(
                                    "team",
                                    "Team"
                                )
                            )[:55]
                    }
                }
                for item
                in candidates[:3]
            ],
            # Discord requires poll duration in whole hours.
            # We create a 1-hour poll and explicitly expire it after 5 minutes.
            "duration":
                1,
            "allow_multiselect":
                False,
            "layout_type":
                1
        },
        "allowed_mentions": {
            "parse": []
        }
    }

    response = requests.post(
        (
            "https://discord.com/api/v10/"
            f"channels/{channel_id}/messages"
        ),
        headers={
            "Authorization":
                f"Bot {token}",
            "Content-Type":
                "application/json"
        },
        json=payload,
        timeout=15
    )

    if response.status_code not in [
        200,
        201
    ]:
        return {
            "success":
                False,
            "status_code":
                response.status_code,
            "error":
                response.text[:1000]
        }

    message = response.json()

    message_id = str(
        message.get(
            "id",
            ""
        )
    )

    record = {
        "key":
            gotw_poll_key(
                season_type,
                week_number,
                test=test
            ),
        "season_type":
            season_type,
        "week":
            week_number,
        "test":
            bool(
                test
            ),
        "message_id":
            message_id,
        "channel_id":
            channel_id,
        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat(),
        "close_after_seconds":
            GOTW_POLL_CLOSE_SECONDS,
        "status":
            "open",
        "candidates":
            candidates[:3],
        "winner":
            None,
        "vote_counts":
            {}
    }

    history = (
        load_gotw_poll_history()
    )

    history[
        "polls"
    ].append(
        record
    )

    save_gotw_poll_history(
        history
    )

    worker = threading.Thread(
        target=close_gotw_poll_after_delay,
        args=(
            message_id,
            channel_id,
            season_type,
            week_number,
            test
        ),
        daemon=True
    )

    worker.start()

    return {
        "success":
            True,
        "message_id":
            message_id,
        "channel_id":
            channel_id,
        "closes_in_seconds":
            GOTW_POLL_CLOSE_SECONDS,
        "candidates":
            candidates[:3]
    }


def get_discord_gotw_message(
    channel_id,
    message_id
):
    token = discord_bot_token()

    response = requests.get(
        (
            "https://discord.com/api/v10/"
            f"channels/{channel_id}/messages/{message_id}"
        ),
        headers={
            "Authorization":
                f"Bot {token}"
        },
        timeout=15
    )

    if response.status_code != 200:
        return None

    try:
        return response.json()
    except Exception:
        return None


def update_gotw_poll_result(
    message,
    season_type,
    week_number,
    test=False
):
    if not isinstance(
        message,
        dict
    ):
        return None

    poll = message.get(
        "poll",
        {}
    )

    answer_text = {
        str(
            answer.get(
                "answer_id"
            )
        ):
            answer.get(
                "poll_media",
                {}
            ).get(
                "text"
            )
        for answer in poll.get(
            "answers",
            []
        )
    }

    vote_counts = {}

    for count in (
        poll.get(
            "results",
            {}
        ).get(
            "answer_counts",
            []
        )
    ):
        answer_id = str(
            count.get(
                "id"
            )
        )

        team = answer_text.get(
            answer_id,
            f"Answer {answer_id}"
        )

        vote_counts[
            team
        ] = int(
            count.get(
                "count",
                0
            )
            or 0
        )

    winner = None

    if vote_counts:
        max_votes = max(
            vote_counts.values()
        )

        tied = [
            team
            for team, votes
            in vote_counts.items()
            if votes
            == max_votes
        ]

        winner = (
            tied[0]
            if len(
                tied
            ) == 1
            else stable_choice(
                tied,
                (
                    f"gotw-tiebreak-{season_type}-"
                    f"{week_number}"
                )
            )
        )

    history = (
        load_gotw_poll_history()
    )

    key = gotw_poll_key(
        season_type,
        week_number,
        test=test
    )

    updated = None

    for item in history.get(
        "polls",
        []
    ):
        if (
            item.get(
                "key"
            )
            == key
            and str(
                item.get(
                    "message_id"
                )
            )
            == str(
                message.get(
                    "id"
                )
            )
        ):
            item[
                "status"
            ] = "closed"

            item[
                "closed_at"
            ] = datetime.now(
                timezone.utc
            ).isoformat()

            item[
                "winner"
            ] = winner

            item[
                "vote_counts"
            ] = vote_counts

            updated = item
            break

    save_gotw_poll_history(
        history
    )

    return updated


def announce_gotw_winner(
    record
):
    if not record:
        return {
            "sent":
                False,
            "error":
                "No GOTW record."
        }

    winner = record.get(
        "winner"
    )

    candidates = record.get(
        "candidates",
        []
    )

    winner_item = next(
        (
            item
            for item in candidates
            if item.get(
                "team"
            ) == winner
        ),
        {}
    )

    if winner:
        content = (
            "🏆 **PROJECT MADDEN GAME OF THE WEEK**\n"
            f"Fan vote winner: **{winner}**\n"
            f"Featured matchup: **{winner_item.get('matchup', '—')}**\n"
            f"Votes: **{record.get('vote_counts', {}).get(winner, 0)}**\n\n"
            "The fans have spoken. This matchup is officially "
            "the Project Madden Game of the Week."
        )
    else:
        content = (
            "🏆 **GOTW VOTING CLOSED**\n"
            "The poll closed without a valid winner."
        )

    response = requests.post(
        (
            "https://discord.com/api/v10/"
            f"channels/{record.get('channel_id')}/messages"
        ),
        headers={
            "Authorization":
                f"Bot {discord_bot_token()}",
            "Content-Type":
                "application/json"
        },
        json={
            "content":
                content,
            "allowed_mentions": {
                "parse": []
            }
        },
        timeout=15
    )

    return {
        "sent":
            response.status_code
            in [
                200,
                201
            ],
        "status_code":
            response.status_code
    }


def close_gotw_poll(
    message_id,
    channel_id,
    season_type,
    week_number,
    test=False
):
    response = requests.post(
        (
            "https://discord.com/api/v10/"
            f"channels/{channel_id}/polls/{message_id}/expire"
        ),
        headers={
            "Authorization":
                f"Bot {discord_bot_token()}",
            "Content-Type":
                "application/json"
        },
        timeout=15
    )

    if response.status_code != 200:
        return {
            "success":
                False,
            "status_code":
                response.status_code,
            "error":
                response.text[:1000]
        }

    message = response.json()

    time.sleep(
        2
    )

    refreshed = (
        get_discord_gotw_message(
            channel_id,
            message_id
        )
    )

    if refreshed:
        message = refreshed

    record = update_gotw_poll_result(
        message,
        season_type,
        week_number,
        test=test
    )

    return {
        "success":
            True,
        "record":
            record,
        "announcement":
            announce_gotw_winner(
                record
            )
    }


def close_gotw_poll_after_delay(
    message_id,
    channel_id,
    season_type,
    week_number,
    test=False
):
    time.sleep(
        GOTW_POLL_CLOSE_SECONDS
    )

    try:
        close_gotw_poll(
            message_id,
            channel_id,
            season_type,
            week_number,
            test=test
        )
    except Exception as e:
        print(
            "GOTW POLL CLOSE ERROR:",
            str(
                e
            )
        )



def recover_overdue_gotw_polls():
    history = load_gotw_poll_history()
    recovered = []
    now = datetime.now(
        timezone.utc
    )

    for item in history.get(
        "polls",
        []
    ):
        if item.get(
            "status"
        ) != "open":
            continue

        try:
            created = datetime.fromisoformat(
                str(
                    item.get(
                        "created_at"
                    )
                ).replace(
                    "Z",
                    "+00:00"
                )
            )
        except Exception:
            continue

        if created.tzinfo is None:
            created = created.replace(
                tzinfo=timezone.utc
            )

        if (
            now
            - created
        ).total_seconds() < GOTW_POLL_CLOSE_SECONDS:
            continue

        try:
            result = close_gotw_poll(
                str(item.get("message_id")),
                str(item.get("channel_id")),
                str(item.get("season_type")),
                int(item.get("week", 1) or 1),
                test=bool(item.get("test"))
            )

            recovered.append({
                "message_id":
                    item.get(
                        "message_id"
                    ),
                "success":
                    bool(
                        result.get(
                            "success"
                        )
                    )
            })
        except Exception as e:
            recovered.append({
                "message_id":
                    item.get(
                        "message_id"
                    ),
                "success":
                    False,
                "error":
                    str(
                        e
                    )
            })

    return recovered


def latest_closed_gotw(
    season_type,
    week_number
):
    record = get_gotw_poll_record(
        season_type,
        week_number,
        test=False
    )

    if (
        record
        and record.get(
            "status"
        ) == "closed"
    ):
        return record

    return None


@app.route(
    "/gotw/status"
)
def gotw_status_route():
    recovered = recover_overdue_gotw_polls()

    return jsonify({
        "overdue_poll_recovery":
            recovered,
        "configured":
            gotw_poll_configured(),
        "channel_id_configured":
            bool(
                gotw_channel_id()
            ),
        "bot_token_configured":
            bool(
                discord_bot_token()
            ),
        "close_seconds":
            GOTW_POLL_CLOSE_SECONDS,
        "history":
            list(
                reversed(
                    load_gotw_poll_history().get(
                        "polls",
                        []
                    )
                )
            )[:20]
    })


@app.route(
    "/gotw/post/<season_type>/<int:week_number>",
    methods=[
        "GET",
        "POST"
    ]
)
def gotw_manual_post_route(
    season_type,
    week_number
):
    return jsonify(
        create_discord_gotw_poll(
            season_type,
            week_number,
            test=False,
            force=False
        )
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




# =========================================================
# ANALYST RECEIPTS + PREDICTION RECORDS
# =========================================================

ANALYST_DISPLAY_NAMES = {
    "marcus": "Marcus Hayes",
    "stephen": "Stephen A. Smith — AI Parody",
    "pat": "Pat McAfee — AI Parody",
    "josh_pate": "Josh Pate — AI Parody",
}

RECEIPT_CALLOUT_LINES = {
    "marcus": [
        "Marcus has the receipt in front of him and is not pretending the miss did not happen.",
        "Marcus missed that one, and the rest of the desk is absolutely keeping the screenshot.",
        "Marcus went on record with the pick. The scoreboard gets the final word.",
    ],
    "stephen": [
        "Stephen A. made the pick with confidence, so the desk is not letting him quietly move on.",
        "The prediction is on the record. Stephen A. has to answer for that miss before changing the subject.",
        "Stephen A. wanted the favorite and the favorite let him down. The receipt is staying on screen.",
    ],
    "pat": [
        "Pat took the swing, missed it, and now the desk gets to have some fun with the receipt.",
        "Pat went bold on the pick. Bold is great until the final score arrives.",
        "The chaos pick did not hit this time, and everybody at the desk remembers it.",
    ],
    "josh_pate": [
        "Josh Pate trusted the process on that pick, but the result says the projection missed.",
        "Josh had a roster-building argument for the pick. The scoreboard just gave him a new data point.",
        "The prediction looked sustainable on paper. The actual game disagreed.",
    ],
}

RECEIPT_DEFENSE_LINES = {
    "marcus": [
        "I missed it. I am not deleting the tape. Give the winner credit and put the loss on my record.",
        "That pick was wrong. No excuses — the team I backed did not execute.",
        "Keep the receipt. I will take the loss and come back next week.",
    ],
    "stephen": [
        "Fine, I was wrong on that game. But do not confuse one missed pick with me lowering the standard.",
        "The pick missed. I will own that. The team I trusted did not perform like the roster said it should.",
        "Put the loss next to my name. I still want an explanation from the team that burned the pick.",
    ],
    "pat": [
        "Yep, that one got me. That is why the games are awesome — sometimes the whole script gets flipped.",
        "I missed it. Somebody clip it, laugh at it, and then give the winner credit.",
        "That pick went straight into the trash can. We move.",
    ],
    "josh_pate": [
        "That is a miss, and it is useful information. The assumption behind the pick did not survive the matchup.",
        "I got it wrong. Now the question is what part of the evaluation needs to change going forward.",
        "The receipt is fair. The result gave us evidence that the pregame model did not have.",
    ],
}


def load_analyst_receipts():
    data = load_json_file(
        ANALYST_RECEIPTS_FILE
    )

    if not isinstance(data, dict):
        data = {}

    data.setdefault(
        "picks",
        []
    )

    return data


def save_analyst_receipts(data):
    picks = data.get(
        "picks",
        []
    )

    if not isinstance(picks, list):
        picks = []

    data["picks"] = picks[-5000:]

    save_json_file(
        ANALYST_RECEIPTS_FILE,
        data
    )


def deterministic_side(
    away,
    home,
    key
):
    return stable_choice(
        [away, home],
        key
    )


def build_analyst_pick_set(
    prediction,
    season_type,
    week_number
):
    away = prediction.get(
        "away"
    )
    home = prediction.get(
        "home"
    )
    favorite = prediction.get(
        "favorite"
    )
    underdog = prediction.get(
        "underdog"
    )
    edge = prediction.get(
        "ovr_edge"
    )

    schedule_id = prediction.get(
        "schedule_id"
    )

    base_key = (
        f"analyst-picks-{season_type}-"
        f"{week_number}-{schedule_id}"
    )

    if favorite in [
        None,
        "TOSS-UP"
    ]:
        marcus_pick = deterministic_side(
            away,
            home,
            base_key + "-marcus"
        )
        stephen_pick = deterministic_side(
            away,
            home,
            base_key + "-stephen"
        )
        pat_pick = deterministic_side(
            away,
            home,
            base_key + "-pat"
        )
        josh_pick = deterministic_side(
            away,
            home,
            base_key + "-josh"
        )
    else:
        # Each analyst has a different prediction personality.
        marcus_pick = favorite

        if (
            edge is not None
            and edge <= 1
        ):
            marcus_pick = stable_choice(
                [
                    favorite,
                    favorite,
                    underdog
                ],
                base_key + "-marcus"
            )

        stephen_pick = stable_choice(
            (
                [favorite, favorite, favorite, underdog]
                if edge is not None and edge <= 2
                else [favorite, favorite, favorite, favorite, underdog]
            ),
            base_key + "-stephen"
        )

        # Pat is the most willing to call an upset.
        pat_pick = stable_choice(
            (
                [favorite, underdog, underdog]
                if edge is not None and edge <= 2
                else [favorite, favorite, underdog]
            ),
            base_key + "-pat"
        )

        # Josh is the most conservative unless the matchup is nearly even.
        josh_pick = stable_choice(
            (
                [favorite, favorite, underdog]
                if edge is not None and edge <= 1
                else [favorite, favorite, favorite, favorite]
            ),
            base_key + "-josh"
        )

    return {
        "marcus":
            marcus_pick,
        "stephen":
            stephen_pick,
        "pat":
            pat_pick,
        "josh_pate":
            josh_pick,
    }


def record_weekly_analyst_predictions(
    season_type,
    week_number,
    predictions
):
    data = load_analyst_receipts()
    picks = data.get(
        "picks",
        []
    )

    existing_keys = {
        (
            str(item.get("season_type")),
            int(item.get("week", 0) or 0),
            str(item.get("schedule_id")),
            str(item.get("analyst"))
        )
        for item in picks
        if isinstance(item, dict)
    }

    created = 0

    for prediction in predictions:
        schedule_id = prediction.get(
            "schedule_id"
        )

        if schedule_id is None:
            continue

        analyst_picks = (
            build_analyst_pick_set(
                prediction,
                season_type,
                week_number
            )
        )

        for analyst, picked_team in analyst_picks.items():
            key = (
                str(season_type),
                int(week_number),
                str(schedule_id),
                analyst
            )

            if key in existing_keys:
                continue

            opponent = None

            if picked_team == prediction.get("away"):
                opponent = prediction.get("home")
            elif picked_team == prediction.get("home"):
                opponent = prediction.get("away")

            picks.append({
                "season_type":
                    season_type,
                "week":
                    week_number,
                "schedule_id":
                    schedule_id,
                "matchup":
                    prediction.get("matchup"),
                "away":
                    prediction.get("away"),
                "home":
                    prediction.get("home"),
                "away_ovr":
                    prediction.get("away_ovr"),
                "home_ovr":
                    prediction.get("home_ovr"),
                "analyst":
                    analyst,
                "analyst_name":
                    ANALYST_DISPLAY_NAMES.get(
                        analyst,
                        analyst
                    ),
                "pick":
                    picked_team,
                "opponent":
                    opponent,
                "status":
                    "pending",
                "actual_winner":
                    None,
                "actual_score":
                    None,
                "created_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
                "settled_at":
                    None,
            })

            existing_keys.add(
                key
            )
            created += 1

    data["picks"] = picks
    save_analyst_receipts(
        data
    )

    return created


def settle_analyst_predictions(
    season_type=None,
    week_number=None
):
    data = load_analyst_receipts()
    picks = data.get(
        "picks",
        []
    )

    by_week = {}

    for item in picks:
        if not isinstance(item, dict):
            continue

        if item.get("status") != "pending":
            continue

        item_season = str(
            item.get(
                "season_type",
                ""
            )
        )

        try:
            item_week = int(
                item.get(
                    "week",
                    0
                )
            )
        except Exception:
            continue

        if (
            season_type is not None
            and item_season != str(
                season_type
            )
        ):
            continue

        if (
            week_number is not None
            and item_week != int(
                week_number
            )
        ):
            continue

        by_week.setdefault(
            (
                item_season,
                item_week
            ),
            []
        ).append(
            item
        )

    settled = 0

    for (
        item_season,
        item_week
    ), week_picks in by_week.items():
        schedule_data = load_weekly_data(
            item_season,
            item_week,
            "schedules"
        )

        if not schedule_data:
            continue

        completed = {}

        for game in schedule_data.get(
            "gameScheduleInfoList",
            []
        ):
            if not game_looks_completed(
                game
            ):
                continue

            schedule_id = str(
                game.get(
                    "scheduleId"
                )
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

            away = safe_team_name(
                game.get(
                    "awayTeamId"
                )
            )
            home = safe_team_name(
                game.get(
                    "homeTeamId"
                )
            )

            if away_score > home_score:
                winner = away
            elif home_score > away_score:
                winner = home
            else:
                winner = "TIE"

            completed[
                schedule_id
            ] = {
                "winner":
                    winner,
                "score":
                    (
                        f"{away} {away_score}, "
                        f"{home} {home_score}"
                    )
            }

        for item in week_picks:
            result = completed.get(
                str(
                    item.get(
                        "schedule_id"
                    )
                )
            )

            if not result:
                continue

            winner = result[
                "winner"
            ]

            if winner == "TIE":
                status = "push"
            elif item.get(
                "pick"
            ) == winner:
                status = "win"
            else:
                status = "loss"

            item[
                "status"
            ] = status

            item[
                "actual_winner"
            ] = winner

            item[
                "actual_score"
            ] = result[
                "score"
            ]

            item[
                "settled_at"
            ] = datetime.now(
                timezone.utc
            ).isoformat()

            settled += 1

    data["picks"] = picks
    save_analyst_receipts(
        data
    )

    return settled


def analyst_prediction_records():
    data = load_analyst_receipts()
    picks = data.get(
        "picks",
        []
    )

    records = {
        analyst: {
            "analyst":
                analyst,
            "name":
                name,
            "wins":
                0,
            "losses":
                0,
            "pushes":
                0,
            "pending":
                0,
            "total_settled":
                0,
            "win_pct":
                0.0,
        }
        for analyst, name in ANALYST_DISPLAY_NAMES.items()
    }

    for item in picks:
        analyst = item.get(
            "analyst"
        )

        if analyst not in records:
            continue

        status = item.get(
            "status",
            "pending"
        )

        if status == "win":
            records[
                analyst
            ][
                "wins"
            ] += 1
        elif status == "loss":
            records[
                analyst
            ][
                "losses"
            ] += 1
        elif status == "push":
            records[
                analyst
            ][
                "pushes"
            ] += 1
        else:
            records[
                analyst
            ][
                "pending"
            ] += 1

    for record in records.values():
        total = (
            record["wins"]
            + record["losses"]
        )

        record[
            "total_settled"
        ] = total

        record[
            "win_pct"
        ] = round(
            (
                record["wins"]
                / total
                * 100
            )
            if total
            else 0.0,
            1
        )

    return records


def analyst_receipts_leaderboard():
    records = analyst_prediction_records()

    ordered = sorted(
        records.values(),
        key=lambda item: (
            item.get(
                "win_pct",
                0
            ),
            item.get(
                "wins",
                0
            )
        ),
        reverse=True
    )

    for index, item in enumerate(
        ordered,
        start=1
    ):
        item[
            "rank"
        ] = index

    return ordered


def recent_bad_analyst_receipts(
    limit=4
):
    data = load_analyst_receipts()

    losses = [
        item
        for item in data.get(
            "picks",
            []
        )
        if isinstance(item, dict)
        and item.get(
            "status"
        ) == "loss"
    ]

    losses.sort(
        key=lambda item: str(
            item.get(
                "settled_at",
                ""
            )
        ),
        reverse=True
    )

    return losses[:limit]


def build_receipts_callout(
    season_type,
    week_number
):
    leaderboard = (
        analyst_receipts_leaderboard()
    )

    losses = recent_bad_analyst_receipts(
        8
    )

    current_losses = [
        item
        for item in losses
        if str(
            item.get(
                "season_type"
            )
        ) == str(
            season_type
        )
        and int(
            item.get(
                "week",
                0
            )
            or 0
        ) == int(
            week_number
        )
    ]

    target = (
        current_losses[0]
        if current_losses
        else (
            losses[0]
            if losses
            else None
        )
    )

    if not target:
        return {
            "headline":
                "No receipts to cash yet",
            "take": (
                "The picks are on the record. "
                "Once games finish, the desk will have wins and losses to answer for."
            ),
            "target":
                None,
            "leaderboard":
                leaderboard,
        }

    analyst = target.get(
        "analyst"
    )

    analyst_name = (
        ANALYST_DISPLAY_NAMES.get(
            analyst,
            analyst
        )
    )

    callout = stable_choice(
        RECEIPT_CALLOUT_LINES.get(
            analyst,
            [
                "The prediction missed, and the receipt is on screen."
            ]
        ),
        (
            f"receipt-callout-{season_type}-"
            f"{week_number}-"
            f"{target.get('schedule_id')}-"
            f"{analyst}"
        )
    )

    defense = stable_choice(
        RECEIPT_DEFENSE_LINES.get(
            analyst,
            [
                "I got the pick wrong. Put it on the record."
            ]
        ),
        (
            f"receipt-defense-{season_type}-"
            f"{week_number}-"
            f"{target.get('schedule_id')}-"
            f"{analyst}"
        )
    )

    return {
        "headline":
            f"Receipt Check: {analyst_name}",
        "take": (
            f"{callout}\n\n"
            f"**The miss:** {analyst_name} picked "
            f"**{target.get('pick')}** in "
            f"**{target.get('matchup')}**. "
            f"Actual winner: **{target.get('actual_winner')}** "
            f"({target.get('actual_score')}).\n\n"
            f"**{analyst_name} responds:** {defense}"
        ),
        "target":
            target,
        "leaderboard":
            leaderboard,
    }


@app.route(
    "/analyst/receipts"
)
def analyst_receipts_route():
    settle_analyst_predictions()

    return jsonify({
        "leaderboard":
            analyst_receipts_leaderboard(),
        "recent_losses":
            recent_bad_analyst_receipts(
                20
            ),
        "picks":
            list(
                reversed(
                    load_analyst_receipts().get(
                        "picks",
                        []
                    )
                )
            )[:200]
    })


@app.route(
    "/analyst/receipts/settle/"
    "<season_type>/<int:week_number>",
    methods=["GET", "POST"]
)
def analyst_receipts_settle_route(
    season_type,
    week_number
):
    settled = settle_analyst_predictions(
        season_type,
        week_number
    )

    return jsonify({
        "settled":
            settled,
        "leaderboard":
            analyst_receipts_leaderboard(),
        "callout":
            build_receipts_callout(
                season_type,
                week_number
            )
    })


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



WEEKLY_REACTION_CONTEXT_LINES = {
    "blowout": [
        "This was not a one-possession coin flip. The winner imposed itself for long stretches.",
        "The margin matters because this game stopped being competitive before the final whistle.",
        "A blowout creates a different conversation because the losing side never found a stable answer.",
        "This result is going to linger because the separation between the teams was obvious.",
    ],
    "close": [
        "This came down to details, situational football, and who handled the final possessions better.",
        "A close game says both teams had answers, but one side executed the final critical moments better.",
        "There was almost nothing separating these teams, so the small mistakes became the entire story.",
        "One third down, one turnover, or one clock-management decision could have flipped this result.",
    ],
    "upset": [
        "The lower-rated team forced everybody to reevaluate what they thought they knew.",
        "OVR is not destiny. Execution erased the paper advantage.",
        "The upset shifts the conversation from ratings to coaching, preparation, and user execution.",
        "This was not the expected script on paper, which is exactly why the result matters.",
    ],
    "shootout": [
        "Both offenses kept answering, so every empty possession became a major problem.",
        "This turned into a scoring race where punts started feeling like turnovers.",
        "The pressure stayed on the quarterbacks and play callers from start to finish.",
        "One stalled drive could change the entire game because neither offense wanted to blink first.",
    ],
    "defensive": [
        "This was a defense-first game where every scoring opportunity carried extra weight.",
        "Field position and patience mattered more than raw yardage.",
        "The defenses controlled the pace and made offensive possessions expensive.",
        "The winning side handled a low-scoring environment better.",
    ],
    "normal": [
        "This result fits into the larger weekly picture, but the details still matter.",
        "There were enough meaningful swings to learn something about both teams.",
        "The final score tells part of the story; the weekly trend tells the rest.",
        "This was another useful checkpoint for where both teams currently stand.",
    ],
}

MARCUS_WEEKLY_ADAPTIVE_LINES = {
    "blowout": [
        "That was domination, not just a win. The losing side has a rough film session coming.",
        "There is losing, and then there is getting controlled in every phase. This was the second one.",
        "The winner did not leave room for excuses. The loser has to explain why the game got away so quickly.",
        "If that team calls itself a contender, a margin like this demands answers immediately.",
    ],
    "close": [
        "This came down to discipline and late-game decisions. One mistake was always going to decide it.",
        "Neither team could separate, so clock management, fourth downs, and red-zone execution became the story.",
        "This was a pressure game. The side that stayed cleaner late earned it.",
        "A game this close tells me the rematch would be must-watch.",
    ],
    "upset": [
        "Everybody who picked off OVR alone just got reminded that users still have to play the game.",
        "The lower-rated roster made the favorite look ordinary. That is a statement.",
        "This upset changes how the league should look at both teams next week.",
        "The underdog just earned respect; the favorite just earned questions.",
    ],
    "shootout": [
        "The defenses basically asked the offenses to win it every drive.",
        "If your offense stalled once, you were in trouble. That is what this game became.",
        "This was a quarterback-and-play-caller game from start to finish.",
        "When both sides are scoring like that, every possession becomes pressure.",
    ],
    "defensive": [
        "Every first down mattered because points were hard to find.",
        "This was about patience, field position, and not giving the other team a short field.",
        "Defense dictated the game, and the offense that stayed composed longest survived it.",
        "That was a grind, and the winner handled the ugly parts better.",
    ],
    "normal": [
        "The result matters, but I am watching whether the habits behind it are repeatable.",
        "One week does not define anybody, but this is a real data point.",
        "The league is starting to separate teams with identity from teams still searching for one.",
        "This was not season-defining, but it absolutely belongs in the weekly conversation.",
    ],
}

STEPHEN_A_WEEKLY_CONTEXT_LINES = {
    "blowout": [
        "I do not want to hear about potential after a performance like that. A contender cannot get pushed around for four quarters and hide behind talent.",
        "That was embarrassing because the game stopped being competitive. High expectations require a higher standard.",
        "The losing team was exposed in areas every opponent is going to test again next week.",
        "If you have championship expectations, a blowout loss demands accountability from the user, quarterback, and roster.",
    ],
    "close": [
        "This is where I look at decision-making: final possessions, ball security, and who understood the moment.",
        "Close games expose discipline. One bad fourth-down decision can erase three quarters of good football.",
        "I am not overreacting to a close loss, but I am absolutely judging the late-game execution.",
        "Both teams can leave with confidence, but only one handled the pressure correctly.",
    ],
    "upset": [
        "Do not tell me about OVR anymore. The lower-rated team outplayed the supposed favorite, and the conversation has changed.",
        "This is why I refuse to crown teams from roster screens. You still have to execute under pressure.",
        "If you are the higher-rated team and lose this game, I am questioning preparation before talent.",
        "The upset is not only about the winner; it is also about what the favorite failed to prove.",
    ],
    "shootout": [
        "When both offenses are scoring like this, the quarterback has to treat every possession like it may be the last one.",
        "This became basketball on grass: possessions, efficiency, and who could survive one empty trip.",
        "In a shootout, the offense cannot disappear for two drives and expect the defense to rescue it.",
        "This was about star players carrying the burden because neither defense could slow the game down.",
    ],
    "defensive": [
        "This was not pretty offense, but that does not mean it was bad football. Somebody had to stay patient.",
        "When points are this scarce, one turnover can feel like giving away ten points.",
        "This was a game for disciplined users because forcing the issue was the fastest way to lose.",
        "The winner understood the environment better and did not panic because the offense was not explosive.",
    ],
    "normal": [
        "I am evaluating whether this team looked like what its roster says it should be.",
        "The result is one thing. The standard is another, and I am judging both.",
        "I want to see whether the good parts of this performance survive against better competition.",
        "Consistency matters more than one impressive box score.",
    ],
}

STEPHEN_A_WEEKLY_PLAYER_COMPARISONS = [
    "{player} is giving me a **LeBron James** type of control — the entire operation changes when he is on.",
    "{player} has **Stephen Curry** type gravity because the opponent is thinking about him before the snap.",
    "{player} is playing with a **Jimmy Butler** kind of edge — the impact gets louder when the pressure rises.",
    "{player} is giving me **Nikola Jokic** efficiency: calm, productive, and always finding the right answer.",
    "{player} has **Anthony Edwards** energy — aggressive, fearless, and constantly attacking.",
    "{player} is giving me **Jayson Tatum** consistency — polished production without needing chaos.",
    "{player} is having a **Shai Gilgeous-Alexander** kind of week — controlled, efficient, and difficult to knock off rhythm.",
    "{player} is bringing **Giannis Antetokounmpo** force — once the momentum starts downhill, the opponent has a serious problem.",
]

PAT_MCAFEE_WEEKLY_CONTEXT_LINES = {
    "blowout": [
        "That thing got sideways fast. Once the momentum turned, everybody knew where it was going.",
        "That was a full-on avalanche. One team kept stacking plays and the other never found the emergency brake.",
        "When a game gets that lopsided, every little mistake starts feeling ten times bigger.",
        "That was one of those games where the Discord probably stopped being polite by halftime.",
    ],
    "close": [
        "That was chaos in the best way. Every drive felt like it could decide the whole thing.",
        "That is the kind of game where everybody is checking the score every two minutes.",
        "One snap here, one fourth down there, and the whole result flips.",
        "That game had the exact kind of late drama this league needs.",
    ],
    "upset": [
        "OVR got thrown out the window. The underdog showed up and made the whole league pay attention.",
        "That is why you play the games. Ratings are cute until somebody starts making plays.",
        "The upset just made next week way more interesting because everybody sees both teams differently now.",
        "The favorite just learned the hard way that Madden does not care about reputation.",
    ],
    "shootout": [
        "That game was absolute fireworks. Nobody could afford to waste a possession.",
        "Every drive felt like a two-minute drill. That is a wild way to live for four quarters.",
        "If you like offense, that was your game of the week.",
        "Nobody wanted to punt, and honestly after watching that game I understand why.",
    ],
    "defensive": [
        "That was a rock fight. Every yard looked like somebody had to pay for it.",
        "That was old-school football energy — field position, pressure, and one mistake deciding everything.",
        "Not every game needs 70 points to be entertaining. That was tense the whole way.",
        "That was ugly, physical, stressful football — which means it was awesome.",
    ],
    "normal": [
        "That game gave us enough to argue about all week.",
        "There is definitely something there, but I want to see it again next week.",
        "That result added another storyline to the league instead of ending one.",
        "That was exactly the kind of weekly result that keeps everybody talking.",
    ],
}

JOSH_PATE_WEEKLY_CONTEXT_LINES = {
    "blowout": [
        "A blowout usually tells you more about structural differences than one lucky bounce. The losing side has to decide whether the problem was matchup-specific or reproducible.",
        "When the margin gets this large, I start looking at roster depth, adjustment quality, and whether there was a second plan after the first one failed.",
        "The question next week is whether this was an outlier or the first sign of a real ceiling problem.",
        "This kind of margin makes me less interested in one bad play and more interested in the entire operation.",
    ],
    "close": [
        "Close games expose operational quality: clock management, fourth-down logic, red-zone decisions, and turnover avoidance.",
        "There is value in a close game because you learn how a team handles stress without the scoreboard hiding the details.",
        "This is a result that should be studied for process, not just outcome.",
        "The score says close; the film should tell us which team actually had the more sustainable formula.",
    ],
    "upset": [
        "An upset is usually where preparation, matchup understanding, and user execution overcome the roster gap.",
        "The lower-rated team found something repeatable enough to neutralize the paper advantage, and that deserves attention.",
        "This is a reminder that team quality and roster rating are related, but they are not identical.",
        "The underdog just showed a better formula for this matchup than the favorite did.",
    ],
    "shootout": [
        "A shootout tests offensive sustainability because every empty possession becomes expensive.",
        "When both teams can score, the differentiator usually becomes situational efficiency rather than raw yardage.",
        "This type of game tells you a lot about quarterback stability and play-caller confidence.",
        "The key question is whether either offense can reproduce this against a defense that controls possessions better.",
    ],
    "defensive": [
        "Low-scoring games tell you whether a team can win without its preferred script.",
        "This was an environment where field position, patience, and avoiding negative plays mattered more than explosive offense.",
        "The winning team showed it could survive when the game did not look comfortable.",
        "This is the kind of game that tests roster depth and user discipline more than highlight ability.",
    ],
    "normal": [
        "I am less interested in one result than in whether the process behind it can survive the next month.",
        "The useful question is whether this performance raised the team’s floor or just created one good Sunday.",
        "This week added evidence, but the trend still needs another data point.",
        "The result matters, but sustainability is the real story I want to track.",
    ],
}


def classify_weekly_game_context(game):
    if not isinstance(game, dict):
        return "normal"

    margin = int(game.get("margin", 0) or 0)

    if bool(game.get("upset")):
        return "upset"

    if margin >= 21:
        return "blowout"

    if margin <= 3:
        return "close"

    winner_score = int(
        game.get(
            "winner_score",
            game.get("score_winner", 0)
        ) or 0
    )
    loser_score = int(
        game.get(
            "loser_score",
            game.get("score_loser", 0)
        ) or 0
    )

    total = winner_score + loser_score

    if total >= 65:
        return "shootout"

    if total and total <= 34:
        return "defensive"

    return "normal"


def weekly_tone_seed(
    analyst,
    season_type,
    week_number,
    topic,
    source_key=""
):
    return (
        f"{analyst}-{season_type}-{week_number}-"
        f"{topic}-{source_key}"
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

    top_game = (
        completed[0]
        if completed
        else None
    )

    context_type = (
        classify_weekly_game_context(
            top_game
        )
    )

    source_key = (
        str(
            top_game.get(
                "game",
                ""
            )
        )
        if isinstance(
            top_game,
            dict
        )
        else "no-game"
    )

    marcus_parts = [
        stable_choice(
            MARCUS_WEEKLY_ADAPTIVE_LINES.get(
                context_type,
                MARCUS_WEEKLY_ADAPTIVE_LINES["normal"]
            ),
            weekly_tone_seed(
                "marcus",
                season_type,
                week_number,
                context_type,
                source_key
            )
        )
    ]

    stephen_parts = [
        stable_choice(
            STEPHEN_A_WEEKLY_CONTEXT_LINES.get(
                context_type,
                STEPHEN_A_WEEKLY_CONTEXT_LINES["normal"]
            ),
            weekly_tone_seed(
                "stephen",
                season_type,
                week_number,
                context_type,
                source_key
            )
        )
    ]

    pat_parts = [
        stable_choice(
            PAT_MCAFEE_WEEKLY_CONTEXT_LINES.get(
                context_type,
                PAT_MCAFEE_WEEKLY_CONTEXT_LINES["normal"]
            ),
            weekly_tone_seed(
                "pat",
                season_type,
                week_number,
                context_type,
                source_key
            )
        )
    ]

    josh_parts = [
        stable_choice(
            JOSH_PATE_WEEKLY_CONTEXT_LINES.get(
                context_type,
                JOSH_PATE_WEEKLY_CONTEXT_LINES["normal"]
            ),
            weekly_tone_seed(
                "josh-pate",
                season_type,
                week_number,
                context_type,
                source_key
            )
        )
    ]

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
            f"{loser} has to explain what failed and whether it can be fixed before next week."
        )

        stephen_parts.append(
            f"I am looking directly at {loser}. "
            "A bad result is one thing; repeating the same mistakes "
            "is where I start questioning the entire approach."
        )

        pat_parts.append(
            f"{winner} made the winning plays, and that is the stuff "
            "the locker room can carry into the next matchup."
        )

        josh_parts.append(
            f"The useful question after {winner} over {loser} is whether "
            "the winning formula is sustainable against a different matchup next week."
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
            f"{player_name} deserves the spotlight: {stat_text}. "
            "Now the question is whether that level of production becomes a trend."
        )

        stephen_compare = stable_choice(
            STEPHEN_A_WEEKLY_PLAYER_COMPARISONS,
            weekly_tone_seed(
                "stephen",
                season_type,
                week_number,
                "player-comparison",
                player_name
            )
        ).format(
            player=player_name
        )

        stephen_parts.append(
            f"If {player_name} is producing like that, "
            "the opponent has no excuse for failing to adjust. "
            f"{stephen_compare}"
        )

        pat_parts.append(
            f"{player_name} was a dude this week. "
            "Production like that changes how the next defense prepares."
        )

        josh_parts.append(
            f"{player_name} is becoming the kind of piece that can raise "
            "the weekly floor of an entire unit, not just fill a stat sheet."
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
        review = (
            trade.get(
                "trade_committee",
                {}
            )
            if isinstance(
                trade.get(
                    "trade_committee"
                ),
                dict
            )
            else {}
        )
        decision = review.get(
            "decision",
            ""
        )
        gap = review.get(
            "value_gap_percent",
            "—"
        )

        marcus_parts.append(
            f"Trade desk: {team_a} and {team_b} put a deal on the table. "
            f"The League Office call is {decision}, with a {gap}% value gap."
        )

        stephen_parts.append(
            f"I do not care how exciting {team_a} and {team_b} look on the graphic. "
            "If one side is giving away premium value without a real roster reason, "
            "I am going to challenge the move."
        )

        pat_parts.append(
            f"{team_a} and {team_b} just gave the league something to argue about. "
            "Trades are about fit as much as ratings."
        )

        josh_parts.append(
            f"For {team_a} and {team_b}, I want to know what the two-deep looks like "
            "after the trade, not just who won the headline."
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
                f"Game pick: {matchup} is a toss-up. {reason}"
            )
            stephen_parts.append(
                f"I am not giving either side a pass in {matchup}. "
                "The team that protects the football should win."
            )
            pat_parts.append(
                f"{matchup} feels like the game where one weird turnover "
                "or special-teams play flips everything."
            )
            josh_parts.append(
                f"In {matchup}, the roster gap is not large enough to settle it. "
                "I am watching situational football and user execution."
            )
        else:
            marcus_parts.append(
                f"My early favorite in {matchup}: **{favorite}**. {reason}"
            )
            stephen_parts.append(
                f"I have **{favorite}** in {matchup}, but if the higher-rated roster "
                "plays sloppy, I will be the first one criticizing them afterward."
            )
            pat_parts.append(
                f"I lean **{favorite}** in {matchup}. "
                "But this is Madden — user execution can erase an OVR edge fast."
            )
            josh_parts.append(
                f"I lean **{favorite}** in {matchup}, but I care more about whether "
                "their weekly formula travels than the rating advantage by itself."
            )

    return {
        "context_type":
            context_type,
        "marcus":
            " ".join(
                marcus_parts[:5]
            ),
        "stephen":
            " ".join(
                stephen_parts[:5]
            ),
        "pat":
            " ".join(
                pat_parts[:5]
            ),
        "josh_pate":
            " ".join(
                josh_parts[:5]
            )
    }




def build_fraud_watch():
    standings = normalize_standings()
    if not standings:
        return []

    out = []

    for team in standings:
        games = int(team.get("games", 0) or 0)
        if games < 3:
            continue

        wins = int(team.get("wins", 0) or 0)
        losses = int(team.get("losses", 0) or 0)
        overall = int(team.get("overall", 80) or 80)
        point_diff = float(team.get("point_diff", 0) or 0)
        win_pct = wins / games if games else 0.0

        score = 0.0
        reasons = []

        if overall >= 84:
            score += (overall - 83) * 5

        if win_pct < 0.500:
            score += (0.500 - win_pct) * 65
            reasons.append(f"{wins}-{losses} record")

        if point_diff < 0:
            score += min(
                30,
                abs(point_diff) / max(games, 1) * 3.5
            )
            reasons.append(f"{int(point_diff)} point differential")

        if overall >= 85 and wins <= losses:
            reasons.append(f"{overall} OVR roster is underperforming")

        if overall >= 84 and score >= 22:
            out.append({
                "team": team.get("team"),
                "team_id": team.get("team_id"),
                "record": f"{wins}-{losses}",
                "overall": overall,
                "point_diff": int(point_diff),
                "fraud_score": round(score, 1),
                "reasons": reasons[:4],
            })

    out.sort(key=lambda x: x["fraud_score"], reverse=True)
    return out[:5]


def build_dark_horse_watch():
    standings = normalize_standings()
    if not standings:
        return []

    out = []

    for team in standings:
        games = int(team.get("games", 0) or 0)
        if games < 3:
            continue

        wins = int(team.get("wins", 0) or 0)
        losses = int(team.get("losses", 0) or 0)
        overall = int(team.get("overall", 80) or 80)
        point_diff = float(team.get("point_diff", 0) or 0)
        streak = str(team.get("streak", "") or "").upper()
        win_pct = wins / games if games else 0.0

        score = 0.0
        reasons = []

        if overall <= 82:
            score += (83 - overall) * 5

        if win_pct >= 0.600:
            score += (win_pct - 0.500) * 70
            reasons.append(f"{wins}-{losses} record")

        if point_diff > 0:
            score += min(
                25,
                point_diff / max(games, 1) * 2.5
            )
            reasons.append(f"+{int(point_diff)} point differential")

        if streak.startswith("W"):
            try:
                streak_count = int(streak[1:])
            except Exception:
                streak_count = 0

            if streak_count >= 2:
                score += min(streak_count, 5) * 4
                reasons.append(f"{streak} streak")

        if overall <= 82:
            reasons.append(f"only {overall} OVR")

        if overall <= 82 and win_pct >= 0.600 and score >= 20:
            out.append({
                "team": team.get("team"),
                "team_id": team.get("team_id"),
                "record": f"{wins}-{losses}",
                "overall": overall,
                "point_diff": int(point_diff),
                "streak": streak,
                "dark_horse_score": round(score, 1),
                "reasons": reasons[:4],
            })

    out.sort(key=lambda x: x["dark_horse_score"], reverse=True)
    return out[:5]


def build_watch_panel_takes(fraud_watch, dark_horses):
    result = {
        "fraud_watch": None,
        "dark_horse": None,
    }

    if fraud_watch:
        team = fraud_watch[0]
        result["fraud_watch"] = {
            "team": team["team"],
            "marcus": (
                f"**{team['team']}** is on Fraud Watch. "
                f"A {team['overall']} OVR roster cannot keep producing "
                f"a {team['record']} record with a {team['point_diff']} "
                "point differential and expect nobody to question it."
            ),
            "stephen": (
                f"I am looking at **{team['team']}** and I am not impressed. "
                "If the roster says contender and the results say mediocre, "
                "that is when the criticism gets louder."
            ),
            "pat": (
                f"**{team['team']}** has the dudes on paper. "
                "Now they need to stop giving games away and play "
                "like the roster rating says they should."
            ),
        }

    if dark_horses:
        team = dark_horses[0]
        result["dark_horse"] = {
            "team": team["team"],
            "marcus": (
                f"Keep an eye on **{team['team']}**. "
                f"They are only {team['overall']} OVR but they are sitting at "
                f"{team['record']}. That is outperforming expectations."
            ),
            "stephen": (
                f"Nobody better overlook **{team['team']}**. "
                "They may not have the prettiest roster rating, but wins count "
                "the same no matter what your OVR says."
            ),
            "pat": (
                f"**{team['team']}** is my sneaky team right now. "
                "They are finding ways to win, and that makes them dangerous."
            ),
        }

    return result


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



PANEL_DEBATE_OPENERS = [
    "The desk is split on this one.",
    "We have our first disagreement of the show.",
    "This is where the panel starts pushing back on each other.",
    "Everybody at the desk sees the same result, but not the same story.",
    "This one turned into a real debate at the desk.",
]

PANEL_AGREEMENT_LINES = [
    "The panel is actually on the same page here.",
    "For once, everybody at the desk agrees on the main point.",
    "There is not much disagreement on this one.",
    "The whole desk is seeing the same warning sign.",
    "This is one of the rare moments where the panel is aligned.",
]

MARCUS_DEBATE_LINES = {
    "agree": [
        "I agree with that part. The result backs it up, and the film should too.",
        "That is fair. I am not fighting the obvious just to make television.",
        "We are saying the same thing from different angles: execution decided it.",
        "I am with you there. The roster can only matter if the user gets the most out of it.",
    ],
    "disagree": [
        "I disagree with that. You are giving the roster too much credit and the actual game too little.",
        "That is where I push back. One big name does not fix bad weekly execution.",
        "I am not buying that argument yet. Show me the same thing next week.",
        "You are looking at the ceiling. I am looking at what they actually put on the field.",
    ],
}

STEPHEN_A_DEBATE_LINES = {
    "agree": [
        "I agree with Marcus on that point. If the evidence is right in front of us, I am not going to pretend otherwise.",
        "That is exactly right. Expectations matter, and the performance has to match them.",
        "I am with the desk on this one. The team earned the criticism or the praise it is getting.",
        "Correct. We cannot keep making excuses for teams with high-end talent.",
    ],
    "disagree": [
        "No, no, no — that is where I disagree. Talent creates expectations, and I am going to hold the team to them.",
        "I hear the argument, but I am not accepting it. A contender has to be judged differently.",
        "That sounds nice, but the scoreboard and the decisions matter more than the excuse.",
        "I disagree completely. You cannot call yourself elite and then ask me to lower the standard after one bad week.",
    ],
}

PAT_MCAFEE_DEBATE_LINES = {
    "agree": [
        "I am with you guys on that. The whole thing looked exactly like what the result says it was.",
        "Yep, I agree. Sometimes the simple answer is the right answer.",
        "That is where I am at too. The team that made the winning plays deserved the win.",
        "I am riding with that take. The momentum and the execution both pointed the same direction.",
    ],
    "disagree": [
        "I do not know about that one. Madden gets weird, and one swing play can make a good team look terrible.",
        "I am pushing back a little. The result matters, but I do not think the sky is falling yet.",
        "I disagree there. One game can get chaotic fast, and I need another week before I bury anybody.",
        "That is a little too far for me. I saw mistakes, but I also saw stuff they can fix.",
    ],
}

JOSH_PATE_DEBATE_LINES = {
    "agree": [
        "I agree with the conclusion, but I care most about whether the process behind it is repeatable.",
        "That is where I land too. The result and the underlying structure are pointing in the same direction.",
        "I am with the panel on that. The team showed something sustainable, not just something flashy.",
        "Agreed. If the same formula works against different opponents, then we can start calling it an identity.",
    ],
    "disagree": [
        "I disagree with the certainty, not necessarily the concern. One week is still a small sample.",
        "That is where I separate from the desk. I need to know whether this was matchup-specific before I make a season-wide claim.",
        "I am not ready to go that far. The result is real, but the structural conclusion still needs another data point.",
        "I disagree with the reaction level. I care more about whether the process can be corrected than how loud the final score looked.",
    ],
}


def build_panel_debate(
    show,
    season_type,
    week_number
):
    games = show.get(
        "top_games",
        []
    )
    players = show.get(
        "top_players",
        []
    )
    trades = show.get(
        "trade_proposals",
        []
    )
    predictions = show.get(
        "game_predictions",
        []
    )

    rivalry_spotlight = show.get(
        "rivalry_spotlight",
        []
    )

    playoff_race = show.get(
        "playoff_race",
        {}
    )

    topic_type = "weekly"
    topic_label = "the overall week"
    source_key = f"{season_type}-{week_number}"

    receipt_callout = show.get(
        "receipts_callout",
        {}
    )

    receipt_target = (
        receipt_callout.get(
            "target"
        )
        if isinstance(
            receipt_callout,
            dict
        )
        else None
    )

    # Receipts get first priority so analysts can call each other out.
    if receipt_target:
        topic_type = "receipt"
        topic_label = (
            f"{receipt_target.get('analyst_name')} "
            f"missed {receipt_target.get('matchup')}"
        )
        source_key = (
            f"{receipt_target.get('schedule_id')}-"
            f"{receipt_target.get('analyst')}"
        )

    # Otherwise choose the strongest real topic available.
    elif rivalry_spotlight:
        rivalry_item = rivalry_spotlight[0]
        rivalry = rivalry_item.get(
            "rivalry",
            {}
        )
        topic_type = "rivalry"
        topic_label = rivalry_item.get(
            "matchup",
            "Rivalry Week"
        )
        source_key = rivalry.get(
            "pair_key",
            topic_label
        )
    elif (
        playoff_race
        and playoff_race.get(
            "game_of_the_week"
        )
    ):
        playoff_game = playoff_race.get(
            "game_of_the_week"
        )
        topic_type = "playoff"
        topic_label = playoff_game.get(
            "matchup",
            "Playoff Race"
        )
        source_key = topic_label
    elif trades:
        trade = trades[0]
        topic_type = "trade"
        topic_label = (
            f"{trade.get('team_a', 'Team A')} ↔ "
            f"{trade.get('team_b', 'Team B')} trade"
        )
        source_key = str(
            trade.get(
                "trade_id",
                topic_label
            )
        )
    elif games:
        game = games[0]
        topic_type = classify_weekly_game_context(
            game
        )
        topic_label = game.get(
            "game",
            "the featured game"
        )
        source_key = str(
            game.get(
                "schedule_id",
                topic_label
            )
        )
    elif players:
        player = players[0]
        topic_type = "player"
        topic_label = player.get(
            "player",
            "the featured player"
        )
        source_key = topic_label
    elif predictions:
        pick = predictions[0]
        topic_type = "prediction"
        topic_label = pick.get(
            "matchup",
            "the featured matchup"
        )
        source_key = topic_label

    # Deterministic but changes across weeks/topics.
    debate_mode = stable_choice(
        [
            "agree",
            "agree",
            "disagree",
            "disagree",
            "mixed",
        ],
        (
            f"panel-debate-mode-"
            f"{season_type}-{week_number}-"
            f"{topic_type}-{source_key}"
        )
    )

    opener_pool = (
        PANEL_AGREEMENT_LINES
        if debate_mode == "agree"
        else PANEL_DEBATE_OPENERS
    )

    opener = stable_choice(
        opener_pool,
        (
            f"panel-debate-open-"
            f"{season_type}-{week_number}-"
            f"{topic_type}-{source_key}-"
            f"{debate_mode}"
        )
    )

    if debate_mode == "agree":
        stances = {
            "marcus": "agree",
            "stephen": "agree",
            "pat": "agree",
            "josh_pate": "agree",
        }
    elif debate_mode == "disagree":
        stances = {
            "marcus": "disagree",
            "stephen": "disagree",
            "pat": "agree",
            "josh_pate": "disagree",
        }
    else:
        # Mixed panels feel more natural.
        mixed_patterns = [
            {
                "marcus": "agree",
                "stephen": "disagree",
                "pat": "agree",
                "josh_pate": "disagree",
            },
            {
                "marcus": "disagree",
                "stephen": "agree",
                "pat": "disagree",
                "josh_pate": "agree",
            },
            {
                "marcus": "agree",
                "stephen": "agree",
                "pat": "disagree",
                "josh_pate": "disagree",
            },
        ]

        stances = stable_choice(
            mixed_patterns,
            (
                f"panel-debate-pattern-"
                f"{season_type}-{week_number}-"
                f"{topic_type}-{source_key}"
            )
        )

    lines = {}

    pools = {
        "marcus": MARCUS_DEBATE_LINES,
        "stephen": STEPHEN_A_DEBATE_LINES,
        "pat": PAT_MCAFEE_DEBATE_LINES,
        "josh_pate": JOSH_PATE_DEBATE_LINES,
    }

    for analyst, stance in stances.items():
        line = stable_choice(
            pools[analyst][stance],
            (
                f"panel-debate-{analyst}-"
                f"{season_type}-{week_number}-"
                f"{topic_type}-{source_key}-"
                f"{stance}"
            )
        )

        lines[analyst] = line

    # Add topic-specific follow-ups so the debate is about actual league data.
    if topic_type == "receipt" and receipt_target:
        missed_analyst = receipt_target.get(
            "analyst"
        )

        missed_name = (
            ANALYST_DISPLAY_NAMES.get(
                missed_analyst,
                receipt_target.get(
                    "analyst_name",
                    missed_analyst
                )
            )
        )

        pick = receipt_target.get(
            "pick"
        )

        actual = receipt_target.get(
            "actual_winner"
        )

        matchup = receipt_target.get(
            "matchup"
        )

        # Everyone else gets a chance to call out the bad pick.
        if missed_analyst != "marcus":
            lines["marcus"] += (
                f" {missed_name}, you picked **{pick}** in {matchup}. "
                f"**{actual}** won. The receipt is right there."
            )
        else:
            lines["marcus"] += (
                " I got that one wrong. Put the loss on my record."
            )

        if missed_analyst != "stephen":
            lines["stephen"] += (
                f" {missed_name}, do not change the subject. "
                f"You picked **{pick}** and **{actual}** beat them."
            )
        else:
            lines["stephen"] += (
                " Fine, the pick missed. I will own it, but I am not lowering the standard."
            )

        if missed_analyst != "pat":
            lines["pat"] += (
                f" Somebody save the screenshot. {missed_name} had **{pick}**, "
                f"and **{actual}** just cooked the prediction."
            )
        else:
            lines["pat"] += (
                " Yep, that receipt is mine. Clip it and give the winner credit."
            )

        if missed_analyst != "josh_pate":
            lines["josh_pate"] += (
                f" The pregame assumption behind {missed_name}'s **{pick}** pick "
                f"did not survive the actual matchup. That is useful evidence."
            )
        else:
            lines["josh_pate"] += (
                " That prediction missed. The result tells me the pregame assumption needs updating."
            )

    elif topic_type == "rivalry" and rivalry_spotlight:
        item = rivalry_spotlight[0]
        rivalry = item.get(
            "rivalry",
            {}
        )

        meetings = rivalry.get(
            "meetings",
            0
        )

        streak = rivalry.get(
            "current_streak"
        )

        streak_text = (
            (
                f"{streak.get('owner')} has won "
                f"{streak.get('wins')} straight"
            )
            if streak
            else "there is no active winning streak"
        )

        lines["marcus"] += (
            f" This is meeting number {meetings}. "
            f"The history matters now, and {streak_text}."
        )

        lines["stephen"] += (
            " Rivalries create expectations. I do not want excuses "
            "from somebody who has been hearing about this matchup all week."
        )

        lines["pat"] += (
            " This is exactly the kind of game that gets the whole server watching."
        )

        lines["josh_pate"] += (
            " Rivalries are useful because repeated matchups reveal which adjustments "
            "are real and which advantages were temporary."
        )

    elif topic_type == "playoff" and playoff_race:
        gotw = playoff_race.get(
            "game_of_the_week",
            {}
        )

        lines["marcus"] += (
            f" {gotw.get('matchup')} has playoff-race consequences. "
            "That makes every mistake more expensive."
        )

        lines["stephen"] += (
            " This is when contenders stop talking and start protecting their season."
        )

        lines["pat"] += (
            " Playoff pressure in Madden is awesome because one turnover can move the whole bracket."
        )

        lines["josh_pate"] += (
            " Late-season playoff games tell you whether a team's weekly process survives real pressure."
        )

    elif topic_type == "trade" and trades:
        trade = trades[0]
        review = (
            trade.get(
                "trade_committee",
                {}
            )
            if isinstance(
                trade.get(
                    "trade_committee"
                ),
                dict
            )
            else {}
        )

        decision = review.get(
            "decision",
            "UNKNOWN"
        )

        gap = review.get(
            "value_gap_percent",
            "—"
        )

        lines["marcus"] += (
            f" On {topic_label}, the League Office has it at "
            f"**{decision}** with a {gap}% value gap."
        )

        lines["stephen"] += (
            " If one side is giving up the better premium asset, "
            "I want the roster logic explained before I praise anybody."
        )

        lines["pat"] += (
            " I care about whether the move fixes a real weakness "
            "or just makes the depth chart look more exciting."
        )

        lines["josh_pate"] += (
            " I want to know what each roster looks like two moves after this one, "
            "because good roster building is about optionality."
        )

    elif games:
        game = games[0]
        winner = game.get(
            "winner",
            "the winner"
        )
        loser = game.get(
            "loser",
            "the loser"
        )

        lines["marcus"] += (
            f" {winner} earned the result; {loser} has to answer for the mistakes."
        )

        lines["stephen"] += (
            f" I am judging {loser} against the standard their roster created."
        )

        lines["pat"] += (
            f" {winner} made the winning plays when the game tilted."
        )

        lines["josh_pate"] += (
            f" The question for {winner} is whether this winning formula travels next week."
        )

    elif topic_type == "player" and players:
        player_name = players[0].get(
            "player",
            "the featured player"
        )

        lines["marcus"] += (
            f" {player_name} earned the spotlight with actual production."
        )

        lines["stephen"] += (
            f" If {player_name} keeps producing, defenses have to change the plan."
        )

        lines["pat"] += (
            f" {player_name} is becoming appointment viewing in this league."
        )

        lines["josh_pate"] += (
            f" The value of {player_name} is whether that performance raises the unit's weekly floor."
        )

    elif topic_type == "prediction" and predictions:
        pick = predictions[0]
        favorite = pick.get(
            "favorite",
            "TOSS-UP"
        )

        lines["marcus"] += (
            f" My early lean in {topic_label} is **{favorite}**."
        )

        lines["stephen"] += (
            " Whoever has the stronger roster still has to prove it under pressure."
        )

        lines["pat"] += (
            " One turnover can flip the whole pick, which is why this matchup is fun."
        )

        lines["josh_pate"] += (
            " I care more about the repeatable matchup advantage than the headline OVR."
        )

    return {
        "topic_type":
            topic_type,
        "topic":
            topic_label,
        "mode":
            debate_mode,
        "opener":
            opener,
        "marcus":
            lines.get(
                "marcus",
                ""
            ),
        "stephen":
            lines.get(
                "stephen",
                ""
            ),
        "pat":
            lines.get(
                "pat",
                ""
            ),
        "josh_pate":
            lines.get(
                "josh_pate",
                ""
            ),
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

    # Save the desk's pregame picks, then grade any games
    # from this week that have already been completed.
    record_weekly_analyst_predictions(
        season_type,
        week_number,
        game_predictions
    )

    settle_analyst_predictions(
        season_type,
        week_number
    )

    analyst_receipts = (
        analyst_receipts_leaderboard()
    )

    receipts_callout = (
        build_receipts_callout(
            season_type,
            week_number
        )
    )

    trade_proposals = (
        weekly_trade_proposals()
    )

    fan_gotw = (
        latest_closed_gotw(
            season_type,
            week_number
        )
    )

    # Keep rivalry history current as completed games appear.
    try:
        record_rivalry_week(
            season_type,
            week_number
        )
    except Exception as e:
        print(
            "RIVALRY UPDATE ERROR:",
            str(e)
        )

    playoff_race = (
        build_playoff_race(
            season_type,
            week_number
        )
    )

    rivalry_spotlight = (
        rivalry_week_spotlight(
            season_type,
            week_number
        )
    )

    analyst_accuracy = (
        analyst_accuracy_by_category()
    )

    super_bowl_favorites = (
        build_super_bowl_favorites()
    )

    hot_seat = (
        build_hot_seat_rankings()
    )

    fraud_watch = (
        build_fraud_watch()
    )

    dark_horse_watch = (
        build_dark_horse_watch()
    )

    watch_panel_takes = (
        build_watch_panel_takes(
            fraud_watch,
            dark_horse_watch
        )
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

    josh_pate_segment = (
        build_josh_pate_segment(
            season_type,
            week_number
        )
    )

    injuries = all_current_injuries()

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
        "analyst_receipts":
            analyst_receipts,
        "receipts_callout":
            receipts_callout,
        "trade_proposals":
            trade_proposals,
        "fan_gotw":
            fan_gotw,
        "injuries":
            injuries,
        "playoff_race":
            playoff_race,
        "rivalry_spotlight":
            rivalry_spotlight,
        "analyst_accuracy":
            analyst_accuracy,
        "super_bowl_favorites":
            super_bowl_favorites,
        "hot_seat":
            hot_seat,
        "hot_seat_panel_take":
            build_hot_seat_panel_take(
                hot_seat
            ),
        "fraud_watch":
            fraud_watch,
        "dark_horse_watch":
            dark_horse_watch,
        "watch_panel_takes":
            watch_panel_takes,
        "stephen_a_parody_segment":
            stephen_segment[:2],
        "pat_mcafee_parody_segment":
            pat_segment[:2],
        "josh_pate_parody_segment":
            josh_pate_segment[:2],
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

    show["panel_debate"] = (
        build_panel_debate(
            show,
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

            analyst_picks = (
                build_analyst_pick_set(
                    pick,
                    show.get(
                        "season_type"
                    ),
                    show.get(
                        "week"
                    )
                )
            )

            lines.append(
                f"**{pick.get('matchup')}**\n"
                f"Model lean: **{pick_text}** — "
                f"{pick.get('reason')}\n"
                f"Marcus: **{analyst_picks.get('marcus')}** | "
                f"Stephen A.: **{analyst_picks.get('stephen')}** | "
                f"Pat: **{analyst_picks.get('pat')}** | "
                f"Josh Pate: **{analyst_picks.get('josh_pate')}**"
            )

        fields.append({
            "name":
                "🎯 Weekly Picks & Favorites",
            "value":
                "\n\n".join(lines)[:1024],
            "inline":
                False
        })

    receipts = show.get(
        "analyst_receipts",
        []
    )

    if receipts:
        lines = []

        for item in receipts:
            record = (
                f"{item.get('wins', 0)}-"
                f"{item.get('losses', 0)}"
            )

            if item.get(
                "pushes",
                0
            ):
                record += (
                    f"-{item.get('pushes', 0)}"
                )

            lines.append(
                f"{item.get('rank')}. "
                f"**{item.get('name')}** — "
                f"{record} "
                f"({item.get('win_pct', 0)}%)"
            )

        fields.append({
            "name":
                "🧾 Analyst Receipts — Prediction Records",
            "value":
                "\n".join(lines)[:1024],
            "inline":
                False
        })

    receipt_callout = show.get(
        "receipts_callout",
        {}
    )

    if (
        receipt_callout
        and receipt_callout.get(
            "target"
        )
    ):
        fields.append({
            "name":
                "📸 Receipt Check",
            "value":
                receipt_callout.get(
                    "take",
                    ""
                )[:1024],
            "inline":
                False
        })

    playoff_race = show.get(
        "playoff_race",
        {}
    )

    if playoff_race:
        lines = []

        for conference in [
            "AFC",
            "NFC"
        ]:
            picture = playoff_race.get(
                conference,
                {}
            )

            seeds = picture.get(
                "seeds",
                []
            )

            if not seeds:
                continue

            seed_text = []

            for index, team in enumerate(
                seeds[:7],
                start=1
            ):
                seed = team.get(
                    "playoff_seed",
                    team.get(
                        "projected_seed",
                        index
                    )
                )

                seed_text.append(
                    f"#{seed} {team.get('team')} "
                    f"({team.get('wins', 0)}-{team.get('losses', 0)})"
                )

            lines.append(
                f"**{conference}:** "
                + " | ".join(
                    seed_text
                )
            )

        gotw = playoff_race.get(
            "game_of_the_week"
        )

        if gotw:
            lines.append(
                (
                    f"\n**Playoff Game of the Week:** "
                    f"{gotw.get('matchup')}\n"
                    + "; ".join(
                        gotw.get(
                            "reasons",
                            []
                        )
                    )
                )
            )

        if lines:
            fields.append({
                "name":
                    "🏆 Playoff Race",
                "value":
                    "\n".join(
                        lines
                    )[:1024],
                "inline":
                    False
            })

        scenarios = playoff_race.get(
            "clinching_scenarios",
            []
        )

        if scenarios:
            scenario_lines = []

            for item in scenarios[:6]:
                scenario_lines.append(
                    (
                        f"**{item.get('team')}** "
                        f"({item.get('record')}) — "
                        f"{item.get('scenario')}"
                    )
                )

            fields.append({
                "name":
                    "🔐 Clinching / Must-Win Watch",
                "value":
                    "\n".join(
                        scenario_lines
                    )[:1024],
                "inline":
                    False
            })

    rivalry_spotlight = show.get(
        "rivalry_spotlight",
        []
    )

    if rivalry_spotlight:
        lines = []

        for item in rivalry_spotlight[:3]:
            rivalry = item.get(
                "rivalry",
                {}
            )

            streak = rivalry.get(
                "current_streak"
            )

            streak_text = (
                (
                    f"{streak.get('owner')} "
                    f"has won {streak.get('wins')} straight"
                )
                if streak
                else "No active streak"
            )

            lines.append(
                (
                    f"**{item.get('matchup')}**\n"
                    f"{item.get('away_owner')} vs {item.get('home_owner')} — "
                    f"{rivalry.get('meetings', 0)} previous meetings\n"
                    f"{streak_text}"
                )
            )

        fields.append({
            "name":
                "⚔️ Rivalry Week",
            "value":
                "\n\n".join(
                    lines
                )[:1024],
            "inline":
                False
        })

    analyst_accuracy = show.get(
        "analyst_accuracy",
        {}
    )

    if analyst_accuracy:
        lines = []

        for analyst in [
            "marcus",
            "stephen",
            "pat",
            "josh_pate"
        ]:
            data = analyst_accuracy.get(
                analyst,
                {}
            )

            categories = data.get(
                "categories",
                {}
            )

            overall = categories.get(
                "overall",
                {}
            )

            upset = categories.get(
                "upset_picks",
                {}
            )

            favorite = categories.get(
                "favorite_picks",
                {}
            )

            lines.append(
                (
                    f"**{data.get('name')}** — "
                    f"Overall {overall.get('wins', 0)}-"
                    f"{overall.get('losses', 0)} "
                    f"({overall.get('win_pct', 0)}%) | "
                    f"Upsets {upset.get('wins', 0)}-"
                    f"{upset.get('losses', 0)} | "
                    f"Favorites {favorite.get('wins', 0)}-"
                    f"{favorite.get('losses', 0)}"
                )
            )

        fields.append({
            "name":
                "🎯 Analyst Accuracy by Category",
            "value":
                "\n".join(
                    lines
                )[:1024],
            "inline":
                False
        })

    injuries = show.get(
        "injuries",
        []
    )

    if injuries:
        lines = [
            (
                f"**{x.get('team')} — {x.get('player')}** "
                f"({x.get('overall') or '—'} OVR) • "
                f"{injury_summary_label(x)}"
            )
            for x in injuries[:8]
        ]

        fields.append({
            "name": "🚑 Injury Report",
            "value": "\n".join(lines)[:1024],
            "inline": False
        })

    fan_gotw = show.get(
        "fan_gotw"
    )

    if fan_gotw:
        winner = fan_gotw.get(
            "winner"
        )

        winner_item = next(
            (
                item
                for item in fan_gotw.get(
                    "candidates",
                    []
                )
                if item.get(
                    "team"
                ) == winner
            ),
            {}
        )

        fields.append({
            "name":
                "🏆 Fan-Voted Game of the Week",
            "value": (
                f"**{winner}** won the fan vote.\n"
                f"Featured matchup: "
                f"**{winner_item.get('matchup', '—')}**\n"
                f"Votes: "
                f"**{fan_gotw.get('vote_counts', {}).get(winner, 0)}**"
            )[:1024],
            "inline":
                False
        })

    fraud_watch = show.get(
        "fraud_watch",
        []
    )

    if fraud_watch:
        lines = []
        for index, item in enumerate(fraud_watch, start=1):
            lines.append(
                f"{index}. **{item.get('team')}** — "
                f"{item.get('record')} | {item.get('overall')} OVR | "
                f"Point Diff {item.get('point_diff')}\n"
                + "; ".join(item.get("reasons", []))
            )

        fields.append({
            "name": "🚨 Fraud Watch",
            "value": "\n\n".join(lines)[:1024],
            "inline": False,
        })

    dark_horses = show.get(
        "dark_horse_watch",
        []
    )

    if dark_horses:
        lines = []
        for index, item in enumerate(dark_horses, start=1):
            lines.append(
                f"{index}. **{item.get('team')}** — "
                f"{item.get('record')} | {item.get('overall')} OVR | "
                f"Point Diff {item.get('point_diff')}\n"
                + "; ".join(item.get("reasons", []))
            )

        fields.append({
            "name": "🐎 Dark Horse Watch",
            "value": "\n\n".join(lines)[:1024],
            "inline": False,
        })

    watch_takes = show.get(
        "watch_panel_takes",
        {}
    )

    fraud_take = (
        watch_takes.get("fraud_watch")
        if isinstance(watch_takes, dict)
        else None
    )

    dark_take = (
        watch_takes.get("dark_horse")
        if isinstance(watch_takes, dict)
        else None
    )

    if fraud_take:
        fields.append({
            "name": "🚨 Fraud Watch — Panel",
            "value": (
                f"**Marcus Hayes:** {fraud_take.get('marcus', '')}\n\n"
                f"**Stephen A. Smith — AI Parody:** {fraud_take.get('stephen', '')}\n\n"
                f"**Pat McAfee — AI Parody:** {fraud_take.get('pat', '')}\n\n"
                "*Stephen A. Smith and Pat McAfee content is fictional AI parody; Josh Pate appears in the main Weekly Show panel as AI parody.*"
            )[:1024],
            "inline": False,
        })

    if dark_take:
        fields.append({
            "name": "🐎 Dark Horse Watch — Panel",
            "value": (
                f"**Marcus Hayes:** {dark_take.get('marcus', '')}\n\n"
                f"**Stephen A. Smith — AI Parody:** {dark_take.get('stephen', '')}\n\n"
                f"**Pat McAfee — AI Parody:** {dark_take.get('pat', '')}\n\n"
                "*Stephen A. Smith and Pat McAfee content is fictional AI parody; Josh Pate appears in the main Weekly Show panel as AI parody.*"
            )[:1024],
            "inline": False,
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

        fields.append({
            "name":
                "🏈 Josh Pate — AI Parody",
            "value": (
                panel.get(
                    "josh_pate",
                    ""
                )
                + "\n\n*Fictional AI parody — not a real "
                "Josh Pate statement.*"
            )[:1024],
            "inline":
                False
        })

    debate = show.get(
        "panel_debate",
        {}
    )

    if debate:
        fields.append({
            "name":
                "🗣️ Panel Debate",
            "value": (
                f"**Topic:** {debate.get('topic', 'Weekly Show')}\n"
                f"*{debate.get('opener', '')}*\n\n"
                f"**Marcus Hayes:** {debate.get('marcus', '')}\n\n"
                f"**Stephen A. Smith — AI Parody:** {debate.get('stephen', '')}\n\n"
                f"**Pat McAfee — AI Parody:** {debate.get('pat', '')}\n\n"
                f"**Josh Pate — AI Parody:** {debate.get('josh_pate', '')}\n\n"
                "*Stephen A. Smith, Pat McAfee, and Josh Pate content is fictional AI parody "
                "and not real statements from those people.*"
            )[:1024],
            "inline":
                False
        })

    josh_pate_segment = show.get(
        "josh_pate_parody_segment",
        []
    )

    if josh_pate_segment:
        lines = []

        for item in josh_pate_segment[:2]:
            headline = item.get(
                "headline",
                "Project Madden Breakdown"
            )

            take = item.get(
                "take",
                ""
            )

            lines.append(
                f"**{headline}**\n{take}"
            )

        fields.append({
            "name": (
                "🎙️ Josh Pate — "
                "AI Parody Segment"
            ),
            "value": (
                "\n\n".join(lines)
                + "\n\n*Fictional AI parody — "
                "not a real Josh Pate statement.*"
            )[:1024],
            "inline": False
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

    try:
        update_record_book_from_week(
            season_type,
            week_number
        )
    except Exception as e:
        print(
            "RECORD BOOK UPDATE ERROR:",
            str(e)
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
        "*Stephen A. Smith, Pat McAfee, and Josh Pate content in this show is fictional AI parody "
        "and not real statements from those people. Picks are Project Madden analysis "
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



def load_record_book():
    data = load_json_file(
        PROJECT_MADDEN_RECORD_BOOK_FILE
    )

    if not isinstance(data, dict):
        data = {}

    data.setdefault("champions", [])
    data.setdefault("mvps", [])
    data.setdefault("single_game_records", {})
    data.setdefault("longest_win_streak", None)
    data.setdefault("biggest_blowout", None)
    data.setdefault("best_user_season", None)
    data.setdefault("legendary_trades", [])

    return data


def save_record_book(data):
    save_json_file(
        PROJECT_MADDEN_RECORD_BOOK_FILE,
        data
    )


def load_hall_of_fame():
    data = load_json_file(
        PROJECT_MADDEN_HALL_OF_FAME_FILE
    )
    return data if isinstance(data, list) else []



def hall_of_fame_channel_id():
    return os.environ.get(
        "HALL_OF_FAME_CHANNEL_ID",
        ""
    ).strip()



def hall_of_fame_category_id():
    raw = os.environ.get(
        "HALL_OF_FAME_CATEGORY_ID",
        ""
    ).strip()

    # Discord category IDs are numeric snowflakes.
    # If somebody accidentally pastes a webhook URL here,
    # ignore it instead of letting channel creation fail.
    if not re.fullmatch(
        r"\d{15,22}",
        raw
    ):
        return ""

    return raw


def hall_of_fame_category_config_issue():
    raw = os.environ.get(
        "HALL_OF_FAME_CATEGORY_ID",
        ""
    ).strip()

    if not raw:
        return None

    if re.fullmatch(
        r"\d{15,22}",
        raw
    ):
        return None

    return (
        "HALL_OF_FAME_CATEGORY_ID is configured, "
        "but it is not a valid numeric Discord category ID. "
        "Channel creation will continue without a parent category."
    )


def safe_discord_channel_name(
    value
):
    text = str(
        value
        or "hall-of-famer"
    ).strip().lower()

    text = re.sub(
        r"[^a-z0-9]+",
        "-",
        text
    )

    text = re.sub(
        r"-+",
        "-",
        text
    ).strip("-")

    if not text:
        text = "hall-of-famer"

    return text[:80]


def hall_of_fame_logo_url(
    hof_id
):
    return (
        "https://project-madden-analytics.onrender.com/"
        f"hall-of-fame/logo/{hof_id}.png"
    )


def hall_of_fame_entry_by_id(
    hof_id
):
    target = str(
        hof_id
        or ""
    ).strip()

    for item in load_hall_of_fame():
        if (
            isinstance(
                item,
                dict
            )
            and str(
                item.get(
                    "hof_id",
                    ""
                )
            )
            == target
        ):
            return item

    return None


def update_hall_of_fame_entry(
    hof_id,
    updates
):
    hall = load_hall_of_fame()
    updated = None

    for item in hall:
        if not isinstance(
            item,
            dict
        ):
            continue

        if str(
            item.get(
                "hof_id",
                ""
            )
        ) != str(
            hof_id
        ):
            continue

        item.update(
            updates
        )
        updated = item
        break

    if updated is not None:
        save_hall_of_fame(
            hall
        )

    return updated


def generate_hall_of_fame_logo_image(
    entry
):
    from PIL import Image, ImageDraw, ImageFont

    size = 1024

    image = Image.new(
        "RGB",
        (
            size,
            size
        ),
        (
            13,
            16,
            24
        )
    )

    draw = ImageDraw.Draw(
        image
    )

    # Gold-style ring and inner badge.
    draw.ellipse(
        (
            70,
            70,
            954,
            954
        ),
        outline=(
            214,
            173,
            71
        ),
        width=28
    )

    draw.ellipse(
        (
            115,
            115,
            909,
            909
        ),
        outline=(
            105,
            85,
            42
        ),
        width=10
    )

    name = str(
        entry.get(
            "name",
            "Hall of Famer"
        )
    ).strip()

    hof_type = str(
        entry.get(
            "type",
            "Inductee"
        )
    ).strip()

    class_year = str(
        entry.get(
            "class_year",
            ""
        )
    ).strip()

    words = [
        part
        for part in re.split(
            r"\s+",
            name
        )
        if part
    ]

    initials = "".join(
        word[0]
        for word in words[:3]
    ).upper()

    if not initials:
        initials = "HOF"

    # Use Pillow's bundled/default font fallback so no external font file is needed.
    try:
        font_big = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            190
        )
        font_title = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            58
        )
        font_small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            38
        )
    except Exception:
        font_big = ImageFont.load_default()
        font_title = ImageFont.load_default()
        font_small = ImageFont.load_default()

    def centered_text(
        y,
        text,
        font,
        fill
    ):
        box = draw.textbbox(
            (
                0,
                0
            ),
            text,
            font=font
        )

        width = (
            box[2]
            - box[0]
        )

        draw.text(
            (
                (
                    size
                    - width
                )
                / 2,
                y
            ),
            text,
            font=font,
            fill=fill
        )

    centered_text(
        155,
        "PROJECT MADDEN",
        font_title,
        (
            214,
            173,
            71
        )
    )

    centered_text(
        355,
        initials,
        font_big,
        (
            244,
            244,
            246
        )
    )

    centered_text(
        665,
        "HALL OF FAME",
        font_title,
        (
            214,
            173,
            71
        )
    )

    centered_text(
        745,
        hof_type[:26],
        font_small,
        (
            210,
            212,
            218
        )
    )

    if class_year:
        centered_text(
            810,
            f"CLASS OF {class_year}",
            font_small,
            (
                210,
                212,
                218
            )
        )

    return image


def create_hall_of_fame_inductee_channel(
    entry
):
    token = discord_bot_token()
    guild_id = discord_guild_id()

    if (
        not token
        or not guild_id
    ):
        return {
            "success":
                False,
            "error":
                (
                    "DISCORD_BOT_TOKEN and DISCORD_GUILD_ID "
                    "are required to create Hall of Fame channels."
                )
        }

    channel_name = (
        "hof-"
        + safe_discord_channel_name(
            entry.get(
                "name"
            )
        )
    )[:100]

    payload = {
        "name":
            channel_name,
        "type":
            0,
        "topic": (
            f"Project Madden Hall of Fame • "
            f"{entry.get('name')} • "
            f"Class of {entry.get('class_year')}"
        )[:1024],
        # Make the inductee channel a read-only museum page for members.
        "permission_overwrites": [
            {
                "id":
                    str(
                        guild_id
                    ),
                "type":
                    0,
                "deny":
                    str(
                        2048
                    )
            }
        ]
    }

    category_id = (
        hall_of_fame_category_id()
    )

    category_warning = (
        hall_of_fame_category_config_issue()
    )

    if category_id:
        payload[
            "parent_id"
        ] = category_id

    response = requests.post(
        (
            "https://discord.com/api/v10/"
            f"guilds/{guild_id}/channels"
        ),
        headers={
            "Authorization":
                f"Bot {token}",
            "Content-Type":
                "application/json"
        },
        json=payload,
        timeout=15
    )

    if response.status_code not in [
        200,
        201
    ]:
        return {
            "success":
                False,
            "status_code":
                response.status_code,
            "error":
                response.text[:500]
        }

    channel = response.json()

    return {
        "success":
            True,
        "channel_id":
            str(
                channel.get(
                    "id",
                    ""
                )
            ),
        "channel_name":
            channel.get(
                "name"
            ),
        "category_warning":
            category_warning
    }


def post_hall_of_fame_inductee_profile(
    entry,
    channel_id
):
    token = discord_bot_token()

    if (
        not token
        or not channel_id
    ):
        return {
            "sent":
                False,
            "error":
                "Bot token or channel ID missing."
        }

    logo_url = hall_of_fame_logo_url(
        entry.get(
            "hof_id"
        )
    )

    fields = [
        {
            "name":
                "🏈 Team / Organization",
            "value":
                entry.get(
                    "team",
                    "Project Madden"
                ),
            "inline":
                True
        },
        {
            "name":
                "🏆 Championships",
            "value":
                str(
                    entry.get(
                        "championships",
                        0
                    )
                ),
            "inline":
                True
        }
    ]

    if entry.get(
        "career_record"
    ):
        fields.append({
            "name":
                "📊 Career Record",
            "value":
                entry.get(
                    "career_record"
                ),
            "inline":
                True
        })

    if entry.get(
        "awards"
    ):
        fields.append({
            "name":
                "🥇 Awards & Honors",
            "value":
                entry.get(
                    "awards"
                )[:1024],
            "inline":
                False
        })

    fields.append({
        "name":
            "📜 Hall of Fame Case",
        "value":
            entry.get(
                "reason",
                ""
            )[:1024],
        "inline":
            False
    })

    embed = {
        "title":
            "🏛️ PROJECT MADDEN HALL OF FAME",
        "description": (
            f"## {entry.get('name')}\n"
            f"**{entry.get('type')}**\n"
            f"**Class of {entry.get('class_year')}**"
        ),
        "thumbnail": {
            "url":
                logo_url
        },
        "image": {
            "url":
                logo_url
        },
        "fields":
            fields,
        "footer": {
            "text":
                (
                    "Project Madden Hall of Fame • "
                    f"{entry.get('hof_id')}"
                )
        }
    }

    response = requests.post(
        (
            "https://discord.com/api/v10/"
            f"channels/{channel_id}/messages"
        ),
        headers={
            "Authorization":
                f"Bot {token}",
            "Content-Type":
                "application/json"
        },
        json={
            "content": (
                f"🏛️ Welcome to the official Hall of Fame channel "
                f"for **{entry.get('name')}**."
            ),
            "embeds": [
                embed
            ],
            "allowed_mentions": {
                "parse": []
            }
        },
        timeout=15
    )

    return {
        "sent":
            response.status_code
            in [
                200,
                201
            ],
        "status_code":
            response.status_code,
        "error":
            (
                ""
                if response.status_code
                in [
                    200,
                    201
                ]
                else response.text[:500]
            )
    }



def delete_discord_channel(
    channel_id
):
    token = discord_bot_token()

    if (
        not token
        or not channel_id
    ):
        return {
            "success":
                False,
            "error":
                "Bot token or channel ID missing."
        }

    response = requests.delete(
        (
            "https://discord.com/api/v10/"
            f"channels/{channel_id}"
        ),
        headers={
            "Authorization":
                f"Bot {token}"
        },
        timeout=15
    )

    return {
        "success":
            response.status_code
            in [
                200,
                204
            ],
        "status_code":
            response.status_code,
        "error":
            (
                ""
                if response.status_code
                in [
                    200,
                    204
                ]
                else response.text[:500]
            )
    }


def post_hall_of_fame_test_profile(
    entry,
    channel_id
):
    from PIL import Image

    token = discord_bot_token()

    if (
        not token
        or not channel_id
    ):
        return {
            "sent":
                False,
            "error":
                "Bot token or channel ID missing."
        }

    image = generate_hall_of_fame_logo_image(
        entry
    )

    buffer = io.BytesIO()

    image.save(
        buffer,
        format="PNG",
        optimize=True
    )

    buffer.seek(
        0
    )

    embed = {
        "title":
            "🧪 PROJECT MADDEN HALL OF FAME TEST",
        "description": (
            f"## {entry.get('name')}\n"
            f"**{entry.get('type')}**\n"
            f"**Class of {entry.get('class_year')}**\n\n"
            "This is a test induction only. "
            "It is not being saved to the permanent Hall of Fame."
        ),
        "thumbnail": {
            "url":
                "attachment://hall-of-fame-test.png"
        },
        "image": {
            "url":
                "attachment://hall-of-fame-test.png"
        },
        "fields": [
            {
                "name":
                    "🏈 Team / Organization",
                "value":
                    entry.get(
                        "team",
                        "Project Madden"
                    ),
                "inline":
                    True
            },
            {
                "name":
                    "🏆 Championships",
                "value":
                    str(
                        entry.get(
                            "championships",
                            0
                        )
                    ),
                "inline":
                    True
            },
            {
                "name":
                    "📜 Test Hall of Fame Case",
                "value":
                    entry.get(
                        "reason",
                        "Testing the Project Madden Hall of Fame system."
                    )[:1024],
                "inline":
                    False
            }
        ],
        "footer": {
            "text":
                "TEST ONLY • Channel auto-deletes in 5 minutes"
        }
    }

    payload_json = json.dumps({
        "content":
            "🧪 **HALL OF FAME SYSTEM TEST**",
        "embeds": [
            embed
        ],
        "allowed_mentions": {
            "parse": []
        }
    })

    response = requests.post(
        (
            "https://discord.com/api/v10/"
            f"channels/{channel_id}/messages"
        ),
        headers={
            "Authorization":
                f"Bot {token}"
        },
        data={
            "payload_json":
                payload_json
        },
        files={
            "files[0]": (
                "hall-of-fame-test.png",
                buffer.getvalue(),
                "image/png"
            )
        },
        timeout=20
    )

    return {
        "sent":
            response.status_code
            in [
                200,
                201
            ],
        "status_code":
            response.status_code,
        "error":
            (
                ""
                if response.status_code
                in [
                    200,
                    201
                ]
                else response.text[:500]
            )
    }


def create_hall_of_fame_test_channel(
    entry
):
    token = discord_bot_token()
    guild_id = discord_guild_id()

    if (
        not token
        or not guild_id
    ):
        return {
            "success":
                False,
            "error":
                (
                    "DISCORD_BOT_TOKEN and DISCORD_GUILD_ID "
                    "are required."
                )
        }

    channel_name = (
        "test-hof-"
        + safe_discord_channel_name(
            entry.get(
                "name"
            )
        )
    )[:100]

    payload = {
        "name":
            channel_name,
        "type":
            0,
        "topic": (
            "TEST ONLY • Project Madden Hall of Fame system check • "
            "Auto-deletes in 5 minutes"
        )[:1024],
        "permission_overwrites": [
            {
                "id":
                    str(
                        guild_id
                    ),
                "type":
                    0,
                "deny":
                    str(
                        2048
                    )
            }
        ]
    }

    category_id = hall_of_fame_category_id()
    category_warning = hall_of_fame_category_config_issue()

    if category_id:
        payload[
            "parent_id"
        ] = category_id

    response = requests.post(
        (
            "https://discord.com/api/v10/"
            f"guilds/{guild_id}/channels"
        ),
        headers={
            "Authorization":
                f"Bot {token}",
            "Content-Type":
                "application/json"
        },
        json=payload,
        timeout=15
    )

    if response.status_code not in [
        200,
        201
    ]:
        return {
            "success":
                False,
            "status_code":
                response.status_code,
            "error":
                response.text[:500]
        }

    channel = response.json()

    return {
        "success":
            True,
        "channel_id":
            str(
                channel.get(
                    "id",
                    ""
                )
            ),
        "channel_name":
            channel.get(
                "name"
            ),
        "category_warning":
            category_warning
    }


def cleanup_hall_of_fame_test_channel_after_delay(
    channel_id,
    delay_seconds=300
):
    time.sleep(
        delay_seconds
    )

    try:
        delete_discord_channel(
            channel_id
        )
    except Exception as e:
        print(
            "HOF TEST CHANNEL CLEANUP ERROR:",
            str(
                e
            )
        )


def run_hall_of_fame_test(
    name="Project Madden Test Inductee",
    inductee_type="Owner / Coach",
    team="Project Madden",
    championships=2
):
    entry = {
        "hof_id":
            "test-" + uuid.uuid4().hex[:8],
        "name":
            str(
                name
            ).strip()
            or "Project Madden Test Inductee",
        "type":
            str(
                inductee_type
            ).strip()
            or "Owner / Coach",
        "team":
            str(
                team
            ).strip()
            or "Project Madden",
        "reason":
            (
                "Testing the full Hall of Fame workflow: "
                "channel creation, custom logo generation, "
                "profile embed, permissions, and cleanup."
            ),
        "career_record":
            "TEST",
        "championships":
            int(
                championships
                or 0
            ),
        "awards":
            "Hall of Fame System Test",
        "class_year":
            datetime.now(
                timezone.utc
            ).year
    }

    channel_result = create_hall_of_fame_test_channel(
        entry
    )

    if not channel_result.get(
        "success"
    ):
        return {
            "success":
                False,
            "stage":
                "channel_creation",
            "error":
                channel_result.get(
                    "error",
                    "Unknown channel creation error"
                )
        }

    channel_id = channel_result.get(
        "channel_id"
    )

    profile_result = post_hall_of_fame_test_profile(
        entry,
        channel_id
    )

    if not profile_result.get(
        "sent"
    ):
        return {
            "success":
                False,
            "stage":
                "profile_post",
            "channel_id":
                channel_id,
            "error":
                profile_result.get(
                    "error",
                    "Unknown profile post error"
                )
        }

    worker = threading.Thread(
        target=cleanup_hall_of_fame_test_channel_after_delay,
        args=(
            channel_id,
            300
        ),
        daemon=True
    )

    worker.start()

    return {
        "success":
            True,
        "channel_id":
            channel_id,
        "channel_name":
            channel_result.get(
                "channel_name"
            ),
        "category_warning":
            channel_result.get(
                "category_warning"
            ),
        "auto_delete_seconds":
            300,
        "saved_permanently":
            False
    }


def provision_hall_of_fame_inductee_space(
    entry
):
    channel_result = (
        create_hall_of_fame_inductee_channel(
            entry
        )
    )

    if not channel_result.get(
        "success"
    ):
        return {
            "success":
                False,
            "channel":
                channel_result
        }

    channel_id = (
        channel_result.get(
            "channel_id"
        )
    )

    logo_url = hall_of_fame_logo_url(
        entry.get(
            "hof_id"
        )
    )

    updated = update_hall_of_fame_entry(
        entry.get(
            "hof_id"
        ),
        {
            "discord_channel_id":
                channel_id,
            "discord_channel_name":
                channel_result.get(
                    "channel_name"
                ),
            "logo_url":
                logo_url
        }
    )

    profile_result = (
        post_hall_of_fame_inductee_profile(
            updated
            or entry,
            channel_id
        )
    )

    return {
        "success":
            True,
        "channel":
            channel_result,
        "profile_post":
            profile_result,
        "logo_url":
            logo_url,
        "entry":
            updated
            or entry
    }


@app.route(
    "/hall-of-fame/logo/<hof_id>.png"
)
def hall_of_fame_logo_route(
    hof_id
):
    entry = hall_of_fame_entry_by_id(
        hof_id
    )

    if not entry:
        return (
            "Hall of Fame entry not found",
            404
        )

    image = (
        generate_hall_of_fame_logo_image(
            entry
        )
    )

    buffer = io.BytesIO()

    image.save(
        buffer,
        format="PNG",
        optimize=True
    )

    buffer.seek(
        0
    )

    return send_file(
        buffer,
        mimetype="image/png",
        download_name=(
            safe_discord_channel_name(
                entry.get(
                    "name"
                )
            )
            + "-hall-of-fame.png"
        )
    )


def hall_of_fame_discord_configured():
    return bool(
        discord_bot_token()
        and hall_of_fame_channel_id()
    )


def hall_of_fame_find_entry(
    query
):
    target = str(
        query
        or ""
    ).strip().lower()

    if not target:
        return None

    for item in load_hall_of_fame():
        if not isinstance(
            item,
            dict
        ):
            continue

        if str(
            item.get(
                "hof_id",
                ""
            )
        ).lower() == target:
            return item

        if str(
            item.get(
                "name",
                ""
            )
        ).strip().lower() == target:
            return item

    return None


def build_hall_of_fame_entry(
    *,
    name,
    inductee_type,
    team,
    reason,
    career_record="",
    championships=0,
    awards="",
    image_url="",
    inducted_by="",
    class_year=None
):
    if class_year is None:
        class_year = datetime.now(
            timezone.utc
        ).year

    return {
        "hof_id":
            uuid.uuid4().hex[:10],
        "name":
            str(
                name
            ).strip(),
        "type":
            str(
                inductee_type
            ).strip(),
        "team":
            str(
                team
                or "Project Madden"
            ).strip(),
        "reason":
            str(
                reason
            ).strip(),
        "career_record":
            str(
                career_record
                or ""
            ).strip(),
        "championships":
            int(
                championships
                or 0
            ),
        "awards":
            str(
                awards
                or ""
            ).strip(),
        "image_url":
            str(
                image_url
                or ""
            ).strip(),
        "class_year":
            int(
                class_year
            ),
        "inducted_by":
            str(
                inducted_by
                or ""
            ).strip(),
        "inducted_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


def add_hall_of_fame_entry(
    entry
):
    hall = load_hall_of_fame()

    existing = hall_of_fame_find_entry(
        entry.get(
            "name"
        )
    )

    if existing:
        return {
            "success":
                False,
            "error":
                (
                    f"{entry.get('name')} is already "
                    "in the Project Madden Hall of Fame."
                ),
            "existing":
                existing
        }

    hall.append(
        entry
    )

    save_hall_of_fame(
        hall
    )

    return {
        "success":
            True,
        "entry":
            entry
    }


def remove_hall_of_fame_entry(
    query
):
    hall = load_hall_of_fame()

    target = hall_of_fame_find_entry(
        query
    )

    if not target:
        return {
            "success":
                False,
            "error":
                "Hall of Fame entry not found."
        }

    target_id = target.get(
        "hof_id"
    )

    new_hall = [
        item
        for item in hall
        if not (
            isinstance(
                item,
                dict
            )
            and item.get(
                "hof_id"
            ) == target_id
        )
    ]

    save_hall_of_fame(
        new_hall
    )

    return {
        "success":
            True,
        "removed":
            target
    }


def send_hall_of_fame_induction_to_discord(
    entry
):
    channel_id = (
        hall_of_fame_channel_id()
    )

    token = (
        discord_bot_token()
    )

    if (
        not channel_id
        or not token
    ):
        return {
            "sent":
                False,
            "error":
                (
                    "DISCORD_BOT_TOKEN + "
                    "HALL_OF_FAME_CHANNEL_ID are required."
                )
        }

    fields = []

    fields.append({
        "name":
            "🏈 Team / Organization",
        "value":
            entry.get(
                "team",
                "Project Madden"
            ),
        "inline":
            True
    })

    fields.append({
        "name":
            "🏆 Championships",
        "value":
            str(
                entry.get(
                    "championships",
                    0
                )
            ),
        "inline":
            True
    })

    if entry.get(
        "career_record"
    ):
        fields.append({
            "name":
                "📊 Career Record",
            "value":
                entry.get(
                    "career_record"
                ),
            "inline":
                True
        })

    if entry.get(
        "awards"
    ):
        fields.append({
            "name":
                "🥇 Awards & Honors",
            "value":
                entry.get(
                    "awards"
                )[:1024],
            "inline":
                False
        })

    fields.append({
        "name":
            "📜 Why They Were Inducted",
        "value":
            entry.get(
                "reason",
                ""
            )[:1024],
        "inline":
            False
    })

    embed = {
        "title":
            "🏛️ PROJECT MADDEN HALL OF FAME",
        "description": (
            f"## {entry.get('name')}\n"
            f"**{entry.get('type')}**\n"
            f"**Class of {entry.get('class_year')}**\n\n"
            "Welcome to immortality."
        ),
        "fields":
            fields,
        "footer": {
            "text":
                (
                    "Project Madden Hall of Fame • "
                    f"Induction ID {entry.get('hof_id')}"
                )
        }
    }

    image_url = str(
        entry.get(
            "image_url",
            ""
        )
    ).strip()

    if image_url:
        embed[
            "thumbnail"
        ] = {
            "url":
                image_url
        }

    response = requests.post(
        (
            "https://discord.com/api/v10/"
            f"channels/{channel_id}/messages"
        ),
        headers={
            "Authorization":
                f"Bot {token}",
            "Content-Type":
                "application/json"
        },
        json={
            "content":
                "🏛️ **NEW PROJECT MADDEN HALL OF FAME INDUCTION**",
            "embeds": [
                embed
            ],
            "allowed_mentions": {
                "parse": []
            }
        },
        timeout=15
    )

    return {
        "sent":
            response.status_code
            in [
                200,
                201
            ],
        "status_code":
            response.status_code,
        "error":
            (
                ""
                if response.status_code
                in [
                    200,
                    201
                ]
                else response.text[:500]
            )
    }


def hall_of_fame_summary_text(
    limit=10
):
    hall = load_hall_of_fame()

    if not hall:
        return (
            "🏛️ **PROJECT MADDEN HALL OF FAME**\n"
            "No inductees yet."
        )

    sorted_hall = sorted(
        [
            item
            for item in hall
            if isinstance(
                item,
                dict
            )
        ],
        key=lambda item: (
            int(
                item.get(
                    "class_year",
                    0
                )
                or 0
            ),
            str(
                item.get(
                    "name",
                    ""
                )
            )
        ),
        reverse=True
    )

    lines = [
        "🏛️ **PROJECT MADDEN HALL OF FAME**"
    ]

    for item in sorted_hall[:limit]:
        lines.append(
            (
                f"**{item.get('name')}** — "
                f"{item.get('type', 'Inductee')} | "
                f"{item.get('team', 'Project Madden')} | "
                f"Class of {item.get('class_year', '—')} | "
                f"🏆 {item.get('championships', 0)}"
            )
        )

    if len(
        sorted_hall
    ) > limit:
        lines.append(
            f"...and {len(sorted_hall) - limit} more."
        )

    return "\n".join(
        lines
    )



def hall_of_fame_role_label(
    entry
):
    inductee_type = str(
        entry.get(
            "type",
            "Inductee"
        )
    ).strip().lower()

    if "player" in inductee_type:
        return "player"

    if (
        "owner" in inductee_type
        or "coach" in inductee_type
    ):
        return "coach"

    if (
        "commissioner" in inductee_type
        or "contributor" in inductee_type
    ):
        return "commissioner/contributor"

    if "team" in inductee_type:
        return "team"

    return "Hall of Famer"


def build_hall_of_fame_analyst_reactions(
    entry
):
    name = entry.get(
        "name",
        "this inductee"
    )

    team = entry.get(
        "team",
        "Project Madden"
    )

    role = hall_of_fame_role_label(
        entry
    )

    championships = int(
        entry.get(
            "championships",
            0
        )
        or 0
    )

    reason = str(
        entry.get(
            "reason",
            ""
        )
    ).strip()

    awards = str(
        entry.get(
            "awards",
            ""
        )
    ).strip()

    record = str(
        entry.get(
            "career_record",
            ""
        )
    ).strip()

    seed = (
        f"hof-{entry.get('hof_id')}-"
        f"{name}-{role}-{team}"
    )

    if role == "player":
        marcus_lines = [
            (
                f"{name} earned this. When you talk about a great player, "
                f"you are talking about somebody who changed games and left a mark "
                f"on {team}. That is Hall of Fame material."
            ),
            (
                f"I have no issue with this one. {name} was the kind of player "
                "people had to account for every week, and that impact is exactly "
                "what a Hall of Fame should recognize."
            ),
        ]

        stephen_lines = [
            (
                f"You do not put a player like {name} in the Hall of Fame by accident. "
                "If the production, winning, and impact are all there, then the résumé speaks loudly."
            ),
            (
                f"{name} was not just another player on the roster. This is somebody "
                "whose name belongs in the history of the league."
            ),
        ]

        pat_lines = [
            (
                f"{name} was a DUDE. That is the easiest way to put it. Big moments, "
                "big plays, and a reputation that followed them every week."
            ),
            (
                f"This is exactly what the Hall is for. {name} gave the league moments "
                "people are going to keep talking about."
            ),
        ]

        josh_lines = [
            (
                f"The strongest Hall of Fame cases are about sustained value, not one hot stretch. "
                f"{name} built the kind of player résumé that holds up over time."
            ),
            (
                f"When you study the full body of work, {name} had real impact on winning. "
                "That matters more than a flashy week or two."
            ),
        ]

    elif role == "coach":
        marcus_lines = [
            (
                f"{name} was a good coach because the results and the program both mattered. "
                f"You can look at what happened with {team} and see a real legacy."
            ),
            (
                f"This is not just about wins. {name} helped shape a team, set standards, "
                "and built something people remember. That is a Hall of Fame coach."
            ),
        ]

        stephen_lines = [
            (
                f"A coach is judged by leadership, preparation, and whether the team responds. "
                f"{name} did enough of that to make this induction completely understandable."
            ),
            (
                f"If you are responsible for winning, discipline, and a lasting identity, "
                f"then your name belongs in the conversation. {name} has that case."
            ),
        ]

        pat_lines = [
            (
                f"{name} could COACH. Players bought in, the team had an identity, "
                "and the league noticed. That is a pretty good Hall of Fame argument."
            ),
            (
                f"There are coaches who just manage games, and there are coaches who create eras. "
                f"{name} left something behind with {team}."
            ),
        ]

        josh_lines = [
            (
                f"The thing I look for with coaches is sustainability. {name} built a résumé "
                "that was bigger than one season, and that is why the induction makes sense."
            ),
            (
                f"Good coaching shows up in roster development, consistency, and meaningful wins. "
                f"{name} checked enough of those boxes to deserve this."
            ),
        ]

    elif role == "commissioner/contributor":
        marcus_lines = [
            (
                f"{name} helped make Project Madden what it is. The Hall of Fame should not only "
                "recognize players and coaches; it should recognize the people who kept the league moving."
            ),
            (
                f"This kind of induction matters. {name} made an impact on the league itself, "
                "and that contribution deserves to be remembered."
            ),
        ]

        stephen_lines = [
            (
                f"People love to focus only on what happens on the field, but a league does not run itself. "
                f"{name} contributed to the structure, standards, and culture behind it."
            ),
            (
                f"If somebody helps build the league, protect the league, and make the experience better, "
                f"then yes, {name} belongs in this Hall of Fame conversation."
            ),
        ]

        pat_lines = [
            (
                f"You need people behind the scenes who keep the whole thing alive. "
                f"{name} was one of those people, and that absolutely deserves some love."
            ),
            (
                f"Every great league has people doing the work nobody sees. {name} made a real difference."
            ),
        ]

        josh_lines = [
            (
                f"A strong league depends on governance, consistency, and people who invest in the long term. "
                f"{name} contributed in that way, which makes this a legitimate Hall of Fame case."
            ),
            (
                f"Contribution is part of legacy. {name} helped strengthen the league beyond any one game or season."
            ),
        ]

    else:
        marcus_lines = [
            f"{name} built a legacy in Project Madden, and this induction recognizes that impact.",
            f"The Hall of Fame is about lasting influence, and {name} clearly left one.",
        ]

        stephen_lines = [
            f"{name} has a résumé worth remembering. That is what the Hall of Fame is supposed to represent.",
            f"You do not get remembered forever without making a real impact. {name} did that.",
        ]

        pat_lines = [
            f"{name} left a mark on this league. That is why we are talking Hall of Fame.",
            f"This is a legacy induction. {name} mattered to Project Madden.",
        ]

        josh_lines = [
            f"{name} has a body of work that deserves historical recognition.",
            f"The overall résumé supports the induction of {name}.",
        ]

    context_bits = []

    if championships:
        context_bits.append(
            f"{championships} championship"
            + (
                ""
                if championships == 1
                else "s"
            )
        )

    if record:
        context_bits.append(
            f"career record {record}"
        )

    if awards:
        context_bits.append(
            f"honors: {awards}"
        )

    context = (
        " • ".join(
            context_bits
        )
        if context_bits
        else reason
    )

    return {
        "marcus":
            stable_choice(
                marcus_lines,
                seed + "-marcus"
            ),
        "stephen":
            stable_choice(
                stephen_lines,
                seed + "-stephen"
            ),
        "pat":
            stable_choice(
                pat_lines,
                seed + "-pat"
            ),
        "josh_pate":
            stable_choice(
                josh_lines,
                seed + "-josh"
            ),
        "context":
            context[:900]
    }


def post_hall_of_fame_analyst_reactions(
    entry
):
    reactions = build_hall_of_fame_analyst_reactions(
        entry
    )

    title = (
        "🏛️ HALL OF FAME REACTION • "
        f"{entry.get('name')}"
    )

    results = {}

    results[
        "marcus"
    ] = send_analyst_embed(
        title,
        (
            f"{reactions.get('marcus')}\n\n"
            f"**Hall of Fame context:** "
            f"{reactions.get('context') or entry.get('reason', '')}"
        )
    )

    results[
        "stephen"
    ] = send_stephen_a_parody_embed(
        title,
        (
            f"{reactions.get('stephen')}\n\n"
            f"**Hall of Fame context:** "
            f"{reactions.get('context') or entry.get('reason', '')}"
        )
    )

    results[
        "josh_pate"
    ] = send_josh_pate_parody_embed(
        title,
        (
            f"{reactions.get('josh_pate')}\n\n"
            f"**Hall of Fame context:** "
            f"{reactions.get('context') or entry.get('reason', '')}"
        )
    )

    results[
        "weekly_show"
    ] = send_weekly_show_embed(
        (
            "🏛️ PROJECT MADDEN HALL OF FAME PANEL • "
            f"{entry.get('name')}"
        ),
        (
            f"**Marcus Hayes:** {reactions.get('marcus')}\n\n"
            f"**Stephen A. Smith — AI Parody:** {reactions.get('stephen')}\n\n"
            f"**Pat McAfee — AI Parody:** {reactions.get('pat')}\n\n"
            f"**Josh Pate — AI Parody:** {reactions.get('josh_pate')}\n\n"
            "*Stephen A. Smith, Pat McAfee, and Josh Pate portions are fictional "
            "AI parody commentary, not real statements from those people.*"
        ),
        [
            {
                "name":
                    "🏛️ Inductee",
                "value":
                    (
                        f"**{entry.get('name')}** • "
                        f"{entry.get('type')} • "
                        f"{entry.get('team')} • "
                        f"Class of {entry.get('class_year')}"
                    )[:1024],
                "inline":
                    False
            },
            {
                "name":
                    "📜 Hall of Fame Case",
                "value":
                    str(
                        entry.get(
                            "reason",
                            ""
                        )
                    )[:1024],
                "inline":
                    False
            }
        ]
    )

    return results


def save_hall_of_fame(data):
    save_json_file(
        PROJECT_MADDEN_HALL_OF_FAME_FILE,
        data
    )


def update_record_book_from_week(
    season_type,
    week_number
):
    book = load_record_book()
    games = build_week_game_reactions(
        season_type,
        week_number
    )
    players = build_week_player_reactions(
        season_type,
        week_number
    )

    for game in games:
        margin = int(game.get("margin", 0) or 0)
        existing = book.get("biggest_blowout") or {}
        if margin > int(existing.get("margin", -1) or -1):
            book["biggest_blowout"] = {
                "season_type": season_type,
                "week": week_number,
                "game": game.get("game"),
                "winner": game.get("winner"),
                "loser": game.get("loser"),
                "margin": margin,
            }

    records = book.setdefault("single_game_records", {})
    category_map = {
        "passing": [
            ("passing_yards", "yards"),
            ("passing_tds", "touchdowns"),
        ],
        "rushing": [
            ("rushing_yards", "yards"),
            ("rushing_tds", "touchdowns"),
        ],
        "receiving": [
            ("receiving_yards", "yards"),
            ("receiving_tds", "touchdowns"),
        ],
        "defense": [
            ("sacks", "sacks"),
            ("interceptions", "interceptions"),
            ("forced_fumbles", "forced_fumbles"),
        ],
    }

    for player in players:
        category = player.get("category", "")
        stats = player.get("stats", {})

        for record_key, stat_key in category_map.get(category, []):
            value = int(stats.get(stat_key, 0) or 0)
            existing = records.get(record_key) or {}

            if value > int(existing.get("value", -1) or -1):
                records[record_key] = {
                    "player": player.get("player"),
                    "value": value,
                    "season_type": season_type,
                    "week": week_number,
                }

    standings = normalize_standings()

    for team in standings:
        streak = str(team.get("streak", "") or "").upper()
        if streak.startswith("W"):
            try:
                streak_count = int(streak[1:])
            except Exception:
                streak_count = 0

            existing = book.get("longest_win_streak") or {}
            if streak_count > int(existing.get("wins", 0) or 0):
                book["longest_win_streak"] = {
                    "team": team.get("team"),
                    "wins": streak_count,
                    "recorded_week": week_number,
                }

    for team in standings:
        games_count = int(team.get("games", 0) or 0)
        if games_count == 0:
            continue

        wins = int(team.get("wins", 0) or 0)
        win_pct = wins / games_count
        existing = book.get("best_user_season") or {}

        if win_pct > float(existing.get("win_pct", -1) or -1):
            team_info = team_by_id(team.get("team_id")) or {}
            book["best_user_season"] = {
                "team": team.get("team"),
                "user": team_info.get("user"),
                "record": (
                    f"{team.get('wins', 0)}-"
                    f"{team.get('losses', 0)}"
                ),
                "win_pct": round(win_pct, 3),
            }

    trades = refresh_trade_winner_tracker()
    grade_points = {
        "A+": 7, "A": 6, "B+": 5, "B": 4,
        "C+": 3, "C": 2, "D": 1, "F": 0,
    }

    legendary = []

    for trade in trades:
        grade_a = (
            trade.get("team_a_grade", {}).get("grade", "C")
            if isinstance(trade.get("team_a_grade"), dict)
            else "C"
        )
        grade_b = (
            trade.get("team_b_grade", {}).get("grade", "C")
            if isinstance(trade.get("team_b_grade"), dict)
            else "C"
        )

        separation = abs(
            grade_points.get(grade_a, 2)
            - grade_points.get(grade_b, 2)
        )

        if separation >= 4:
            legendary.append({
                "trade_id": trade.get("trade_id"),
                "team_a": trade.get("team_a"),
                "team_b": trade.get("team_b"),
                "team_a_grade": grade_a,
                "team_b_grade": grade_b,
                "winner": (
                    trade.get("winner_tracker", {}).get("winner")
                    if isinstance(trade.get("winner_tracker"), dict)
                    else None
                ),
            })

    book["legendary_trades"] = legendary[-20:]

    save_record_book(book)
    return book


@app.route("/analyst/record-book")
def analyst_record_book():
    return jsonify(load_record_book())




@app.route(
    "/hall-of-fame/diagnostics"
)
def hall_of_fame_diagnostics_route():
    try:
        hall = load_hall_of_fame()
        db_read_ok = True
        db_error = ""
    except Exception as e:
        hall = []
        db_read_ok = False
        db_error = str(
            e
        )

    return jsonify({
        "app_version":
            PROJECT_MADDEN_APP_VERSION,
        "interaction_endpoint":
            discord_interactions_url(),
        "bot_token_configured":
            bool(
                discord_bot_token()
            ),
        "guild_id_configured":
            bool(
                discord_guild_id()
            ),
        "hall_channel_id_configured":
            bool(
                hall_of_fame_channel_id()
            ),
        "hall_category_id_configured":
            bool(
                hall_of_fame_category_id()
            ),
        "hall_category_config_valid":
            (
                hall_of_fame_category_config_issue()
                is None
            ),
        "hall_category_config_issue":
            hall_of_fame_category_config_issue(),
        "database_read_ok":
            db_read_ok,
        "database_error":
            db_error,
        "inductee_count":
            len(
                hall
            ),
        "testhof_uses_deferred_response":
            True,
        "inducthof_uses_deferred_response":
            True
    })


@app.route(
    "/hall-of-fame/status"
)
def hall_of_fame_status_route():
    hall = load_hall_of_fame()

    return jsonify({
        "configured":
            hall_of_fame_discord_configured(),
        "bot_token_configured":
            bool(
                discord_bot_token()
            ),
        "channel_id_configured":
            bool(
                hall_of_fame_channel_id()
            ),
        "category_id_configured":
            bool(
                hall_of_fame_category_id()
            ),
        "category_config_issue":
            hall_of_fame_category_config_issue(),
        "dedicated_inductee_channels":
            True,
        "custom_logo_generation":
            True,
        "analyst_reactions_enabled":
            True,
        "hof_commands_deferred":
            True,
        "testhof_auto_delete_seconds":
            300,
        "inductee_count":
            len(
                hall
            ),
        "league_owner_role_id":
            LEAGUE_OWNER_TEST_ROLE_ID
    })


@app.route("/analyst/hall-of-fame")
def analyst_hall_of_fame():
    return jsonify({
        "hall_of_fame": load_hall_of_fame()
    })


@app.route(
    "/analyst/record-book/update/"
    "<season_type>/<int:week_number>",
    methods=["GET", "POST"]
)
def analyst_record_book_update(
    season_type,
    week_number
):
    return jsonify(
        update_record_book_from_week(
            season_type,
            week_number
        )
    )


@app.route("/analyst/fraud-watch")
def analyst_fraud_watch():
    fraud = build_fraud_watch()
    panel = build_watch_panel_takes(
        fraud,
        []
    ).get("fraud_watch")

    return jsonify({
        "fraud_watch": fraud,
        "panel": panel,
    })


@app.route("/analyst/dark-horse-watch")
def analyst_dark_horse_watch():
    dark_horses = build_dark_horse_watch()
    panel = build_watch_panel_takes(
        [],
        dark_horses
    ).get("dark_horse")

    return jsonify({
        "dark_horse_watch": dark_horses,
        "panel": panel,
    })


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


STEPHEN_A_TRADE_LINES = [
    "I am looking at this trade and asking one question: who actually got better? Because collecting names is not the same thing as building a team.",
    "If you are moving premium talent, the return better make sense immediately. I am not accepting 'maybe it works later' as an explanation.",
    "This is the kind of trade where one front office is betting on fit and the other is betting on raw talent. Somebody is going to look very smart or very foolish.",
    "You cannot send out a franchise-level player and come back with a package full of question marks. That is how a roster gets set back.",
    "The grades matter, but so does context. If the move fixes a major weakness, I can understand it. If it creates two new weaknesses, then what are we doing?",
    "I want to know what the plan is after this trade. Good teams do not make moves just to make headlines.",
    "If the League Office has to stare at the value gap this long, that alone tells you this is not a simple deal.",
    "Somebody is betting on upside, somebody is betting on certainty. I want the side that knows exactly what it is getting.",
    "Do not tell me a player is untouchable and then move him for a package that does not change your championship ceiling.",
    "This trade may look even on paper, but roster construction is not a spreadsheet. Fit, age, development and positional value matter.",
]

STEPHEN_A_PLAYER_COMPARE_LINES = [
    "{player} is giving me a little bit of that **LeBron James** effect — everything runs through him, and when he controls the game, everybody else looks better.",
    "{player} has that **Stephen Curry** type of gravity right now. The defense has to account for him before the play even develops.",
    "{player} is playing with a **Jimmy Butler** kind of edge — not always pretty, but when the pressure rises, the impact gets louder.",
    "{player} reminds me of **Nikola Jokic** in one specific way: the production keeps finding the right place even when it does not look flashy.",
    "{player} is giving me **Anthony Edwards** energy — aggressive, fearless, and always looking for the next big play.",
    "{player} has that **Kevin Durant** feel when the matchup is right — smooth production and very difficult to completely take away.",
    "{player} is playing like a football version of **Jayson Tatum** when he gets rolling: steady, polished, and capable of carrying the offense for long stretches.",
    "{player} has a little **Draymond Green** impact to his game right now — the box score may not tell the whole story, but he changes what the opponent can do.",
    "{player} is giving me **Shai Gilgeous-Alexander** vibes — calm, efficient, and somehow always finding a way to get exactly what he wants.",
    "{player} is playing with a **Giannis Antetokounmpo** level of force in this matchup — once the momentum starts going downhill, it gets difficult to stop.",
]

STEPHEN_A_BAD_PLAYER_COMPARE_LINES = [
    "{player} is looking like a star who forgot the fourth quarter existed. That is the football version of putting up numbers and disappearing when the game gets tight.",
    "{player} is giving me empty-calorie production right now — the basketball equivalent of scoring 30 while your team loses by 20.",
    "{player} cannot keep turning the ball over and expect the talent label to protect him. That is like a point guard handing out assists to the other team.",
    "{player} has the reputation, but the production is not matching it. In basketball terms, that is an All-Star name with a bench-level impact tonight.",
    "{player} is forcing too much. It is the football version of taking contested threes every possession instead of running the offense.",
]

STEPHEN_A_PARODY_OPENERS = [
    "Now hold on! We need to talk about what just happened here.",
    "Ladies and gentlemen, this cannot simply be ignored.",
    "I have seen enough. Somebody needs to explain this immediately.",
    "Excuse me, but are we really going to act like that performance was normal?",
    "This is exactly the kind of result that gets everybody in the league talking.",
    "I am not letting this one slide. There is too much to discuss.",
    "I need everybody to stop acting like the obvious is complicated. The tape is telling us exactly what happened.",
    "There are moments where I can be patient, and then there are moments where the performance leaves me no choice but to question everything.",
    "This is where expectations matter. If you call yourself a contender, I am going to judge you like one.",
    "Do not give me excuses after the fact. Show me the adjustment, show me the execution, and then we can talk.",
    "Some teams want the praise before they have earned it. I am not doing that today.",
    "I am looking at this situation and somebody has to take responsibility. Talent alone is not enough.",
]

STEPHEN_A_PARODY_GAME_LINES = [
    "{winner} handled business, and {loser} has to answer for it. You can dress it up however you want, but the scoreboard is the scoreboard.",
    "{winner} made the statement. {loser} now has to prove this was an exception and not the beginning of a problem.",
    "When {winner} walks away with that result, the conversation changes immediately. {loser} cannot just shrug this off.",
    "There are wins, and then there are wins that put pressure on everybody else. {winner} just delivered one of those.",
    "{winner} did what serious teams are supposed to do: they took control and made {loser} play from behind. Now {loser} has to prove this was one bad day and not a pattern.",
    "The problem for {loser} is not just the loss. It is that {winner} made the game look easier than it should have been.",
    "I keep hearing about how talented {loser} is. Wonderful. {winner} just showed us that talent without execution is decoration.",
    "{winner} earned every bit of this conversation. If {loser} wants the respect back, go win the next one and stop asking for sympathy.",
]

STEPHEN_A_PARODY_PLAYER_LINES = [
    "{player} put up a performance that demands attention. If you are building a game plan next week, that name is now circled.",
    "I do not care what anybody expected coming in — {player} showed up and made the entire league notice.",
    "{player} just gave us the kind of performance that changes how opponents prepare.",
    "That was not background production from {player}. That was a headline performance.",
    "{player} is not just putting up numbers; {player} is dictating how the opponent has to play. That is star-level impact.",
    "When {player} is producing like this, every coordinator in the league is writing that name at the top of the game plan.",
    "{player} gave us the kind of performance that turns a regular weekly recap into a full segment.",
    "If {player} keeps stacking performances like this, the conversation is going to move from good season to league-wide problem.",
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



def build_stephen_trade_reaction(
    analysis
):
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
    )

    gap = review.get(
        "value_gap_percent",
        review.get(
            "gap_percentage"
        )
    )

    key = (
        f"stephen-trade-"
        f"{analysis.get('trade_id')}-"
        f"{team_a}-{team_b}-"
        f"{decision}-{gap}"
    )

    line = stable_choice(
        STEPHEN_A_TRADE_LINES,
        key
    )

    if "AUTO DENY" in decision.upper():
        ending = (
            "And if the League Office is already at AUTO DENY, "
            "that is your sign to go back to the negotiating table."
        )
    elif "STRONG" in decision.upper():
        ending = (
            "This is exactly why the Trade Committee needs to look at it. "
            "The package may have an idea behind it, but the imbalance is too loud to ignore."
        )
    elif "REVIEW" in decision.upper():
        ending = (
            "I can see the argument for both sides, but I am not rubber-stamping it. "
            "Let the committee make them explain the logic."
        )
    else:
        ending = (
            "If the numbers are this close and the roster fit makes sense, "
            "I can live with the League Office approving it."
        )

    return (
        f"{line} {ending}"
    )


def build_stephen_player_comparison(
    player_name,
    positive=True,
    key_suffix=""
):
    choices = (
        STEPHEN_A_PLAYER_COMPARE_LINES
        if positive
        else STEPHEN_A_BAD_PLAYER_COMPARE_LINES
    )

    template = stable_choice(
        choices,
        (
            f"stephen-player-compare-"
            f"{player_name}-{key_suffix}-"
            f"{'good' if positive else 'bad'}"
        )
    )

    return template.format(
        player=player_name
    )


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

        comparison = build_stephen_player_comparison(
            player,
            positive=(
                top_player.get(
                    "story_type",
                    ""
                )
                not in [
                    "bad_qb",
                    "bad",
                    "struggle"
                ]
            ),
            key_suffix=(
                f"{season_type}-"
                f"{week_number}-"
                f"{source_key}"
            )
        )

        stories.append({
            "story_type": "player",
            "source_key": source_key,
            "headline": player,
            "take": (
                f"{opener} {body} {comparison}"
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
# JOSH PATE - AI PARODY SPECIAL SEGMENT
# =========================================================

JOSH_PATE_PARODY_OPENERS = [
    "Here is the thing I keep coming back to: what did the game actually tell us beyond the final score?",
    "I care less about the label and more about whether the team is building something repeatable.",
    "This is where roster construction, situational football, and week-to-week consistency separate real contenders from paper contenders.",
    "You can have all the talent in the world, but if the operation is sloppy, the ceiling drops fast.",
    "I am looking for what travels: quarterback play, line play, discipline, and whether the staff has answers when Plan A gets taken away.",
    "One result does not define a team, but the way a team wins or loses can reveal what is coming next.",
]

JOSH_PATE_GAME_LINES = [
    "{winner} showed more of the things that tend to travel week to week, while {loser} left too many self-inflicted problems on the field.",
    "{winner} did a better job controlling the terms of the game. {loser} now has to prove the issues are correctable and not structural.",
    "The score matters, but the bigger takeaway is that {winner} looked more stable in the moments where the game could have tilted either way.",
    "{winner} gave me more confidence in its operation. {loser} has questions to answer about execution, adjustments, and consistency.",
    "This was not just about who had the better highlights. {winner} won the down-to-down battle more consistently than {loser}.",
]

JOSH_PATE_PLAYER_LINES = [
    "{player} looked like the kind of player who changes what a coordinator is willing to call.",
    "{player} is becoming the type of piece that can raise the floor of an entire unit.",
    "{player} gave the kind of performance that makes the next opponent change its weekly plan.",
    "{player} looked dependable, and dependable stars are what survive when the schedule gets tougher.",
    "{player} did more than fill the box score; the performance changed how the game had to be played.",
]

JOSH_PATE_TRADE_LINES = [
    "I evaluate trades the same way I evaluate roster building: does this move raise your floor, your ceiling, or preferably both?",
    "The headline name is not enough for me. I want to know what the move does to the two-deep, the future, and the team’s margin for error.",
    "A trade can be fair on a value chart and still be bad roster management if it creates a bigger hole somewhere else.",
    "If you are moving a premium asset, the return has to make sense in both the short term and the long term.",
    "The best trades usually make a team more flexible, not more fragile. That is the part I am looking at here.",
    "Draft capital matters because optionality matters. Proven talent matters because certainty matters. The question is whether the balance makes sense.",
]

JOSH_PATE_COMPARE_LINES = [
    "{player} is functioning like a true program centerpiece — the kind of player everything else can be built around.",
    "{player} is giving this team the football equivalent of a high-level floor general: steady, efficient, and able to keep the whole operation on schedule.",
    "{player} is becoming the kind of mismatch piece that forces opponents to change structure, not just personnel.",
    "{player} is playing like the kind of veteran anchor every contender needs when a game gets weird.",
    "{player} has become the sort of player whose value is bigger than one stat category because he changes what the opponent is allowed to do.",
]


def get_josh_pate_parody_webhook():
    return os.environ.get(
        "JOSH_PATE_PARODY_WEBHOOK_URL",
        ""
    ).strip()


def josh_pate_parody_webhook_configured():
    return bool(get_josh_pate_parody_webhook())


def build_josh_pate_player_comparison(player_name, key_suffix=""):
    template = stable_choice(
        JOSH_PATE_COMPARE_LINES,
        f"josh-pate-compare-{player_name}-{key_suffix}"
    )
    return template.format(player=player_name)


def build_josh_pate_segment(season_type, week_number):
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

        winner = top_game.get("winner", "the winner")
        loser = top_game.get("loser", "the opponent")
        source_key = str(
            top_game.get(
                "schedule_id",
                top_game.get("game", "")
            )
        )

        opener = stable_choice(
            JOSH_PATE_PARODY_OPENERS,
            f"josh-pate-game-open-{season_type}-{week_number}-{source_key}"
        )
        body = stable_choice(
            JOSH_PATE_GAME_LINES,
            f"josh-pate-game-body-{season_type}-{week_number}-{source_key}"
        ).format(
            winner=winner,
            loser=loser
        )

        stories.append({
            "story_type": "game",
            "headline": f"{winner} vs {loser}",
            "take": f"{opener} {body}"
        })

    if player_reactions:
        top_player = player_reactions[0]
        player = top_player.get("player", "This player")
        source_key = (
            f"{player}-"
            f"{top_player.get('category', '')}-"
            f"{top_player.get('story_type', '')}"
        )

        opener = stable_choice(
            JOSH_PATE_PARODY_OPENERS,
            f"josh-pate-player-open-{season_type}-{week_number}-{source_key}"
        )
        body = stable_choice(
            JOSH_PATE_PLAYER_LINES,
            f"josh-pate-player-body-{season_type}-{week_number}-{source_key}"
        ).format(player=player)
        comparison = build_josh_pate_player_comparison(
            player,
            key_suffix=f"{season_type}-{week_number}-{source_key}"
        )

        stories.append({
            "story_type": "player",
            "headline": player,
            "take": f"{opener} {body} {comparison}"
        })

    return stories


def build_josh_pate_trade_reaction(analysis):
    team_a = analysis.get("team_a", "Team A")
    team_b = analysis.get("team_b", "Team B")
    review = analysis.get("trade_committee", {})
    decision = str(review.get("decision", ""))

    line = stable_choice(
        JOSH_PATE_TRADE_LINES,
        (
            f"josh-pate-trade-{analysis.get('trade_id')}-"
            f"{team_a}-{team_b}-{decision}"
        )
    )

    if "AUTO DENY" in decision.upper():
        closer = (
            "If the League Office is already at AUTO DENY, "
            "the structure of the deal needs to change before we even get to roster fit."
        )
    elif "REVIEW" in decision.upper():
        closer = (
            "This is the kind of deal where the committee should look beyond the raw number "
            "and ask what each roster actually becomes afterward."
        )
    else:
        closer = (
            "If both sides can explain the roster logic and the value is close, "
            "I am comfortable with the League Office letting it through."
        )

    return f"{line} {closer}"


def send_josh_pate_parody_embed(title, description):
    webhook_url = get_josh_pate_parody_webhook()

    if not webhook_url:
        return {
            "sent": False,
            "error": (
                "JOSH_PATE_PARODY_WEBHOOK_URL "
                "is not configured."
            )
        }

    payload = {
        "username": "Josh Pate | AI Parody",
        "embeds": [
            {
                "title": title,
                "description": description,
                "footer": {
                    "text": (
                        "AI parody segment • "
                        "Not real Josh Pate statements"
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

        if response.status_code not in [200, 204]:
            return {
                "sent": False,
                "error": (
                    f"Discord returned {response.status_code}: "
                    f"{response.text[:500]}"
                )
            }

        return {"sent": True}

    except Exception as e:
        return {
            "sent": False,
            "error": str(e)
        }


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
JOSH_PATE_PARODY_HISTORY_FILE = "josh_pate_parody_posts.json"
MARCUS_TRADE_REACTION_HISTORY_FILE = "marcus_trade_reaction_posts.json"
WEEKLY_SHOW_HISTORY_FILE = "weekly_show_posts.json"
ANALYST_RECEIPTS_FILE = "analyst_receipts.json"
TRADE_HISTORY_FILE = "trade_history.json"
PROJECT_MADDEN_RECORD_BOOK_FILE = "project_madden_record_book.json"
PROJECT_MADDEN_HALL_OF_FAME_FILE = "project_madden_hall_of_fame.json"


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


def get_trade_logs_webhook():
    return os.environ.get(
        "TRADE_LOGS_DISCORD_WEBHOOK_URL",
        ""
    ).strip()


def trade_logs_webhook_configured():
    return bool(
        get_trade_logs_webhook()
    )


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



def discord_member_role_ids(
    interaction
):
    roles = (
        interaction
        .get(
            "member",
            {}
        )
        .get(
            "roles",
            []
        )
    )

    if not isinstance(
        roles,
        list
    ):
        return set()

    return {
        str(
            role_id
        )
        for role_id
        in roles
    }


def discord_member_has_league_owner_role(
    interaction
):
    return (
        LEAGUE_OWNER_TEST_ROLE_ID
        in discord_member_role_ids(
            interaction
        )
    )


def discord_test_role_denied():
    return discord_ephemeral(
        "🔒 This test command is locked to "
        "@League owner."
    )



def expected_project_madden_commands():
    return [
        "setup",
        "server",
        "trade",
        "inducthof",
        "removehof",
        "hof",
        "hofping",
        "injuries",
        "testinjuries",
        "testmarcus",
        "teststephena",
        "weeklyshow",
        "testweeklyshow",
        "testjoshpate",
        "testpat",
        "testsystem",
        "testgotw",
        "testhof",
    ]


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


    setup_command = {
        "name":
            "setup",
        "description":
            "Server admin: connect this Discord server to Project Madden"
    }

    server_command = {
        "name":
            "server",
        "description":
            "View this server's Project Madden league connection"
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


    induct_hof_command = {
        "name":
            "inducthof",
        "description":
            "League Owner: induct someone into the Project Madden Hall of Fame",
        "options": [
            {
                "type":
                    3,
                "name":
                    "name",
                "description":
                    "Inductee name",
                "required":
                    True,
                "max_length":
                    100
            },
            {
                "type":
                    3,
                "name":
                    "type",
                "description":
                    "Inductee type",
                "required":
                    True,
                "choices": [
                    {
                        "name":
                            "Owner / Coach",
                        "value":
                            "Owner / Coach"
                    },
                    {
                        "name":
                            "Player",
                        "value":
                            "Player"
                    },
                    {
                        "name":
                            "Team",
                        "value":
                            "Team"
                    },
                    {
                        "name":
                            "Commissioner / Contributor",
                        "value":
                            "Commissioner / Contributor"
                    }
                ]
            },
            {
                "type":
                    3,
                "name":
                    "team",
                "description":
                    "Team or organization",
                "required":
                    True,
                "max_length":
                    100
            },
            {
                "type":
                    3,
                "name":
                    "reason",
                "description":
                    "Why they belong in the Hall of Fame",
                "required":
                    True,
                "max_length":
                    1000
            },
            {
                "type":
                    4,
                "name":
                    "championships",
                "description":
                    "Project Madden championships",
                "required":
                    False,
                "min_value":
                    0,
                "max_value":
                    99
            },
            {
                "type":
                    3,
                "name":
                    "career_record",
                "description":
                    "Optional career record, example 54-18",
                "required":
                    False,
                "max_length":
                    50
            },
            {
                "type":
                    3,
                "name":
                    "awards",
                "description":
                    "Optional awards and honors",
                "required":
                    False,
                "max_length":
                    1000
            },
            {
                "type":
                    3,
                "name":
                    "image_url",
                "description":
                    "Optional image URL for the induction embed",
                "required":
                    False,
                "max_length":
                    500
            },
            {
                "type":
                    4,
                "name":
                    "class_year",
                "description":
                    "Optional Hall of Fame class year",
                "required":
                    False,
                "min_value":
                    2020,
                "max_value":
                    2100
            }
        ]
    }

    remove_hof_command = {
        "name":
            "removehof",
        "description":
            "League Owner: remove a Hall of Fame entry",
        "options": [
            {
                "type":
                    3,
                "name":
                    "name_or_id",
                "description":
                    "Exact inductee name or induction ID",
                "required":
                    True,
                "max_length":
                    100
            }
        ]
    }

    hof_command = {
        "name":
            "hof",
        "description":
            "View the Project Madden Hall of Fame"
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
        "description": "Post Weekly Show with Marcus + Stephen A. + Pat McAfee + Josh Pate parody",
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

    test_josh_pate_command = {
        "name": "testjoshpate",
        "description": "Send a Josh Pate AI parody test segment",
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


    test_pat_command = {
        "name": "testpat",
        "description": "Send a Pat McAfee AI parody test segment",
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




    hof_ping_command = {
        "name":
            "hofping",
        "description":
            "Check Hall of Fame slash-command response"
    }



    injuries_command = {
        "name": "injuries",
        "description": "View the current Project Madden injury report"
    }

    test_injuries_command = {
        "name": "testinjuries",
        "description": "League Owner: test the injury alert system"
    }


    test_hof_command = {
        "name":
            "testhof",
        "description":
            "League Owner: test Hall of Fame channel + logo system",
        "options": [
            {
                "type":
                    3,
                "name":
                    "name",
                "description":
                    "Optional test inductee name",
                "required":
                    False,
                "max_length":
                    100
            },
            {
                "type":
                    3,
                "name":
                    "type",
                "description":
                    "Test inductee type",
                "required":
                    False,
                "choices": [
                    {
                        "name":
                            "Owner / Coach",
                        "value":
                            "Owner / Coach"
                    },
                    {
                        "name":
                            "Player",
                        "value":
                            "Player"
                    },
                    {
                        "name":
                            "Team",
                        "value":
                            "Team"
                    },
                    {
                        "name":
                            "Commissioner / Contributor",
                        "value":
                            "Commissioner / Contributor"
                    }
                ]
            },
            {
                "type":
                    3,
                "name":
                    "team",
                "description":
                    "Optional test team",
                "required":
                    False,
                "max_length":
                    100
            }
        ]
    }


    test_gotw_command = {
        "name": "testgotw",
        "description": "League Owner: post a 5-minute GOTW test poll",
        "options": [
            {
                "type": 3,
                "name": "season_type",
                "description": "Season type",
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

    test_system_command = {
        "name": "testsystem",
        "description": "League Owner: test Project Madden systems in Discord",
        "options": [
            {
                "type": 3,
                "name": "system",
                "description": "System to test",
                "required": True,
                "choices": [
                    {
                        "name": "Everything",
                        "value": "all"
                    },
                    {
                        "name": "Permanent Storage",
                        "value": "storage"
                    },
                    {
                        "name": "Playoff Race",
                        "value": "playoffs"
                    },
                    {
                        "name": "Rivalry Tracker",
                        "value": "rivalries"
                    },
                    {
                        "name": "Analyst Accuracy",
                        "value": "accuracy"
                    },
                    {
                        "name": "Analyst Receipts",
                        "value": "receipts"
                    },
                    {
                        "name": "Panel Debate",
                        "value": "debate"
                    },
                    {
                        "name": "Record Book",
                        "value": "recordbook"
                    },
                    {
                        "name": "Hall of Fame",
                        "value": "halloffame"
                    },
                    {
                        "name": "Trade History",
                        "value": "tradehistory"
                    },
                    {
                        "name": "GOTW Poll System",
                        "value": "gotw"
                    }
                ]
            },
            {
                "type": 3,
                "name": "season_type",
                "description": "Season type for weekly systems",
                "required": False,
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
                "description": "Week number for weekly systems",
                "required": False,
                "min_value": 1,
                "max_value": 25
            }
        ]
    }

    commands = [
        setup_command,
        server_command,
        command,
        induct_hof_command,
        remove_hof_command,
        hof_command,
        hof_ping_command,
        injuries_command,
        test_injuries_command,
        test_marcus_command,
        test_stephen_command,
        weekly_show_command,
        test_weekly_show_command,
        test_josh_pate_command,
        test_pat_command,
        test_system_command,
        test_gotw_command,
        test_hof_command
    ]

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

    # Register GLOBAL commands so Project Madden works in every
    # server that installs the Discord app.
    global_url = (
        f"{DISCORD_API_BASE}/applications/"
        f"{app_id}/commands"
    )

    global_response = requests.put(
        global_url,
        headers=headers,
        json=commands,
        timeout=15
    )

    if global_response.status_code not in [
        200,
        201
    ]:
        return {
            "success":
                False,
            "status_code":
                global_response.status_code,
            "scope":
                "global",
            "error":
                global_response.text[:500]
        }

    # Keep a guild-scoped copy in the home server for near-instant
    # command refresh while developing/testing.
    if guild_id:
        url = (
            f"{DISCORD_API_BASE}/applications/"
            f"{app_id}/guilds/{guild_id}/commands"
        )
        scope = "global+home_guild"
    else:
        url = global_url
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

    registered_names = [
        item.get(
            "name"
        )
        for item in body
    ]

    expected_names = (
        expected_project_madden_commands()
    )

    missing_expected = [
        name
        for name in expected_names
        if name not in registered_names
    ]

    return {
        "success":
            len(
                missing_expected
            ) == 0,
        "registered":
            registered_names,
        "expected":
            expected_names,
        "missing_expected":
            missing_expected,
        "scope": scope,
        "guild_id_configured": bool(guild_id),
        "global_commands_enabled": True,
        "trade_ui": (
            "5 clean player/pick asset slots per team "
            "+ optional Madden trade screenshot"
        ),
        "note": (
            "Global commands are enabled for other Discord servers. "
            "The home server also receives a guild-scoped copy for fast refresh."
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


def discord_ephemeral_link(
    content,
    label,
    url
):
    return jsonify({
        "type": 4,
        "data": {
            "content": content,
            "flags": 64,
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 5,
                            "label": str(label)[:80],
                            "url": str(url)
                        }
                    ]
                }
            ]
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

    try:
        trade_history_upsert(
            analysis
        )
    except Exception as e:
        print(
            "TRADE HISTORY ERROR:",
            str(e)
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

    try:
        post_trade_to_logs(analysis)
    except Exception as e:
        print("TRADE LOG DISCORD ERROR:", str(e))

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

    try:
        stephen_trade_take = (
            build_stephen_trade_reaction(
                analysis
            )
        )

        send_stephen_a_parody_embed(
            "💼 TRADE DESK • Stephen A. Smith — AI Parody",
            (
                f"## {analysis.get('team_a')} ↔ {analysis.get('team_b')}\n"
                f"{stephen_trade_take}\n\n"
                "⚠️ *Fictional AI parody for Project Madden. "
                "This is not a real Stephen A. Smith quote or statement.*"
            )
        )
    except Exception as e:
        print(
            "STEPHEN TRADE REACTION ERROR:",
            str(e)
        )

    try:
        josh_pate_trade_take = (
            build_josh_pate_trade_reaction(
                analysis
            )
        )

        send_josh_pate_parody_embed(
            "💼 TRADE DESK • Josh Pate — AI Parody",
            (
                f"## {analysis.get('team_a')} ↔ {analysis.get('team_b')}\n"
                f"{josh_pate_trade_take}\n\n"
                "⚠️ *Fictional AI parody for Project Madden. "
                "This is not a real Josh Pate quote or statement.*"
            )
        )
    except Exception as e:
        print(
            "JOSH PATE TRADE REACTION ERROR:",
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



def process_setup_interaction_background(
    interaction
):
    try:
        guild_id = str(
            interaction.get(
                "guild_id",
                ""
            )
        ).strip()

        if not guild_id:
            content = (
                "❌ Run /setup inside a Discord server."
            )

        elif not discord_member_can_manage_guild(
            interaction
        ):
            content = (
                "🔒 /setup requires Manage Server "
                "or Administrator."
            )

        else:
            config = ensure_guild_config(
                guild_id
            )

            if not config:
                content = (
                    "❌ Project Madden could not create this "
                    "server's setup record. DATABASE_URL must be "
                    "connected and PostgreSQL must be reachable."
                )
            else:
                setup_url = guild_setup_url(
                    config.get(
                        "setup_token"
                    )
                )

                content = (
                    "🏈 **PROJECT MADDEN SERVER SETUP**\\n"
                    "Your private dashboard setup link is ready:\\n"
                    f"{setup_url}\\n\\n"
                    "⚠️ **Current data source:** Snallabot is still "
                    "required for official league data while the "
                    "Project Madden direct EA connector is being built.\\n\\n"
                    "Use the setup page to connect this Discord server "
                    "to its Snallabot/Madden league. Do not post this "
                    "private setup link publicly."
                )

    except Exception as e:
        content = (
            "❌ Project Madden setup error: "
            + str(
                e
            )[:1200]
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


def process_server_interaction_background(
    interaction
):
    try:
        guild_id = str(
            interaction.get(
                "guild_id",
                ""
            )
        ).strip()

        config = get_guild_config(
            guild_id
        )

        content = guild_config_summary(
            config
        )

        content += (
            "\\n\\n⚠️ Project Madden currently requires Snallabot "
            "as the official live Madden data source."
        )

    except Exception as e:
        content = (
            "❌ Could not load this server's Project Madden "
            "connection: "
            + str(
                e
            )[:1200]
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




def build_weekly_show_test_panel(
    headline
):
    seed = str(
        headline
        or "Project Madden Weekly Show"
    )

    return {
        "marcus":
            stable_choice(
                [
                    "The desk is connected. Once real weekly data arrives, I will react to what actually happened instead of manufacturing a storyline.",
                    "Project Madden Media is online. Give me the real games, trades, standings, and performances and I will have plenty to discuss.",
                    "This is the control-room check. The Weekly Show pipeline is ready for real league information."
                ],
                seed + "-marcus-test"
            ),
        "stephen":
            stable_choice(
                [
                    "The test is working, but the real conversation begins when somebody gives us an actual result to debate.",
                    "We are connected. When the league gives us real material, the fictional parody desk will have something to argue about.",
                    "The studio connection is live. No fake statistics are needed for this test."
                ],
                seed + "-stephen-test"
            ),
        "pat":
            stable_choice(
                [
                    "The whole Project Madden desk is plugged in and ready to go when the league starts producing real moments.",
                    "This is a clean studio test. Once games are played, this show can get loud.",
                    "Everything on this side of the desk is connected. Now we wait for the actual league action."
                ],
                seed + "-pat-test"
            ),
    }


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



def compact_record_text(
    record
):
    if not isinstance(
        record,
        dict
    ):
        return "0-0"

    return (
        f"{record.get('wins', 0)}-"
        f"{record.get('losses', 0)}"
    )


def build_discord_system_test(
    system_name,
    season_type,
    week_number
):
    fields = []
    failures = []

    def add_field(
        name,
        value
    ):
        fields.append({
            "name":
                name,
            "value":
                str(
                    value
                )[:1024],
            "inline":
                False
        })

    def run_check(
        key,
        label,
        fn
    ):
        try:
            result = fn()

            add_field(
                f"✅ {label}",
                result
            )

            return True
        except Exception as e:
            failures.append(
                (
                    key,
                    str(e)
                )
            )

            add_field(
                f"❌ {label}",
                str(e)
            )

            return False

    selected = (
        [
            "storage",
            "playoffs",
            "rivalries",
            "accuracy",
            "receipts",
            "debate",
            "recordbook",
            "halloffame",
            "tradehistory",
            "gotw",
        ]
        if system_name == "all"
        else [
            system_name
        ]
    )

    for item in selected:
        if item == "storage":
            def storage_check():
                status = persistent_storage_status()

                datasets = status.get(
                    "datasets",
                    {}
                )

                dataset_lines = []

                for filename, info in datasets.items():
                    dataset_lines.append(
                        (
                            f"{filename}: "
                            f"DB={'✅' if info.get('database') else '—'} "
                            f"Cache={'✅' if info.get('local_cache') else '—'}"
                        )
                    )

                return (
                    f"Configured: **{status.get('configured')}**\n"
                    f"Database ready: **{status.get('database_ready')}**\n"
                    f"Driver available: **{status.get('driver_available')}**\n"
                    f"Weekly Show webhook: **{weekly_show_webhook_configured()}**\n"
                    f"Analyst webhook: **{analyst_webhook_configured()}**\n"
                    f"Stephen A webhook: **{bool(get_stephen_a_parody_webhook())}**\n"
                    f"Josh Pate webhook: **{josh_pate_parody_webhook_configured()}**\n"
                    f"Trade webhook: **{bool(os.environ.get('DISCORD_WEBHOOK_URL'))}**\n"
                    f"Trade Logs webhook: **{trade_logs_webhook_configured()}**\n"
                    + "\n".join(
                        dataset_lines
                    )
                )

            run_check(
                "storage",
                "Permanent Storage",
                storage_check
            )

        elif item == "playoffs":
            def playoff_check():
                race = build_playoff_race(
                    season_type,
                    week_number
                )

                lines = []

                for conference in [
                    "AFC",
                    "NFC"
                ]:
                    seeds = (
                        race.get(
                            conference,
                            {}
                        ).get(
                            "seeds",
                            []
                        )
                    )

                    if seeds:
                        seed_text = ", ".join(
                            (
                                f"#{team.get('playoff_seed', team.get('projected_seed', index))} "
                                f"{team.get('team')}"
                            )
                            for index, team in enumerate(
                                seeds[:7],
                                start=1
                            )
                        )
                    else:
                        seed_text = (
                            "No usable standings yet"
                        )

                    lines.append(
                        f"**{conference}:** {seed_text}"
                    )

                gotw = race.get(
                    "game_of_the_week"
                )

                if gotw:
                    lines.append(
                        f"Game of Week: **{gotw.get('matchup')}**"
                    )

                scenarios = race.get(
                    "clinching_scenarios",
                    []
                )

                lines.append(
                    f"Clinching/Must-Win scenarios: **{len(scenarios)}**"
                )

                return "\n".join(
                    lines
                )

            run_check(
                "playoffs",
                "Playoff Race / Clinching",
                playoff_check
            )

        elif item == "rivalries":
            def rivalry_check():
                created = record_rivalry_week(
                    season_type,
                    week_number
                )

                spotlights = rivalry_week_spotlight(
                    season_type,
                    week_number
                )

                top = build_top_rivalries(
                    5
                )

                lines = [
                    f"New completed games saved: **{created}**",
                    f"Rivalry Week spotlights: **{len(spotlights)}**",
                    f"Tracked rivalries: **{len(top)}**",
                ]

                for item in top[:3]:
                    lines.append(
                        (
                            f"• {item.get('owner_a')} vs "
                            f"{item.get('owner_b')} — "
                            f"{item.get('meetings')} meetings"
                        )
                    )

                return "\n".join(
                    lines
                )

            run_check(
                "rivalries",
                "Rivalry Tracker / Rivalry Week",
                rivalry_check
            )

        elif item == "accuracy":
            def accuracy_check():
                accuracy = (
                    analyst_accuracy_by_category()
                )

                lines = []

                for analyst in [
                    "marcus",
                    "stephen",
                    "pat",
                    "josh_pate"
                ]:
                    data = accuracy.get(
                        analyst,
                        {}
                    )

                    categories = data.get(
                        "categories",
                        {}
                    )

                    overall = categories.get(
                        "overall",
                        {}
                    )

                    upset = categories.get(
                        "upset_picks",
                        {}
                    )

                    lines.append(
                        (
                            f"• **{data.get('name', analyst)}** "
                            f"{overall.get('wins', 0)}-"
                            f"{overall.get('losses', 0)} "
                            f"({overall.get('win_pct', 0)}%) | "
                            f"Upsets {upset.get('wins', 0)}-"
                            f"{upset.get('losses', 0)}"
                        )
                    )

                return "\n".join(
                    lines
                )

            run_check(
                "accuracy",
                "Analyst Accuracy by Category",
                accuracy_check
            )

        elif item == "receipts":
            def receipts_check():
                settled = settle_analyst_predictions(
                    season_type,
                    week_number
                )

                leaderboard = (
                    analyst_receipts_leaderboard()
                )

                lines = [
                    f"New picks settled: **{settled}**"
                ]

                for entry in leaderboard:
                    lines.append(
                        (
                            f"{entry.get('rank')}. "
                            f"**{entry.get('name')}** — "
                            f"{entry.get('wins', 0)}-"
                            f"{entry.get('losses', 0)} "
                            f"({entry.get('win_pct', 0)}%)"
                        )
                    )

                return "\n".join(
                    lines
                )

            run_check(
                "receipts",
                "Analyst Receipts",
                receipts_check
            )

        elif item == "debate":
            def debate_check():
                show = build_weekly_show_summary(
                    season_type,
                    week_number
                )

                debate = show.get(
                    "panel_debate",
                    {}
                )

                return (
                    f"Topic: **{debate.get('topic', 'No topic yet')}**\n"
                    f"Mode: **{debate.get('mode', '—')}**\n"
                    f"Marcus: {debate.get('marcus', '')[:180]}\n"
                    f"Stephen A. AI Parody: {debate.get('stephen', '')[:180]}\n"
                    f"Pat AI Parody: {debate.get('pat', '')[:180]}\n"
                    f"Josh Pate AI Parody: {debate.get('josh_pate', '')[:180]}"
                )

            run_check(
                "debate",
                "AI Panel Debate",
                debate_check
            )

        elif item == "recordbook":
            def recordbook_check():
                book = load_record_book()

                single = book.get(
                    "single_game_records",
                    {}
                )

                return (
                    f"Champions: **{len(book.get('champions', []))}**\n"
                    f"MVPs: **{len(book.get('mvps', []))}**\n"
                    f"Single-game records: **{len(single)}**\n"
                    f"Biggest blowout stored: **{bool(book.get('biggest_blowout'))}**\n"
                    f"Longest streak stored: **{bool(book.get('longest_win_streak'))}**"
                )

            run_check(
                "recordbook",
                "Project Madden Record Book",
                recordbook_check
            )

        elif item == "halloffame":
            def hof_check():
                hall = load_hall_of_fame()

                return (
                    f"Hall of Fame entries: **{len(hall)}**\n"
                    "Storage read completed successfully."
                )

            run_check(
                "halloffame",
                "Project Madden Hall of Fame",
                hof_check
            )

        elif item == "tradehistory":
            def trade_history_check():
                history = load_trade_history()

                return (
                    f"Saved trades: **{len(history)}**\n"
                    f"Trade logs webhook configured: "
                    f"**{trade_logs_webhook_configured()}**\n"
                    f"League Office webhook configured: "
                    f"**{bool(os.environ.get('DISCORD_WEBHOOK_URL'))}**"
                )

            run_check(
                "tradehistory",
                "Trade History / Discord Trade Connections",
                trade_history_check
            )

        elif item == "gotw":
            def gotw_check():
                candidates = choose_gotw_poll_candidates(
                    season_type,
                    week_number
                )

                return (
                    f"Configured: **{gotw_poll_configured()}**\n"
                    f"Discord bot token: **{bool(discord_bot_token())}**\n"
                    f"#gotw channel ID: **{bool(gotw_channel_id())}**\n"
                    f"Close timer: **{GOTW_POLL_CLOSE_SECONDS} seconds**\n"
                    f"Eligible candidates found: **{len(candidates)}**\n"
                    + (
                        "Candidates: "
                        + ", ".join(
                            str(item.get("team"))
                            for item in candidates[:3]
                        )
                        if candidates
                        else (
                            "No poll candidates yet. "
                            "Run the schedule export for this week first."
                        )
                    )
                )

            run_check(
                "gotw",
                "GOTW Fan Poll",
                gotw_check
            )

    success = (
        len(
            failures
        )
        == 0
    )

    return {
        "success":
            success,
        "fields":
            fields,
        "failures":
            failures
    }


def process_test_system_background(
    interaction
):
    options = discord_option_map(
        interaction
    )

    system_name = str(
        options.get(
            "system",
            "all"
        )
    ).strip().lower()

    season_type = str(
        options.get(
            "season_type",
            "reg"
        )
    ).strip().lower()

    if season_type not in [
        "pre",
        "reg"
    ]:
        season_type = "reg"

    try:
        week_number = int(
            options.get(
                "week",
                1
            )
            or 1
        )
    except Exception:
        week_number = 1

    try:
        test = build_discord_system_test(
            system_name,
            season_type,
            week_number
        )

        sent = send_weekly_show_embed(
            (
                "🧪 PROJECT MADDEN SYSTEM TEST • "
                f"{system_name.upper()}"
            ),
            (
                f"Requested by a member with the "
                f"**@League owner** role.\n"
                f"Season: **{season_type.upper()}** • "
                f"Week: **{week_number}**\n\n"
                f"Overall result: "
                f"**{'PASS ✅' if test.get('success') else 'CHECK REQUIRED ⚠️'}**"
            ),
            test.get(
                "fields",
                []
            )
        )

        if sent.get(
            "sent"
        ):
            content = (
                "✅ System test posted to the "
                "Project Madden Weekly Show channel."
            )
        else:
            content = (
                "❌ Test ran, but the Discord test post failed: "
                + str(
                    sent.get(
                        "error",
                        "Unknown error"
                    )
                )[:900]
            )

    except Exception as e:
        content = (
            "❌ Project Madden system test crashed: "
            + str(
                e
            )[:900]
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



def process_hof_view_background(
    interaction
):
    try:
        summary = hall_of_fame_summary_text(
            20
        )
        content = summary
    except Exception as e:
        content = (
            "❌ Hall of Fame could not load: "
            + str(
                e
            )[:800]
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


def process_remove_hof_background(
    interaction
):
    try:
        options = discord_option_map(
            interaction
        )

        query = str(
            options.get(
                "name_or_id",
                ""
            )
        ).strip()

        result = remove_hall_of_fame_entry(
            query
        )

        if not result.get(
            "success"
        ):
            content = (
                "❌ "
                + str(
                    result.get(
                        "error",
                        "Hall of Fame entry not found."
                    )
                )[:800]
            )
        else:
            removed = result.get(
                "removed",
                {}
            )

            content = (
                f"🗑️ Removed **{removed.get('name')}** "
                "from the Project Madden Hall of Fame."
            )

    except Exception as e:
        content = (
            "❌ Hall of Fame removal crashed: "
            + str(
                e
            )[:800]
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


def process_test_hof_background(
    interaction
):
    try:
        options = discord_option_map(
            interaction
        )

        result = run_hall_of_fame_test(
            name=str(
                options.get(
                    "name",
                    "Project Madden Test Inductee"
                )
            ).strip(),
            inductee_type=str(
                options.get(
                    "type",
                    "Owner / Coach"
                )
            ).strip(),
            team=str(
                options.get(
                    "team",
                    "Project Madden"
                )
            ).strip()
        )

        if result.get(
            "success"
        ):
            test_entry = {
                "hof_id":
                    "test-preview",
                "name":
                    str(
                        options.get(
                            "name",
                            "Project Madden Test Inductee"
                        )
                    ).strip()
                    or "Project Madden Test Inductee",
                "type":
                    str(
                        options.get(
                            "type",
                            "Owner / Coach"
                        )
                    ).strip()
                    or "Owner / Coach",
                "team":
                    str(
                        options.get(
                            "team",
                            "Project Madden"
                        )
                    ).strip()
                    or "Project Madden",
                "reason":
                    (
                        "Testing how the Project Madden analysts react "
                        "to a Hall of Fame induction."
                    ),
                "championships":
                    2,
                "class_year":
                    datetime.now(
                        timezone.utc
                    ).year
            }

            preview = (
                build_hall_of_fame_analyst_reactions(
                    test_entry
                )
            )

            send_weekly_show_embed(
                "🧪 HALL OF FAME ANALYST TEST",
                (
                    f"**Marcus Hayes:** {preview.get('marcus')}\n\n"
                    f"**Stephen A. Smith — AI Parody:** {preview.get('stephen')}\n\n"
                    f"**Pat McAfee — AI Parody:** {preview.get('pat')}\n\n"
                    f"**Josh Pate — AI Parody:** {preview.get('josh_pate')}\n\n"
                    "*Real-person portions are fictional AI parody, not actual statements.*"
                )
            )

            content = (
                "✅ Hall of Fame test created successfully.\n"
                f"Test channel: <#{result.get('channel_id')}>\n"
                "A custom HOF logo and induction profile were posted.\n"
                "Nothing was saved to the permanent Hall of Fame.\n"
                "🧹 The test channel will auto-delete in 5 minutes."
                + (
                    "\n⚠️ "
                    + str(
                        result.get(
                            "category_warning",
                            ""
                        )
                    )
                    if result.get(
                        "category_warning"
                    )
                    else ""
                )
            )
        else:
            content = (
                "❌ Hall of Fame test failed at "
                f"**{result.get('stage', 'setup')}**: "
                f"{str(result.get('error', 'Unknown error'))[:800]}"
            )

    except Exception as e:
        print(
            "TEST HOF BACKGROUND ERROR:",
            repr(
                e
            )
        )

        content = (
            "❌ Hall of Fame test crashed: "
            f"{str(e)[:900]}"
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


def process_induct_hof_background(
    interaction
):
    try:
        options = discord_option_map(
            interaction
        )

        name = str(
            options.get(
                "name",
                ""
            )
        ).strip()

        inductee_type = str(
            options.get(
                "type",
                ""
            )
        ).strip()

        team = str(
            options.get(
                "team",
                ""
            )
        ).strip()

        reason = str(
            options.get(
                "reason",
                ""
            )
        ).strip()

        if not (
            name
            and inductee_type
            and team
            and reason
        ):
            content = (
                "❌ Name, type, team, and reason are required."
            )
        else:
            user_data = (
                interaction.get(
                    "member",
                    {}
                ).get(
                    "user",
                    {}
                )
            )

            inducted_by = (
                user_data.get(
                    "global_name"
                )
                or user_data.get(
                    "username"
                )
                or "League Owner"
            )

            class_year = options.get(
                "class_year"
            )

            entry = build_hall_of_fame_entry(
                name=name,
                inductee_type=inductee_type,
                team=team,
                reason=reason,
                career_record=str(
                    options.get(
                        "career_record",
                        ""
                    )
                ).strip(),
                championships=int(
                    options.get(
                        "championships",
                        0
                    )
                    or 0
                ),
                awards=str(
                    options.get(
                        "awards",
                        ""
                    )
                ).strip(),
                image_url=str(
                    options.get(
                        "image_url",
                        ""
                    )
                ).strip(),
                inducted_by=inducted_by,
                class_year=(
                    int(
                        class_year
                    )
                    if class_year
                    is not None
                    else None
                )
            )

            saved = add_hall_of_fame_entry(
                entry
            )

            if not saved.get(
                "success"
            ):
                content = (
                    "❌ "
                    + str(
                        saved.get(
                            "error",
                            "Could not save induction."
                        )
                    )[:900]
                )
            else:
                analyst_reactions = (
                    post_hall_of_fame_analyst_reactions(
                        entry
                    )
                )

                discord_result = (
                    send_hall_of_fame_induction_to_discord(
                        entry
                    )
                )

                space_result = (
                    provision_hall_of_fame_inductee_space(
                        entry
                    )
                )

                channel_id = (
                    space_result.get(
                        "channel",
                        {}
                    ).get(
                        "channel_id"
                    )
                )

                if (
                    discord_result.get(
                        "sent"
                    )
                    and space_result.get(
                        "success"
                    )
                ):
                    content = (
                        f"🏛️ **{name}** has been inducted into the "
                        f"Project Madden Hall of Fame — Class of "
                        f"{entry.get('class_year')}.\n"
                        f"Induction ID: `{entry.get('hof_id')}`\n"
                        f"Dedicated channel: <#{channel_id}>\n"
                        f"Custom Hall of Fame logo: "
                        f"{hall_of_fame_logo_url(entry.get('hof_id'))}"
                    )
                elif space_result.get(
                    "success"
                ):
                    content = (
                        f"✅ **{name}** was saved and received a dedicated "
                        f"Hall of Fame channel <#{channel_id}> with a custom logo.\n"
                        "The main Hall of Fame announcement channel post failed, "
                        "so check HALL_OF_FAME_CHANNEL_ID.\n"
                        f"Induction ID: `{entry.get('hof_id')}`"
                    )
                else:
                    content = (
                        f"✅ **{name}** was saved permanently, but Discord channel "
                        "creation failed. The bot needs **Manage Channels** and "
                        "**Send Messages / Embed Links** permissions.\n"
                        f"Induction ID: `{entry.get('hof_id')}`"
                    )

    except Exception as e:
        print(
            "INDUCT HOF BACKGROUND ERROR:",
            repr(
                e
            )
        )

        content = (
            "❌ Hall of Fame induction crashed: "
            f"{str(e)[:900]}"
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



def discord_deferred_ephemeral():
    return jsonify({
        "type":
            5,
        "data": {
            "flags":
                64
        }
    })


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

    debug_payload = {
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
    }

    # Never block Discord's 3-second acknowledgement window on
    # debug-file/database I/O.
    threading.Thread(
        target=save_discord_debug,
        args=(
            debug_payload,
        ),
        daemon=True
    ).start()

    guild_id_from_interaction = str(
        interaction.get(
            "guild_id",
            ""
        )
    ).strip()

    if guild_id_from_interaction:
        threading.Thread(
            target=ensure_guild_config,
            args=(
                guild_id_from_interaction,
            ),
            daemon=True
        ).start()

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

        # Runtime security gate for every current and future /test* command.
        if (
            str(
                command_name
                or ""
            ).lower().startswith(
                "test"
            )
            and not discord_member_has_league_owner_role(
                interaction
            )
        ):
            return discord_test_role_denied()

        if command_name in [
            "inducthof",
            "removehof"
        ] and not discord_member_has_league_owner_role(
            interaction
        ):
            return discord_ephemeral(
                "🔒 Hall of Fame management is locked to @League owner."
            )

        if command_name == "setup":
            guild_id = str(
                interaction.get(
                    "guild_id",
                    ""
                )
            ).strip()

            if not guild_id:
                return discord_ephemeral(
                    "❌ Run /setup inside a Discord server."
                )

            if not discord_member_can_manage_guild(
                interaction
            ):
                return discord_ephemeral(
                    "🔒 /setup requires Manage Server or Administrator."
                )

            # IMPORTANT: no database call, Discord API call, or network
            # request occurs here. This keeps the response comfortably
            # inside Discord's acknowledgement window.
            setup_url = setup_start_url(
                guild_id
            )

            return discord_ephemeral_link(
                (
                    "🏈 **PROJECT MADDEN SERVER SETUP**\\n"
                    "Tap the button below to open your private setup dashboard.\\n\\n"
                    "⚠️ **Snallabot is required right now** for official "
                    "Madden league data. Project Madden's direct EA "
                    "connector is still being developed.\\n\\n"
                    "Do not share the setup button/link publicly."
                ),
                "OPEN SETUP DASHBOARD",
                setup_url
            )

        if command_name == "server":
            threading.Thread(
                target=process_server_interaction_background,
                args=(
                    interaction,
                ),
                daemon=True
            ).start()

            return discord_deferred_ephemeral()

        if command_name == "injuries":
            return discord_ephemeral(
                injury_report_text(
                    25
                )
            )

        if command_name == "testinjuries":
            test_event = {
                "event_type": "new",
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "team_id": "test",
                "player": {
                    "team": "Project Madden Test Team",
                    "player": "Test Player",
                    "position": "WR",
                    "overall": 90,
                    "injury": "Test Injury",
                    "status": "Out",
                    "weeks_remaining": 2,
                    "reserve": False
                }
            }

            result = send_injury_event(
                test_event
            )

            if result.get("sent"):
                return discord_ephemeral(
                    "✅ Injury test sent. This was not saved as a real league injury."
                )

            return discord_ephemeral(
                "❌ Injury test failed: "
                + str(result.get("error","Unknown error"))[:800]
            )

        if command_name == "hofping":
            return discord_ephemeral(
                "✅ Hall of Fame interaction endpoint is responding."
            )

        if command_name == "hof":
            worker = threading.Thread(
                target=process_hof_view_background,
                args=(
                    interaction,
                ),
                daemon=True
            )
            worker.start()
            return discord_deferred_ephemeral()

        if command_name == "inducthof":
            worker = threading.Thread(
                target=process_induct_hof_background,
                args=(
                    interaction,
                ),
                daemon=True
            )

            worker.start()

            return discord_deferred_ephemeral()

        if command_name == "removehof":
            worker = threading.Thread(
                target=process_remove_hof_background,
                args=(
                    interaction,
                ),
                daemon=True
            )
            worker.start()
            return discord_deferred_ephemeral()

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

        if command_name == "testhof":
            worker = threading.Thread(
                target=process_test_hof_background,
                args=(
                    interaction,
                ),
                daemon=True
            )

            worker.start()

            return discord_deferred_ephemeral()

        if command_name == "testgotw":
            options = discord_option_map(
                interaction
            )

            season_type = str(
                options.get(
                    "season_type",
                    "reg"
                )
            ).strip().lower()

            try:
                week_number = int(
                    options.get(
                        "week",
                        1
                    )
                )
            except Exception:
                week_number = 1

            if not discord_bot_token():
                return discord_ephemeral(
                    "❌ GOTW test cannot start because DISCORD_BOT_TOKEN "
                    "is not configured in Render."
                )

            if not gotw_channel_id():
                return discord_ephemeral(
                    "❌ GOTW test cannot start because GOTW_CHANNEL_ID "
                    "is not configured in Render."
                )

            candidates = choose_gotw_poll_candidates(
                season_type,
                week_number
            )

            if len(candidates) < 3:
                return discord_ephemeral(
                    (
                        "❌ GOTW needs at least 3 unplayed matchups from the "
                        f"{season_type.upper()} Week {week_number} schedule export. "
                        f"I only found {len(candidates)} candidate(s). "
                        "Run the Snallabot schedules export for that week first."
                    )
                )

            result = create_discord_gotw_poll(
                season_type,
                week_number,
                test=True,
                force=True
            )

            if result.get(
                "success"
            ):
                return discord_ephemeral(
                    "✅ GOTW test poll posted in #gotw. "
                    "It will close automatically in 5 minutes."
                )

            return discord_ephemeral(
                "❌ GOTW test poll failed: "
                + str(
                    result.get(
                        "error",
                        "Unknown error"
                    )
                )[:900]
            )

        if command_name == "testpat":
            options = discord_option_map(
                interaction
            )

            headline = str(
                options.get(
                    "headline",
                    "Project Madden Test Segment"
                )
            ).strip()

            result = send_weekly_show_embed(
                "🧪 TEST • Pat McAfee — AI Parody",
                (
                    f"## {headline}\n"
                    "Alright, this is a Project Madden connection test. "
                    "The Weekly Show channel is live, the desk is connected, "
                    "and this fictional AI parody segment is ready for weekly reactions.\n\n"
                    "⚠️ *Fictional AI parody for Project Madden. "
                    "This is not a real Pat McAfee quote or statement.*"
                )
            )

            if result.get(
                "sent"
            ):
                return discord_ephemeral(
                    "✅ Pat McAfee AI parody test sent."
                )

            return discord_ephemeral(
                "❌ Pat AI parody test failed: "
                + str(
                    result.get(
                        "error",
                        "Unknown error"
                    )
                )[:1000]
            )

        if command_name == "testsystem":
            worker = threading.Thread(
                target=process_test_system_background,
                args=(
                    interaction,
                ),
                daemon=True
            )

            worker.start()

            return jsonify({
                "type":
                    5,
                "data": {
                    "flags":
                        64
                }
            })

        if command_name == "testjoshpate":
            options = discord_option_map(
                interaction
            )

            headline = str(
                options.get(
                    "headline",
                    "Project Madden Test Segment"
                )
            ).strip()

            take = str(
                options.get(
                    "take",
                    "AI parody test."
                )
            ).strip()

            result = send_josh_pate_parody_embed(
                "🧪 TEST • Josh Pate — AI Parody",
                (
                    f"## {headline}\n"
                    f"{take}\n\n"
                    "⚠️ *Fictional AI parody for Project Madden. "
                    "This is not a real Josh Pate quote or statement.*"
                )
            )

            if result.get("sent"):
                return discord_ephemeral(
                    "✅ Josh Pate AI parody test sent."
                )

            return discord_ephemeral(
                "❌ Josh Pate parody test failed: "
                + str(
                    result.get(
                        "error",
                        "Unknown error"
                    )
                )[:1000]
            )

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
    "/discord/ack-check",
    methods=["GET"]
)
def discord_ack_check():
    return jsonify({
        "app_version":
            PROJECT_MADDEN_APP_VERSION,
        "debug_persistence_async":
            True,
        "hofping":
            "immediate",
        "hof":
            "deferred",
        "testhof":
            "deferred",
        "inducthof":
            "deferred",
        "removehof":
            "deferred"
    })


@app.route(
    "/discord/route-check",
    methods=["GET"]
)
def discord_route_check():
    rules = []

    for rule in app.url_map.iter_rules():
        if str(
            rule.rule
        ) == "/discord/interactions":
            rules.append({
                "rule":
                    str(
                        rule.rule
                    ),
                "endpoint":
                    str(
                        rule.endpoint
                    ),
                "methods":
                    sorted(
                        list(
                            rule.methods
                        )
                    )
            })

    return jsonify({
        "app_version":
            PROJECT_MADDEN_APP_VERSION,
        "discord_interaction_routes":
            rules,
        "correct_endpoint":
            any(
                item.get(
                    "endpoint"
                ) == "discord_interactions"
                for item in rules
            )
    })


@app.route(
    "/discord/test-readiness",
    methods=["GET"]
)
def discord_test_readiness():
    commands = {
        "testmarcus": {
            "ready":
                analyst_webhook_configured(),
            "needs":
                "ANALYST_DISCORD_WEBHOOK_URL"
        },
        "teststephena": {
            "ready":
                stephen_a_parody_webhook_configured(),
            "needs":
                "STEPHEN_A_PARODY_WEBHOOK_URL"
        },
        "testpat": {
            "ready":
                weekly_show_webhook_configured(),
            "needs":
                "WEEKLY_SHOW_DISCORD_WEBHOOK_URL"
        },
        "testjoshpate": {
            "ready":
                josh_pate_parody_webhook_configured(),
            "needs":
                "JOSH_PATE_PARODY_WEBHOOK_URL"
        },
        "testweeklyshow": {
            "ready":
                weekly_show_webhook_configured(),
            "needs":
                "WEEKLY_SHOW_DISCORD_WEBHOOK_URL"
        },
        "testsystem": {
            "ready":
                weekly_show_webhook_configured(),
            "needs":
                "WEEKLY_SHOW_DISCORD_WEBHOOK_URL"
        },
        "testgotw": {
            "ready":
                gotw_poll_configured(),
            "needs":
                "DISCORD_BOT_TOKEN + GOTW_CHANNEL_ID"
        },
        "testinjuries": {
            "ready":
                bool(
                    injury_webhook_url()
                ),
            "needs":
                "INJURY_DISCORD_WEBHOOK_URL"
        },
        "testhof": {
            "ready":
                bool(
                    discord_bot_token()
                    and discord_guild_id()
                ),
            "needs":
                (
                    "DISCORD_BOT_TOKEN + DISCORD_GUILD_ID + "
                    "bot Manage Channels / Send Messages / Embed Links"
                )
        },
        "halloffame": {
            "ready":
                hall_of_fame_discord_configured(),
            "needs":
                (
                    "DISCORD_BOT_TOKEN + HALL_OF_FAME_CHANNEL_ID "
                    "+ optional HALL_OF_FAME_CATEGORY_ID"
                )
        }
    }

    return jsonify({
        "league_owner_role_id":
            LEAGUE_OWNER_TEST_ROLE_ID,
        "all_test_commands_role_locked":
            True,
        "commands":
            commands,
        "all_ready":
            all(
                item.get(
                    "ready"
                )
                for item in commands.values()
            )
    })




@app.route(
    "/discord/register-setup",
    methods=["GET", "POST"]
)
def register_setup_commands_only():
    app_id = discord_application_id()
    token = discord_bot_token()
    guild_id = discord_guild_id()

    if not app_id or not token:
        return jsonify({
            "success": False,
            "error": (
                "DISCORD_APPLICATION_ID or DISCORD_BOT_TOKEN is missing."
            )
        }), 500

    commands = [
        {
            "name": "setup",
            "description": (
                "Server admin: connect this Discord server to Project Madden"
            )
        },
        {
            "name": "server",
            "description": (
                "View this server's Project Madden league connection"
            )
        }
    ]

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

    results = {}

    # Upsert individually so this endpoint never deletes the rest of the
    # Project Madden command set.
    for scope_name, base in [
        (
            "global",
            f"{DISCORD_API_BASE}/applications/{app_id}/commands"
        ),
        (
            "home_guild",
            (
                f"{DISCORD_API_BASE}/applications/{app_id}/guilds/"
                f"{guild_id}/commands"
                if guild_id
                else ""
            )
        )
    ]:
        if not base:
            continue

        scope_results = []

        for command in commands:
            response = requests.post(
                base,
                headers=headers,
                json=command,
                timeout=15
            )

            scope_results.append({
                "command": command["name"],
                "status_code": response.status_code,
                "success": response.status_code in [200, 201],
                "response": (
                    response.json()
                    if response.status_code in [200, 201]
                    else response.text[:500]
                )
            })

        results[scope_name] = scope_results

    return jsonify({
        "success": all(
            item.get("success")
            for group in results.values()
            for item in group
        ),
        "results": results,
        "note": (
            "This endpoint only upserts /setup and /server and does not "
            "delete any other Project Madden commands."
        )
    })


@app.route(
    "/discord/force-register",
    methods=["GET", "POST"]
)
def discord_force_register():
    result = register_trade_slash_command()

    expected = expected_project_madden_commands()

    registered = result.get(
        "registered",
        []
    )

    missing = [
        name
        for name in expected
        if name not in registered
    ]

    return jsonify({
        "app_version":
            PROJECT_MADDEN_APP_VERSION,
        "success":
            bool(
                result.get(
                    "success"
                )
            )
            and not missing,
        "scope":
            result.get(
                "scope"
            ),
        "guild_id_configured":
            result.get(
                "guild_id_configured"
            ),
        "registered":
            registered,
        "expected":
            expected,
        "missing":
            missing,
        "raw":
            result
    }), (
        200
        if (
            result.get(
                "success"
            )
            and not missing
        )
        else 400
    )


@app.route(
    "/version",
    methods=["GET"]
)
def app_version_route():
    return jsonify({
        "app_version":
            PROJECT_MADDEN_APP_VERSION,
        "expected_discord_commands":
            expected_project_madden_commands()
    })


@app.route(
    "/discord/status",
    methods=["GET"]
)
def discord_status():
    return jsonify({
        "multi_server_enabled":
            True,
        "global_commands_enabled":
            True,
        "discord_install_url":
            discord_install_url(),
        "dashboard_url":
            PROJECT_MADDEN_BASE_URL
            + "/dashboard",
        "app_version":
            PROJECT_MADDEN_APP_VERSION,
        "expected_commands":
            expected_project_madden_commands(),
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
            "/testpat",
            "/testjoshpate",
            "/testweeklyshow",
            "/testsystem",
            "/testgotw",
            "/testhof",
            "/hofping"
        ],
        "test_commands_locked_to_role":
            "League owner",
        "test_role_id":
            LEAGUE_OWNER_TEST_ROLE_ID,
        "testsystem_choices": [
            "all",
            "storage",
            "playoffs",
            "rivalries",
            "accuracy",
            "receipts",
            "debate",
            "recordbook",
            "halloffame",
            "tradehistory",
            "gotw"
        ],
        "weekly_show_command":
            "/weeklyshow",
        "gotw_poll_configured":
            gotw_poll_configured(),
        "gotw_channel_id_configured":
            bool(
                gotw_channel_id()
            ),
        "gotw_poll_close_seconds":
            GOTW_POLL_CLOSE_SECONDS,
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
,
        "trade_logs_webhook_configured":
            trade_logs_webhook_configured()
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

        try:
            injury_result = process_team_injury_export(
                team_id,
                data
            )
        except Exception as e:
            injury_result = {
                "success": False,
                "error": str(e)
            }

        return jsonify({
            "success": True,
            "type": "roster",
            "team_id": team_id,
            "injury_tracking": injury_result
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
        gotw_auto_post = None

        # Create the weekly GOTW fan poll as soon as schedules arrive.
        if stat_type == "schedules":
            try:
                gotw_auto_post = create_discord_gotw_poll(
                    season_type,
                    int(
                        week_number
                    ),
                    test=False,
                    force=False
                )
            except Exception as e:
                gotw_auto_post = {
                    "success":
                        False,
                    "error":
                        str(
                            e
                        )
                }

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
            "marcus_auto_post": auto_post,
            "gotw_auto_post": gotw_auto_post
        })

    return jsonify({
        "success": True,
        "type": "unknown",
        "path": subpath
    })



# =========================================================
# PROJECT MADDEN WEB DASHBOARD
# =========================================================

PROJECT_MADDEN_DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Project Madden Analytics</title>
<style>
:root{
  --bg:#070b12;--panel:#101722;--panel2:#151e2c;--line:#243044;
  --text:#f4f7fb;--muted:#9facbf;--accent:#55b8ff;--good:#57d38c;
  --warn:#f4c95d;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(135deg,#070b12,#0c1320 60%,#0a1019);
color:var(--text);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{color:inherit;text-decoration:none}
.shell{max-width:1260px;margin:auto;padding:24px}
.top{display:flex;gap:16px;align-items:center;justify-content:space-between;padding:18px 0 28px}
.brand{font-size:25px;font-weight:900;letter-spacing:.04em}
.brand span{color:var(--accent)}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:12px 18px;border-radius:12px;
background:var(--accent);color:#04111c;font-weight:800;border:0}
.btn.secondary{background:var(--panel2);color:var(--text);border:1px solid var(--line)}
.hero{background:radial-gradient(circle at top right,#153c5c,transparent 45%),var(--panel);
border:1px solid var(--line);border-radius:22px;padding:30px;margin-bottom:22px}
.hero h1{font-size:42px;line-height:1.05;margin:0 0 12px}
.hero p{max-width:740px;color:var(--muted);font-size:17px;line-height:1.6}
.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px}
.card h3{margin:0 0 8px}.muted{color:var(--muted)}
.status{display:inline-block;padding:5px 10px;border-radius:999px;font-size:12px;font-weight:800;
background:#163323;color:#74e9a8;margin-top:10px}
.status.off{background:#342b13;color:#f2cf72}
.kpi{font-size:34px;font-weight:900;margin:6px 0}
.footer{padding:34px 0 12px;text-align:center;color:var(--muted);font-size:14px}
@media(max-width:800px){.grid{grid-template-columns:1fr}.hero h1{font-size:34px}.top{align-items:flex-start;flex-direction:column}}
</style>
</head>
<body>
<div class="shell">
  <div class="top">
    <div class="brand">PROJECT MADDEN <span>ANALYTICS</span></div>
    <a class="btn" href="{{ install_url }}">+ Add Project Madden to Discord</a>
  </div>
  <section class="hero">
    <h1>Your Madden league.<br>One control center.</h1>
    <p>Connect Discord servers and Madden leagues to Project Madden Analytics.
    <b>Snallabot is currently required as the official Madden data source</b> while
    Project Madden runs the dashboard, league setup, media, injuries, Hall of Fame,
    GOTW, trades, and analytics. We are researching a direct EA connection so
    Project Madden can eventually become its own source.</p>
    <div class="actions">
      <a class="btn" href="{{ install_url }}">Add to Discord</a>
      <a class="btn secondary" href="/discord/status">Discord Status</a>
    </div>
  </section>

  <div class="grid" style="margin-bottom:22px">
    <div class="card">
      <div class="muted">CURRENT OFFICIAL DATA SOURCE</div>
      <div class="kpi" style="font-size:25px">Snallabot</div>
      <div class="status">OFFICIAL SOURCE</div>
      <p class="muted">Project Madden receives the league exports and powers the dashboard around them.</p>
    </div>
    <div class="card">
      <div class="muted">PROJECT MADDEN DIRECT EA CONNECTOR</div>
      <div class="kpi" style="font-size:25px">Coming Soon</div>
      <div class="status off">DISABLED FOR NOW</div>
      <p class="muted">Goal: connect Madden/EA directly and remove the Snallabot requirement.</p>
    </div>
    <div class="card">
      <div class="muted">SERVER SETUP</div>
      <div class="kpi" style="font-size:25px">/setup</div>
      <p class="muted">Server admins receive a private dashboard connection link from Discord.</p>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="muted">CONNECTED SERVERS</div>
      <div class="kpi">{{ guild_count }}</div>
      <div class="muted">Discord servers registered with Project Madden.</div>
    </div>
    <div class="card">
      <div class="muted">LEAGUES CONNECTED</div>
      <div class="kpi">{{ league_count }}</div>
      <div class="muted">Servers with a Snallabot league ID configured.</div>
    </div>
    <div class="card">
      <div class="muted">APP VERSION</div>
      <div class="kpi" style="font-size:20px">{{ app_version }}</div>
      <div class="status">MULTI-SERVER FOUNDATION</div>
    </div>
  </div>

  <h2 style="margin-top:30px">Connected Servers</h2>
  <div class="grid">
    {% for guild in guilds %}
    <div class="card">
      <h3>{{ guild.guild_name or ("Discord Server " ~ guild.guild_id) }}</h3>
      <div class="muted">{{ guild.league_name or "League setup not completed" }}</div>
      {% if guild.snallabot_league_id %}
        <div class="status">CONNECTED</div>
        <p class="muted">Snallabot ID {{ guild.snallabot_league_id }} • {{ guild.platform or "Platform not set" }}</p>
      {% else %}
        <div class="status off">SETUP NEEDED</div>
      {% endif %}
    </div>
    {% else %}
    <div class="card">
      <h3>No servers connected yet</h3>
      <p class="muted">Install Project Madden in Discord, then run <b>/setup</b>.</p>
    </div>
    {% endfor %}
  </div>

  <div class="footer">Built for Project Madden • Thanks to Developer Jay</div>
</div>
</body>
</html>
"""


PROJECT_MADDEN_SETUP_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect League • Project Madden</title>
<style>
:root{
  --bg:#070b12;--panel:#101722;--panel2:#0b111a;--line:#243044;
  --text:#f4f7fb;--muted:#9facbf;--accent:#55b8ff;--good:#57d38c;
  --warn:#f4c95d;--purple:#8b5cf6
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#070b12,#09111b);color:var(--text);
font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:850px;margin:0 auto;padding:28px 18px 70px}
.brand{font-weight:950;font-size:24px;margin-bottom:24px;letter-spacing:.02em}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:22px;padding:24px;box-shadow:0 20px 60px rgba(0,0,0,.25)}
h1{margin:0 0 8px;font-size:38px;line-height:1.08}
p{color:var(--muted);line-height:1.6}
.server-pill{display:inline-flex;align-items:center;gap:8px;background:#0c1722;border:1px solid var(--line);border-radius:999px;padding:8px 12px;color:#dce7f5;font-size:13px;margin:6px 0 14px}
.info{background:#0d1824;border:1px solid var(--line);padding:14px;border-radius:12px;margin:14px 0;color:var(--muted);word-break:break-word}
.auto{background:linear-gradient(135deg,#10283a,#171d39);border:1px solid #315778;border-radius:16px;padding:16px;margin:18px 0}
.auto-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.auto-title{font-weight:900;font-size:17px}
.badge{padding:5px 9px;border-radius:999px;background:#162d22;color:#7ee8ac;font-size:11px;font-weight:900}
.badge.wait{background:#302912;color:#f1d071}
label{display:block;font-size:13px;font-weight:850;margin:18px 0 7px}
input,select{width:100%;padding:13px 14px;background:var(--panel2);border:1px solid var(--line);border-radius:11px;color:var(--text);font-size:16px}
select{appearance:auto}
button{margin-top:20px;width:100%;padding:14px;background:linear-gradient(135deg,#58baff,#7c62ff);color:#05111c;border:0;border-radius:12px;font-weight:950;font-size:16px}
button.secondary{margin-top:10px;background:#141d2a;color:#e9f2ff;border:1px solid var(--line)}
.ok{background:#10291d;border:1px solid #245b3e;color:#85eeb2;padding:12px;border-radius:10px;margin:14px 0}
.detect-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.detect-item{background:#0a1119;border:1px solid #243044;border-radius:10px;padding:10px}
.detect-label{font-size:11px;color:#8fa0b6;text-transform:uppercase;font-weight:900}
.detect-value{margin-top:4px;font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.note{font-size:12px;color:#8fa0b6;margin-top:6px}
.footer{text-align:center;color:var(--muted);padding-top:28px;font-size:13px}
@media(max-width:640px){
  h1{font-size:34px}.panel{padding:20px}.detect-grid{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">PROJECT MADDEN ANALYTICS <span style="color:#55b8ff">V3</span></div>
  <div class="panel">
    <h1>Connect Your Madden League</h1>
    <div class="server-pill">🟢 Discord Server: <b id="serverName">{{ guild.guild_name or guild.guild_id }}</b></div>

    <div class="info"><b>Important:</b> Snallabot is currently required for Project Madden to receive official Madden league data. Direct EA connection is coming soon and will not require you to provide a private EA client secret.</div>

    {% if saved %}<div class="ok">✅ League settings saved.</div>{% endif %}

    <div class="auto">
      <div class="auto-head">
        <div>
          <div class="auto-title">✨ Discord Auto Detect</div>
          <div class="note">Project Madden scans the server the bot is installed in and tries to match your channels automatically.</div>
        </div>
        <span class="badge wait" id="detectBadge">SCANNING</span>
      </div>

      <div class="detect-grid" id="detectGrid">
        <div class="detect-item"><div class="detect-label">Server</div><div class="detect-value" id="detServer">Checking…</div></div>
        <div class="detect-item"><div class="detect-label">Channels</div><div class="detect-value" id="detChannels">Checking…</div></div>
        <div class="detect-item"><div class="detect-label">GOTW</div><div class="detect-value" id="detGotw">Checking…</div></div>
        <div class="detect-item"><div class="detect-label">Injuries</div><div class="detect-value" id="detInjuries">Checking…</div></div>
      </div>

      <button type="button" class="secondary" onclick="runAutoDetect(true)">RUN AUTO DETECT AGAIN</button>

      <div id="discordRepair" style="display:none;margin-top:14px;padding:14px;border:1px solid #6b4e13;background:#2b2410;border-radius:12px">
        <div style="font-weight:900;color:#ffd66b">Discord Bot Access Required</div>
        <div class="note" id="discordRepairText" style="margin:7px 0 12px">Project Madden cannot read this server yet.</div>
        <a id="discordRepairLink" href="#" target="_blank" rel="noopener"
           style="display:block;text-align:center;padding:12px;border-radius:10px;background:#5865F2;color:white;font-weight:900;text-decoration:none">
          RECONNECT PROJECT MADDEN BOT
        </a>
      </div>
    </div>

    <div class="auto">
      <div class="auto-head">
        <div>
          <div class="auto-title">🎮 Direct EA Connection</div>
          <div class="note">Coming soon. Project Madden will not ask you for an EA client secret or private EA developer credential.</div>
        </div>
        <span class="badge wait">COMING SOON</span>
      </div>
      <div class="info">
        <b>Current official Madden data source:</b> Snallabot.<br>
        Keep your Snallabot League ID connected so Project Madden can receive teams, rosters, standings, schedules, stats, and injuries.
      </div>
    </div>

    <div class="auto">
      <div class="auto-head">
        <div>
          <div class="auto-title">🛠️ Create Project Madden Channels</div>
          <div class="note">If your server does not already have the channels, Project Madden can create them for you.</div>
        </div>
        <span class="badge wait" id="createBadge">OPTIONAL</span>
      </div>

      <div class="detect-grid">
        <label class="detect-item" style="margin:0;cursor:pointer">
          <div class="detect-label">Category</div>
          <div class="detect-value"><input type="checkbox" id="makeCategory" checked style="width:auto"> project-madden</div>
        </label>
        <label class="detect-item" style="margin:0;cursor:pointer">
          <div class="detect-label">Channel</div>
          <div class="detect-value"><input type="checkbox" id="makeGotw" checked style="width:auto"> #gotw</div>
        </label>
        <label class="detect-item" style="margin:0;cursor:pointer">
          <div class="detect-label">Channel</div>
          <div class="detect-value"><input type="checkbox" id="makeHof" checked style="width:auto"> #hall-of-fame</div>
        </label>
        <label class="detect-item" style="margin:0;cursor:pointer">
          <div class="detect-label">Channel</div>
          <div class="detect-value"><input type="checkbox" id="makeInjuries" checked style="width:auto"> #injuries</div>
        </label>
        <label class="detect-item" style="margin:0;cursor:pointer">
          <div class="detect-label">Channel</div>
          <div class="detect-value"><input type="checkbox" id="makeWeekly" checked style="width:auto"> #weekly-show</div>
        </label>
      </div>

      <button type="button" onclick="createChannels()">CREATE SELECTED CHANNELS</button>
      <div class="note" id="createResult">Requires the Project Madden bot to have Manage Channels permission.</div>
    </div>

    <form method="post">
      <label>League Name</label>
      <input name="league_name" value="{{ guild.league_name or '' }}" placeholder="Project Madden 32" required>

      <label>Snallabot League ID</label>
      <input name="snallabot_league_id" value="{{ guild.snallabot_league_id or '' }}" placeholder="1360051" inputmode="numeric" required>
      <div class="note">Required for now. Project Madden uses this Snallabot league connection as the official Madden data source.</div>

      <label>Platform</label>
      <select name="platform">
        {% for value,label in platforms %}
          <option value="{{ value }}" {% if guild.platform == value %}selected{% endif %}>{{ label }}</option>
        {% endfor %}
      </select>

      <label>GOTW Channel</label>
      <select name="gotw_channel_id" id="gotw_channel_id" data-current="{{ settings.get('gotw_channel_id','') }}">
        <option value="">Auto detect / Not configured</option>
      </select>

      <label>Hall of Fame Channel</label>
      <select name="hall_of_fame_channel_id" id="hall_of_fame_channel_id" data-current="{{ settings.get('hall_of_fame_channel_id','') }}">
        <option value="">Auto detect / Not configured</option>
      </select>

      <label>Hall of Fame Category</label>
      <select name="hall_of_fame_category_id" id="hall_of_fame_category_id" data-current="{{ settings.get('hall_of_fame_category_id','') }}">
        <option value="">Auto detect / Not configured</option>
      </select>

      <label>Injury Channel</label>
      <select name="injury_channel_id" id="injury_channel_id" data-current="{{ settings.get('injury_channel_id','') }}">
        <option value="">Auto detect / Not configured</option>
      </select>

      <label>Weekly Show Channel</label>
      <select name="weekly_show_channel_id" id="weekly_show_channel_id" data-current="{{ settings.get('weekly_show_channel_id','') }}">
        <option value="">Auto detect / Not configured</option>
      </select>

      <button type="submit">SAVE LEAGUE CONNECTION</button>
    </form>

    {% if guild.snallabot_league_id %}
    <div class="info">
      <b>Snallabot receiver base</b><br>
      {{ base_url }}/snallabot/{{ guild.platform or 'xbsx' }}/{{ guild.snallabot_league_id }}
    </div>
    {% endif %}
  </div>

  <div class="footer">Built for Project Madden • Thanks to Developer Jay</div>
</div>

<script>
const autoDetectUrl = "/dashboard/setup/autodetect/{{ guild.setup_token }}";
const createChannelsUrl = "/dashboard/setup/create-channels/{{ guild.setup_token }}";

function escapeText(value){
  return String(value ?? "");
}

function channelNameById(list,id){
  const found=(list||[]).find(x=>String(x.id)===String(id));
  return found ? "#" + found.name : "Not found";
}

function fillSelect(id,items,suggested){
  const el=document.getElementById(id);
  if(!el) return;

  const current=el.dataset.current || "";
  const desired=current || suggested || "";

  el.innerHTML='<option value="">Not configured</option>';

  (items||[]).forEach(item=>{
    const opt=document.createElement("option");
    opt.value=String(item.id);
    opt.textContent=(item.type===4 ? "📁 " : "#") + item.name;
    if(String(item.id)===String(desired)){
      opt.selected=true;
    }
    el.appendChild(opt);
  });
}

async function runAutoDetect(manual=false){
  const badge=document.getElementById("detectBadge");
  const repair=document.getElementById("discordRepair");
  const repairText=document.getElementById("discordRepairText");
  const repairLink=document.getElementById("discordRepairLink");

  badge.textContent="SCANNING";
  badge.className="badge wait";

  try{
    const res=await fetch(autoDetectUrl,{cache:"no-store"});
    const data=await res.json();
    const diag=data.discord_diagnostic || {};

    if(data.reinstall_url && repairLink){
      repairLink.href=data.reinstall_url;
    }

    if(!diag.success){
      repair.style.display="block";
      const guildCode=diag.guild_status_code ?? "—";
      const channelCode=diag.channels_status_code ?? "—";
      repairText.textContent=
        (diag.error || "Project Madden cannot read this Discord server.") +
        ` Guild API: ${guildCode}; Channels API: ${channelCode}.`;

      document.getElementById("detServer").textContent=
        guildCode===404 ? "Bot not installed" :
        guildCode===403 ? "Access denied" :
        guildCode===401 ? "Bot token rejected" :
        "Could not read";

      document.getElementById("detChannels").textContent=
        channelCode===403 ? "Permission denied" :
        channelCode===404 ? "Bot not installed" :
        "Unavailable";

      document.getElementById("detGotw").textContent="Reconnect bot";
      document.getElementById("detInjuries").textContent="Reconnect bot";

      badge.textContent="FIX ACCESS";
      badge.className="badge wait";
      return;
    }

    repair.style.display="none";

    if(!res.ok || !data.success){
      throw new Error(data.error || "Discord channels could not be read.");
    }

    const text=data.text_channels || [];
    const cats=data.categories || [];
    const s=data.suggestions || {};

    document.getElementById("serverName").textContent=
      data.guild_name || diag.guild_name || "Connected Server";
    document.getElementById("detServer").textContent=
      data.guild_name || diag.guild_name || "Connected";

    document.getElementById("detChannels").textContent=
      `${data.channel_count || 0} channels • ${data.category_count || 0} categories`;

    document.getElementById("detGotw").textContent=
      s.gotw_channel_id ? channelNameById(text,s.gotw_channel_id) : "No exact match";

    document.getElementById("detInjuries").textContent=
      s.injury_channel_id ? channelNameById(text,s.injury_channel_id) : "No exact match";

    fillSelect("gotw_channel_id",text,s.gotw_channel_id);
    fillSelect("hall_of_fame_channel_id",text,s.hall_of_fame_channel_id);
    fillSelect("injury_channel_id",text,s.injury_channel_id);
    fillSelect("weekly_show_channel_id",text,s.weekly_show_channel_id);
    fillSelect("hall_of_fame_category_id",cats,s.hall_of_fame_category_id);

    badge.textContent="DETECTED";
    badge.className="badge";
  }catch(err){
    repair.style.display="block";
    repairText.textContent=String(err.message || err);
    badge.textContent="MANUAL";
    badge.className="badge wait";
    document.getElementById("detServer").textContent="Could not read";
    document.getElementById("detChannels").textContent="Check Discord access";
    document.getElementById("detGotw").textContent="Choose manually";
    document.getElementById("detInjuries").textContent="Choose manually";
  }
}

async function createChannels(){
  const badge=document.getElementById("createBadge");
  const resultEl=document.getElementById("createResult");

  badge.textContent="CREATING";
  badge.className="badge wait";
  resultEl.textContent="Creating selected Project Madden channels…";

  try{
    const res=await fetch(createChannelsUrl,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        create_category:document.getElementById("makeCategory").checked,
        create_gotw:document.getElementById("makeGotw").checked,
        create_hof:document.getElementById("makeHof").checked,
        create_injuries:document.getElementById("makeInjuries").checked,
        create_weekly_show:document.getElementById("makeWeekly").checked
      })
    });

    const data=await res.json();

    if(!res.ok){
      throw new Error(data.error || "Channel creation failed.");
    }

    const created=Object.keys(data.created || {});
    const failed=Object.keys(data.failures || {});

    if(created.length){
      resultEl.textContent="Created: " + created.join(", ") + (failed.length ? " • Failed: " + failed.join(", ") : "");
    }else{
      resultEl.textContent="No channels were created. Check bot Manage Channels permission.";
    }

    badge.textContent=failed.length ? "PARTIAL" : "CREATED";
    badge.className=failed.length ? "badge wait" : "badge";

    await runAutoDetect(true);
  }catch(err){
    badge.textContent="FAILED";
    badge.className="badge wait";
    resultEl.textContent=String(err.message || err);
  }
}

document.addEventListener("DOMContentLoaded",()=>runAutoDetect(false));
</script>
</body>
</html>
"""



@app.route(
    "/dashboard"
)
def project_madden_dashboard():
    guilds = list_guild_configs()

    return render_template_string(
        PROJECT_MADDEN_DASHBOARD_HTML,
        guilds=guilds,
        guild_count=len(
            guilds
        ),
        league_count=len(
            [
                guild
                for guild in guilds
                if guild.get(
                    "snallabot_league_id"
                )
            ]
        ),
        install_url=discord_install_url(),
        app_version=PROJECT_MADDEN_APP_VERSION
    )


@app.route(
    "/install"
)
def project_madden_install():
    url = discord_install_url()

    if not url:
        return jsonify({
            "success":
                False,
            "error":
                "DISCORD_APPLICATION_ID is not configured."
        }), 500

    return (
        "<!doctype html><meta name='viewport' "
        "content='width=device-width,initial-scale=1'>"
        "<body style='background:#070b12;color:white;font-family:system-ui;"
        "display:grid;place-items:center;min-height:100vh'>"
        "<div style='text-align:center'>"
        "<h1>Add Project Madden to Discord</h1>"
        f"<a href='{url}' style='display:inline-block;padding:14px 20px;"
        "background:#55b8ff;color:#05111c;border-radius:12px;"
        "font-weight:900;text-decoration:none'>CONTINUE TO DISCORD</a>"
        "<p style='color:#9facbf'>Built for Project Madden • "
        "Thanks to Developer Jay</p></div></body>"
    )


@app.route(
    "/dashboard/setup/start/<guild_id>/<signature>"
)
def project_madden_setup_start(
    guild_id,
    signature
):
    if not valid_setup_start_signature(
        guild_id,
        signature
    ):
        return (
            "Invalid Project Madden setup link.",
            403
        )

    config = ensure_guild_config(
        guild_id
    )

    if not config:
        return (
            "<!doctype html><meta name='viewport' "
            "content='width=device-width,initial-scale=1'>"
            "<body style='background:#070b12;color:white;"
            "font-family:system-ui;padding:28px'>"
            "<h1>Project Madden Setup</h1>"
            "<p>We could not create the server record.</p>"
            "<p>Project Madden could not write the server configuration. "
            "Open /dashboard/setup-health for the exact storage status, "
            "then reload this page.</p>"
            "<p><b>Snallabot is still required for official Madden "
            "league data right now.</b></p>"
            "</body>",
            503
        )

    return redirect(
        guild_setup_url(
            config.get(
                "setup_token"
            )
        ),
        code=302
    )




@app.route(
    "/dashboard/setup/create-channels/<setup_token>",
    methods=[
        "POST"
    ]
)
def project_madden_setup_create_channels(
    setup_token
):
    guild = get_guild_config_by_token(
        setup_token
    )

    if not guild:
        return jsonify({
            "success":
                False,
            "error":
                "Invalid or expired setup token."
        }), 404

    payload = request.get_json(
        silent=True
    ) or {}

    result = create_project_madden_channel_bundle(
        guild.get(
            "guild_id"
        ),
        create_category=bool(
            payload.get(
                "create_category",
                True
            )
        ),
        create_gotw=bool(
            payload.get(
                "create_gotw",
                True
            )
        ),
        create_hof=bool(
            payload.get(
                "create_hof",
                True
            )
        ),
        create_injuries=bool(
            payload.get(
                "create_injuries",
                True
            )
        ),
        create_weekly_show=bool(
            payload.get(
                "create_weekly_show",
                True
            )
        )
    )

    # Save newly created channel IDs into this server's setup settings.
    try:
        current = get_guild_config_by_token(
            setup_token
        )

        settings = dict(
            current.get(
                "settings",
                {}
            )
            if current
            else {}
        )

        created = result.get(
            "created",
            {}
        )

        mapping = {
            "gotw":
                "gotw_channel_id",
            "hall_of_fame":
                "hall_of_fame_channel_id",
            "injuries":
                "injury_channel_id",
            "weekly_show":
                "weekly_show_channel_id",
            "category":
                "hall_of_fame_category_id"
        }

        for created_key, setting_key in mapping.items():
            item = created.get(
                created_key
            )

            if (
                isinstance(
                    item,
                    dict
                )
                and item.get(
                    "id"
                )
            ):
                settings[
                    setting_key
                ] = str(
                    item[
                        "id"
                    ]
                )

        if current:
            save_guild_setup(
                current.get(
                    "guild_id"
                ),
                current.get(
                    "league_name",
                    ""
                ),
                current.get(
                    "snallabot_league_id",
                    ""
                ),
                current.get(
                    "platform",
                    ""
                ),
                settings
            )

    except Exception as e:
        result[
            "settings_save_error"
        ] = str(
            e
        )

    result[
        "note"
    ] = (
        "The bot must have Manage Channels permission in this Discord server."
    )

    return jsonify(
        result
    )


@app.route(
    "/dashboard/setup/discord-access/<setup_token>"
)
def project_madden_setup_discord_access(
    setup_token
):
    guild = get_guild_config_by_token(
        setup_token
    )

    if not guild:
        return jsonify({
            "success": False,
            "error": "Invalid or expired setup token."
        }), 404

    diagnostic = discord_guild_api_diagnostic(
        guild.get(
            "guild_id"
        )
    )

    diagnostic[
        "reinstall_url"
    ] = discord_install_url(
        guild.get(
            "guild_id"
        )
    )

    diagnostic[
        "required_permissions"
    ] = [
        "Manage Channels",
        "View Channels",
        "Send Messages",
        "Embed Links",
        "Attach Files",
        "Read Message History"
    ]

    return jsonify(
        diagnostic
    )


@app.route(
    "/dashboard/setup/autodetect/<setup_token>",
    methods=[
        "GET"
    ]
)
def project_madden_setup_autodetect(
    setup_token
):
    guild = get_guild_config_by_token(
        setup_token
    )

    if not guild:
        return jsonify({
            "success":
                False,
            "error":
                "Invalid or expired setup token."
        }), 404

    guild_id = guild.get(
        "guild_id"
    )

    discord_diagnostic = discord_guild_api_diagnostic(
        guild_id
    )

    detected = detect_discord_setup(
        guild_id
    )

    detected[
        "discord_diagnostic"
    ] = discord_diagnostic

    detected[
        "reinstall_url"
    ] = discord_install_url(
        guild_id
    )

    # If Discord returned the real server name, persist it.
    if detected.get(
        "guild_name"
    ):
        try:
            store = _load_guild_store()

            guild_id = str(
                guild.get(
                    "guild_id"
                )
            )

            current = (
                store
                .get(
                    "guilds",
                    {}
                )
                .get(
                    guild_id
                )
            )

            if isinstance(
                current,
                dict
            ):
                current[
                    "guild_name"
                ] = detected[
                    "guild_name"
                ]

                current[
                    "updated_at"
                ] = datetime.now(
                    timezone.utc
                ).isoformat()

                store[
                    "guilds"
                ][
                    guild_id
                ] = current

                _save_guild_store(
                    store
                )
        except Exception as e:
            print(
                "GUILD NAME AUTODETECT SAVE ERROR:",
                repr(
                    e
                )
            )

    detected[
        "snallabot_required"
    ] = True

    detected[
        "league_id_auto_detected"
    ] = False

    detected[
        "league_id_note"
    ] = (
        "Discord channel IDs and the server name can be auto-detected. "
        "The Snallabot League ID still has to be entered unless the server "
        "has already been connected, because Discord does not expose "
        "Snallabot's league mapping."
    )

    return jsonify(
        detected
    )


@app.route(
    "/dashboard/setup/<setup_token>",
    methods=[
        "GET",
        "POST"
    ]
)
def project_madden_guild_setup(
    setup_token
):
    guild = get_guild_config_by_token(
        setup_token
    )

    if not guild:
        return (
            "Invalid or expired Project Madden setup link.",
            404
        )

    saved = False

    if request.method == "POST":
        settings = dict(
            guild.get(
                "settings",
                {}
            )
        )

        for key in [
            "gotw_channel_id",
            "hall_of_fame_channel_id",
            "hall_of_fame_category_id",
            "injury_channel_id",
            "weekly_show_channel_id"
        ]:
            value = str(
                request.form.get(
                    key,
                    ""
                )
            ).strip()

            if value:
                settings[
                    key
                ] = value
            else:
                settings.pop(
                    key,
                    None
                )

        saved = save_guild_setup(
            guild.get(
                "guild_id"
            ),
            request.form.get(
                "league_name",
                ""
            ),
            request.form.get(
                "snallabot_league_id",
                ""
            ),
            request.form.get(
                "platform",
                "xbsx"
            ),
            settings
        )

        guild = get_guild_config_by_token(
            setup_token
        )

    return render_template_string(
        PROJECT_MADDEN_SETUP_HTML,
        guild=guild,
        settings=(
            guild.get(
                "settings",
                {}
            )
            if guild
            else {}
        ),
        saved=saved,
        base_url=PROJECT_MADDEN_BASE_URL,
        platforms=[
            (
                "xbsx",
                "Xbox Series X|S"
            ),
            (
                "ps5",
                "PlayStation 5"
            ),
            (
                "pc",
                "PC"
            )
        ]
    )


@app.route(
    "/dashboard/api/servers"
)
def project_madden_servers_api():
    guilds = list_guild_configs()

    safe_guilds = []

    for guild in guilds:
        safe_guilds.append({
            "guild_id":
                guild.get(
                    "guild_id"
                ),
            "guild_name":
                guild.get(
                    "guild_name"
                ),
            "league_name":
                guild.get(
                    "league_name"
                ),
            "snallabot_league_id":
                guild.get(
                    "snallabot_league_id"
                ),
            "platform":
                guild.get(
                    "platform"
                ),
            "connected":
                bool(
                    guild.get(
                        "snallabot_league_id"
                    )
                )
        })

    return jsonify({
        "app_version":
            PROJECT_MADDEN_APP_VERSION,
        "current_official_data_source":
            PROJECT_MADDEN_DATA_SOURCE,
        "snallabot_required":
            True,
        "direct_ea_connector_status":
            DIRECT_EA_CONNECTOR_STATUS,
        "server_count":
            len(
                safe_guilds
            ),
        "servers":
            safe_guilds
    })



@app.route(
    "/dashboard/setup-link-preview/<guild_id>"
)
def project_madden_setup_link_preview(
    guild_id
):
    url = setup_start_url(
        guild_id
    )

    return jsonify({
        "success": bool(
            url.startswith(
                "https://"
            )
        ),
        "base_url": PROJECT_MADDEN_BASE_URL,
        "guild_id": str(
            guild_id
        ),
        "url_scheme": (
            "https"
            if url.startswith(
                "https://"
            )
            else "invalid"
        ),
        "url_path_present": (
            "/dashboard/setup/start/"
            in url
        ),
        "message": (
            "Signed setup URL generated correctly."
            if url.startswith(
                "https://"
            )
            else "Setup URL is invalid. Check PROJECT_MADDEN_BASE_URL."
        )
    })


@app.route(
    "/dashboard/setup-health"
)
def project_madden_setup_health():
    return jsonify({
        "official_madden_data_source":
            PROJECT_MADDEN_DATA_SOURCE,
        "snallabot_required":
            True,
        "direct_ea_connector":
            PROJECT_MADDEN_DIRECT_EA_STATUS,
        "direct_ea_enabled":
            False,
        "discord_required_permissions_integer":
            discord_required_permissions(),
        "dashboard_setup_version":
            "v3-auto-detect",
        "discord_channel_auto_detect":
            True,
        "discord_channel_creator":
            True,
        "snallabot_league_id_auto_detect":
            False,
        "app_version":
            PROJECT_MADDEN_APP_VERSION,
        "database_url_configured":
            bool(
                DATABASE_URL
            ),
        "psycopg_available":
            psycopg is not None,
        "multi_server_database_ready":
            ensure_multi_server_db(),
        "guild_storage_backend":
            "persistent_json",
        "guild_storage_file":
            GUILD_CONFIG_FILE,
        "discord_application_id_configured":
            bool(
                discord_application_id()
            ),
        "discord_bot_token_configured":
            bool(
                discord_bot_token()
            ),
        "discord_public_key_configured":
            bool(
                discord_public_key()
            ),
        "current_official_data_source":
            PROJECT_MADDEN_DATA_SOURCE,
        "snallabot_required":
            True,
        "direct_ea_connector_status":
            DIRECT_EA_CONNECTOR_STATUS,
        "setup_link_secret_configured":
            bool(
                os.environ.get(
                    "PROJECT_MADDEN_SETUP_SECRET",
                    ""
                ).strip()
            ),
        "setup_command":
            "instant-signed-link-v24"
    })




@app.route(
    "/data-source/status"
)
def project_madden_data_source_status_route():
    return jsonify(
        project_madden_data_source_status()
    )


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

    try:
        trade_history_upsert(
            analysis
        )
    except Exception as e:
        print(
            "TRADE HISTORY ERROR:",
            str(e)
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



@app.route(
    "/weekly-show/health/<season_type>/<int:week_number>"
)
def weekly_show_healthcheck(
    season_type,
    week_number
):
    checks = {
        "math_imported": hasattr(math, "exp"),
        "summary_build": False,
        "panel_takes": False,
        "panel_debate": False,
        "analyst_receipts": False,
        "gotw_poll_system": False,
        "permanent_storage": False,
        "analyst_accuracy": False,
        "rivalry_tracker": False,
        "playoff_race": False,
        "fraud_watch": False,
        "dark_horse_watch": False,
        "hot_seat": False,
        "super_bowl_favorites": False,
        "josh_pate_segment": False,
    }

    errors = {}

    try:
        show = build_weekly_show_summary(
            season_type,
            week_number
        )

        checks["summary_build"] = True
        checks["panel_takes"] = (
            isinstance(
                show.get("panel_takes"),
                dict
            )
            and bool(
                show.get("panel_takes")
            )
        )

        checks["panel_debate"] = (
            isinstance(
                show.get("panel_debate"),
                dict
            )
            and bool(
                show.get("panel_debate")
            )
        )

        checks["analyst_receipts"] = (
            isinstance(
                show.get("analyst_receipts"),
                list
            )
            and len(
                show.get(
                    "analyst_receipts",
                    []
                )
            ) == 4
        )

        checks["gotw_poll_system"] = (
            gotw_poll_configured()
        )

        checks["permanent_storage"] = (
            persistent_storage_status().get(
                "database_ready",
                False
            )
        )

        checks["playoff_race"] = (
            isinstance(
                show.get(
                    "playoff_race"
                ),
                dict
            )
        )

        checks["rivalry_tracker"] = (
            isinstance(
                show.get(
                    "rivalry_spotlight"
                ),
                list
            )
        )

        checks["analyst_accuracy"] = (
            isinstance(
                show.get(
                    "analyst_accuracy"
                ),
                dict
            )
            and len(
                show.get(
                    "analyst_accuracy",
                    {}
                )
            ) == 4
        )
        checks["fraud_watch"] = (
            "fraud_watch" in show
        )
        checks["dark_horse_watch"] = (
            "dark_horse_watch" in show
        )
        checks["hot_seat"] = (
            "hot_seat" in show
        )
        checks["super_bowl_favorites"] = (
            "super_bowl_favorites" in show
        )
        checks["josh_pate_segment"] = (
            "josh_pate_parody_segment" in show
        )

    except Exception as e:
        errors["summary_build"] = str(e)

    return jsonify({
        "ok":
            all(checks.values())
            and not errors,
        "checks":
            checks,
        "errors":
            errors
    })



if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
