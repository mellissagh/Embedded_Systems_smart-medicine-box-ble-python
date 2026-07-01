import sqlite3
import os
from datetime import datetime

# Path where the database file will live
DB_DIR = "data"
DB_NAME = "medicine_box.db"
DB_PATH = os.path.join(DB_DIR, DB_NAME)


def init_database():
    """
    Initializes the SQLite database.
    Creates the data directory if it does not exist and creates the history_logs table.
    """
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)
        print(f"[DB LOG] Created directory: '{DB_DIR}/'")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            compartment TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

    print(f"[DB LOG] Database initialized successfully at '{DB_PATH}'")


def log_event(compartment, status):
    """
    Logs a new medicine box event.

    Example:
        log_event("C1", "TAKEN")
        log_event("C1", "LID_OPEN_PILL_INSIDE")
    """
    init_database()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO history_logs (timestamp, compartment, status)
        VALUES (?, ?, ?)
    """, (now_str, compartment, status))

    conn.commit()
    conn.close()

    print(f"[DB LOG] Recorded entry -> [{now_str}] {compartment}: {status}")
    return now_str


def fetch_all_events():
    """
    Fetches all saved events from the database.

    Returns:
        List of tuples:
        [(id, timestamp, compartment, status), ...]
    """
    init_database()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, timestamp, compartment, status
        FROM history_logs
        ORDER BY timestamp DESC
    """)

    rows = cursor.fetchall()

    conn.close()
    return rows


# =====================================================================
# LOCAL VERIFICATION BLOCK
# =====================================================================
if __name__ == "__main__":
    print("--- Executing Standalone Database Diagnostics ---")

    init_database()

    print("\nSimulating prototype sensor events...")
    log_event("C1", "COMPARTMENT_FULL_AND_CLOSED")
    log_event("C1", "LID_OPEN_PILL_INSIDE")
    log_event("C1", "TAKEN")

    print("\nReading database history:")
    history = fetch_all_events()

    for row in history:
        print(f"  Row ID {row[0]} | Time: {row[1]} | Unit: {row[2]} | Event: {row[3]}")