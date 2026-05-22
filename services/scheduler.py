from datetime import datetime, timedelta

# Fake busy slots (simulate CEO calendar)
BUSY_SLOTS = [
    "2026-05-05 15:00:00",
    "2026-05-05 17:00:00"
]


def get_available_slots():
    today = datetime.now()

    # check next day (simple logic)
    target_day = today + timedelta(days=1)

    # available time slots
    slots = [
        target_day.replace(hour=14, minute=0, second=0, microsecond=0),
        target_day.replace(hour=16, minute=0, second=0, microsecond=0),
        target_day.replace(hour=18, minute=0, second=0, microsecond=0),
    ]

    available = []

    for slot in slots:
        if slot.strftime("%Y-%m-%d %H:%M:%S") not in BUSY_SLOTS:
            available.append(slot.strftime("%I:%M %p"))

    return available