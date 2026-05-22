from database.db import get_connection


# =========================================
# SAVE MESSAGE
# =========================================

def save_message(data, ai_enabled):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO messages
        (
            platform,
            user_id,
            message,
            status,
            ai_enabled,
            meeting_scheduled
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            data.platform,
            data.user_id,
            data.message,
            "received",
            ai_enabled,
            False
        )
    )

    message_id = cursor.fetchone()[0]

    conn.commit()

    conn.close()

    return message_id


# =========================================
# UPDATE MESSAGE
# =========================================

def update_message(
    message_id,
    status,
    response=None,
    meeting_scheduled=None
):

    conn = get_connection()

    cursor = conn.cursor()

    # =========================================
    # UPDATE WITHOUT MEETING STATUS
    # =========================================

    if meeting_scheduled is None:

        cursor.execute(
            """
            UPDATE messages
            SET status=%s,
                response=%s
            WHERE id=%s
            """,
            (
                status,
                response,
                message_id
            )
        )

    # =========================================
    # UPDATE WITH MEETING STATUS
    # =========================================

    else:

        cursor.execute(
            """
            UPDATE messages
            SET status=%s,
                response=%s,
                meeting_scheduled=%s
            WHERE id=%s
            """,
            (
                status,
                response,
                meeting_scheduled,
                message_id
            )
        )

    conn.commit()

    conn.close()


# =========================================
# CHECK EXISTING MEETING
# =========================================

def meeting_already_scheduled(user_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM messages
        WHERE user_id=%s
        AND meeting_scheduled=TRUE
        LIMIT 1
        """,
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return result is not None