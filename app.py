from flask import Flask, request, jsonify
from datetime import datetime
import json

app = Flask(__name__)

def handle_export(platform=None, league_id=None, data_type=None):
    print("\n==============================")
    print("MADDEN EXPORT RECEIVED")
    print("Time:", datetime.utcnow().isoformat())
    print("Method:", request.method)
    print("Platform:", platform)
    print("League ID:", league_id)
    print("Data Type:", data_type)
    print("Content-Type:", request.content_type)

    raw_data = request.get_data(as_text=True)

    print("RAW BODY:")
    print(raw_data)

    if request.is_json:
        print("JSON:")
        print(json.dumps(request.get_json(silent=True), indent=2))

    print("==============================\n")

    return jsonify({
        "success": True,
        "platform": platform,
        "league_id": league_id,
        "data_type": data_type,
        "message": "Project Madden received the export"
    }), 200


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "GET":
        return "Project Madden endpoint is online."

    return handle_export()


@app.route("/snallabot", methods=["GET", "POST"])
def snallabot():
    if request.method == "GET":
        return "Snallabot endpoint is online."

    return handle_export()


@app.route(
    "/snallabot/<platform>/<league_id>/<data_type>",
    methods=["POST", "PUT"]
)
def snallabot_export(platform, league_id, data_type):
    return handle_export(platform, league_id, data_type)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
