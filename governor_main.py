from flask import Flask, request, jsonify
from groq import Groq
import os
import json
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle

# =========================================
# LOAD ENV VARIABLES
# =========================================

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# =========================================
# DEBUG PRINTS
# =========================================

print("\n======================")
print("ENV VARIABLES LOADED")
print("======================")
print("GROQ API KEY:", GROQ_API_KEY[:25] if GROQ_API_KEY else "MISSING")
print("DATABASE URL:", DATABASE_URL[:40] if DATABASE_URL else "MISSING")

# =========================================
# FLASK APP
# =========================================

app = Flask(__name__)

# =========================================
# GROQ CLIENT
# =========================================

groq_client = Groq(api_key=GROQ_API_KEY)

# =========================================
# DATABASE CONNECTION
# =========================================

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn


# =========================================
# INITIALIZE DATABASE SCHEMA
# =========================================

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Conversations table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            platform VARCHAR(50),
            user_id VARCHAR(255),
            role VARCHAR(20),
            message TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Meetings table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id SERIAL PRIMARY KEY,
            platform VARCHAR(50),
            user_id VARCHAR(255),
            title VARCHAR(255),
            meeting_date VARCHAR(100),
            meeting_time VARCHAR(100),
            description TEXT,
            google_event_id VARCHAR(255),
            google_meet_link VARCHAR(500),
            status VARCHAR(50) DEFAULT 'scheduled',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Logs table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            event_type VARCHAR(100),
            platform VARCHAR(50),
            user_id VARCHAR(255),
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("\n✅ DATABASE SCHEMA INITIALIZED")


# =========================================
# DB HELPERS
# =========================================

def save_message(platform, user_id, role, message):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO conversations (platform, user_id, role, message) VALUES (%s, %s, %s, %s)",
            (platform, user_id, role, message)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("❌ DB save_message error:", str(e))


def get_conversation_history(platform, user_id, limit=10):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """SELECT role, message FROM conversations
               WHERE platform=%s AND user_id=%s
               ORDER BY timestamp DESC LIMIT %s""",
            (platform, user_id, limit)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        # Reverse so oldest is first
        return list(reversed(rows))
    except Exception as e:
        print("❌ DB get_history error:", str(e))
        return []


def save_meeting(platform, user_id, title, meeting_date, meeting_time, description, google_event_id="", google_meet_link=""):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO meetings
               (platform, user_id, title, meeting_date, meeting_time, description, google_event_id, google_meet_link)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (platform, user_id, title, meeting_date, meeting_time, description, google_event_id, google_meet_link)
        )
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Meeting saved to DB")
    except Exception as e:
        print("❌ DB save_meeting error:", str(e))


def save_log(event_type, platform, user_id, details):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO logs (event_type, platform, user_id, details) VALUES (%s, %s, %s, %s)",
            (event_type, platform, user_id, details)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("❌ DB save_log error:", str(e))


# =========================================
# GOOGLE CALENDAR
# =========================================

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    try:
        creds = None

        if GOOGLE_CREDENTIALS_JSON:
            creds_data = json.loads(GOOGLE_CREDENTIALS_JSON)
            creds = Credentials.from_authorized_user_info(creds_data, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                print("⚠️ Google Calendar: no valid credentials")
                return None

        service = build("calendar", "v3", credentials=creds)
        return service

    except Exception as e:
        print("❌ Google Calendar service error:", str(e))
        return None


def create_calendar_event(title, date_str, time_str, description=""):
    try:
        service = get_calendar_service()

        if not service:
            print("⚠️ Skipping Google Calendar — no credentials")
            return None, None

        # Parse date and time
        # Expects date_str like "2026-05-25" and time_str like "14:00"
        start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(hours=1)

        event = {
            "summary": title,
            "description": description,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": "Asia/Karachi"
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": "Asia/Karachi"
            },
            "conferenceData": {
                "createRequest": {
                    "requestId": f"fyp-{start_dt.timestamp()}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"}
                }
            }
        }

        created = service.events().insert(
            calendarId="primary",
            body=event,
            conferenceDataVersion=1
        ).execute()

        event_id = created.get("id", "")
        meet_link = created.get("hangoutLink", "")

        print(f"✅ Google Calendar event created: {event_id}")
        print(f"📅 Meet link: {meet_link}")

        return event_id, meet_link

    except Exception as e:
        print("❌ Google Calendar create event error:", str(e))
        return None, None


# =========================================
# CEO ASSISTANT SYSTEM PROMPT
# =========================================

CEO_SYSTEM_PROMPT = """You are an elite executive assistant AI managing communications for a busy CEO.

YOUR PERSONA:
- Professional, concise, and highly efficient
- Warm but not overly casual
- You speak on behalf of the CEO
- You manage scheduling, inquiries, and communications

YOUR CAPABILITIES:
- Answer questions about the CEO's business
- Schedule meetings when requested
- Handle professional inquiries intelligently
- Maintain context across the conversation

MEETING SCHEDULING:
- When someone requests a meeting, extract: title/purpose, preferred date, preferred time
- Always confirm the meeting details before finalizing
- If date/time is unclear, ask for clarification politely

RESPONSE STYLE:
- Keep replies concise (2-4 sentences max for simple replies)
- Always professional and representing the CEO's brand
- Never mention you are an AI unless directly asked

MEETING DETECTION:
- If the user wants to schedule, book, set up, or arrange a meeting/call/appointment,
  respond with a JSON block at the END of your message in this exact format:
  [MEETING_REQUEST: {"title": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "description": "..."}]
- Only include this block if you have enough info (title + date + time)
- If info is missing, ask for it naturally without the JSON block
"""


# =========================================
# INTENT DETECTION + AI REPLY
# =========================================

def process_with_ai(platform, user_id, user_message):

    # Get conversation history
    history = get_conversation_history(platform, user_id, limit=10)

    # Build messages array
    messages = [{"role": "system", "content": CEO_SYSTEM_PROMPT}]

    # Add history
    for row in history:
        messages.append({
            "role": row["role"],
            "content": row["message"]
        })

    # Add current message
    messages.append({
        "role": "user",
        "content": user_message
    })

    print("\n======================")
    print("🤖 CALLING GROQ AI")
    print("======================")
    print(f"History loaded: {len(history)} messages")

    # Call Groq
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=500,
        temperature=0.7
    )

    full_reply = response.choices[0].message.content.strip()

    print("RAW AI REPLY:", full_reply)

    # =========================================
    # DETECT MEETING REQUEST IN REPLY
    # =========================================

    meeting_data = None
    clean_reply = full_reply

    meeting_match = re.search(
        r'\[MEETING_REQUEST:\s*(\{.*?\})\]',
        full_reply,
        re.DOTALL
    )

    if meeting_match:
        try:
            meeting_data = json.loads(meeting_match.group(1))
            # Remove the JSON block from the reply
            clean_reply = full_reply[:meeting_match.start()].strip()
            print("\n📅 MEETING DETECTED:", meeting_data)
        except Exception as e:
            print("❌ Meeting JSON parse error:", str(e))

    return clean_reply, meeting_data


# =========================================
# HOME ROUTE
# =========================================

@app.route("/")
def home():
    return "Governor AI Running"


# =========================================
# MAIN PROCESS MESSAGE ENDPOINT
# =========================================

@app.route("/process_message", methods=["POST"])
def process_message():

    data = request.get_json()

    print("\n======================")
    print("📩 GOVERNOR RECEIVED")
    print("======================")
    print(data)

    platform = data.get("platform", "unknown")
    user_id = data.get("user_id", "unknown")
    user_message = data.get("message", "")

    if not user_message:
        return jsonify({"reply": "I didn't receive a message."})

    # Save user message to DB
    save_message(platform, user_id, "user", user_message)
    save_log("message_received", platform, user_id, user_message)

    try:
        # Get AI reply + detect meeting
        reply, meeting_data = process_with_ai(platform, user_id, user_message)

        # Save assistant reply to DB
        save_message(platform, user_id, "assistant", reply)
        save_log("reply_sent", platform, user_id, reply)

        # Handle meeting scheduling
        if meeting_data:
            title = meeting_data.get("title", "Meeting")
            date_str = meeting_data.get("date", "")
            time_str = meeting_data.get("time", "")
            description = meeting_data.get("description", "")

            # Create Google Calendar event
            event_id, meet_link = create_calendar_event(
                title, date_str, time_str, description
            )

            # Save meeting to DB
            save_meeting(
                platform, user_id,
                title, date_str, time_str,
                description,
                event_id or "",
                meet_link or ""
            )

            save_log("meeting_scheduled", platform, user_id,
                     f"{title} on {date_str} at {time_str}")

            # Add meet link to reply if available
            if meet_link:
                reply += f"\n\nGoogle Meet link: {meet_link}"

        print("\n✅ FINAL REPLY:", reply)

        return jsonify({"reply": reply})

    except Exception as e:

        print("\n❌ GOVERNOR PROCESSING ERROR:", str(e))
        save_log("error", platform, user_id, str(e))

        return jsonify({"reply": "I'm temporarily unavailable. Please try again shortly."})


# =========================================
# DASHBOARD API ENDPOINTS
# =========================================

@app.route("/dashboard/conversations", methods=["GET"])
def get_conversations():
    try:
        platform = request.args.get("platform")
        user_id = request.args.get("user_id")
        limit = int(request.args.get("limit", 50))

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        query = "SELECT * FROM conversations WHERE 1=1"
        params = []

        if platform:
            query += " AND platform=%s"
            params.append(platform)

        if user_id:
            query += " AND user_id=%s"
            params.append(user_id)

        query += " ORDER BY timestamp DESC LIMIT %s"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify({
            "conversations": [dict(r) for r in rows],
            "count": len(rows)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard/meetings", methods=["GET"])
def get_meetings():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM meetings ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify({
            "meetings": [dict(r) for r in rows],
            "count": len(rows)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard/logs", methods=["GET"])
def get_logs():
    try:
        limit = int(request.args.get("limit", 100))

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM logs ORDER BY timestamp DESC LIMIT %s",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify({
            "logs": [dict(r) for r in rows],
            "count": len(rows)
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard/stats", methods=["GET"])
def get_stats():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("SELECT COUNT(*) as total FROM conversations WHERE role='user'")
        total_messages = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) as total FROM meetings")
        total_meetings = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(DISTINCT user_id) as total FROM conversations")
        total_users = cur.fetchone()["total"]

        cur.execute(
            "SELECT COUNT(*) as total FROM meetings WHERE status='scheduled'"
        )
        upcoming_meetings = cur.fetchone()["total"]

        cur.close()
        conn.close()

        return jsonify({
            "total_messages": total_messages,
            "total_meetings": total_meetings,
            "total_users": total_users,
            "upcoming_meetings": upcoming_meetings
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================================
# START SERVER
# =========================================

# Initialize DB on startup
with app.app_context():
    try:
        init_db()
    except Exception as e:
        print("❌ DB init error:", str(e))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)