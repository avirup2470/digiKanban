import sqlite3

DB_PATH = 'inventory.db'

def add_part(id,name, description, initial_qty=0, location=None, fifo=100):
    """Inserts a new part into the Part table."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    cursor.execute('''
        INSERT INTO Part (id,name, description, total_quantity, location, fifo)
        VALUES (?,?, ?, ?, ?, ?)
    ''', (id, name, description, initial_qty, location, fifo))
    

    conn.commit()
    conn.close()
    print(f"Part '{name}' created successfully with ID: {id}")
    return id

def create_card(part_id, quantity, location, activation='false', trolly_no=None, trolly_type=None):
    """Creates a tracking card bound to a specific part, initializing FIFO flags."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    try:
        cursor.execute('''
            INSERT INTO Card (part_id, quantity, location, activation, trolly_no, trolly_type, red)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        ''', (part_id, quantity, location, activation, trolly_no, trolly_type))
        
        card_id = cursor.lastrowid
        conn.commit()
        print(f"Card created successfully with ID: {card_id} for Part ID: {part_id}")
        return card_id
    except sqlite3.IntegrityError as e:
        print(f"Error creating card: {e} (Does Part ID {part_id} exist?)")
        return None
    finally:
        conn.close()

def create_task(part_id, target, initial=0, priority='Normal'):
    """Sets a production task target for a part."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    try:
        cursor.execute('''
            INSERT INTO Task (part_id, target, initial, current, priority, status)
            VALUES (?, ?, ?, ?, ?, 'Pending')
        ''', (part_id, target, initial, initial, priority))
        
        task_id = cursor.lastrowid
        conn.commit()
        print(f"Task #{task_id} added: Target of {target} units for Part ID: {part_id}")
        return task_id
    except sqlite3.IntegrityError as e:
        print(f"Error creating task: {e} (Does Part ID {part_id} exist?)")
        return None
    finally:
        conn.close()

def get_current_status():
    """Prints out the complete current state of parts, tracking cards, tasks, and FIFO metrics."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("\n" + "="*50)
    print("                 CURRENT SYSTEM STATUS           ")
    print("="*50)

    # 1. Parts
    print("\n--- CURRENT PART STOCK ---")
    cursor.execute("SELECT id, name, total_quan, location FROM Part")
    for row in cursor.fetchall():
        print(f"ID {row[0]}: {row[1]} | Qty: {row[2]} | Location: {row[3]}")
        
    # 2. Tracking Cards (including Red Status)
    print("\n--- TRACKING CARDS ---")
    cursor.execute("SELECT id, part_id, activation, quantity, red, last_activated FROM Card")
    for row in cursor.fetchall():
        status = "ACTIVE" if row[2] == 'true' else "INACTIVE"
        alert = "⚠️ [RED FLAG - FIFO BYPASSED]" if row[4] == 1 else "✅ [OK]"
        last_act = row[5] if row[5] else "Never"
        print(f"Card #{row[0]} (Part {row[1]}) | State: {status} | Qty: {row[3]} | Last Act: {last_act} | FIFO: {alert}")

    # 3. Tasks
    print("\n--- ACTIVE PRODUCTION TASKS ---")
    cursor.execute('''
        SELECT Task.id, Part.name, Task.target, Task.current, Task.status, Task.priority 
        FROM Task 
        JOIN Part ON Task.part_id = Part.id
        WHERE Task.status != 'Completed'
    ''')
    active_tasks = cursor.fetchall()
    if not active_tasks:
         print("No active/pending tasks.")
    for row in active_tasks:
        print(f"Task #{row[0]} ({row[5]} priority) | Part: {row[1]} | Progress: {row[3]}/{row[2]} | Status: {row[4]}")
        
    # 4. Monthly FIFO Performance stats
    print("\n--- MONTHLY FIFO STATISTICS ---")
    cursor.execute('''
        SELECT FifoStats.location, Part.name, FifoStats.month_year, FifoStats.total_count, FifoStats.fifo_count 
        FROM FifoStats 
        JOIN Part ON FifoStats.part_id = Part.id
    ''')
    stats = cursor.fetchall()
    if not stats:
        print("No FIFO transactions logged yet.")
    for row in stats:
        loc, name, period, total, success = row
        rate = (success / total) * 100 if total > 0 else 0.0
        print(f"Loc: {loc} | Part: {name} | Period: {period} | Accuracy: {success}/{total} ({rate:.1f}%)")
        
    print("="*50 + "\n")
    conn.close()

if __name__ == "__main__":
    print("Database utility operations loaded.")
    get_current_status()
