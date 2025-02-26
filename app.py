import requests
from datetime import datetime, timedelta
from flask import Flask

app = Flask(__name__)

DB_URL = "https://get-crunchy-credentials-default-rtdb.firebaseio.com"

def update_slot_times_daily():
    # This code block is indented
    now = datetime.now()
    # All your existing code inside this function
   
    # 1) Fetch settings from DB, including last_update
    settings_resp = requests.get(DB_URL + "settings.json")
    if settings_resp.status_code == 200 and settings_resp.json():
        data = settings_resp.json()
        
        # Get override
        override = data.get("override", False)
        
        # Get last_update from DB (string). If missing, treat now as last_update
        last_update_str = data.get("last_update", "")
        if last_update_str:
            last_update_dt = datetime.strptime(last_update_str, "%Y-%m-%d %H:%M:%S")
        else:
            last_update_dt = now  # fallback if missing

        # 2) Check if 24 hours have passed since last_update
        delta = now - last_update_dt
        if delta < timedelta(hours=24):
            print(f"Only {delta} since last update; not 24h yet. Skipping shift.")
            return  # No daily shift yet

        # 3) If 24 hours or more, do your existing override logic
        print(f"24h+ since last update. Proceeding with slot shift. override={override}")

        slot_start_str = data.get("slot_start", "")
        if slot_start_str:
            slot_start_dt = datetime.strptime(slot_start_str, "%Y-%m-%d %H:%M:%S")
        else:
            # fallback to 9 AM if missing
            slot_start_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)

        if override:
            print("Override is true, continuing from existing slot times.")
            # shift slot_start_dt by 1 day
            next_slot_start = slot_start_dt + timedelta(days=1)
            next_slot_end   = next_slot_start + timedelta(days=1)

            new_data = {
                "slot_start": next_slot_start.strftime("%Y-%m-%d %H:%M:%S"),
                "slot_end":   next_slot_end.strftime("%Y-%m-%d %H:%M:%S"),
                "override":   True,
                # NEW: Update last_update to now
                "last_update": now.strftime("%Y-%m-%d %H:%M:%S")
            }
            patch_resp = requests.patch(DB_URL + "settings.json", json=new_data)
            if patch_resp.status_code == 200:
                print("Continuing slot times from DB for next day (override=true).")
            else:
                print("Failed to update slot times:", patch_resp.text)
            return
        else:
            print("override=false -> forcibly set 9 AM -> 9 AM.")
            next_slot_start = slot_start_dt + timedelta(days=1)
            next_slot_end   = next_slot_start + timedelta(days=1)

            new_data = {
                "slot_start": next_slot_start.strftime("%Y-%m-%d %H:%M:%S"),
                "slot_end":   next_slot_end.strftime("%Y-%m-%d %H:%M:%S"),
                "override":   False,
                # NEW: Update last_update to now
                "last_update": now.strftime("%Y-%m-%d %H:%M:%S")
            }
            patch_resp = requests.patch(DB_URL + "settings.json", json=new_data)
            if patch_resp.status_code == 200:
                print("Updated slot times to next day (override=false).")
            else:
                print("Failed to update slot times:", patch_resp.text)
    else:
        print("No settings found or request error.")
   
    
    
    print("Slot times updated or skipped...")

@app.route("/update_slot")
def update_slot():
    # A separate function, not indented under update_slot_times_daily
    update_slot_times_daily()
    return "Slot times updated!\n", 200
