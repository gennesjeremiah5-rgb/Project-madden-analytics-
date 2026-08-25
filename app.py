from flask import Flask, request, jsonify
import json
import os

app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


@app.route("/")
def home():
    return "Project Madden Analytics is online."


@app.route("/snallabot/<path:subpath>", methods=["GET", "POST", "PUT"])
def snallabot_receiver(subpath):
    if request.method == "GET":
        return jsonify({
            "working": True,
            "path": subpath
        })

    data = request.get_json(silent=True)

    if data is None:
        return jsonify({
            "success": False,
            "error": "No JSON received"
        }), 400

    parts = subpath.split("/")

    print("SNALLABOT EXPORT RECEIVED")
    print("Path:", subpath)

    # Example:
    # xbsx/1360051/team/777781280/roster
    if "team" in parts and "roster" in parts:
        team_index = parts.index("team")
        team_id = parts[team_index + 1]

        filename = os.path.join(
            DATA_DIR,
            f"roster_{team_id}.json"
        )

        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

        player_count = len(data.get("rosterInfoList", []))

        print(
            f"Saved roster for team {team_id} "
            f"with {player_count} players"
        )

        return jsonify({
            "success": True,
            "type": "roster",
            "team_id": team_id,
            "players": player_count
        }), 200

    # Extra league data
    if "extra" in parts:
        filename = os.path.join(
            DATA_DIR,
            "extra.json"
        )

        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

        print("Saved extra league data")

        return jsonify({
            "success": True,
            "type": "extra"
        }), 200

    # Save anything else so we can inspect it later
    safe_name = subpath.replace("/", "_")

    filename = os.path.join(
        DATA_DIR,
        f"{safe_name}.json"
    )

    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

    print("Saved unknown export:", subpath)

    return jsonify({
        "success": True,
        "type": "unknown",
        "path": subpath
    }), 200


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
