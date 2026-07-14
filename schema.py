import sqlite3

def init_db(db_path='inventory.db'):
    """
    Initializes the SQLite database with the tables defined in the schemas:
    - Part
    - Card
    - Event
    - Task
    - FifoStats (New: Added for offline FIFO tracking metrics)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # CRITICAL: SQLite requires explicit activation of foreign key constraints
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    # 1. Create Part Table
    # Schema properties: id, name, description, total_quan, location, fifo
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Part (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            total_quantity INTEGER DEFAULT 0,
            location TEXT,
            fifo INTEGER DEFAULT 1
        )
    ''')
    
    # 2. Create Card Table
    # Schema properties: id, created_on, location, activation, part_id, quantity, trolly_no, trolly_type, red, last_activated
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Card (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_on DATETIME DEFAULT CURRENT_TIMESTAMP,
            location TEXT,
            activation TEXT DEFAULT 'false',
            part_id INTEGER,
            quantity INTEGER,
            trolly_no TEXT,
            trolly_type TEXT,
            red INTEGER DEFAULT 0,            -- 1 if a FIFO rule bypass occurred
            last_activated DATETIME,          -- Records the most recent activation time
            FOREIGN KEY (part_id) REFERENCES Part(id) ON DELETE CASCADE
        )
    ''')
    
    # 3. Create Event Table (Sanitized version from drawing)
    # Schema properties: id, card_id, part_id, location, quantity, timestamp
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER,
            part_id TEXT,
            location TEXT,
            quantity INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (card_id) REFERENCES Card(id) ON DELETE SET NULL,
            FOREIGN KEY (part_id) REFERENCES Part(id) ON DELETE CASCADE
        )
    ''')
    
    # 4. Create Task Table
    # Schema properties: id, time, part_id, target, initial, current, priority, status
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Task (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time DATETIME DEFAULT CURRENT_TIMESTAMP,
            part_id TEXT,
            target INTEGER NOT NULL,
            initial INTEGER DEFAULT 0,
            current INTEGER DEFAULT 0,
            priority TEXT,
            status TEXT DEFAULT 'Pending',
            FOREIGN KEY (part_id) REFERENCES Part(id) ON DELETE CASCADE
        )
    ''')

    # 5. Create FifoStats Table (Offline tracking of monthly warehouse metrics)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS FifoStats (
            location TEXT,
            part_id TEXT,
            month_year TEXT,
            total_count INTEGER DEFAULT 0,
            fifo_count INTEGER DEFAULT 0,
            PRIMARY KEY (location, part_id, month_year),
            FOREIGN KEY (part_id) REFERENCES Part(id) ON DELETE CASCADE
        )
    ''')
    
    conn.commit()
    conn.close()
    print(f"Database successfully initialized with FIFO tracking support at: {db_path}")

if __name__ == "__main__":
    init_db()
