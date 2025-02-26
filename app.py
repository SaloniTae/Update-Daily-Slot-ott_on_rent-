# app.py
import requests
from datetime import datetime, timedelta
from flask import Flask

app = Flask(__name__)

DB_URL = "https://get-crunchy-credentials-default-rtdb.firebaseio.com"

def update_slot_times_daily():
    # ... your logic ...
    # (the code you already wrote for override / last_update checks)

@app.route("/update_slot")
def update_slot():
    update_slot_times_daily()
    return "Slot times updated!\n", 200

# if you want to test locally:
# if __name__ == "__main__":
#     app.run(host="0.0.0.0", port=5000)
