import requests
import os


# =========================================
# CONFIG
# =========================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

GROQ_URL = (
    "https://api.groq.com/openai/v1/chat/completions"
)


# =========================================
# ASK GROQ
# =========================================

def ask_groq(message):

    headers = {

        "Authorization":
        f"Bearer {GROQ_API_KEY}",

        "Content-Type":
        "application/json"
    }

    system_prompt = """
You are a smart AI assistant.

RULES:
- Keep replies short
- Human-like replies
- Friendly but concise
- No robotic responses
- No signatures
- No greetings unless necessary
- Avoid long explanations
- Reply naturally like a real person
"""

    payload = {

        "model":
        "llama-3.1-8b-instant",

        "messages": [

            {
                "role": "system",
                "content": system_prompt
            },

            {
                "role": "user",
                "content": message
            }
        ],

        "temperature": 0.5
    }

    response = requests.post(

        GROQ_URL,

        headers=headers,

        json=payload
    )

    data = response.json()

    print("\n======================")
    print("🧠 GROQ RESPONSE")
    print("======================")
    print(data)

    if "choices" not in data:

        return (
            "Temporary AI service issue."
        )

    return (
        data["choices"][0]
        ["message"]["content"]
        .strip()
    )


# =========================================
# MAIN DECISION ENGINE
# =========================================

def decide(data):

    try:

        platform = data.get(
            "platform",
            "unknown"
        )

        user_id = data.get(
            "user_id",
            ""
        )

        message = data.get(
            "message",
            ""
        )

        print("\n======================")
        print("📩 NEW MESSAGE")
        print("======================")

        print("Platform:", platform)
        print("User ID:", user_id)
        print("Message:", message)

        # =========================================
        # GENERATE AI REPLY
        # =========================================

        reply = ask_groq(message)

        return {

            "success": True,

            "platform": platform,

            "reply": reply,

            "summary_user":
            "User sent message",

            "summary_ai":
            "AI generated reply"
        }

    except Exception as e:

        print(
            "\n❌ DECISION ENGINE ERROR ❌"
        )

        print(str(e))

        return {

            "success": False,

            "reply":
            f"System error: {str(e)}",

            "summary_user":
            "System error",

            "summary_ai":
            "Reply generation failed"
        }