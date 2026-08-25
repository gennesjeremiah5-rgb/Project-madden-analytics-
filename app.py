from flask import Flask, request, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

LATEST_FILE = os.path.join(DATA_DIR, "latest_exports.json")


def save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    return path


def update_latest(export_type, path, data):
    latest = {}

    if os.path.exists(LATEST_FILE):
        try:
            with open(LATEST_FILE, "r") as f:
                latest = json.load(f)
        except:
            latest = {}

    latest[export_type] = {
        "received_at": datetime.utcnow().isoformat(),
        "path": path,
        "data": data
    }

    with open(LATEST_FILE, "w") as f:
        json.dump(latest, f, indent=2)


@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "Project Madden Analytics"
    })


@app.route("/health")
def health():
    return jsonify({
        "online": True,
        "service": "Project Madden Analytics"
    })


@app.route("/snallabot/<path:subpath>", methods=["GET", "POST", "PUT"])
def snallabot_receiver(subpath):

    if request.method == "GET":
        return jsonify({
            "working": True,
            "path": subpath
        })

    data = request.get_json(silent=True)

    if data is None:
        raw = request.get_data(as_text=True)

        return jsonify({
            "success": False,
            "error": "No JSON received",
            "raw_length": len(raw)
        }), 400

    parts = subpath.split("/")

    print("")
    print("========================================")
    print("PROJECT MADDEN EXPORT")
    print("Path:", subpath)
    print("========================================")

    #
    # EXTRA DATA
    #
    if parts[-1] == "extra":

        save_json(
            "extra.json",
            data
        )

        update_latest(
            "extra",
            subpath,
            data
        )

        print("Saved: EXTRA DATA")

        return jsonify({
            "success": True,
            "type": "extra"
        }), 200

    #
    # TEAM ROSTER
    #
    if "team" in parts and parts[-1] == "roster":

        team_index = parts.index("team")

        if len(parts) > team_index + 1:
            team_id = parts[team_index + 1]
        else:
            team_id = "unknown"

        players = data.get("rosterInfoList", [])

        save_json(
            f"roster_{team_id}.json",
            data
        )

        update_latest(
            f"roster_{team_id}",
            subpath,
            data
        )

        print(
            f"Saved roster: Team {team_id} | "
            f"{len(players)} players"
        )

        return jsonify({
            "success": True,
            "type": "roster",
            "team_id": team_id,
            "player_count": len(players)
        }), 200

    #
    # WEEKLY DATA
    #
    if "week" in parts:

        try:
            week_index = parts.index("week")

            season_type = parts[week_index + 1]
            week_number = parts[week_index + 2]
            stat_type = parts[week_index + 3]

        except Exception:
            season_type = "unknown"
            week_number = "unknown"
            stat_type = "unknown"

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

        filename = os.path.join(
            weekly_dir,
            f"{stat_type}.json"
        )

        with open(filename, "w") as f:
            json.dump(
                data,
                f,
                indent=2
            )

        update_latest(
            f"{season_type}_week_{week_number}_{stat_type}",
            subpath,
            data
        )

        print(
            f"Saved weekly data | "
            f"{season_type.upper()} "
            f"Week {week_number} | "
            f"{stat_type}"
        )

        return jsonify({
            "success": True,
            "type": "weekly",
            "season_type": season_type,
            "week": week_number,
            "stat_type": stat_type
        }), 200

    #
    # LEAGUE INFO
    #
    if "league" in parts or parts[-1] in [
        "info",
        "leagueinfo",
        "standings"
    ]:

        save_json(
            "league_info.json",
            data
        )

        update_latest(
            "league_info",
            subpath,
            data
        )

        print("Saved: LEAGUE INFO")

        return jsonify({
            "success": True,
            "type": "league_info"
        }), 200

    #
    # UNKNOWN EXPORT
    #
    safe_path = subpath.replace("/", "_")

    save_json(
        f"unknown_{safe_path}.json",
        data
    )

    update_latest(
        f"unknown_{safe_path}",
        subpath,
        data
    )

    print("Saved unknown export:", subpath)

    return jsonify({
        "success": True,
        "type": "unknown",
        "path": subpath
    }), 200


@app.route("/analytics/status")
def analytics_status():

    files = []

    for root, dirs, filenames in os.walk(DATA_DIR):
        for filename in filenames:
            full_path = os.path.join(root, filename)
            files.append(full_path)

    return jsonify({
        "service": "Project Madden Analytics",
        "saved_files": files,
        "count": len(files)
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
