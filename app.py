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
    # WEEKLY STATS
    #
    if "week" in parts or "weekly" in parts:

        safe_path = subpath.replace("/", "_")

        save_json(
            f"weekly_{safe_path}.json",
            data
        )

        update_latest(
            safe_path,
            subpath,
            data
        )

        print("Saved: WEEKLY DATA")
        print("Path:", subpath)

        return jsonify({
            "success": True,
            "type": "weekly",
            "path": subpath
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

    if os.path.exists(DATA_DIR):
        files = os.listdir(DATA_DIR)

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
