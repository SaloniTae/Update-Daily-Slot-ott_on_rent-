import os
import requests
from datetime import datetime, timedelta
import pytz

from flask import Flask, request, jsonify

app = Flask(__name__)

# -------------------------------------------------------------------
# 1) ENV VARS FOR DB URL + SECRET
# -------------------------------------------------------------------
REAL_DB_URL  = os.getenv("REAL_DB_URL", "")     # e.g. "https://xxx.firebaseio.com/"
PROXY_SECRET = os.getenv("PROXY_SECRET", "")    # e.g. "YOUR_SUPER_SECRET_TOKEN"

ist = pytz.timezone("Asia/Kolkata")

def parse_ist(dt_str: str) -> datetime:
    """Parse a 'YYYY-MM-DD HH:MM:SS' as IST-aware datetime."""
    naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return ist.localize(naive)

def format_ist(dt_aware: datetime) -> str:
    """Format an IST-aware datetime back to 'YYYY-MM-DD HH:MM:SS'."""
    return dt_aware.strftime("%Y-%m-%d %H:%M:%S")

# -------------------------------------------------------------------
# 2) HELPER: is_credential
# -------------------------------------------------------------------
def is_credential(node):
    """
    Return True if 'node' is shaped like a credential:
      {email, password, expiry_date, locked, usage_count, max_usage}
    """
    if not isinstance(node, dict):
        return False
    required = ["email", "password", "expiry_date", "locked", "usage_count", "max_usage"]
    return all(r in node for r in required)

# -------------------------------------------------------------------
# 3) LOCKING
# -------------------------------------------------------------------
def lock_all_except_2():
    """
    Lock all credentials with locked=0 (skip locked=1 or locked=2).
    """
    resp = requests.get(REAL_DB_URL + ".json")
    if resp.status_code == 200 and resp.json():
        all_data = resp.json()
        locked_count = 0
        for key, node in all_data.items():
            if not is_credential(node):
                continue
            locked_val = int(node["locked"])
            if locked_val == 0:  # auto-lock
                patch_url = REAL_DB_URL + f"/{key}.json"
                patch_data = {"locked": 1}
                patch_resp = requests.patch(patch_url, json=patch_data)
                if patch_resp.status_code == 200:
                    locked_count += 1
        print(f"Locked {locked_count} credentials.")
    else:
        print("Failed to fetch credentials for locking.")

def update_credential_locked(credential_key, new_locked):
    url = REAL_DB_URL + f"/{credential_key}.json"
    data = {"locked": new_locked}
    resp = requests.patch(url, json=data)
    print(f"Locking {credential_key} => locked={new_locked}, resp={resp.text}")

# -------------------------------------------------------------------
# 4) SHIFTING MULTIPLE SLOTS
# -------------------------------------------------------------------
def update_all_slots():
    """
    Loop over each slot in settings/slots. If enabled == true, check
    if it's daily or weekly or uses override logic. If 24h (daily) or
    168h (weekly) have passed since 'last_update', shift slot_start/slot_end
    forward and lock credentials.
    """
    # 1) Read entire DB
    resp = requests.get(REAL_DB_URL + ".json")
    if resp.status_code != 200 or not resp.json():
        print("No DB data or request error in update_all_slots.")
        return

    db_data = resp.json()
    settings = db_data.get("settings", {})
    slots    = settings.get("slots", {})

    now_ist = datetime.now(ist)

    for slot_id, slot_info in slots.items():
        if not isinstance(slot_info, dict):
            continue
        # only proceed if enabled
        if not slot_info.get("enabled", False):
            continue

        slot_type = slot_info.get("type", "daily")   # e.g. "daily" or "weekly"
        override  = slot_info.get("override", False) # if you want to skip logic or not
        last_up_str = slot_info.get("last_update", "")
        if last_up_str:
            try:
                last_up_dt = parse_ist(last_up_str)
            except ValueError:
                last_up_dt = now_ist
        else:
            last_up_dt = now_ist

        # Decide threshold
        if slot_type == "weekly":
            threshold_hours = 168
        else:
            threshold_hours = 24

        # If override is true, you might skip or forcibly shift. Up to you:
        if override:
            print(f"Slot {slot_id} override=true => forcibly shift anyway.")
            can_shift = True
        else:
            delta = now_ist - last_up_dt
            if delta.total_seconds() >= threshold_hours * 3600:
                can_shift = True
            else:
                print(f"Slot {slot_id}: only {delta}, not enough for shift.")
                can_shift = False

        if can_shift:
            slot_start_str = slot_info.get("slot_start", "")
            slot_end_str   = slot_info.get("slot_end", "")
            try:
                start_dt = parse_ist(slot_start_str)
            except ValueError:
                start_dt = now_ist
            try:
                end_dt   = parse_ist(slot_end_str)
            except ValueError:
                end_dt   = start_dt + timedelta(days=1)

            # SHIFT AMOUNT
            if slot_type == "weekly":
                shift_amount = timedelta(days=7)
            else:
                shift_amount = timedelta(days=1)

            new_start = start_dt + shift_amount
            new_end   = end_dt   + shift_amount

            # 2) Patch them back
            patch_data = {
              f"settings/slots/{slot_id}/slot_start":  format_ist(new_start),
              f"settings/slots/{slot_id}/slot_end":    format_ist(new_end),
              f"settings/slots/{slot_id}/last_update": format_ist(now_ist),
              f"settings/slots/{slot_id}/override":    False  # if you want override to reset after shift
            }
            patch_url = REAL_DB_URL + ".json"
            patch_resp = requests.patch(patch_url, json=patch_data)
            if patch_resp.status_code == 200:
                print(f"Slot {slot_id} shifted => {new_start} to {new_end}. Now lock credentials.")
                lock_all_except_2()
            else:
                print(f"Failed to patch slot {slot_id}: {patch_resp.text}")

@app.route("/update_slots")
def update_slots():
    """Endpoint to shift all slots if needed, then lock creds."""
    update_all_slots()
    return "Slots updated!\n", 200

# -------------------------------------------------------------------
# 5) lock_check
# -------------------------------------------------------------------
@app.route("/lock_check")
def lock_check():
    """
    If now >= any slot_end - 2 min => lock all (locked=0) credentials
    """
    resp = requests.get(REAL_DB_URL + ".json")
    if resp.status_code != 200 or not resp.json():
        return "No DB or request error.\n", 200

    db_data = resp.json()
    settings = db_data.get("settings", {})
    slots    = settings.get("slots", {})

    now_ist = datetime.now(ist)
    margin  = timedelta(minutes=2)
    do_lock = False

    for slot_id, slot_info in slots.items():
        if not slot_info.get("enabled", False):
            continue
        slot_end_str = slot_info.get("slot_end", "")
        if not slot_end_str:
            continue
        try:
            end_dt = parse_ist(slot_end_str)
        except ValueError:
            continue

        # if now >= slot_end - 2 min => lock
        if now_ist >= (end_dt - margin):
            do_lock = True
            break

    if do_lock:
        lock_all_except_2()
        return "Locked credentials.\n", 200
    else:
        return "Not time to lock yet.\n", 200

# -------------------------------------------------------------------
# 6) PROXY ROUTES
# -------------------------------------------------------------------
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

    return jsonify({"status": "ok", "resp": resp.text})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
