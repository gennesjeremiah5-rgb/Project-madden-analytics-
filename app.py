from flask import Flask, request, jsonify
from datetime import datetime
import json

app = Flask(__name__)

def handle_export(path=""):
    print("\n==============================")
    print("MADDEN EXPORT RECEIVED")
    print("Time:", datetime.utcnow().isoformat())
    print("Method:", request.method)
    print("Path:", path)
    print("Full URL:", request.url)
    print("Content-Type:", request.content_type)

    raw_data = request.get_data(as_text=True)

    print("RAW BODY:")
    print(raw_data)

    if request.is_json:
        try:
            data = request.get_json()
            print("JSON DATA:")
            print(json.dumps(data, indent=2))
        except Exception as e:
            print("JSON ERROR:", e)

    print("==============================\n")

    return jsonify({
        "success": True,
        "received": True,
        "path": path
    }), 200


@app.route("/", methods=["GET"])
def home():
    return "Project Madden endpoint is online."


@app.route("/snallabot", methods=["GET", "POST", "PUT"])
def snallabot_root():
    if request.method == "GET":
        return "Snallabot receiver is online."

    return handle_export("")


@app.route("/snallabot/<path:subpath>", methods=["GET", "POST", "PUT"])
def snallabot_catch_all(subpath):
    return handle_export(subpath)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
