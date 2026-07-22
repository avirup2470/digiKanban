import json
import time
import threading
import socket
import os
import requests


HOST_PI_IP="10.108.216.54"
HOST_PI_PORT="5000"
url = f"http://{HOST_PI_IP}:{HOST_PI_PORT}/api/events/run"
LOCATION="FG"
data_str="""{"Parts document ID":"S107134100","Id":"9IYXdIZ6QZZ8jHOKIyL2","ArrivalLocation":"SA","Type":"FG","Qt":24}"""
payload = {
    "source_location": LOCATION,
    "event_json": data_str
    }


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
        
    return clean_msg


def main():
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
        last_error_message ="Host Unreachable"
        current_effect = 'fail'

if __name__ == "__main__":
    main()