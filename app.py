from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Project Madden endpoint is online."

@app.route("/snallabot", methods=["POST"])
def snallabot():
    data = request.get_json(silent=True)

    print("=== SNALLABOT EXPORT RECEIVED ===")
    print("Time:", datetime.utcnow().isoformat())
    print(data)

    return jsonify({
        "success": True,
        "message": "Project Madden received the export"
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
