import requests
from datetime import datetime, timedelta
from flask import Flask
import pytz

app = Flask(__name__)

DB_URL = "https://get-crunchy-credentials-default-rtdb.firebaseio.com/"

# 1) Define the IST timezone
ist = pytz.timezone("Asia/Kolkata")

def parse_ist(dt_str: str) -> datetime:
    """
    Parse a string like '2025-02-26 13:45:00' as IST-aware datetime.
    """
    # Step A: parse as naive
    naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    # Step B: localize to IST
    return ist.localize(naive)

def format_ist(dt_aware: datetime) -> str:
    """
    Convert an IST-aware datetime back to a string 'YYYY-MM-DD HH:MM:SS'.
    """
    return dt_aware.strftime("%Y-%m-%d %H:%M:%S")

def update_slot_times_daily():
    """
    Runs the daily slot logic. 
    We interpret all times in IST to match your 'YYYY-MM-DD HH:MM:SS' naive strings in DB.
    """
    # Current time in IST
    now = datetime.now(ist)
    
    # 1) Fetch settings from DB, including last_update
    settings_resp = requests.get(DB_URL + "settings.json")
    if settings_resp.status_code == 200 and settings_resp.json():
        data = settings_resp.json()
        
        # Get override
        override = data.get("override", False)
        
        # Get last_update from DB (string). If missing, treat now as last_update
        last_update_str = data.get("last_update", "")
        if last_update_str:
            try:
                last_update_dt = parse_ist(last_update_str)
            except ValueError:
                # If parsing fails, fallback to now
                last_update_dt = now
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
            try:
                slot_start_dt = parse_ist(slot_start_str)
            except ValueError:
                # fallback to 9 AM if parsing fails
                slot_start_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            # fallback to 9 AM if missing
            slot_start_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)

        if override:
            print("Override is true, continuing from existing slot times.")
            # shift slot_start_dt by 1 day
            next_slot_start = slot_start_dt + timedelta(days=1)
            next_slot_end   = next_slot_start + timedelta(days=1)

            new_data = {
                "slot_start": format_ist(next_slot_start),   # store as 'YYYY-MM-DD HH:MM:SS' (IST)
                "slot_end":   format_ist(next_slot_end),
                "override":   True,
                # Update last_update to now in IST
                "last_update": format_ist(now)
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
                "slot_start": format_ist(next_slot_start),
                "slot_end":   format_ist(next_slot_end),
                "override":   False,
                "last_update": format_ist(now)
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
    # This route is triggered by Cron-Job.org or manual GET
    update_slot_times_daily()
    return "Slot times updated!\n", 200
