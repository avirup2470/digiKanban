

HOST_PI_IP="10.127.38.54"
HOST_PI_PORT="5000"
url = f"http://{HOST_PI_IP}:{HOST_PI_PORT}/api/events/run"
LOCATION="FG"
data_str="""{"Parts document ID":"S107134100","Id":"9IYXdIZ6QZZ8jHOKIyL2","ArrivalLocation":"SA","Type":"FG","Qt":24}"""
payload = {
    "source_location": LOCATION,
    "event_json": data_str
    }