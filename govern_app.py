from flask import Flask, request, jsonify
from services.scheduler import get_available_slots
from services.decision_engine import decide

from dotenv import load_dotenv

import os
import json

load_dotenv()

app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

print("VERIFY TOKEN LOADED:", VERIFY_TOKEN)


# =========================================
# GOVERN ENDPOINT
# =========================================

@app.route("/govern", methods=["POST"])
def govern():

    data = request.get_json(silent=True)

    if not data:

        return jsonify({

            "reply":
            "Sorry, I couldn't process that message.",

            "action": None,
            "data": None
        })

    decision = decide(data)

    return jsonify(decision)


# =========================================
# INSTAGRAM WEBHOOK VERIFICATION
# =========================================

@app.route("/instagram/webhook", methods=["GET"])
def verify_instagram_webhook():

    mode = request.args.get("hub.mode")

    token = request.args.get("hub.verify_token")

    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:

        print("\n✅ INSTAGRAM WEBHOOK VERIFIED")

        return challenge, 200

    return "Verification failed", 403


# =========================================
# INSTAGRAM WEBHOOK EVENTS
# =========================================

@app.route("/instagram/webhook", methods=["POST"])
def instagram_webhook():

    data = request.get_json()

    print("\n======================")
    print("📩 INSTAGRAM WEBHOOK EVENT")
    print("======================")

    print(data)

    return jsonify({
        "status": "received"
    })


# =========================================
# START SERVER
# =========================================

if __name__ == "__main__":

    app.run(
        port=5001,
        debug=True
    )