import os
import requests
from datetime import datetime, timedelta
import pytz

from flask import Flask, request, jsonify

app = Flask(__name__)

REAL_DB_URL = os.getenv("REAL_DB_URL", "")
PROXY_SECRET = os.getenv("PROXY_SECRET", "")

ist = pytz.timezone("Asia/Kolkata")

def parse_ist(dt_str: str):
    naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return ist.localize(naive)

def format_ist(dt_aware: datetime) -> str:
    return dt_aware.strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------------
# 1) New Helper: is_credential
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

def lock_all_except_2():
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
                patch_url = REAL_DB_URL + f"/{key}.json"
                patch_data = {"locked": 1}
                patch_resp = requests.patch(patch_url, json=patch_data)
                if patch_resp.status_code == 200:
                    locked_count += 1

        print(f"Locked {locked_count} credentials.")
    else:
        print("Failed to fetch credentials for locking.")

def update_slot_times_daily():
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
            return

        print(f"24h+ since last update. Proceeding with slot shift. override={override}")

        slot_start_str = data.get("slot_start", "")
        if slot_start_str:
            try:
                slot_start_dt = parse_ist(slot_start_str)
            except ValueError:
                slot_start_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
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
            lock_all_except_2()  # 🔥 Immediately lock all credentials
        else:
            print("Failed to update slot times:", patch_resp.text)
    else:
        print("No settings found or request error.")

    print("Slot times updated or skipped...")

@app.route("/update_slot")
def update_slot():
    update_slot_times_daily()
    return "Slot times updated!\n", 200

@app.route("/lock_check")
def lock_check():
    now = datetime.now()
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
                    # skip if not a credential
                    if not is_credential(node):
                        continue

                    locked_val = int(node["locked"])
                    # if locked=0 => lock it
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
    url = REAL_DB_URL + f"/{credential_key}.json"
    data = {"locked": new_locked}
    response = requests.patch(url, json=data)
    print(f"Locking {credential_key} -> locked={new_locked}, resp={response.text}")

# NEW PROXY ROUTES
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
