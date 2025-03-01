import os
import requests
from datetime import datetime, timedelta
import pytz

from flask import Flask, request, jsonify

app = Flask(__name__)

# -----------------------------------------------------------------
# Environment variables for your real DB URL & proxy secret token
# (Set these in Render or your hosting environment)
# -----------------------------------------------------------------
REAL_DB_URL   = os.getenv("REAL_DB_URL", "")
PROXY_SECRET  = os.getenv("PROXY_SECRET", "")

# IST timezone
ist = pytz.timezone("Asia/Kolkata")

def parse_ist(dt_str: str):
    naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return ist.localize(naive)

def format_ist(dt_aware: datetime) -> str:
    return dt_aware.strftime("%Y-%m-%d %H:%M:%S")

# ------------------------------------------------
# Check if node is shaped like a credential
# ------------------------------------------------
def is_credential(node):
    if not isinstance(node, dict):
        return False
    required = ["email","password","expiry_date","locked","usage_count","max_usage"]
    return all(r in node for r in required)

# ------------------------------------------------
# Lock all credentials (locked=0) except locked=2
# ------------------------------------------------
def lock_all_except_2():
    resp = requests.get(REAL_DB_URL + ".json")
    if resp.status_code == 200 and resp.json():
        all_data = resp.json()
        locked_count = 0

        for key, node in all_data.items():
            if not is_credential(node):
                continue
            locked_val = int(node.get("locked", 0))

            # locked=2 => skip
            # locked=1 => already locked
            if locked_val == 0:
                patch_url  = REAL_DB_URL + f"/{key}.json"
                patch_data = {"locked": 1}
                patch_resp = requests.patch(patch_url, json=patch_data)
                if patch_resp.status_code == 200:
                    locked_count += 1

        print(f"Locked {locked_count} credentials.")
    else:
        print("Failed to fetch credentials for locking.")

# ------------------------------------------------
# SHIFT each slot's time if 24h passed since last_update
# daily => +1 day, weekly => +7 days
# ------------------------------------------------
def update_slot_times_daily():
    now_ist = datetime.now(ist)

    # 1) Fetch settings node
    settings_resp = requests.get(REAL_DB_URL + "settings.json")
    if settings_resp.status_code != 200 or not settings_resp.json():
        print("No settings found or request error.")
        return

    settings_data = settings_resp.json()
    # e.g. "slots": { "slot_1": {...}, "slot_2": {...}, "slot_3": {...} }
    all_slots = settings_data.get("slots", {})

    any_slot_shifted = False

    # 2) For each slot_{n}, check if enabled & if 24h have passed => shift
    for slot_id, slot_info in all_slots.items():
        if not isinstance(slot_info, dict):
            continue
        if not slot_info.get("enabled", False):
            continue

        # if you also want to handle 'override' or skip logic, do it here
        override = slot_info.get("override", False)

        last_update_str = slot_info.get("last_update", "")
        if last_update_str:
            try:
                last_update_dt = parse_ist(last_update_str)
            except ValueError:
                last_update_dt = now_ist
        else:
            last_update_dt = now_ist

        delta = now_ist - last_update_dt
        if delta < timedelta(hours=24):
            print(f"Slot {slot_id}: Only {delta} since last update; not 24h yet. Skipping shift.")
            continue

        print(f"Slot {slot_id}: 24h+ since last update. override={override}")

        slot_start_str = slot_info.get("slot_start","")
        if slot_start_str:
            try:
                slot_start_dt = parse_ist(slot_start_str)
            except ValueError:
                slot_start_dt = now_ist.replace(hour=9,minute=0,second=0,microsecond=0)
        else:
            slot_start_dt = now_ist.replace(hour=9,minute=0,second=0,microsecond=0)

        slot_end_str = slot_info.get("slot_end","")
        if slot_end_str:
            try:
                slot_end_dt = parse_ist(slot_end_str)
            except ValueError:
                slot_end_dt = slot_start_dt + timedelta(days=1)
        else:
            slot_end_dt = slot_start_dt + timedelta(days=1)

        # frequency => daily or weekly
        freq = slot_info.get("frequency","daily")
        if freq.lower() == "weekly":
            shift_delta = timedelta(days=7)
        else:
            shift_delta = timedelta(days=1)

        # SHIFT by shift_delta
        next_slot_start = slot_start_dt + shift_delta
        next_slot_end   = slot_end_dt   + shift_delta

        # Update the slot in memory
        slot_info["slot_start"]  = format_ist(next_slot_start)
        slot_info["slot_end"]    = format_ist(next_slot_end)
        slot_info["last_update"] = format_ist(now_ist)
        # keep override as is or do your logic

        any_slot_shifted = True
        print(f"Slot {slot_id} SHIFTED -> start={slot_info['slot_start']} end={slot_info['slot_end']} freq={freq}")

    # 3) If we updated any slot, patch them back to DB & lock credentials
    if any_slot_shifted:
        patch_resp = requests.patch(REAL_DB_URL + "settings.json", json={"slots": all_slots})
        if patch_resp.status_code == 200:
            print("Slots shift successful for changed slots.")
            lock_all_except_2()
        else:
            print("Failed to update slots in DB:", patch_resp.text)
    else:
        print("No slot was shifted. No changes made.")

# ------------------------------------------------
# /update_slot => triggers daily shifting for each slot
# ------------------------------------------------
@app.route("/update_slot")
def update_slot():
    update_slot_times_daily()
    return "Slot times updated!\n", 200

# ------------------------------------------------
# /lock_check => check each slot's slot_end - 2min => lock
# ------------------------------------------------
@app.route("/lock_check")
def lock_check():
    now_ist = datetime.now(ist)
    settings_resp = requests.get(REAL_DB_URL + "settings.json")
    if settings_resp.status_code != 200 or not settings_resp.json():
        return "No settings or request error.\n", 200

    settings_data = settings_resp.json()
    all_slots = settings_data.get("slots", {})

    margin = timedelta(minutes=2)
    locked_count_total = 0

    for slot_id, slot_info in all_slots.items():
        if not isinstance(slot_info, dict):
            continue
        if not slot_info.get("enabled", False):
            continue

        slot_end_str = slot_info.get("slot_end","9999-12-31 09:00:00")
        try:
            slot_end_dt = parse_ist(slot_end_str)
        except ValueError:
            # skip invalid
            continue

        # if now >= slot_end_dt - margin => lock
        if now_ist >= (slot_end_dt - margin):
            # Lock all credentials with locked=0
            lock_resp = requests.get(REAL_DB_URL + ".json")
            if lock_resp.status_code == 200 and lock_resp.json():
                all_data = lock_resp.json()
                for key, node in all_data.items():
                    if not is_credential(node):
                        continue
                    locked_val = int(node.get("locked",0))
                    if locked_val == 0:
                        patch_url  = REAL_DB_URL + f"/{key}.json"
                        patch_data = {"locked":1}
                        p = requests.patch(patch_url, json=patch_data)
                        if p.status_code == 200:
                            locked_count_total += 1
            # else skip
    return f"Locked {locked_count_total} creds.\n", 200

# If you still want a function to do single credential locked=1:
def update_credential_locked(credential_key, new_locked):
    url = REAL_DB_URL + f"/{credential_key}.json"
    data = {"locked": new_locked}
    response = requests.patch(url, json=data)
    print(f"Locking {credential_key} -> locked={new_locked}, resp={response.text}")

# ------------------------------------------------
#  NEW PROXY ROUTES to hide DB URL
# ------------------------------------------------
@app.route("/getData", methods=["GET"])
def get_data():
    token = request.headers.get("X-Secret")
    if token != PROXY_SECRET:
        return jsonify({"error": "Unauthorized"}), 403

    url = REAL_DB_URL + ".json"
    resp = requests.get(url)
    if resp.status_code != 200:
        return jsonify({"error": "Failed to read DB"}), 500

    return jsonify(resp.json())

@app.route("/setData", methods=["POST"])
def set_data():
    token = request.headers.get("X-Secret")
    if token != PROXY_SECRET:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    url = REAL_DB_URL + ".json"
    resp = requests.put(url, json=data)
    if resp.status_code != 200:
        return jsonify({"error": "Failed to write DB"}), 500

    return jsonify({"status":"ok","resp":resp.text})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
