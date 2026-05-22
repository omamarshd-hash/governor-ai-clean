import requests
import re
import os

from groq import Groq

from datetime import datetime
from datetime import timedelta

from zoneinfo import ZoneInfo

from services.calendar_service import (
    create_meeting,
    get_calendar_service,
    is_time_available
)


# =========================================
# CONFIG
# =========================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

GROQ_URL = (
    "https://api.groq.com/openai/v1/chat/completions"
)


# =========================================
# AI ENABLE CHECK
# =========================================

def is_ai_enabled(user_id):

    return True


# =========================================
# EXTRACT MEETING TIME
# =========================================

def extract_meeting_time(message):

    text = message.lower()

    weekdays = {

        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6
    }

    found_day = None

    for day in weekdays:

        if day in text:

            found_day = day

            break

    time_match = re.search(
        r'(\d{1,2})\s*(am|pm)',
        text
    )

    if not found_day or not time_match:

        return None

    hour = int(
        time_match.group(1)
    )

    am_pm = time_match.group(2)

    # =========================================
    # CONVERT TO 24-HOUR FORMAT
    # =========================================

    if am_pm == "pm" and hour != 12:

        hour += 12

    if am_pm == "am" and hour == 12:

        hour = 0

    # =========================================
    # PAKISTAN TIMEZONE
    # =========================================

    pakistan_tz = ZoneInfo(
        "Asia/Karachi"
    )

    today = datetime.now(
        pakistan_tz
    )

    target_day = weekdays[
        found_day
    ]

    days_ahead = (
        target_day - today.weekday()
    )

    if days_ahead <= 0:

        days_ahead += 7

    # =========================================
    # CREATE FUTURE DATE
    # =========================================

    future_date = (
        today + timedelta(days=days_ahead)
    )

    # =========================================
    # SET EXACT MEETING TIME
    # =========================================

    meeting_date = future_date.replace(

        hour=hour,

        minute=0,

        second=0,

        microsecond=0
    )

    # =========================================
    # FORCE TIMEZONE
    # =========================================

    meeting_date = meeting_date.astimezone(
        pakistan_tz
    )

    print("\n📅 FINAL PARSED TIME:")
    print(meeting_date)

    return meeting_date


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
You are a professional executive assistant.

RULES:
- Keep replies concise
- Professional tone
- No greetings
- No signatures
- Never write 'Best regards'
- Never write 'Sincerely'
- Never sign the email
- Never use placeholders
- Never write [Your Name]
- Never invent addresses
- Never invent phone numbers
- Never invent meeting locations
- Never invent unavailable information
- Never create fictional details
- Only respond using information provided in the email thread
- Only generate the email body
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

        "temperature": 0.3
    }

    response = requests.post(

        GROQ_URL,

        headers=headers,

        json=payload
    )

    data = response.json()

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

        user_id = data.get(
            "user_id",
            ""
        )

        message = data.get(
            "message",
            ""
        )

        # =========================================
        # LATEST MESSAGE ONLY
        # =========================================

        latest_message = message.split(
            "On "
        )[0]

        lower_message = (
            latest_message.lower()
        )

        # =========================================
        # FULL THREAD
        # =========================================

        full_thread = message.lower()

        print("\n======================")
        print("📨 NEW EMAIL")
        print("======================")

        print(latest_message)

        # =========================================
        # MEETING DETECTION
        # =========================================

        meeting_keywords = [

            "meeting",
            "schedule",
            "call",
            "conference"
        ]

        is_meeting_email = any(

            word in lower_message

            for word in meeting_keywords
        )

        # =========================================
        # CONFIRMATION DETECTION
        # =========================================

        confirmation_keywords = [

            "please go ahead",

            "go ahead and schedule",

            "schedule the meeting",

            "please schedule the meeting",

            "book the meeting",

            "yes schedule it",

            "please proceed with scheduling"

            "please confirm"

            "confirmed"

            "yes schedule it"
        ]

        is_confirmation = any(

            word in lower_message

            for word in confirmation_keywords
        )

        print(
            "\n📅 MEETING EMAIL:",
            is_meeting_email
        )

        print(
            "✅ CONFIRMATION:",
            is_confirmation
        )

        # =========================================
        # STEP 1:
        # CHECK CALENDAR BEFORE CONFIRMATION
        # =========================================

        if is_meeting_email and not is_confirmation:

            lines = latest_message.split(
                "\n"
            )

            meeting_time = None

            # =========================================
            # USE LATEST VALID TIME
            # =========================================

            for line in lines:

                extracted = extract_meeting_time(
                    line
                )

                if extracted:

                    meeting_time = extracted

            print("\n📅 EXTRACTED TIME:")
            print(meeting_time)

            if not meeting_time:

                return {

                    "mode": "ai",

                    "reply": (
                        "I could not determine "
                        "the requested meeting time."
                    )
                }

            formatted_time = meeting_time.strftime(
                "%A at %I:%M %p"
            )

            # =========================================
            # CHECK CALENDAR
            # =========================================

            service = get_calendar_service()

            end_time = (
                meeting_time
                + timedelta(minutes=30)
            )

            available = is_time_available(

                service,

                meeting_time,

                end_time
            )

            # =========================================
            # SLOT OCCUPIED
            # =========================================

            if not available:

                return {

                    "mode": "ai",

                    "reply": (

                        f"{formatted_time} is already occupied. "

                        f"Please suggest another available slot."
                    )
                }

            # =========================================
            # SLOT AVAILABLE
            # =========================================

            return {

                "mode": "ai",

                "reply": (

                    f"{formatted_time} works for me. "

                    f"Please confirm if you "

                    f"would like me to "

                    f"schedule the meeting."
                )
            }

        # =========================================
        # STEP 2:
        # CREATE MEETING
        # =========================================

        if is_confirmation:

            lines = full_thread.split(
                "\n"
            )

            meeting_time = None

            # =========================================
            # USE LATEST VALID TIME
            # =========================================

            for line in lines:

                extracted = extract_meeting_time(
                    line
                )

                if extracted:

                    meeting_time = extracted

            print("\n📅 FINAL EXTRACTED TIME:")
            print(meeting_time)

            if not meeting_time:

                return {

                    "mode": "ai",

                    "reply": (
                        "I could not determine "
                        "the requested meeting time."
                    )
                }

            formatted_time = meeting_time.strftime(
                "%A at %I:%M %p"
            )

            print("\n🚀 CREATING REAL MEETING...")

            meeting_result = create_meeting(

                summary="AI Scheduled Meeting",

                start_time=meeting_time,

                attendee_email=user_id
            )

            print("\n📌 MEETING RESULT:")
            print(meeting_result)

            # =========================================
            # SUCCESS
            # =========================================

            if meeting_result["success"]:

                meet_link = meeting_result.get(
                    "meet_link"
                )

                return {

                    "mode": "ai",

                    "reply": (

                        f"The meeting has been "

                        f"scheduled successfully "

                        f"for {formatted_time}.\n\n"

                        f"Google Meet Link:\n"

                        f"{meet_link}"
                    )
                }

            # =========================================
            # SLOT OCCUPIED
            # =========================================

            return {

                "mode": "ai",

                "reply": (

                    f"{formatted_time} is already occupied. "

                    f"Please suggest another available slot."
                )
            }

        # =========================================
        # NORMAL AI RESPONSE
        # =========================================

        reply = ask_groq(
            latest_message
        )

        return {

            "mode": "ai",

            "reply": reply
        }

    except Exception as e:

        print(
            "\n❌ DECISION ENGINE ERROR ❌"
        )

        print(str(e))

        return {

            "mode": "ai",

            "reply":
            f"System error: {str(e)}"
        }