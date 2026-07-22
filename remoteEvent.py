import json
import time
import threading
import socket
import os
import requests  # Use requests library instead of calling raw Node.js/Firebase script
from evdev import InputDevice, categorize, ecodes
import RPi.GPIO as GPIO
import display
import scanData as scan_db

# --- CONFIGURATION ---
LOCATION = "FG"                       # Local logging location identifier for this workstation
HOST_PI_IP = "192.168.1.45"          # IP Address of your Host Raspberry Pi running the Flask server
HOST_PI_PORT = 5000                   # Flask API web port

# BOARD Pins (Translating previous GPIO usage to match your other script's pin mapping)
RELAY_PIN = 40    # Physical BOARD Pin 40 for the warning buzzer/relay on error

# Global State for Communication between threads
current_effect = 'idle'  # Possible states: 'idle', 'uploading', 'success', 'fail'
last_scanned_part_number = "" # Cache current part number to register to database upon upload success
last_error_message = "" # Cache descriptive failure reason from Flask API endpoint response

# Set up GPIO mode
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)

GPIO.setup(RELAY_PIN, GPIO.OUT)
GPIO.output(RELAY_PIN, GPIO.LOW)

def check_internet():
    """Checks for a basic local server/network connection by trying to reach the Host Pi."""
    try:
        # Verify network path to Host Pi port is active
        socket.create_connection((HOST_PI_IP, HOST_PI_PORT), timeout=2)
        return True
    except OSError:
        return False

def beep(duration, repeat=1, gap=0.1):
    """Utility to handle buzzer patterns (reused if buzzer/sound trigger is wired)."""
    for _ in range(repeat):
        # Triggering buzzer signals here if needed
        time.sleep(duration)
        if repeat > 1:
            time.sleep(gap)

def wrap_and_center_text(text, max_line_len=14, max_lines=3):
    """
    Splits text into up to max_lines of max_line_len characters,
    and pads each line with spaces to visually center-align it on round LCD.
    """
    words = text.split()
    lines = []
    current_line = []
    current_length = 0
    
    for word in words:
        if len(word) > max_line_len:
            word = word[:max_line_len]
            
        if current_length + len(word) + (1 if current_line else 0) <= max_line_len:
            current_line.append(word)
            current_length += len(word) + (1 if len(current_line) > 1 else 0)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_length = len(word)
            if len(lines) >= max_lines:
                break
                
    if current_line and len(lines) < max_lines:
        lines.append(" ".join(current_line))
        
    lines = lines[:max_lines]
    if not lines:
        return ""
        
    max_len = max(len(l) for l in lines)
    centered_lines = []
    for l in lines:
        diff = max_len - len(l)
        left_pad = diff // 2
        right_pad = diff - left_pad
        centered_lines.append(" " * left_pad + l + " " * right_pad)
        
    return "\n".join(centered_lines)

def clean_error_message(raw_err):
    """
    Cleans up server logs and JSON error strings so they 
    remain human-readable and fit cleanly on the round 240x240 display.
    """
    if not raw_err:
        return "  Unknown   \n   Error    \n            "
    
    # Strip unnecessary characters
    clean_msg = raw_err.strip()
    if "Error:" in clean_msg:
        clean_msg = clean_msg.split("Error:", 1)[-1].strip()
        
    return wrap_and_center_text(clean_msg, max_line_len=14, max_lines=4)

def status_manager():
    """
    Manages hardware feedback and round display UI states independently of the main scanner loop.
    """
    global current_effect, last_scanned_part_number, last_error_message
    
    network_was_down = False
    
    while True:
        # 1. Connectivity Status Check to Host Pi
        if not check_internet():
            if not network_was_down:
                print(f"[System] Host server {HOST_PI_IP}:{HOST_PI_PORT} offline. Displaying network block...")
                display.display_content(
                    content="om.jpg",
                    color="yellow",
                    bgcolor="black"
                )
                network_was_down = True
            time.sleep(2)
            continue
            
        # Restore screen lock if connection returns
        if network_was_down:
            print("[System] Host server online! Restoring background UI...")
            display.override_active = False 
            display.display_page(display.current_page) 
            network_was_down = False

        # 2. State-based Feedback UI Loop
        if current_effect == 'uploading':
            display.display_content(
                content="Uploading\nData...",
                color="cyan",
                bgcolor="#020813",
                font_size=28
            )
            beep(0.05, repeat=1)
            time.sleep(0.3) 
            
        elif current_effect == 'success':
            if last_scanned_part_number:
                print(f"[System] Success! Registering scan locally for part: {last_scanned_part_number}")
                scan_db.register_successful_scan(last_scanned_part_number)
                last_scanned_part_number = ""

            display.display_content(
                content="Upload\nSuccessful!",
                color="#00FF00", 
                bgcolor="#0A0F1D",
                font_size=28
            )
            time.sleep(2)
            current_effect = 'idle'
            
        elif current_effect == 'fail':
            # Activate error output indicator (Relay / Buzzer)
            GPIO.output(RELAY_PIN, GPIO.HIGH)
            
            print(f"[Display] Showing centered error:\n{last_error_message}")
            display.display_content(
                content=f"UPLOAD FAILED\n\n{last_error_message}",
                color="#FF3B30", 
                bgcolor="#1C0D0D",
                font_size=18,
                set_override=True 
            )
            
            print("[System] Failure registered. Relay Pin is ACTIVE. Waiting for button reset or new scan...")
            while display.override_active and current_effect == 'fail':
                time.sleep(0.1)
            
            GPIO.output(RELAY_PIN, GPIO.LOW)
            print("[System] Reset detected. Relay deactivated.")
            
            if current_effect == 'fail':
                current_effect = 'idle'
            
        else:
            GPIO.output(RELAY_PIN, GPIO.LOW)
            time.sleep(0.1)

def run_http_upload(data_str):
    """
    Submits the scanned raw barcode JSON over Wi-Fi directly to the Host Pi Flask API.
    Replaces old subprocess.run(['node', ...]) mechanism.
    """
    global current_effect, last_scanned_part_number, last_error_message
    
    # 1. PRE-VALIDATION: Ensure it's valid JSON
    try:
        parsed_json = json.loads(data_str)
    except json.JSONDecodeError:
        print(f"[Error] Invalid JSON received from scanner: {data_str}")
        last_error_message = wrap_and_center_text("Invalid Barcode JSON", max_line_len=14, max_lines=3)
        current_effect = 'fail'
        return

    # 2. Extract part/card identifier to track locally on client display
    part_number = None
    try:
        if isinstance(parsed_json, dict):
            for key in ["Parts document ID", "partNumber", "part_id", "barcode", "Id"]:
                if key in parsed_json:
                    part_number = str(parsed_json[key]).strip()
                    break
        else:
            part_number = str(parsed_json).strip()
    except Exception as e:
        print(f"[Parser Warning] Failed to parse part number: {e}")
        part_number = None

    if not part_number:
        part_number = data_str.strip()

    last_scanned_part_number = part_number
    print(f"[System] Parsed Identifier: {last_scanned_part_number}")

    print(f"[System] Starting upload to Host Pi ({HOST_PI_IP})...")
    current_effect = 'uploading'
    
    # Pack payload for Flask server processing endpoint
    url = f"http://{HOST_PI_IP}:{HOST_PI_PORT}/api/events/run"
    payload = {
        "source_location": LOCATION,
        "event_json": data_str
    }
    
    try:
        # HTTP POST transmission over network
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            print("[Success] Upload complete. Server accepted transaction.")
            current_effect = 'success'
        else:
            # Handle rejection (e.g. "Card already active", "FIFO Violation")
            try:
                res_data = response.json()
                server_err = res_data.get('error', f"HTTP Error {response.status_code}")
            except Exception:
                server_err = response.text or f"HTTP Error {response.status_code}"
                
            last_error_message = clean_error_message(server_err)
            print(f"[Error] Server Rejection: {server_err}")
            current_effect = 'fail'
            
    except requests.exceptions.RequestException as e:
        print(f"[Network Error] Connection failed: {e}")
        last_error_message = wrap_and_center_text("Host Unreachable", max_line_len=14, max_lines=3)
        current_effect = 'fail'

def main():
    print("Initializing display via display module...")
    display.init_display()

    # Pre-populate registered model pages in display buffer
    scan_db.update_display_pages()

    # Present initial background page layout
    display.display_page(1)
    time.sleep(1)

    # Start independent status feedback monitor thread
    threading.Thread(target=status_manager, daemon=True).start()

    print(f"--- Remote Scanner Active [Logging Location: {LOCATION}] ---")
    
    try:
        scanner = InputDevice(DEVICE_PATH)
        scanner.grab() # Take exclusive input focus
        
        # Keyboard scancode mapping rules (Standard US Layout)
        map_low = {2: u'1', 3: u'2', 4: u'3', 5: u'4', 6: u'5', 7: u'6', 8: u'7', 9: u'8', 10: u'9', 11: u'0', 30: u'a', 31: u's', 32: u'd', 33: u'f', 34: u'g', 35: u'h', 36: u'j', 37: u'k', 38: u'l', 44: u'z', 45: u'x', 46: u'c', 47: u'v', 48: u'b', 49: u'n', 50: u'm', 16: u'q', 17: u'w', 18: u'e', 19: u'r', 20: u't', 21: u'y', 22: u'u', 23: u'i', 24: u'o', 25: u'p', 12: u'-', 13: u'=', 26: u'[', 27: u']', 39: u';', 40: u"'", 41: u'`', 43: u'\\', 51: u',', 52: u'.', 53: u'/', 57: u' '}
        map_high = {2: u'!', 3: u'@', 4: u'#', 5: u'$', 6: u'%', 7: u'^', 8: u'&', 9: u'*', 10: u'(', 11: u')', 30: u'A', 31: u'S', 32: u'D', 33: u'F', 34: u'G', 35: u'H', 36: u'J', 37: u'K', 38: u'L', 44: u'Z', 45: u'X', 46: u'C', 47: u'V', 48: u'B', 49: u'N', 50: u'M', 16: u'Q', 17: u'W', 18: u'E', 19: u'R', 20: u'T', 21: u'Y', 22: u'U', 23: u'I', 24: u'O', 25: u'P', 12: u'_', 13: u'+', 26: u'{', 27: u'}', 39: u':', 40: u'"', 41: u'~', 43: u'|', 51: u'<', 52: u'>', 53: u'?', 57: u' '}

        barcode_chars = []
        shift_active = False
        
        for event in scanner.read_loop():
            if event.type == ecodes.EV_KEY:
                key_event = categorize(event)
                
                if key_event.scancode in [42, 54]:
                    shift_active = (key_event.keystate != 0)
                    continue

                if key_event.keystate == 1: 
                    code = key_event.scancode
                    if code == 28: # Enter key detected
                        full_string = "".join(barcode_chars)
                        full_string = "".join(filter(lambda x: x.isprintable(), full_string))
                        
                        if full_string:
                            print(f"[Captured Scan] {full_string}")
                            # Execute upload to Host Pi in the background
                            threading.Thread(target=run_http_upload, args=(full_string,)).start()
                            barcode_chars = []
                    else:
                        char = map_high.get(code) if shift_active else map_low.get(code)
                        if char:
                            barcode_chars.append(char)

    except KeyboardInterrupt:
        print("\n[System] Cleaning up and exiting...")
    finally:
        try: 
            scanner.ungrab()
        except: 
            pass
        GPIO.cleanup()

if __name__ == "__main__":
    main()
