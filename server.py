import sqlite3
import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
DB_PATH = 'inventory.db'
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
def get_db_connection():
    """
    Establishes an SQLite connection with active foreign key tracking.
    This guarantees referential integrity between Parts, Cards, and Events.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def check_and_apply_db_updates():
    """
    Initializes the local SQLite database schema. Sets up matching tables
    and structures that mirror the previous Firebase Firestore schema layout.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Part Table (Tracks master record of materials/parts in the facility)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Part (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            total_quan INTEGER DEFAULT 0,
            location TEXT,
            fifo TEXT DEFAULT 'FIFO'
        )
    ''')
    
    # 2. Card Table (Represents physical Kanban cards mapped to active material units)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Card (
            id INTEGER PRIMARY KEY,
            created_on DATETIME DEFAULT CURRENT_TIMESTAMP,
            location TEXT,
            activation TEXT DEFAULT 'false',
            part_id INTEGER,
            quantity INTEGER,
            trolly_no TEXT,
            trolly_type TEXT,
            red INTEGER DEFAULT 0,            -- 1 if flagged as a FIFO violation
            last_activated DATETIME,          -- Keeps track of the most recent scan timestamp
            FOREIGN KEY (part_id) REFERENCES Part(id) ON DELETE CASCADE
        )
    ''')
    
    # 3. Event Table (Auditable historical ledger of transaction scans)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER,
            part_id INTEGER,
            location TEXT,
            quantity REAL,
            source_location TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (card_id) REFERENCES Card(id) ON DELETE SET NULL,
            FOREIGN KEY (part_id) REFERENCES Part(id) ON DELETE CASCADE
        )
    ''')
    
    # 4. FifoStats Table (Offline tracking of monthly warehouse metrics by location/part)
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
    
    # 5. Task Table (Manages active manufacturing operations and production runs)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Task (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time DATETIME DEFAULT CURRENT_TIMESTAMP,
            part_id INTEGER,
            target INTEGER NOT NULL,
            initial INTEGER DEFAULT 0,
            current INTEGER DEFAULT 0,
            priority TEXT,
            status TEXT DEFAULT 'Pending',
            FOREIGN KEY (part_id) REFERENCES Part(id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()

def calculate_fifo_audit(cursor, current_card_id, part_id, arrival_location):
    """
    Ported Firebase Node.js FIFO tracking mechanism.
    Analyzes active tracking card records for this part, updates Red warning flags
    for bypassed containers, and logs accuracy statistics.
    """
    now = datetime.now()
    month_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    # Formats month and year to match previous Firebase key schema (e.g., "July26")
    month_year_str = f"{month_names[now.month - 1]}{str(now.year)[-2:]}" 

    # Retrieve all currently active cards assigned to this part
    cursor.execute('''
        SELECT id, last_activated FROM Card 
        WHERE part_id = ? AND activation = 'true'
    ''', (part_id,))
    active_cards = [dict(row) for row in cursor.fetchall()]

    current_card_data = None
    for card in active_cards:
        if card['id'] == current_card_id:
            current_card_data = card
            break

    if not current_card_data:
        return

    # Helper function to convert SQLite date strings back to unix milliseconds
    def to_millis(dt_str):
        if not dt_str:
            return 0
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            return int(dt.timestamp() * 1000)
        except Exception:
            return 0

    current_timestamp = to_millis(current_card_data['last_activated']) if current_card_data['last_activated'] else float('inf')
    is_fifo_followed = True

    # Audit all active cards of this part against the currently selected card
    for card in active_cards:
        card_timestamp = to_millis(card['last_activated']) if card['last_activated'] else 0
        # If another card has been active LONGER (older timestamp) and was bypassed, flag it
        if card['id'] != current_card_id and card_timestamp < current_timestamp:
            is_fifo_followed = False
            cursor.execute("UPDATE Card SET red = 1 WHERE id = ?", (card['id'],))

    # Increment stats (upsert logic to monthly location tracker)
    fifo_inc = 1 if is_fifo_followed else 0
    cursor.execute('''
        INSERT INTO FifoStats (location, part_id, month_year, total_count, fifo_count)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(location, part_id, month_year) DO UPDATE SET
            total_count = total_count + 1,
            fifo_count = fifo_count + ?
    ''', (arrival_location, part_id, month_year_str, fifo_inc, fifo_inc))


# --- WEB ROUTING ---

@app.route('/')
def serve_index():
    """Serves the front-end dashboard interface."""
    return send_from_directory(BASE_DIR,'index.html')

@app.route('/public/<path:filename>')
def serve_public(filename):
    """Serves static client assets."""
    return send_from_directory('public', filename)
@app.route('/inventory.db')
def serve_db():
    return send_from_directory('.', 'inventory.db', mimetype='application/x-sqlite3')

# --- REST API ENDPOINTS ---

@app.route('/api/parts', methods=['GET'])
def api_get_parts():
    """Returns detailed information about all registered parts."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, description, total_quan, location, fifo FROM Part ORDER BY id ASC")
    parts = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(parts)

@app.route('/api/cards', methods=['GET'])
def api_get_cards():
    """Returns dynamic details of all active tracking cards."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, part_id, activation, quantity, location, red, last_activated FROM Card ORDER BY id ASC")
    cards = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(cards)

@app.route('/api/fifo-stats', methods=['GET'])
def api_get_fifo_stats():
    """Returns monthly locations-based FIFO metric percentages."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT location, part_id, month_year, total_count, fifo_count FROM FifoStats ORDER BY month_year DESC")
    stats = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(stats)

@app.route('/api/events-logs', methods=['GET'])
def api_get_events_logs():
    """Returns historically logged scan events (recent 50 entries)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, card_id, part_id, location, quantity, source_location, timestamp FROM Event ORDER BY timestamp DESC LIMIT 50")
    logs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(logs)


@app.route('/api/events/upload', methods=['POST'])
def api_process_event():
    """
    Unified JSON Event processor.
    Accepts incoming payload scans from workstation scanners (remoteEvent.py), 
    applies business logic, checks FIFO rules, and commits transactions to SQLite.
    """
    data = request.json or {}
    source_logging_location = data.get('source_location')
    event_json = data.get('event_json')

    if not source_logging_location or not event_json:
        return jsonify({"error": "Missing 'source_location' or 'event_json' parameters"}), 400

    try:
        raw_data = json.loads(event_json)
        event = raw_data[0] if isinstance(raw_data, list) else raw_data

        part_id = event.get('Parts document ID')
        card_id = event.get('Id')
        arrival_location = event.get('ArrivalLocation')
        
        try:
            base_quantity = float(event.get('Qt', 0))
        except (ValueError, TypeError):
            base_quantity = 0.0

        if not part_id or not card_id or not arrival_location or base_quantity <= 0:
            return jsonify({"error": "Invalid Event Data fields. Verify 'Parts document ID', 'Id', 'ArrivalLocation', and 'Qt'."}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # Step 1: Handle Parent table dependencies. Create part if auto-discovered on scan.
        cursor.execute("SELECT id FROM Part WHERE id = ?", (part_id,))
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO Part (id, name, total_quan, location)
                VALUES (?, ?, 0, ?)
            ''', (part_id, f"Part-{part_id}", arrival_location))

        is_production = (source_logging_location == arrival_location)
        
        cursor.execute("SELECT activation FROM Card WHERE id = ?", (card_id,))
        card_row = cursor.fetchone()
        card_exists = card_row is not None
        card_activation = card_row[0] if card_exists else None

        signed_qt = base_quantity
        if is_production:
            if card_exists and card_activation == 'true':
                conn.close()
                return jsonify({"error": f"Card ID {card_id} is already active."}), 400
            signed_qt = base_quantity
        else:
            if card_exists and card_activation == 'false':
                conn.close()
                return jsonify({"error": f"Card ID {card_id} is already inactive."}), 400
            
            # Execute physical FIFO audit calculations
            calculate_fifo_audit(cursor, card_id, part_id, arrival_location)
            signed_qt = -base_quantity

        # Step 2: Establish current Card state parameters
        activation_state = 'true' if is_production else 'false'
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if card_exists:
            # Re-entering production resets local Red Warnings
            cursor.execute('''
                UPDATE Card 
                SET activation = ?, part_id = ?, last_activated = ?, red = CASE WHEN ? = 'false' THEN red ELSE 0 END, location = ?
                WHERE id = ?
            ''', (activation_state, part_id, now_str, activation_state, arrival_location, card_id))
        else:
            cursor.execute('''
                INSERT INTO Card (id, activation, part_id, last_activated, red, quantity, location)
                VALUES (?, ?, ?, ?, 0, ?, ?)
            ''', (card_id, activation_state, part_id, now_str, base_quantity, arrival_location))

        # Step 3: Insert Entry into Event Logs Ledger (Child table)
        cursor.execute('''
            INSERT INTO Event (card_id, part_id, location, quantity, source_location, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (card_id, part_id, arrival_location, signed_qt, source_logging_location, now_str))

        # Step 4: Perform final inventory transaction balance adjustments
        cursor.execute('''
            UPDATE Part 
            SET total_quan = total_quan + ?, location = ?
            WHERE id = ?
        ''', (signed_qt, arrival_location, part_id))

        conn.commit()
        conn.close()
        return jsonify({"message": f"SUCCESS: Event processed successfully for Card #{card_id}"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Ensure database is configured with correct tables upon startup
    check_and_apply_db_updates()
    
    print("==================================================")
    print("Starting Raspberry Pi Webserver (JSON Event Hub)...")
    print("Dashboard UI: http://localhost:5000")
    print("==================================================")
    
    # Run server listening on port 5000 from any incoming local network IP
    app.run(host='0.0.0.0', port=5000, debug=True)
