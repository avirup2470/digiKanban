import sqlite3
import json
import sys
from datetime import datetime

DB_PATH = 'inventory.db'

def ensure_schema_compatibility():
    """
    Ensures that the SQLite schema has the necessary columns and tables 
    to support the Firebase port (e.g., FIFO statistics, Red flag, and LastActivated tracking).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Check/Add Red column to Card table
    try:
        cursor.execute("ALTER TABLE Card ADD COLUMN red INTEGER DEFAULT 0;")
    except sqlite3.OperationalError:
        pass  # Column already exists
        
    # 2. Check/Add last_activated column to Card table
    try:
        cursor.execute("ALTER TABLE Card ADD COLUMN last_activated DATETIME;")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # 3. Create a FifoStats table to mirror the Firebase "Locations/.../FIFO" stats path
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS FifoStats (
            location TEXT,
            part_id INTEGER,
            month_year TEXT,
            total_count INTEGER DEFAULT 0,
            fifo_count INTEGER DEFAULT 0,
            PRIMARY KEY (location, part_id, month_year),
            FOREIGN KEY (part_id) REFERENCES Part(id) ON DELETE CASCADE
        )
    ''')
    
    conn.commit()
    conn.close()

def fifo_update(cursor, current_card_id, part_id, arrival_location):
    """
    Ported FIFO Tracking Logic:
    1. Looks up all currently active cards for this part.
    2. Compares activation timestamps to see if the FIFO rule was violated.
    3. Marks any bypassed older active cards as 'Red' (red = 1).
    4. Logs/increments the monthly FIFO stats for this location and part.
    """
    now = datetime.now()
    month_year_str = now.strftime("%B%y") # e.g., "July26"
    
    # Fetch all active cards for this part
    cursor.execute('''
        SELECT id, last_activated FROM Card 
        WHERE part_id = ? AND activation = 'true'
    ''', (part_id,))
    active_cards = cursor.fetchall()
    
    current_card_data = None
    active_part_cards = []
    
    for card_id, last_act in active_cards:
        card_info = {'id': card_id, 'last_activated': last_act}
        active_part_cards.append(card_info)
        if card_id == current_card_id:
            current_card_data = card_info
            
    if not current_card_data:
        return

    # Treat empty or null timestamps as infinity or 0 for comparison safety
    current_ts = current_card_data['last_activated'] if current_card_data['last_activated'] else "9999-12-31 23:59:59"
    is_fifo_followed = True

    for card in active_part_cards:
        if card['id'] != current_card_id:
            card_ts = card['last_activated'] if card['last_activated'] else "1970-01-01 00:00:00"
            # If there is another active card that was activated BEFORE the current one, FIFO was violated
            if card_ts < current_ts:
                is_fifo_followed = False
                # Mark the bypassed older card as Red
                cursor.execute("UPDATE Card SET red = 1 WHERE id = ?", (card['id'],))
                print(f"[!] FIFO Violation: Card #{card['id']} was bypassed. Marked as RED.")

    # Update or insert monthly stats (mirroring Firebase transactions)
    fifo_inc = 1 if is_fifo_followed else 0
    cursor.execute('''
        INSERT INTO FifoStats (location, part_id, month_year, total_count, fifo_count)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(location, part_id, month_year) DO UPDATE SET
            total_count = total_count + 1,
            fifo_count = fifo_count + ?
    ''', (arrival_location, part_id, month_year_str, fifo_inc, fifo_inc))

def process_event(event_json, source_logging_location):
    """
    Processes a single inventory event.

    Returns:
        (True, success_message)  -> on success
        (False, error_message)   -> on failure
    """
    ensure_schema_compatibility()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    try:
        # -------------------------------------------------------------
        # Parse JSON
        # -------------------------------------------------------------
        raw_data = json.loads(event_json)
        event = raw_data[0] if isinstance(raw_data, list) else raw_data

        part_id = event.get("Parts document ID")
        card_id = event.get("Id")
        arrival_location = event.get("ArrivalLocation")

        try:
            base_quantity = float(event.get("Qt", 0))
        except (ValueError, TypeError):
            base_quantity = 0

        if not part_id:
            raise ValueError("Missing 'Parts document ID'.")

        if not card_id:
            raise ValueError("Missing 'Id' (Card ID).")

        if not arrival_location:
            raise ValueError("Missing 'ArrivalLocation'.")

        if base_quantity <= 0:
            raise ValueError("Quantity (Qt) must be greater than zero.")

        # -------------------------------------------------------------
        # Ensure Part Exists
        # -------------------------------------------------------------
        cursor.execute(
            "SELECT id FROM Part WHERE id = ?",
            (part_id,)
        )

        part_exists = cursor.fetchone() is not None

        if not part_exists:
            cursor.execute("""
                INSERT INTO Part
                (id, name, total_quantity, location)
                VALUES (?, ?, ?, ?)
            """, (
                part_id,
                f"Part-{part_id}",
                0,
                arrival_location
            ))

        # -------------------------------------------------------------
        # Determine Event Type
        # -------------------------------------------------------------
        is_production = (
            source_logging_location == arrival_location
            
        )
        if(is_production):
            print("production")
        # -------------------------------------------------------------
        # Check Card Status
        # -------------------------------------------------------------
        cursor.execute("""
            SELECT activation
            FROM Card
            WHERE id = ?
        """, (card_id,))

        card_row = cursor.fetchone()

        card_exists = card_row is not None
        card_activation = card_row["activation"] if card_exists else None

        signed_qt = base_quantity

        if is_production:

            if card_exists and card_activation == "true":
                raise ValueError(
                    f"Card '{card_id}' is already ACTIVE."
                )

            signed_qt = base_quantity

        else:

            if card_exists and card_activation == "false":
                raise ValueError(
                    f"Card '{card_id}' is already INACTIVE."
                )

            fifo_update(
                cursor,
                card_id,
                part_id,
                arrival_location
            )

            signed_qt = -base_quantity

        # -------------------------------------------------------------
        # Update / Create Card
        # -------------------------------------------------------------
        activation_state = "true" if is_production else "false"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if card_exists:

            cursor.execute("""
                UPDATE Card
                SET
                    activation=?,
                    part_id=?,
                    last_activated=?,
                    red=CASE
                        WHEN ?='false'
                        THEN red
                        ELSE 0
                    END
                WHERE id=?
            """, (
                activation_state,
                part_id,
                now_str,
                activation_state,
                card_id
            ))

        else:

            cursor.execute("""
                INSERT INTO Card
                (
                    id,
                    activation,
                    part_id,
                    last_activated,
                    red,
                    quantity,
                    location
                )
                VALUES
                (?, ?, ?, ?, ?, ?, ?)
            """, (
                card_id,
                activation_state,
                part_id,
                now_str,
                0,
                base_quantity,
                arrival_location
            ))

        # -------------------------------------------------------------
        # Insert Event
        # -------------------------------------------------------------
        cursor.execute("""
            INSERT INTO Event
            (
                card_id,
                part_id,
                location,
                quantity,
                timestamp
            )
            VALUES
            (?, ?, ?, ?, ?)
        """, (
            card_id,
            part_id,
            arrival_location,
            signed_qt,
            now_str
        ))

        # -------------------------------------------------------------
        # Update Part Inventory
        # -------------------------------------------------------------
        cursor.execute("""
            UPDATE Part
            SET
                total_quantity = total_quantity + ?,
                location = ?
            WHERE id = ?
        """, (
            signed_qt,
            arrival_location,
            part_id
        ))

        # -------------------------------------------------------------
        # Update Active Task
        # -------------------------------------------------------------
        cursor.execute("""
            SELECT
                id,
                target,
                current
            FROM Task
            WHERE
                part_id = ?
                AND status != 'Completed'
            ORDER BY time ASC
            LIMIT 1
        """, (part_id,))

        active_task = cursor.fetchone()

        if active_task and signed_qt > 0:

            task_id = active_task["id"]
            target = active_task["target"]
            current_qty = active_task["current"]

            new_current = current_qty + signed_qt
            new_status = (
                "Completed"
                if new_current >= target
                else "In Progress"
            )

            cursor.execute("""
                UPDATE Task
                SET
                    current=?,
                    status=?
                WHERE id=?
            """, (
                new_current,
                new_status,
                task_id
            ))

        # -------------------------------------------------------------
        # Commit
        # -------------------------------------------------------------
        conn.commit()

        success_message = (
            f"Card '{card_id}' processed successfully."
        )

        print(success_message)

        return True, success_message

    except sqlite3.IntegrityError as e:

        conn.rollback()

        error_message = (
            f"Database integrity error: {str(e)}"
        )

        print(error_message)

        return False, error_message

    except ValueError as e:

        conn.rollback()

        error_message = str(e)

        print(error_message)

        return False, error_message

    except Exception as e:

        conn.rollback()

        error_message = (
            f"Unexpected error: {str(e)}"
        )

        print(error_message)

        return False, error_message

    finally:
        conn.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2:
        logging_loc = args[0]
        json_data = args[1]
        process_event(json_data, logging_loc)
    else:
        print("Usage: python events.py <SourceLocation> '<JsonData>'")
        print("Example:\npython events.py \"Warehouse\" '{\"Parts document ID\": 1, \"Id\": 101, \"ArrivalLocation\": \"AssemblyLine\", \"Qt\": 10}'")
