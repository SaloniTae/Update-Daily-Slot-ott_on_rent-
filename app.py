import os
import requests
from datetime import datetime, timedelta
import pytz

from flask import Flask, request, jsonify

app = Flask(__name__)

REAL_DB_URL  = os.getenv("REAL_DB_URL", "")
PROXY_SECRET = os.getenv("PROXY_SECRET", "")

ist = pytz.timezone("Asia/Kolkata")

def parse_ist(dt_str: str):
    naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return ist.localize(naive)

def format_ist(dt_aware: datetime) -> str:
    return dt_aware.strftime("%Y-%m-%d %H:%M:%S")

def is_credential(node):
    if not isinstance(node, dict):
        return False
    required_fields = ["email","password","expiry_date","locked","usage_count","max_usage"]
    return all(r in node for r in required_fields)

def lock_all_except_2():
    resp = requests.get(REAL_DB_URL + ".json")
    if resp.status_code == 200 and resp.json():
        all_data = resp.json()
        locked_count = 0
        for key, node in all_data.items():
            if not is_credential(node):
                continue
            locked_val = int(node.get("locked", 0))
            if locked_val == 0:  # locked=0 => lock it
                patch_url  = REAL_DB_URL + f"/{key}.json"
                patch_data = {"locked": 1}
                p = requests.patch(patch_url, json=patch_data)
                if p.status_code == 200:
                    locked_count += 1
        print(f"Locked {locked_count} credentials.")
    else:
        print("Failed to fetch credentials for locking.")

def update_slot_times_daily():
    now_ist = datetime.now(ist)

    # fetch 'settings'
    settings_resp = requests.get(REAL_DB_URL + "settings.json")
    if settings_resp.status_code != 200 or not settings_resp.json():
        print("No settings found or request error.")
        return

    settings_data = settings_resp.json()

    # If you STILL want the single-slot logic to remain for older usage:
    # We'll check if "slots" sub-node exists. If not, do the old single-slot approach.
    all_slots = settings_data.get("slots")
    if not isinstance(all_slots, dict):
        # fallback to old single-slot approach
        old_single_slot_shift(now_ist, settings_data)
        return

    # otherwise do multi-slot approach
    any_slot_shifted = False
    for slot_id, slot_info in all_slots.items():
        if not isinstance(slot_info, dict):
            continue
        enabled = bool(slot_info.get("enabled", False))
        if not enabled:
            continue

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
                slot_start_dt = now_ist.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            slot_start_dt = now_ist.replace(hour=9, minute=0, second=0, microsecond=0)

        slot_end_str = slot_info.get("slot_end","")
        if slot_end_str:
            try:
                slot_end_dt = parse_ist(slot_end_str)
            except ValueError:
                slot_end_dt = slot_start_dt + timedelta(days=1)
        else:
            slot_end_dt = slot_start_dt + timedelta(days=1)

        freq = slot_info.get("frequency","daily").lower()
        shift_delta = timedelta(days=7) if freq == "weekly" else timedelta(days=1)

        next_slot_start = slot_start_dt + shift_delta
        next_slot_end   = slot_end_dt   + shift_delta

        slot_info["slot_start"]  = format_ist(next_slot_start)
        slot_info["slot_end"]    = format_ist(next_slot_end)
        slot_info["last_update"] = format_ist(now_ist)

        any_slot_shifted = True
        print(f"Slot {slot_id} SHIFTED -> {slot_info['slot_start']} to {slot_info['slot_end']} freq={freq}")

    if any_slot_shifted:
        patch_resp = requests.patch(REAL_DB_URL + "settings.json", json={"slots": all_slots})
        if patch_resp.status_code == 200:
            print("Slots shift successful.")
            lock_all_except_2()
        else:
            print("Failed to update multi-slots in DB:", patch_resp.text)
    else:
        print("No multi-slot was shifted. No changes made.")

def old_single_slot_shift(now_ist, settings_data):
    """
    Fallback to your original single-slot approach if 'slots' node doesn't exist.
    """
    override = settings_data.get("override", False)
    last_update_str = settings_data.get("last_update", "")
    if last_update_str:
        try:
            last_update_dt = parse_ist(last_update_str)
        except ValueError:
            last_update_dt = now_ist
    else:
        last_update_dt = now_ist

    delta = now_ist - last_update_dt
    if delta < timedelta(hours=24):
        print(f"Only {delta} since last update; not 24h yet. Skipping shift (single-slot).")
        return

    print(f"24h+ since last update. override={override} (single-slot)")

    slot_start_str = settings_data.get("slot_start","")
    if slot_start_str:
        try:
            slot_start_dt = parse_ist(slot_start_str)
        except ValueError:
            slot_start_dt = now_ist.replace(hour=9,minute=0,second=0,microsecond=0)
    else:
        slot_start_dt = now_ist.replace(hour=9,minute=0,second=0,microsecond=0)

    slot_end_str = settings_data.get("slot_end","")
    if slot_end_str:
        try:
            slot_end_dt = parse_ist(slot_end_str)
        except ValueError:
            slot_end_dt = slot_start_dt + timedelta(days=1)
    else:
        slot_end_dt = slot_start_dt + timedelta(days=1)

    next_slot_start = slot_start_dt + timedelta(days=1)
    next_slot_end   = slot_end_dt   + timedelta(days=1)

    new_data = {
        "slot_start":  format_ist(next_slot_start),
        "slot_end":    format_ist(next_slot_end),
        "override":    override,
        "last_update": format_ist(now_ist)
    }
    patch_resp = requests.patch(REAL_DB_URL + "settings.json", json=new_data)
    if patch_resp.status_code == 200:
        print("Single-slot shift successful.")
        lock_all_except_2()
    else:
        print("Failed to update single-slot times:", patch_resp.text)

@app.route("/update_slot")
def update_slot():
    update_slot_times_daily()
    return "Slot times updated!\n", 200

@app.route("/lock_check")
def lock_check():
    now_ist = datetime.now(ist)
    settings_resp = requests.get(REAL_DB_URL + "settings.json")
    if settings_resp.status_code != 200 or not settings_resp.json():
        return "No settings or request error.\n", 200

    settings_data = settings_resp.json()
    all_slots = settings_data.get("slots")

    # if no multi-slot, fallback single-slot check
    if not isinstance(all_slots, dict):
        return old_single_slot_lock(now_ist, settings_data)

    margin = timedelta(minutes=2)
    locked_count_total = 0

    # For each enabled slot, if now >= slot_end - 2 min => lock all locked=0
    for slot_id, slot_info in all_slots.items():
        if not isinstance(slot_info, dict):
            continue
        if not slot_info.get("enabled",False):
            continue

        slot_end_str = slot_info.get("slot_end","9999-12-31 09:00:00")
        try:
            slot_end_dt = parse_ist(slot_end_str)
        except ValueError:
            continue

        if now_ist >= (slot_end_dt - margin):
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
    return f"Locked {locked_count_total} creds.\n", 200

def old_single_slot_lock(now_ist, settings_data):
    # your old approach to read slot_end, etc.
    slot_end_str = settings_data.get("slot_end","9999-12-31 09:00:00")
    try:
        slot_end_dt = parse_ist(slot_end_str)
    except ValueError:
        return "slot_end invalid.\n", 200

    margin = timedelta(minutes=2)
    if now_ist >= (slot_end_dt - margin):
        lock_resp = requests.get(REAL_DB_URL + ".json")
        if lock_resp.status_code == 200 and lock_resp.json():
            all_data = lock_resp.json()
            locked_count = 0
            for key, node in all_data.items():
                if not is_credential(node):
                    continue
                locked_val = int(node.get("locked",0))
                if locked_val == 0:
                    patch_url = REAL_DB_URL + f"/{key}.json"
                    patch_data = {"locked":1}
                    p = requests.patch(patch_url, json=patch_data)
                    if p.status_code == 200:
                        locked_count += 1
            return f"Locked {locked_count} creds.\n", 200
        else:
            return "Failed to fetch credentials.\n", 200
    else:
        return "Not time to lock yet.\n", 200

def update_credential_locked(credential_key, new_locked):
    url = REAL_DB_URL + f"/{credential_key}.json"
    data = {"locked": new_locked}
    response = requests.patch(url, json=data)
    print(f"Locking {credential_key} -> locked={new_locked}, resp={response.text}")

# PROXY ROUTES
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

    return jsonify({"status": "ok","resp":resp.text})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
