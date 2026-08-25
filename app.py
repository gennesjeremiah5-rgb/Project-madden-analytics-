from flask import Flask, request, jsonify
from datetime import datetime
import json

app = Flask(__name__)

def handle_export():
    print("\n==============================")
    print("MADDEN EXPORT RECEIVED")
    print("Time:", datetime.utcnow().isoformat())
    print("Method:", request.method)
    print("Content-Type:", request.content_type)

    raw_data = request.get_data(as_text=True)

    print("RAW BODY:")
    print(raw_data)

    if request.is_json:
        print("JSON:")
        print(json.dumps(request.get_json(silent=True), indent=2))

    if request.form:
        print("FORM DATA:")
        print(request.form.to_dict())

    print("==============================\n")

    return jsonify({
        "success": True,
        "message": "Project Madden received the export"
    }), 200


@app.route("/", methods=["GET", "POST", "PUT"])
def home():
    if request.method == "GET":
        return "Project Madden endpoint is online."

    return handle_export()


@app.route("/snallabot", methods=["GET", "POST", "PUT"])
def snallabot():
    if request.method == "GET":
        return "Snallabot endpoint is online."

    return handle_export()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
