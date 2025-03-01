import os
import requests
from datetime import datetime, timedelta
import pytz

from flask import Flask, request, jsonify

app = Flask(__name__)

# ------------------------------------------------------
# Environment variables on your hosting environment
# e.g. Render: set these in "Environment" settings
# ------------------------------------------------------
REAL_DB_URL   = os.getenv("REAL_DB_URL", "")     # e.g. "https://get-crunchy-credentials-default-rtdb.firebaseio.com/"
PROXY_SECRET  = os.getenv("PROXY_SECRET", "")    # e.g. "YOUR_SUPER_SECRET_TOKEN"

# Timezone for IST
ist = pytz.timezone("Asia/Kolkata")

def parse_ist(dt_str: str) -> datetime:
    """
    Convert 'YYYY-MM-DD HH:MM:SS' string to IST-aware datetime
    """
    naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return ist.localize(naive)

def format_ist(dt_aware: datetime) -> str:
    """
    Convert IST-aware datetime back to 'YYYY-MM-DD HH:MM:SS'
    """
    return dt_aware.strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------------
# 1) Helper: is_credential
# ---------------------------------
def is_credential(node):
    """
    Return True if 'node' is a dict with these required fields:
      email, password, expiry_date, locked, usage_count, max_usage
    """
    if not isinstance(node, dict):
        return False
    required_fields = ["email", "password", "expiry_date", "locked", "usage_count", "max_usage"]
    return all(field in node for field in required_fields)


# ------------------------------------------------------
# Locking logic
# ------------------------------------------------------
def lock_all_except_2():
    """
    Lock all credentials that have locked=0 (skip locked=2).
    Called right after we do a slot shift (or can be called in /lock_check).
    """
    resp = requests.get(REAL_DB_URL + ".json")
    if resp.status_code == 200 and resp.json():
        all_data = resp.json()
        locked_count = 0

        for key, node in all_data.items():
            # only lock if it's a real credential
            if not is_credential(node):
                continue

            locked_val = int(node["locked"])
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


# ------------------------------------------------------
# Multi-slot update route
# ------------------------------------------------------
@app.route("/update_slots")
def update_slots():
    """
    Loops through `settings/slots/*`, checks each slot's last_update vs. now,
    and shifts by the slot's 'period':
      - daily => shift by 1 day
      - weekly => shift by 7 days
    Then calls lock_all_except_2() if it does shift.
    """
    now = datetime.now(ist)

    # 1) read the "slots" node
    multi_url = REAL_DB_URL + "settings/slots.json"
    resp = requests.get(multi_url)
    if resp.status_code != 200 or not resp.json():
        return "No 'slots' or request error.\n", 200

    slots_data = resp.json()  # e.g. { "slot_1": {...}, "slot_2": {...}, "slot_3": {...} }

    for slot_key, slot_info in slots_data.items():
        if not isinstance(slot_info, dict):
            continue

        # skip if 'enabled' is not True
        if not slot_info.get("enabled", False):
            print(f"{slot_key} => not enabled => skip.")
            continue

        override   = slot_info.get("override", False)
        period_str = slot_info.get("period", "daily")  # daily or weekly
        last_up_str= slot_info.get("last_update", "")
        slot_start_str = slot_info.get("slot_start", "")

        try:
            last_update_dt = parse_ist(last_up_str) if last_up_str else now
        except ValueError:
            last_update_dt = now

        # how many hours do we wait before shifting?
        if period_str == "weekly":
            required_hours = 24*7  # 168
        else:
            # default daily
            required_hours = 24

        delta_hours = (now - last_update_dt).total_seconds() / 3600.0
        if delta_hours < required_hours:
            print(f"{slot_key} => only {delta_hours:.1f}h since last update => skip.")
            continue

        print(f"{slot_key} => period={period_str}, override={override}. Shifting slot...")

        if override:
            print(f"{slot_key} => override=true => skip shifting.")
            continue

        # parse slot_start
        try:
            start_dt = parse_ist(slot_start_str)
        except ValueError:
            # fallback
            start_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)

        # shift by 1 day if daily, 7 days if weekly
        if period_str == "weekly":
            shift_days = 7
        else:
            shift_days = 1

        next_slot_start = start_dt + timedelta(days=shift_days)
        next_slot_end   = next_slot_start + timedelta(days=shift_days)

        # patch this slot
        patch_data = {
            "slot_start": format_ist(next_slot_start),
            "slot_end":   format_ist(next_slot_end),
            "last_update": format_ist(now)
        }
        patch_url = REAL_DB_URL + f"settings/slots/{slot_key}.json"
        p = requests.patch(patch_url, json=patch_data)
        if p.status_code == 200:
            print(f"Shifted {slot_key} by {shift_days} day(s).")
            # lock credentials
            lock_all_except_2()
        else:
            print(f"Failed to update {slot_key} => {p.text}")

    return "All multi-slots updated.\n", 200


# ------------------------------------------------------
# Single-slot route if you still want it:
#   /update_slot => old approach for the default slot
#   (Optional. You can remove if you only do multi-slot)
# ------------------------------------------------------
@app.route("/update_slot")
def update_slot():
    """
    The old single-slot approach for 'settings/slot_start' etc.
    If you still want it. Otherwise remove it.
    """
    now = datetime.now(ist)

    settings_resp = requests.get(REAL_DB_URL + "settings.json")
    if settings_resp.status_code == 200 and settings_resp.json():
        data = settings_resp.json()

        override = data.get("override", False)
        last_update_str = data.get("last_update", "")
        if last_update_str:
            try:
                last_update_dt = parse_ist(last_update_str)
            except ValueError:
                last_update_dt = now
        else:
            last_update_dt = now

        delta = now - last_update_dt
        if delta < timedelta(hours=24):
            print(f"Only {delta} since last update; not 24h yet. Skipping shift.")
            return "No single-slot shift.\n", 200

        print(f"24h+ since last update. Proceeding with single-slot shift. override={override}")

        slot_start_str = data.get("slot_start", "")
        try:
            slot_start_dt = parse_ist(slot_start_str)
        except ValueError:
            slot_start_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)

        next_slot_start = slot_start_dt + timedelta(days=1)
        next_slot_end   = next_slot_start + timedelta(days=1)

        new_data = {
            "slot_start": format_ist(next_slot_start),
            "slot_end":   format_ist(next_slot_end),
            "override":   override,
            "last_update": format_ist(now)
        }
        patch_resp = requests.patch(REAL_DB_URL + "settings.json", json=new_data)
        if patch_resp.status_code == 200:
            print("Slot shift successful.")
            lock_all_except_2()
        else:
            print("Failed to update single-slot times:", patch_resp.text)

        print("Single-slot updated or skipped...")
        return "Single-slot shift done.\n", 200

    else:
        print("No settings found or request error for single-slot.")
        return "No single-slot update.\n", 200


# ------------------------------------------------------
# Lock check route (like old /lock_check)
# ------------------------------------------------------
@app.route("/lock_check")
def lock_check():
    """
    Called by Cron-Job.org every minute.
    If now >= slot_end - 2 min, lock all credentials that are locked=0.
    For multi-slot approach, you can either:
     - loop each slot, check if now >= that slot_end - 2 min
     - or keep your old approach
    """
    now = datetime.now()
    # EXAMPLE: old approach => single 'slot_end' in settings
    settings_resp = requests.get(REAL_DB_URL + "settings.json")
    if settings_resp.status_code == 200 and settings_resp.json():
        s_data = settings_resp.json()
        slot_end_str = s_data.get("slot_end", "9999-12-31 09:00:00")
        try:
            slot_end_dt = datetime.strptime(slot_end_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return "slot_end invalid.\n", 200

        margin = timedelta(minutes=2)
        if now >= (slot_end_dt - margin):
            lock_resp = requests.get(REAL_DB_URL + ".json")
            if lock_resp.status_code == 200 and lock_resp.json():
                all_data = lock_resp.json()
                locked_count = 0
                for key, node in all_data.items():
                    if not is_credential(node):
                        continue
                    locked_val = int(node.get("locked", 0))
                    if locked_val == 0:
                        update_credential_locked(key, 1)
                        locked_count += 1
                return f"Locked {locked_count} creds.\n", 200
            else:
                return "Failed to fetch credentials.\n", 200
        else:
            return "Not time to lock yet.\n", 200
    else:
        return "No settings or request error.\n", 200


def update_credential_locked(credential_key, new_locked):
    """
    Patch a single credential's locked field
    """
    url = REAL_DB_URL + f"/{credential_key}.json"
    data = {"locked": new_locked}
    response = requests.patch(url, json=data)
    print(f"Locking {credential_key} -> locked={new_locked}, resp={response.text}")


# ------------------------------------------------------
# PROXY routes to hide DB URL
# ------------------------------------------------------
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
    url  = REAL_DB_URL + ".json"
    resp = requests.put(url, json=data)
    if resp.status_code != 200:
        return jsonify({"error": "Failed to write DB"}), 500

    return jsonify({"status": "ok", "resp": resp.text})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
