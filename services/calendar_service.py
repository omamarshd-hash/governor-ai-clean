from datetime import timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

import uuid
import os


# =========================================
# GOOGLE SCOPES
# =========================================

SCOPES = [
    "https://www.googleapis.com/auth/calendar"
]


# =========================================
# AUTHENTICATE GOOGLE
# =========================================

def get_calendar_service():

    creds = None

    if os.path.exists("calendar_token.json"):

        creds = Credentials.from_authorized_user_file(
            "calendar_token.json",
            SCOPES
        )

    if not creds or not creds.valid:

        if creds and creds.expired and creds.refresh_token:

            creds.refresh(Request())

        else:

            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json",
                SCOPES
            )

            creds = flow.run_local_server(
                port=0
            )

        with open(
            "calendar_token.json",
            "w"
        ) as token:

            token.write(
                creds.to_json()
            )

    return build(
        "calendar",
        "v3",
        credentials=creds
    )


# =========================================
# CHECK REAL CALENDAR AVAILABILITY
# =========================================

def is_time_available(
    service,
    start_time,
    end_time
):

    body = {

        "timeMin": (
            start_time
            .astimezone()
            .isoformat()
        ),

        "timeMax": (
            end_time
            .astimezone()
            .isoformat()
        ),

        "timeZone": "Asia/Karachi",

        "items": [

            {
                "id": "primary"
            }
        ]
    }

    print("\n📅 FREEBUSY REQUEST:")
    print(body)

    freebusy_result = service.freebusy().query(
        body=body
    ).execute()

    print("\n📅 FREEBUSY RESPONSE:")
    print(freebusy_result)

    busy_slots = (

        freebusy_result

        .get("calendars", {})

        .get("primary", {})

        .get("busy", [])
    )

    print("\n📅 BUSY SLOTS:")
    print(busy_slots)

    # =========================================
    # SLOT FREE
    # =========================================

    if len(busy_slots) == 0:

        return True

    # =========================================
    # SLOT OCCUPIED
    # =========================================

    return False


# =========================================
# CREATE MEETING
# =========================================

def create_meeting(
    summary,
    start_time,
    attendee_email
):

    try:

        service = get_calendar_service()

        end_time = start_time + timedelta(
            minutes=30
        )

        # =========================================
        # CHECK SLOT AVAILABILITY
        # =========================================

        available = is_time_available(

            service,

            start_time,

            end_time
        )

        if not available:

            return {

                "success": False,

                "message": (
                    "This slot is already occupied."
                )
            }

        # =========================================
        # UNIQUE GOOGLE MEET REQUEST
        # =========================================

        request_id = str(
            uuid.uuid4()
        )

        # =========================================
        # EVENT BODY
        # =========================================

        event = {

            "summary": summary,

            "start": {

                "dateTime": start_time.isoformat(),

                "timeZone": "Asia/Karachi"
            },

            "end": {

                "dateTime": end_time.isoformat(),

                "timeZone": "Asia/Karachi"
            },

            "attendees": [

                {
                    "email": attendee_email
                }
            ],

            "conferenceData": {

                "createRequest": {

                    "requestId": request_id,

                    "conferenceSolutionKey": {

                        "type": "hangoutsMeet"
                    }
                }
            }
        }

        print("\n🚀 CREATING EVENT:")
        print(event)

        # =========================================
        # CREATE GOOGLE EVENT
        # =========================================

        created_event = service.events().insert(

            calendarId="primary",

            body=event,

            conferenceDataVersion=1,

            sendUpdates="all"

        ).execute()

        print("\n✅ EVENT CREATED")
        print(created_event)

        # =========================================
        # REAL GOOGLE MEET LINK
        # =========================================

        meet_link = created_event.get(
            "hangoutLink"
        )

        print("\n🎥 GOOGLE MEET LINK:")
        print(meet_link)

        return {

            "success": True,

            "meet_link": meet_link
        }

    except Exception as e:

        print("\n❌ CALENDAR ERROR ❌")
        print(str(e))

        return {

            "success": False,

            "message": str(e)
        }