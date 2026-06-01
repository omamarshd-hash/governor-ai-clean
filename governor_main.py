from flask import Flask, request, jsonify
from groq import Groq
import os
import json
import re
import threading
import time
import jwt
import bcrypt
from datetime import datetime, timedelta
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from flask_cors import CORS
import requests
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
CORS(app, origins="*", allow_headers=["Content-Type", "Authorization"], methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

JWT_SECRET = os.getenv("JWT_SECRET", "fyp-vertex-ai-secret-2026")

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

    # Users table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255),
            email VARCHAR(255) UNIQUE,
            password VARCHAR(255),
            business_name VARCHAR(255),
            industry VARCHAR(255),
            website VARCHAR(500),
            onboarding_complete BOOLEAN DEFAULT FALSE,
            meta_verified BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add missing columns to users if they don't exist
    for col, typedef in [
        ("industry", "VARCHAR(255)"),
        ("website", "VARCHAR(500)"),
        ("onboarding_complete", "BOOLEAN DEFAULT FALSE"),
        ("meta_verified", "BOOLEAN DEFAULT FALSE"),
        ("meta_verification_status", "VARCHAR(50) DEFAULT 'not_started'"),
        ("meta_verification_submitted_at", "TIMESTAMP"),
    ]:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {typedef}")
        except:
            pass

    # Connected platforms table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS connected_platforms (
            id SERIAL PRIMARY KEY,
            ceo_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            platform VARCHAR(50),
            account_id VARCHAR(255),
            account_name VARCHAR(255),
            access_token TEXT,
            page_id VARCHAR(255),
            phone_number_id VARCHAR(255),
            status VARCHAR(50) DEFAULT 'active',
            is_verified BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ceo_id, platform)
        )
    """)

    # Test accounts table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS test_accounts (
            id SERIAL PRIMARY KEY,
            ceo_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            platform VARCHAR(50),
            account_id VARCHAR(255),
            account_name VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ceo_id, platform, account_id)
        )
    """)

    # Conversations table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            ceo_id INTEGER,
            platform VARCHAR(50),
            user_id VARCHAR(255),
            role VARCHAR(20),
            message TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add ceo_id to conversations if missing
    try:
        cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS ceo_id INTEGER")
    except:
        pass

    # Meetings table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id SERIAL PRIMARY KEY,
            ceo_id INTEGER,
            platform VARCHAR(50),
            user_id VARCHAR(255),
            title VARCHAR(255),
            meeting_date VARCHAR(100),
            meeting_time VARCHAR(100),
            description TEXT,
            google_event_id VARCHAR(255),
            google_meet_link VARCHAR(500),
            user_email VARCHAR(255),
            status VARCHAR(50) DEFAULT 'scheduled',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add ceo_id to meetings if missing
    try:
        cur.execute("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS ceo_id INTEGER")
    except:
        pass

    # User profiles
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            id SERIAL PRIMARY KEY,
            ceo_id INTEGER,
            platform VARCHAR(50),
            user_id VARCHAR(255),
            email VARCHAR(255),
            name VARCHAR(255),
            UNIQUE(platform, user_id)
        )
    """)

    try:
        cur.execute("ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS ceo_id INTEGER")
    except:
        pass

    # Pending meetings
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_meetings (
            id SERIAL PRIMARY KEY,
            ceo_id INTEGER,
            platform VARCHAR(50),
            user_id VARCHAR(255),
            title VARCHAR(255),
            meeting_date VARCHAR(100),
            meeting_time VARCHAR(100),
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        cur.execute("ALTER TABLE pending_meetings ADD COLUMN IF NOT EXISTS ceo_id INTEGER")
    except:
        pass

    # Logs table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            ceo_id INTEGER,
            event_type VARCHAR(100),
            platform VARCHAR(50),
            user_id VARCHAR(255),
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        cur.execute("ALTER TABLE logs ADD COLUMN IF NOT EXISTS ceo_id INTEGER")
    except:
        pass

    conn.commit()
    cur.close()
    conn.close()
    print("\n✅ DATABASE SCHEMA INITIALIZED")


# =========================================
# DB HELPERS
# =========================================

def save_message(platform, user_id, role, message, ceo_id=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO conversations (platform, user_id, role, message, ceo_id) VALUES (%s, %s, %s, %s, %s)",
            (platform, user_id, role, message, ceo_id)
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


def cancel_meeting_in_db(platform, user_id, date_str):
    """Cancel a meeting by date and return the google_event_id if found"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """SELECT id, google_event_id FROM meetings
               WHERE platform=%s AND user_id=%s AND meeting_date=%s AND status='scheduled'
               ORDER BY created_at DESC LIMIT 1""",
            (platform, user_id, date_str)
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE meetings SET status='cancelled' WHERE id=%s",
                (row["id"],)
            )
            conn.commit()
            cur.close()
            conn.close()
            print(f"✅ Meeting cancelled in DB: id={row['id']}")
            return row["google_event_id"]
        cur.close()
        conn.close()
        return None
    except Exception as e:
        print("❌ DB cancel_meeting error:", str(e))
        return None


def get_upcoming_meetings(platform, user_id):
    """Get all scheduled meetings for a user"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """SELECT title, meeting_date, meeting_time FROM meetings
               WHERE platform=%s AND user_id=%s AND status='scheduled'
               ORDER BY meeting_date ASC""",
            (platform, user_id)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print("❌ DB get_upcoming_meetings error:", str(e))
        return []


def save_user_email(platform, user_id, email):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_profiles (platform, user_id, email)
            VALUES (%s, %s, %s)
            ON CONFLICT (platform, user_id) DO UPDATE SET email=%s
        """, (platform, user_id, email, email))
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ User email saved: {email}")
    except Exception as e:
        print("❌ DB save_user_email error:", str(e))


def get_user_email(platform, user_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT email FROM user_profiles WHERE platform=%s AND user_id=%s",
            (platform, user_id)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row["email"] if row else None
    except Exception as e:
        print("❌ DB get_user_email error:", str(e))
        return None


def save_pending_meeting(platform, user_id, title, date, time, description):
    try:
        conn = get_db()
        cur = conn.cursor()
        # Remove any existing pending meeting for this user
        cur.execute(
            "DELETE FROM pending_meetings WHERE platform=%s AND user_id=%s",
            (platform, user_id)
        )
        cur.execute("""
            INSERT INTO pending_meetings (platform, user_id, title, meeting_date, meeting_time, description)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (platform, user_id, title, date, time, description))
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Pending meeting saved")
    except Exception as e:
        print("❌ DB save_pending_meeting error:", str(e))


def get_pending_meeting(platform, user_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM pending_meetings WHERE platform=%s AND user_id=%s ORDER BY created_at DESC LIMIT 1",
            (platform, user_id)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print("❌ DB get_pending_meeting error:", str(e))
        return None


def delete_pending_meeting(platform, user_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM pending_meetings WHERE platform=%s AND user_id=%s",
            (platform, user_id)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("❌ DB delete_pending_meeting error:", str(e))


def get_ceo_for_platform(platform, account_id):
    """Look up which CEO owns a platform account"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT ceo_id FROM connected_platforms
            WHERE platform=%s AND (account_id=%s OR page_id=%s) AND status='active'
            LIMIT 1
        """, (platform, account_id, account_id))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return row["ceo_id"]
        # Fall back to first user
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row["id"] if row else None
    except Exception as e:
        print("❌ get_ceo_for_platform error:", str(e))
        return None


def save_log(event_type, platform, user_id, details, ceo_id=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO logs (event_type, platform, user_id, details, ceo_id) VALUES (%s, %s, %s, %s, %s)",
            (event_type, platform, user_id, details, ceo_id)
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


def send_email_notification(to_email, subject, body):
    """Send email notification via Gmail API"""
    try:
        service = get_gmail_service_for_email()
        if not service:
            print("⚠️ Email notification skipped — no Gmail service")
            return False

        from email.mime.text import MIMEText
        import base64

        message = MIMEText(body)
        message["to"] = to_email
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode()

        service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        print(f"✅ Email notification sent to {to_email}")
        return True

    except Exception as e:
        print(f"❌ Email notification error: {str(e)}")
        return False


def get_gmail_service_for_email():
    """Get Gmail service using same credentials"""
    try:
        creds_data = json.loads(GOOGLE_CREDENTIALS_JSON)
        scopes = [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events"
        ]
        creds = Credentials.from_authorized_user_info(creds_data, scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        print("❌ Gmail service error:", str(e))
        return None


def cancel_google_event(event_id):
    """Delete a Google Calendar event by ID"""
    try:
        if not event_id:
            return
        service = get_calendar_service()
        if not service:
            return
        service.events().delete(
            calendarId="primary",
            eventId=event_id
        ).execute()
        print(f"✅ Google Calendar event deleted: {event_id}")
    except Exception as e:
        print("❌ Google Calendar delete error:", str(e))


def check_calendar_availability(date_str, time_str):
    """Returns True if slot is free, False if busy"""
    try:
        service = get_calendar_service()
        if not service:
            return True  # assume free if no calendar access

        start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(hours=1)

        events_result = service.events().list(
            calendarId="primary",
            timeMin=start_dt.isoformat() + "+05:00",
            timeMax=end_dt.isoformat() + "+05:00",
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])

        if events:
            print(f"⚠️ Slot busy — {len(events)} event(s) found at {date_str} {time_str}")
            return False

        print(f"✅ Slot is free at {date_str} {time_str}")
        return True

    except Exception as e:
        print("❌ Availability check error:", str(e))
        return True  # assume free on error


def create_calendar_event(title, date_str, time_str, description="", attendee_email=None):
    try:
        service = get_calendar_service()
        if not service:
            print("⚠️ Skipping Google Calendar — no credentials")
            return None, None

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

        # Add attendee if email provided
        if attendee_email:
            event["attendees"] = [{"email": attendee_email}]
            event["guestsCanSeeOtherGuests"] = False

        created = service.events().insert(
            calendarId="primary",
            body=event,
            conferenceDataVersion=1,
            sendUpdates="all" if attendee_email else "none"
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

def get_system_prompt(business_context=""):
    today = datetime.now()
    today_str = today.strftime("%A, %B %d, %Y")
    tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    current_year = today.strftime("%Y")

    return f"""You are an elite AI executive assistant managing all social media communications on behalf of a busy CEO.

TODAY'S DATE: {today_str}
CURRENT YEAR: {current_year}
TOMORROW: {tomorrow_str}

{f"BUSINESS CONTEXT:{business_context}" if business_context else ""}

YOUR PERSONA:
- Professional, warm, and highly efficient
- You represent the CEO with authority and grace
- You speak in first person on behalf of the CEO ("The CEO will..." or "We would be happy to...")
- You never mention you are an AI unless directly asked
- You keep replies concise — 2-4 sentences for simple queries, more only when needed

YOUR CAPABILITIES:
1. GENERAL INQUIRIES — Answer questions about the business, services, pricing, partnerships
2. MEETING SCHEDULING — Book meetings with proper date/time handling
3. MEETING CANCELLATION — Cancel existing meetings when requested
4. TASK NOTING — Acknowledge requests, complaints, or tasks professionally
5. COMPLAINTS — Handle with empathy, escalate professionally
6. FOLLOW-UPS — Acknowledge and confirm next steps clearly

RESPONSE GUIDELINES:
- Match the tone of the person messaging (formal if they're formal, friendly if casual)
- For complaints: empathize first, then offer a resolution path
- For inquiries you can't answer: say the CEO will follow up personally
- For tasks/requests: confirm you've noted it and will action it
- Always end interactions positively and professionally
- End every reply with exactly: "Best regards,\nExecutive Assistant" — no placeholders, no variations
- NEVER use "[CEO's Representative]" or any placeholder text in signatures

STRICT MEETING RULES:
- Only add [MEETING_REQUEST:...] when the user explicitly asks to SCHEDULE/BOOK a meeting
- Only add [CANCEL_MEETING:...] when the user explicitly asks to CANCEL a meeting
- NEVER add these blocks for thank you messages, greetings, complaints, or general questions
- A "thank you" or "ok" or "sure" is NEVER a meeting request or cancellation

MEETING SCHEDULING — only when explicitly requested:
[MEETING_REQUEST: {{"title": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "description": "..."}}]
- Only include when you have title + date + time
- Always use YYYY-MM-DD format, never relative terms
- Ask for missing details naturally if needed

MEETING CANCELLATION — only when explicitly requested:
[CANCEL_MEETING: {{"date": "YYYY-MM-DD"}}]
- Only include when cancellation is clearly and explicitly requested
- Convert relative dates (Sunday, Monday, tomorrow) to YYYY-MM-DD
- Keep reply short: confirm the cancellation simply
"""


# =========================================
# INTENT DETECTION + AI REPLY
# =========================================

def process_with_ai(platform, user_id, user_message, business_context=""):

    # Get conversation history
    history = get_conversation_history(platform, user_id, limit=10)

    # Get upcoming meetings for context
    upcoming = get_upcoming_meetings(platform, user_id)
    meetings_context = ""
    if upcoming:
        meetings_list = "\n".join([
            f"- {m['title']} on {m['meeting_date']} at {m['meeting_time']}"
            for m in upcoming
        ])
        meetings_context = f"\n\nSCHEDULED MEETINGS FOR THIS USER:\n{meetings_list}"

    # Build system prompt with business context + meetings
    system_prompt = get_system_prompt(business_context) + meetings_context
    messages = [{"role": "system", "content": system_prompt}]

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
    print(f"Upcoming meetings: {len(upcoming)}")

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
    cancel_data = None
    clean_reply = full_reply

    # Check for cancellation
    cancel_match = re.search(
        r'\[CANCEL_MEETING:\s*(\{.*?\})\]',
        full_reply,
        re.DOTALL
    )

    if cancel_match:
        try:
            cancel_data = json.loads(cancel_match.group(1))
            clean_reply = full_reply[:cancel_match.start()].strip()
            print("\n🗑️ CANCELLATION DETECTED:", cancel_data)
        except Exception as e:
            print("❌ Cancel JSON parse error:", str(e))

    # Check for new meeting request (only if not a cancellation)
    if not cancel_data:
        meeting_match = re.search(
            r'\[MEETING_REQUEST:\s*(\{.*?\})\]',
            full_reply,
            re.DOTALL
        )
        if meeting_match:
            try:
                meeting_data = json.loads(meeting_match.group(1))
                clean_reply = full_reply[:meeting_match.start()].strip()
                print("\n📅 MEETING DETECTED:", meeting_data)
            except Exception as e:
                print("❌ Meeting JSON parse error:", str(e))

    return clean_reply, meeting_data, cancel_data


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
    business_context = data.get("business_context", "")
    user_email = data.get("user_email", "")
    page_id = data.get("page_id", os.getenv("PAGE_ID", ""))

    # Look up which CEO owns this platform account
    ceo_id = get_ceo_for_platform(platform, page_id or user_id)
    print(f"✅ Routing to ceo_id={ceo_id}")

    # Auto-save email if provided by platform (e.g. Gmail)
    if user_email and "@" in user_email:
        save_user_email(platform, user_id, user_email)
        print(f"✅ Auto-saved user email: {user_email}")

    if not user_message:
        return jsonify({"reply": "I didn't receive a message."})

    # Save user message to DB with ceo_id
    save_message(platform, user_id, "user", user_message, ceo_id)
    save_log("message_received", platform, user_id, user_message, ceo_id)

    try:
        # =========================================
        # IGNORE SOCIAL RESPONSES
        # Don't treat thank you/greetings as actions
        # =========================================

        social_phrases = [
            "thank you", "thanks", "perfect", "great",
            "awesome", "ok", "okay", "sure", "noted",
            "got it", "understood", "no problem", "welcome",
            "bye", "goodbye", "see you", "talk later",
            "sounds good", "alright", "fine", "good"
        ]

        message_lower = user_message.lower().strip()
        is_social = any(phrase in message_lower for phrase in social_phrases)

        # =========================================
        # CHECK IF USER IS PROVIDING THEIR EMAIL
        # (after bot asked for it)
        # =========================================

        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        email_match = re.search(email_pattern, user_message)

        pending = get_pending_meeting(platform, user_id)

        if email_match and pending and not is_social:
            # User just provided their email — finalize the meeting
            user_email = email_match.group(0)
            print(f"\n📧 EMAIL COLLECTED: {user_email}")

            # Save email to user profile
            save_user_email(platform, user_id, user_email)

            # Check availability
            is_free = check_calendar_availability(
                pending["meeting_date"], pending["meeting_time"]
            )

            if not is_free:
                reply = f"I'm sorry, the CEO is not available on {pending['meeting_date']} at {pending['meeting_time']}. Could you suggest another date and time?"
            else:
                # Create calendar event with attendee
                event_id, meet_link = create_calendar_event(
                    pending["title"],
                    pending["meeting_date"],
                    pending["meeting_time"],
                    pending["description"],
                    attendee_email=user_email
                )

                # Save meeting to DB
                save_meeting(
                    platform, user_id,
                    pending["title"],
                    pending["meeting_date"],
                    pending["meeting_time"],
                    pending["description"],
                    event_id or "",
                    meet_link or ""
                )

                # Send cancellation confirmation email
                send_email_notification(
                    to_email=user_email,
                    subject=f"Meeting Confirmed: {pending['title']}",
                    body=(
                        f"Hello,\n\n"
                        f"Your meeting has been confirmed.\n\n"
                        f"Details:\n"
                        f"Title: {pending['title']}\n"
                        f"Date: {pending['meeting_date']}\n"
                        f"Time: {pending['meeting_time']}\n"
                        f"Google Meet: {meet_link or 'Link will be shared shortly'}\n\n"
                        f"Best regards,\n"
                        f"Executive Assistant"
                    )
                )

                # Delete pending meeting
                delete_pending_meeting(platform, user_id)
                save_log("meeting_scheduled", platform, user_id,
                         f"{pending['title']} on {pending['meeting_date']} at {pending['meeting_time']}")

                reply = f"Your meeting has been confirmed for {pending['meeting_date']} at {pending['meeting_time']}."
                if meet_link:
                    reply += f"\n\nGoogle Meet link: {meet_link}"
                reply += f"\n\nA calendar invite has been sent to {user_email}."

            save_message(platform, user_id, "user", user_message)
            save_message(platform, user_id, "assistant", reply)
            save_log("email_collected", platform, user_id, user_email)

            return jsonify({"reply": reply})

        # =========================================
        # NORMAL AI PROCESSING
        # =========================================

        # Get AI reply + detect meeting/cancellation
        reply, meeting_data, cancel_data = process_with_ai(
            platform, user_id, user_message, business_context
        )

        # Save assistant reply to DB
        save_message(platform, user_id, "assistant", reply)
        save_log("reply_sent", platform, user_id, reply)

        # =========================================
        # HANDLE CANCELLATION
        # =========================================

        if cancel_data:
            date_str = cancel_data.get("date", "")
            event_id = cancel_meeting_in_db(platform, user_id, date_str)

            if event_id is not None:
                cancel_google_event(event_id)

                # Send cancellation email if we have user's email
                user_email = get_user_email(platform, user_id)
                if user_email:
                    send_email_notification(
                        to_email=user_email,
                        subject="Meeting Cancelled",
                        body=(
                            f"Hello,\n\n"
                            f"Your meeting scheduled on {date_str} has been cancelled.\n\n"
                            f"If you'd like to reschedule, please reach out.\n\n"
                            f"Best regards,\n"
                            f"Executive Assistant"
                        )
                    )
                    reply = f"Your meeting on {date_str} has been successfully cancelled. A confirmation has been sent to {user_email}."
                else:
                    reply = f"Your meeting on {date_str} has been successfully cancelled."

                save_log("meeting_cancelled", platform, user_id,
                         f"Cancelled meeting on {date_str}")
            else:
                reply = f"I couldn't find a scheduled meeting on {date_str}. Please check the date and try again."
                save_log("meeting_cancel_notfound", platform, user_id,
                         f"No meeting found for date: {date_str}")

            save_message(platform, user_id, "assistant", reply)

        # =========================================
        # HANDLE NEW MEETING SCHEDULING
        # =========================================

        elif meeting_data:
            title = meeting_data.get("title", "Meeting")
            date_str = meeting_data.get("date", "")
            time_str = meeting_data.get("time", "")
            description = meeting_data.get("description", "")

            # Check if we already have user's email
            user_email = get_user_email(platform, user_id)

            if user_email:
                # Already have email — check availability and schedule directly
                is_free = check_calendar_availability(date_str, time_str)

                if not is_free:
                    reply = f"I'm sorry, the CEO is not available on {date_str} at {time_str}. Could you suggest another date and time?"
                    save_message(platform, user_id, "assistant", reply)
                else:
                    event_id, meet_link = create_calendar_event(
                        title, date_str, time_str, description,
                        attendee_email=user_email
                    )

                    save_meeting(
                        platform, user_id, title, date_str, time_str,
                        description, event_id or "", meet_link or ""
                    )

                    # Send confirmation email
                    send_email_notification(
                        to_email=user_email,
                        subject=f"Meeting Confirmed: {title}",
                        body=(
                            f"Hello,\n\n"
                            f"Your meeting has been confirmed.\n\n"
                            f"Title: {title}\n"
                            f"Date: {date_str}\n"
                            f"Time: {time_str}\n"
                            f"Google Meet: {meet_link or 'Link will be shared shortly'}\n\n"
                            f"Best regards,\n"
                            f"Executive Assistant"
                        )
                    )

                    save_log("meeting_scheduled", platform, user_id,
                             f"{title} on {date_str} at {time_str}")

                    if meet_link:
                        reply += f"\n\nGoogle Meet link: {meet_link}"
                    reply += f"\n\nA calendar invite has been sent to {user_email}."
                    save_message(platform, user_id, "assistant", reply)

            else:
                # No email yet — save as pending and ask for email
                save_pending_meeting(platform, user_id, title, date_str, time_str, description)
                reply = f"I'd be happy to schedule that meeting. Could you please share your email address so I can send you the calendar invite and Google Meet link?"
                save_message(platform, user_id, "assistant", reply)
                save_log("pending_meeting_created", platform, user_id,
                         f"{title} on {date_str} at {time_str}")

        print("\n✅ FINAL REPLY:", reply)

        return jsonify({"reply": reply})

    except Exception as e:

        print("\n❌ GOVERNOR PROCESSING ERROR:", str(e))
        save_log("error", platform, user_id, str(e))

        return jsonify({"reply": "I'm temporarily unavailable. Please try again shortly."})


# =========================================
# AUTH HELPERS
# =========================================

def generate_token(user_id, email):
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.utcnow() + timedelta(days=7)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except:
        return None


def auth_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"error": "No token provided"}), 401
        payload = verify_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401
        request.user = payload
        return f(*args, **kwargs)
    return decorated


# =========================================
# AUTH ENDPOINTS
# =========================================

@app.route("/auth/register", methods=["POST"])
def register():
    try:
        data = request.get_json()
        name = data.get("name", "")
        email = data.get("email", "")
        password = data.get("password", "")
        business_name = data.get("business_name", "")

        if not email or not password or not name:
            return jsonify({"error": "Name, email and password are required"}), 400

        # Hash password
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Check if email exists
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"error": "Email already registered"}), 409

        # Create user
        cur.execute(
            "INSERT INTO users (name, email, password, business_name) VALUES (%s, %s, %s, %s) RETURNING id, name, email, business_name",
            (name, email, hashed, business_name)
        )
        user = dict(cur.fetchone())
        conn.commit()
        cur.close()
        conn.close()

        token = generate_token(user["id"], user["email"])

        return jsonify({
            "success": True,
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "email": user["email"], "business_name": user["business_name"]}
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/auth/login", methods=["POST"])
def login():
    try:
        data = request.get_json()
        email = data.get("email", "")
        password = data.get("password", "")

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if not user:
            return jsonify({"error": "Invalid email or password"}), 401

        if not bcrypt.checkpw(password.encode("utf-8"), user["password"].encode("utf-8")):
            return jsonify({"error": "Invalid email or password"}), 401

        token = generate_token(user["id"], user["email"])

        return jsonify({
            "success": True,
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "email": user["email"], "business_name": user["business_name"]}
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/auth/me", methods=["GET"])
@auth_required
def get_me():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, email, business_name, created_at FROM users WHERE id=%s", (request.user["user_id"],))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if not user:
            return jsonify({"error": "User not found"}), 404

        return jsonify({"user": dict(user)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/utils/resolve_user", methods=["POST"])
def resolve_user():
    """Resolve platform user ID to display name"""
    try:
        data = request.get_json()
        platform = data.get("platform", "")
        user_id = data.get("user_id", "")
        access_token = os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN", "")

        # First check DB cache
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT name FROM user_profiles WHERE platform=%s AND user_id=%s", (platform, user_id))
        row = cur.fetchone()
        if row and row.get("name"):
            cur.close()
            conn.close()
            return jsonify({"name": row["name"]})

        # Try Meta API
        name = user_id
        if platform in ["instagram", "facebook"] and access_token:
            try:
                res = requests.get(
                    f"https://graph.facebook.com/v25.0/{user_id}",
                    params={"fields": "name,username", "access_token": access_token},
                    timeout=5
                )
                user_data = res.json()
                name = user_data.get("name") or user_data.get("username") or user_id
                # Save to DB if we got a real name
                if name != user_id:
                    cur.execute("""
                        INSERT INTO user_profiles (platform, user_id, name)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (platform, user_id) DO UPDATE SET name=%s
                    """, (platform, user_id, name, name))
                    conn.commit()
            except:
                pass

        cur.close()
        conn.close()
        return jsonify({"name": name})

    except Exception as e:
        return jsonify({"name": user_id})


@app.route("/auth/change_password", methods=["POST"])
@auth_required
def change_password():
    try:
        data = request.get_json()
        current_password = data.get("current_password", "")
        new_password = data.get("new_password", "")

        if not current_password or not new_password:
            return jsonify({"error": "Both fields are required"}), 400
        if len(new_password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM users WHERE id=%s", (request.user["user_id"],))
        user = cur.fetchone()

        if not bcrypt.checkpw(current_password.encode("utf-8"), user["password"].encode("utf-8")):
            cur.close()
            conn.close()
            return jsonify({"error": "Current password is incorrect"}), 401

        hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        cur.execute("UPDATE users SET password=%s WHERE id=%s", (hashed, request.user["user_id"]))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True, "message": "Password changed successfully"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def migrate_existing_data():
    """Assign all existing data to the first user account (Cherry Mewie)"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get first user
        cur.execute("SELECT id, email FROM users ORDER BY id ASC LIMIT 1")
        first_user = cur.fetchone()

        if not first_user:
            cur.close()
            conn.close()
            return

        ceo_id = first_user["id"]
        print(f"\n✅ Migrating existing data to ceo_id={ceo_id} ({first_user['email']})")

        # Assign conversations
        cur.execute("UPDATE conversations SET ceo_id=%s WHERE ceo_id IS NULL", (ceo_id,))
        # Assign meetings
        cur.execute("UPDATE meetings SET ceo_id=%s WHERE ceo_id IS NULL", (ceo_id,))
        # Assign logs
        cur.execute("UPDATE logs SET ceo_id=%s WHERE ceo_id IS NULL", (ceo_id,))
        # Assign user_profiles
        cur.execute("UPDATE user_profiles SET ceo_id=%s WHERE ceo_id IS NULL", (ceo_id,))
        # Assign pending_meetings
        cur.execute("UPDATE pending_meetings SET ceo_id=%s WHERE ceo_id IS NULL", (ceo_id,))

        # Add default connected platforms for first user from env vars
        instagram_token = os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN", "")
        page_id = os.getenv("PAGE_ID", "1003745256147352")
        instagram_account_id = os.getenv("INSTAGRAM_ACCOUNT_ID", "17841478520495248")

        # Always register the original page for the first user (cosmetic + routing)
        cur.execute("""
            INSERT INTO connected_platforms (ceo_id, platform, account_id, page_id, account_name, access_token, status, is_verified)
            VALUES (%s, 'instagram', %s, %s, %s, %s, 'active', false)
            ON CONFLICT (ceo_id, platform) DO UPDATE SET status='active', page_id=EXCLUDED.page_id
        """, (ceo_id, instagram_account_id, page_id, "Instagram (Main)", instagram_token))

        cur.execute("""
            INSERT INTO connected_platforms (ceo_id, platform, account_id, page_id, account_name, access_token, status, is_verified)
            VALUES (%s, 'facebook', %s, %s, %s, %s, 'active', false)
            ON CONFLICT (ceo_id, platform) DO UPDATE SET status='active', page_id=EXCLUDED.page_id
        """, (ceo_id, page_id, page_id, "Facebook Page (Main)", instagram_token))

        # Add gmail from env
        gmail_address = first_user["email"]
        cur.execute("""
            INSERT INTO connected_platforms (ceo_id, platform, account_id, account_name, status, is_verified)
            VALUES (%s, 'gmail', %s, %s, 'active', true)
            ON CONFLICT (ceo_id, platform) DO NOTHING
        """, (ceo_id, gmail_address, gmail_address))

        # Add existing test accounts (whitelisted IDs from meta bot)
        whitelisted = ["2381442649051546", "33227605106886622"]
        for wid in whitelisted:
            cur.execute("""
                INSERT INTO test_accounts (ceo_id, platform, account_id, account_name)
                VALUES (%s, 'instagram', %s, %s)
                ON CONFLICT (ceo_id, platform, account_id) DO NOTHING
            """, (ceo_id, wid, f"Test Account ({wid[-4:]})"))

        conn.commit()
        cur.close()
        conn.close()
        print("✅ Migration complete")
    except Exception as e:
        print(f"❌ Migration error: {str(e)}")


# =========================================
# PLATFORM CONNECTION ENDPOINTS
# =========================================

@app.route("/platforms/connect", methods=["POST"])
@auth_required
def connect_platform():
    """Connect a platform to CEO account"""
    try:
        data = request.get_json()
        platform = data.get("platform", "")
        account_id = data.get("account_id", "")
        account_name = data.get("account_name", "")
        access_token = data.get("access_token", "")
        page_id = data.get("page_id", "")
        phone_number_id = data.get("phone_number_id", "")
        ceo_id = request.user["user_id"]

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            INSERT INTO connected_platforms
            (ceo_id, platform, account_id, account_name, access_token, page_id, phone_number_id, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')
            ON CONFLICT (ceo_id, platform) DO UPDATE SET
            account_id=%s, account_name=%s, access_token=%s, page_id=%s,
            phone_number_id=%s, status='active'
        """, (ceo_id, platform, account_id, account_name, access_token, page_id, phone_number_id,
              account_id, account_name, access_token, page_id, phone_number_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/platforms/list", methods=["GET"])
@auth_required
def list_platforms():
    """Get all connected platforms for CEO"""
    try:
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM connected_platforms WHERE ceo_id=%s", (ceo_id,))
        platforms = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return jsonify({"platforms": platforms})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/platforms/disconnect", methods=["POST"])
@auth_required
def disconnect_platform():
    """Disconnect a platform"""
    try:
        platform = request.get_json().get("platform", "")
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE connected_platforms SET status='disconnected' WHERE ceo_id=%s AND platform=%s",
                    (ceo_id, platform))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/test_accounts/list", methods=["GET"])
@auth_required
def list_test_accounts():
    """Get test accounts for CEO"""
    try:
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM test_accounts WHERE ceo_id=%s", (ceo_id,))
        accounts = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return jsonify({"test_accounts": accounts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/platforms/by_page/<page_id>", methods=["GET"])
def get_platform_by_page(page_id):
    """Look up CEO config by page_id — used by meta bot"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT cp.*, u.meta_verified, u.id as ceo_id
            FROM connected_platforms cp
            JOIN users u ON u.id = cp.ceo_id
            WHERE (cp.account_id=%s OR cp.page_id=%s)
            AND cp.status='active'
            LIMIT 1
        """, (page_id, page_id))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return jsonify(dict(row))
        return jsonify({"error": "Not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/test_accounts/by_ceo/<int:ceo_id>", methods=["GET"])
def get_test_accounts_by_ceo(ceo_id):
    """Get test accounts for a CEO — used by meta bot"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM test_accounts WHERE ceo_id=%s", (ceo_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"test_accounts": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/test_accounts/add", methods=["POST"])
@auth_required
def add_test_account():
    """Add a test account"""
    try:
        data = request.get_json()
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Max 5 test accounts per platform
        cur.execute("SELECT COUNT(*) as cnt FROM test_accounts WHERE ceo_id=%s AND platform=%s",
                    (ceo_id, data.get("platform")))
        count = cur.fetchone()["cnt"]
        if count >= 5:
            cur.close()
            conn.close()
            return jsonify({"error": "Maximum 5 test accounts per platform"}), 400

        cur.execute("""
            INSERT INTO test_accounts (ceo_id, platform, account_id, account_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (ceo_id, platform, account_id) DO NOTHING
            RETURNING id
        """, (ceo_id, data.get("platform"), data.get("account_id"), data.get("account_name", "")))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/test_accounts/remove", methods=["POST"])
@auth_required
def remove_test_account():
    """Remove a test account"""
    try:
        data = request.get_json()
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM test_accounts WHERE ceo_id=%s AND id=%s",
                    (ceo_id, data.get("id")))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/meta/verification/submit", methods=["POST"])
@auth_required
def submit_verification():
    """Mark that CEO has submitted Meta verification"""
    try:
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE users SET
            meta_verification_status='submitted',
            meta_verification_submitted_at=CURRENT_TIMESTAMP
            WHERE id=%s
        """, (ceo_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "message": "Verification submission recorded"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/meta/verification/status", methods=["GET"])
@auth_required
def get_verification_status():
    """Get Meta verification status for CEO"""
    try:
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT meta_verified, meta_verification_status,
            meta_verification_submitted_at
            FROM users WHERE id=%s
        """, (ceo_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return jsonify(dict(row))
        return jsonify({"meta_verified": False, "meta_verification_status": "not_started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/meta/verification/check", methods=["POST"])
@auth_required
def check_verification_with_meta():
    """
    Attempt to check real verification status with Meta's Graph API.
    Falls back to stored status if the token lacks business_management permission
    (which requires Meta app review).
    """
    try:
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get the CEO's stored facebook token
        cur.execute("""
            SELECT access_token, page_id FROM connected_platforms
            WHERE ceo_id=%s AND platform='facebook' AND status='active' LIMIT 1
        """, (ceo_id,))
        row = cur.fetchone()

        real_status = None
        checked_with_meta = False

        if row and row.get("access_token"):
            token = row["access_token"]
            # Try to query the business verification status
            try:
                # First get the business linked to this page
                biz_res = requests.get(
                    f"https://graph.facebook.com/v25.0/{row['page_id']}",
                    params={"fields": "business", "access_token": token},
                    timeout=10
                ).json()
                business = biz_res.get("business")
                if business and business.get("id"):
                    verify_res = requests.get(
                        f"https://graph.facebook.com/v25.0/{business['id']}",
                        params={"fields": "verification_status", "access_token": token},
                        timeout=10
                    ).json()
                    if "verification_status" in verify_res:
                        real_status = verify_res["verification_status"]
                        checked_with_meta = True
            except Exception as e:
                print(f"⚠️ Meta verification check failed (likely missing permission): {e}")

        # If we got a real status from Meta, update our DB to match
        if checked_with_meta and real_status:
            is_verified = real_status == "verified"
            cur.execute("""
                UPDATE users SET meta_verified=%s,
                meta_verification_status=%s WHERE id=%s
            """, (is_verified, real_status, ceo_id))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({
                "checked_with_meta": True,
                "verification_status": real_status,
                "meta_verified": is_verified
            })

        # Fallback — return stored status, note we couldn't reach Meta
        cur.execute("SELECT meta_verified, meta_verification_status FROM users WHERE id=%s", (ceo_id,))
        stored = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({
            "checked_with_meta": False,
            "note": "Real-time check requires Meta app review (business_management permission). Using recorded status.",
            "verification_status": stored.get("meta_verification_status"),
            "meta_verified": stored.get("meta_verified")
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/meta/verification/confirm", methods=["POST"])
@auth_required
def confirm_verification():
    """Manually confirm Meta verification (after CEO receives confirmation from Meta)"""
    try:
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE users SET
            meta_verified=TRUE,
            meta_verification_status='verified'
            WHERE id=%s
        """, (ceo_id,))
        conn.commit()
        cur.close()
        conn.close()

        # Update localStorage user data
        return jsonify({"success": True, "meta_verified": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/oauth/facebook/url", methods=["GET"])
@auth_required
def get_facebook_oauth_url():
    """Generate Facebook OAuth URL for CEO to connect their page"""
    try:
        ceo_id = request.user["user_id"]
        FB_APP_ID = os.getenv("FB_APP_ID", "1408349267537588")
        REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "https://governor-ai-1odr.onrender.com/oauth/facebook/callback")
        scope = "pages_messaging,pages_read_engagement,instagram_manage_messages,instagram_basic,pages_show_list,business_management"
        import urllib.parse
        state = str(ceo_id)
        url = (
            f"https://www.facebook.com/v25.0/dialog/oauth?"
            f"client_id={FB_APP_ID}&"
            f"redirect_uri={urllib.parse.quote(REDIRECT_URI)}&"
            f"scope={scope}&"
            f"state={state}&"
            f"response_type=code"
        )
        return jsonify({"url": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/oauth/facebook/callback", methods=["GET"])
def facebook_oauth_callback():
    """Handle Facebook OAuth callback — fetch pages + Instagram accounts"""
    try:
        code = request.args.get("code")
        state = request.args.get("state")
        error = request.args.get("error")
        FB_APP_ID = os.getenv("FB_APP_ID", "1408349267537588")
        FB_APP_SECRET = os.getenv("FB_APP_SECRET", "3ba762e84e4615f44b1207e311ede9d3")
        REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "https://governor-ai-1odr.onrender.com/oauth/facebook/callback")
        DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://vertex-ai-dashboard.vercel.app")

        if error:
            return f"<script>window.location='{DASHBOARD_URL}?oauth_error={error}'</script>"
        if not code or not state:
            return f"<script>window.location='{DASHBOARD_URL}?oauth_error=missing_params'</script>"

        ceo_id = int(state)

        # Exchange code for token
        token_res = requests.get(
            "https://graph.facebook.com/v25.0/oauth/access_token",
            params={"client_id": FB_APP_ID, "client_secret": FB_APP_SECRET, "redirect_uri": REDIRECT_URI, "code": code}
        ).json()

        if "error" in token_res:
            return f"<script>window.location='{DASHBOARD_URL}?oauth_error=token_failed'</script>"

        user_token = token_res["access_token"]

        # Get long-lived token
        long_token_res = requests.get(
            "https://graph.facebook.com/v25.0/oauth/access_token",
            params={"grant_type": "fb_exchange_token", "client_id": FB_APP_ID, "client_secret": FB_APP_SECRET, "fb_exchange_token": user_token}
        ).json()
        long_token = long_token_res.get("access_token", user_token)

        # Get pages
        pages_res = requests.get(
            "https://graph.facebook.com/v25.0/me/accounts",
            params={"access_token": long_token, "fields": "id,name,access_token,instagram_business_account"}
        ).json()
        pages = pages_res.get("data", [])

        if not pages:
            return f"<script>window.location='{DASHBOARD_URL}?oauth_error=no_pages'</script>"

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        connected = []

        for page in pages:
            page_id = page["id"]
            page_name = page["name"]
            page_token = page.get("access_token", long_token)

            # Connect Facebook
            cur.execute("""
                INSERT INTO connected_platforms (ceo_id, platform, account_id, page_id, account_name, access_token, status)
                VALUES (%s, 'facebook', %s, %s, %s, %s, 'active')
                ON CONFLICT (ceo_id, platform) DO UPDATE SET
                account_id=%s, page_id=%s, account_name=%s, access_token=%s, status='active'
            """, (ceo_id, page_id, page_id, page_name, page_token, page_id, page_id, page_name, page_token))
            connected.append(f"Facebook: {page_name}")

            # Connect Instagram if linked
            ig = page.get("instagram_business_account")
            if ig:
                ig_id = ig["id"]
                ig_res = requests.get(f"https://graph.facebook.com/v25.0/{ig_id}",
                    params={"fields": "id,name,username", "access_token": page_token}).json()
                ig_name = ig_res.get("username") or ig_res.get("name") or f"Instagram ({ig_id[-4:]})"
                cur.execute("""
                    INSERT INTO connected_platforms (ceo_id, platform, account_id, page_id, account_name, access_token, status)
                    VALUES (%s, 'instagram', %s, %s, %s, %s, 'active')
                    ON CONFLICT (ceo_id, platform) DO UPDATE SET
                    account_id=%s, page_id=%s, account_name=%s, access_token=%s, status='active'
                """, (ceo_id, ig_id, page_id, ig_name, page_token, ig_id, page_id, ig_name, page_token))
                connected.append(f"Instagram: @{ig_name}")

        conn.commit()
        cur.close()
        conn.close()
        save_log("oauth_connected", "facebook", str(ceo_id), ", ".join(connected))
        return f"<script>window.location='{DASHBOARD_URL}?oauth_success=true&connected={len(connected)}'</script>"

    except Exception as e:
        print("❌ OAuth error:", str(e))
        DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://vertex-ai-dashboard.vercel.app")
        return f"<script>window.location='{DASHBOARD_URL}?oauth_error=server_error'</script>"



@app.route("/meta/verification/reset", methods=["POST"])
@auth_required
def reset_verification():
    """Reset verification status back to not_started (for testing)"""
    try:
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE users SET
            meta_verified=FALSE,
            meta_verification_status='not_started'
            WHERE id=%s
        """, (ceo_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "meta_verified": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/onboarding/complete", methods=["POST"])
@auth_required
def complete_onboarding():
    """Mark onboarding as complete"""
    try:
        data = request.get_json()
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE users SET
            onboarding_complete=TRUE,
            business_name=%s,
            industry=%s,
            website=%s
            WHERE id=%s
        """, (data.get("business_name", ""), data.get("industry", ""),
              data.get("website", ""), ceo_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/auth/me/update", methods=["POST"])
@auth_required
def update_profile():
    """Update user profile"""
    try:
        data = request.get_json()
        ceo_id = request.user["user_id"]
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            UPDATE users SET name=%s, business_name=%s
            WHERE id=%s
            RETURNING id, name, email, business_name, meta_verified, onboarding_complete
        """, (data.get("name", ""), data.get("business_name", ""), ceo_id))
        user = dict(cur.fetchone())
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "user": user})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# When a CEO adds their website, we scrape it
# and store it as business_context in the DB
# =========================================

@app.route("/onboard/scrape_website", methods=["POST"])
def scrape_website():
    try:
        data = request.get_json()
        url = data.get("url", "")
        ceo_id = data.get("ceo_id", "")

        if not url or not ceo_id:
            return jsonify({"error": "url and ceo_id are required"}), 400

        import urllib.request
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.skip = False
            def handle_starttag(self, tag, attrs):
                if tag in ["script", "style", "nav", "footer"]:
                    self.skip = True
            def handle_endtag(self, tag):
                if tag in ["script", "style", "nav", "footer"]:
                    self.skip = False
            def handle_data(self, data):
                if not self.skip and data.strip():
                    self.text.append(data.strip())

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")

        parser = TextExtractor()
        parser.feed(html)
        raw_text = " ".join(parser.text)[:3000]  # limit to 3000 chars

        # Store in DB
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS business_profiles (
                ceo_id VARCHAR(255) PRIMARY KEY,
                website_url VARCHAR(500),
                business_context TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            INSERT INTO business_profiles (ceo_id, website_url, business_context)
            VALUES (%s, %s, %s)
            ON CONFLICT (ceo_id) DO UPDATE
            SET website_url=%s, business_context=%s
        """, (ceo_id, url, raw_text, url, raw_text))
        conn.commit()
        cur.close()
        conn.close()

        save_log("website_scraped", "system", ceo_id, url)

        return jsonify({
            "success": True,
            "ceo_id": ceo_id,
            "context_length": len(raw_text),
            "preview": raw_text[:200]
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/debug/clear_pending/<platform>/<user_id>", methods=["GET"])
def clear_pending(platform, user_id):
    try:
        delete_pending_meeting(platform, user_id)
        return jsonify({"success": True, "message": f"Cleared pending meeting for {user_id}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================================
# DASHBOARD API ENDPOINTS
# =========================================

def get_ceo_id_from_request():
    """Extract ceo_id from JWT token if present"""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token:
        payload = verify_token(token)
        if payload:
            return payload.get("user_id")
    return None


@app.route("/dashboard/conversations", methods=["GET"])
def get_conversations():
    try:
        platform = request.args.get("platform")
        user_id = request.args.get("user_id")
        limit = int(request.args.get("limit", 50))
        ceo_id = get_ceo_id_from_request()

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = "SELECT * FROM conversations WHERE 1=1"
        params = []

        if ceo_id:
            query += " AND (ceo_id=%s OR ceo_id IS NULL)"
            params.append(ceo_id)
        if platform:
            query += " AND platform=%s"
            params.append(platform)
        if user_id:
            query += " AND user_id=%s"
            params.append(user_id)

        query += " ORDER BY id ASC LIMIT %s"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"conversations": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard/meetings", methods=["GET"])
def get_meetings():
    try:
        ceo_id = get_ceo_id_from_request()
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if ceo_id:
            cur.execute("SELECT * FROM meetings WHERE ceo_id=%s OR ceo_id IS NULL ORDER BY created_at DESC", (ceo_id,))
        else:
            cur.execute("SELECT * FROM meetings ORDER BY created_at DESC")

        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"meetings": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard/logs", methods=["GET"])
def get_logs():
    try:
        limit = int(request.args.get("limit", 100))
        ceo_id = get_ceo_id_from_request()
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if ceo_id:
            cur.execute("SELECT * FROM logs WHERE ceo_id=%s OR ceo_id IS NULL ORDER BY timestamp DESC LIMIT %s",
                        (ceo_id, limit))
        else:
            cur.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT %s", (limit,))

        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"logs": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dashboard/stats", methods=["GET"])
def get_stats():
    try:
        ceo_id = get_ceo_id_from_request()
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        filter_clause = "AND (ceo_id=%s OR ceo_id IS NULL)" if ceo_id else ""
        params = (ceo_id,) if ceo_id else ()

        cur.execute(f"SELECT COUNT(*) as total FROM conversations WHERE role='user' {filter_clause}", params)
        total_messages = cur.fetchone()["total"]

        cur.execute(f"SELECT COUNT(*) as total FROM meetings {('WHERE ceo_id=%s OR ceo_id IS NULL' if ceo_id else '')}", params)
        total_meetings = cur.fetchone()["total"]

        cur.execute(f"SELECT COUNT(DISTINCT user_id) as total FROM conversations WHERE 1=1 {filter_clause}", params)
        total_users = cur.fetchone()["total"]

        cur.execute(f"SELECT COUNT(*) as total FROM meetings WHERE status='scheduled' {('AND (ceo_id=%s OR ceo_id IS NULL)' if ceo_id else '')}", params)
        upcoming_meetings = cur.fetchone()["total"]

        platform_counts = {}
        for platform in ['instagram', 'facebook', 'gmail', 'whatsapp']:
            p_params = (platform, ceo_id) if ceo_id else (platform,)
            p_filter = "AND (ceo_id=%s OR ceo_id IS NULL)" if ceo_id else ""
            cur.execute(f"SELECT COUNT(*) as total FROM conversations WHERE role='user' AND platform=%s {p_filter}", p_params)
            platform_counts[platform] = cur.fetchone()["total"]

        cur.close()
        conn.close()
        return jsonify({
            "total_messages": total_messages,
            "total_meetings": total_meetings,
            "total_users": total_users,
            "upcoming_meetings": upcoming_meetings,
            "instagram_messages": platform_counts.get("instagram", 0),
            "facebook_messages": platform_counts.get("facebook", 0),
            "gmail_messages": platform_counts.get("gmail", 0),
            "whatsapp_messages": platform_counts.get("whatsapp", 0),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================================
# KEEP ALIVE PINGER
# =========================================

def keep_alive():
    while True:
        time.sleep(600)  # ping every 10 minutes
        urls = [
            "https://governor-ai-1odr.onrender.com",
            "https://fb-webhook-bot-uhxb.onrender.com",
            "https://gmail-bot-k5cc.onrender.com",
            "https://whatsapp-bot-rzg9.onrender.com",
            os.getenv("GMAIL_BOT_URL", ""),
            os.getenv("WHATSAPP_BOT_URL", "")
        ]
        for url in urls:
            if not url:
                continue
            try:
                requests.get(url, timeout=10)
                print(f"✅ Keep-alive ping: {url}")
            except Exception as e:
                print(f"⚠️ Keep-alive failed for {url}: {str(e)}")


# =========================================
# START SERVER
# =========================================

# Initialize DB on startup
with app.app_context():
    try:
        init_db()
        migrate_existing_data()
    except Exception as e:
        print("❌ DB init error:", str(e))

# Start keep-alive thread
ping_thread = threading.Thread(target=keep_alive, daemon=True)
ping_thread.start()
print("✅ Keep-alive pinger started")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)