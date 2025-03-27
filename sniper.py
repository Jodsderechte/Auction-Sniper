import os
import json
import glob
import sqlite3
import datetime
import requests
import concurrent.futures
from functools import lru_cache
# Directories and files
AUCTIONS_DIR = "data/auctions"
ITEMS_DIR = "data/items"
RELEVANT_REALMS_FILE = "data/relevantRealms.json"

# SQLite database file to store historical auction data.
DB_FILE = "auctions.db"

# Discord webhook URL (set as environment variable in GitHub Actions)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Threshold constants
MIN_BUYOUT = 100000000       # Only consider auctions with buyout at least 10k (4 extra zeroes to convert from gold to copper)
THRESHOLD_RATIO = 0.20   # Auction is a "snipe" if buyout is less than 20% of historical average


def init_db(conn):
    """Create a table for aggregated item prices if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS item_prices (
            realm TEXT,
            item_id INTEGER,
            min_buyout INTEGER,
            timestamp TEXT,
            PRIMARY KEY (realm, item_id, timestamp)
        );
    """)
    conn.commit()

def process_files(conn, relevant_realms):
    """
    Process auction JSON files, filtering by relevant realms.
    - Aggregates data by (realm, item_id) to get the minimum buyout (persistent).
    - Builds a temporary list of full auction records (from relevant realms) for calculation.
    Returns the temporary list of full records.
    """
    files = glob.glob(os.path.join(AUCTIONS_DIR, "*.json"))
    batch_data = {}   # Key: (realm, item_id), Value: min_buyout from current files
    full_records = [] # Temporary list of full records (only from relevant realms)
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    for file in files:
        records = parse_file(file)
        for record in records:
            realm, auction_id, item_id, buyout, quantity, time_left, ts = record
            # Filter out records that are not from relevant realms.
            if realm not in relevant_realms:
                continue
            full_records.append(record)
            key = (realm, item_id)
            if key not in batch_data or buyout < batch_data[key]:
                batch_data[key] = buyout
    
    # Insert aggregated data into the database.
    for (realm, item_id), min_buyout in batch_data.items():
        conn.execute("""
            INSERT INTO item_prices (realm, item_id, min_buyout, timestamp)
            VALUES (?, ?, ?, ?);
        """, (realm, item_id, min_buyout, timestamp))
    conn.commit()
    return full_records

def parse_file(file):
    """
    Parse a single JSON file from AUCTIONS_DIR and return a list of auction records.
    Each record is a tuple (realm, auction_id, item_id, buyout, quantity, time_left, timestamp)
    """
    records = []
    try:
        with open(file, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error processing {file}: {e}")
        return records

    # Determine realm using connected_realm href; fallback to filename
    realm = data.get("connected_realm", {}).get("href", "")
    if realm:
        realm = realm.split("/")[-1]
        realm = realm.split('?')[0]
    else:
        realm = os.path.basename(file).split(".")[0]

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    auctions = data.get("auctions", [])
    for auction in auctions:
        item = auction.get("item", {})
        record = (
            realm,
            auction.get("id"),
            item.get("id"),
            auction.get("buyout", 0),
            auction.get("quantity", 1),
            auction.get("time_left", ""),
            timestamp
        )
        records.append(record)
    return records

def get_historical_averages(conn):
    """
    Calculate historical average buyout per item from the entire database.
    Returns a dictionary mapping item_id to average buyout.
    """
    cursor = conn.execute("""
        SELECT item_id, AVG(min_buyout) as avg_price
        FROM item_prices
        GROUP BY item_id;
    """)
    averages = {row[0]: row[1] for row in cursor.fetchall()}
    return averages

def load_relevant_realms():
    """
    Load the relevant realms from RELEVANT_REALMS_FILE.
    Expected JSON format: an object mapping realm_id to a friendly realm name.
    e.g. { "1923": "Stormwind", "1924": "Orgrimmar", ... }
    """
    try:
        with open(RELEVANT_REALMS_FILE, "r") as f:
            realms = json.load(f)
        return realms  # a dict mapping realm id to realm name
    except Exception as e:
        print(f"Error loading relevant realms: {e}")
        return {}
@lru_cache(maxsize=10000)
def load_item_data(item_id):
    """
    Load an item's JSON data from ITEMS_DIR using the item_id.
    Returns the parsed JSON as a dict, or None if an error occurs.
    """
    item_file = os.path.join(ITEMS_DIR, f"{item_id}.json")
    try:
        with open(item_file, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading item data for item {item_id}: {e}")
        return None

def load_special_items():
    """
    Load the special items configuration from data/specialItems.json.
    Returns a dict mapping item_id (as string) to its threshold value.
    """
    try:
        with open("data/specialItems.json", "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading special items: {e}")
        return {}


def load_expansion_data():
    try:
        with open("data/ItemSearchName.json", "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading expansion data: {e}")
        return {}

def compute_latest_expansion(expansion_data):
    try:
        return max(info.get("ExpansionID", 0) for info in expansion_data.values())
    except ValueError:
        return 0


def load_expansion_presets():
    try:
        with open("data/expansionPresets.json", "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading expansion presets: {e}")
        return {}
    
def preprocess_presets(expansion_presets):
    for key, preset in expansion_presets.items():
        if preset.get("allowed_qualities") != "all":
            preset["allowed_qualities"] = set(preset["allowed_qualities"])
        if preset.get("allowed_subclass") and preset.get("allowed_subclass") != "all":
            preset["allowed_subclass"] = set(preset["allowed_subclass"])
    return expansion_presets

def cross_reference_item(record, avg, special_items, expansion_data, presets, latest_expansion):
    realm, auction_id, item_id, buyout, quantity, time_left, timestamp = record
    item_data = load_item_data(item_id)
    if not item_data:
        return None

    exp_info = expansion_data.get(str(item_id))
    expansion_id = exp_info.get("ExpansionID", 0) if exp_info else 0

    name_val = item_data.get("item_class", {}).get("name", "")
    if isinstance(name_val, str):
        item_class = name_val.lower()
    else:
        item_class = name_val.get("en_us", "").lower()
    preset = presets.get(item_class)
    if preset:
        allowed_expansions = preset.get("allowed_expansions", "all")
        allowed_qualities = preset.get("allowed_qualities", "all")
        allowed_subclasses = preset.get("allowed_subclass", "all")
        
        if allowed_expansions == "latest":
            allowed_expansions = {latest_expansion}
        
        # If allowed_expansions is not "all", ensure it's a set for fast membership test.
        if allowed_expansions != "all" and expansion_id not in allowed_expansions:
            return None

        # Check subclass if needed.
        if allowed_subclasses != "all":
            item_subclass = item_data.get("item_subclass", {}).get("name", "").lower()
            if item_subclass not in allowed_subclasses:
                return None

        quality = item_data.get("quality", {}).get("type", "").upper()
        if allowed_qualities != "all" and quality not in allowed_qualities:
            return None
    else:
        print(f"Item class for item {item_id} is: {item_class} and subclass is {item_data.get('item_subclass', {}).get('name', '')}")
        quality = item_data.get("quality", {}).get("type", "").upper()

    if "min_buyout_overwrite" in preset:
        threshold = preset["min_buyout_overwrite"]
    else:
        threshold = MIN_BUYOUT

    if "threshold_ratio_overwrite" in preset:
        ratio = preset["threshold_ratio_overwrite"]
    else:
        ratio = THRESHOLD_RATIO
    # Existing threshold logic.
    special_threshold = special_items.get(str(auction_id))
    if special_threshold is not None and buyout < special_threshold:
        qualifies = True
    elif buyout < (ratio  * avg) and buyout >= threshold:
        qualifies = True
    else:
        qualifies = False

    if not qualifies:
        return None

    item_name = item_data.get("name", "Unknown Item")
    icon = item_data.get("icon_path", "")
    saving_pct = ((avg - buyout) / avg) * 100
    return {
        "realm_id": realm,
        "auction_id": auction_id,
        "item_id": item_id,
        "buyout": buyout,
        "quantity": quantity,
        "time_left": time_left,
        "timestamp": timestamp,
        "item_name": item_name,
        "icon": icon,
        "saving_pct": saving_pct,
        "avg_price": avg
    }

def process_record(record, averages, special_items, expansion_data, expansion_presets, latest_expansion):
    realm = record[0]
    item_id = record[2]
    avg = averages.get(item_id)
    if not avg:
        return None
    extended = cross_reference_item(record, avg, special_items, expansion_data, expansion_presets, latest_expansion)
    if extended:
        return (realm, item_id, extended)
    return None


def find_cheap_items(new_records, averages, relevant_realms, expansion_data, expansion_presets):
    """
    Filter full auction records (only from relevant realms) to include one entry per (realm, item_id)
    using the minimum buyout. Returns a list of dictionaries with extended auction/item data.
    """
    candidates = {}
    # Load special items only once.
    special_items = load_special_items()
    
    # Import partial to fix extra arguments for process_record.
    from functools import partial
    process_func = partial(process_record, averages=averages, special_items=special_items,
                             expansion_data=expansion_data, expansion_presets=expansion_presets)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=None) as executor:
        results = list(executor.map(process_func, new_records))
    
    for res in results:
        if res is None:
            continue
        realm, item_id, extended = res
        key = (realm, item_id)
        if key not in candidates or extended["buyout"] < candidates[key]["buyout"]:
            candidates[key] = extended

    candidate_list = list(candidates.values())
    candidate_list.sort(key=lambda x: x["buyout"])
    return candidate_list[:5]

def notify_discord(cheap_items, relevant_realms):
    """
    Send a Discord webhook notification using embed objects with file attachments.
    Each embed includes the item name, thumbnail (loaded as an attachment), realm (friendly name),
    buyout, historical average price, and auction ID.
    If an icon is used for multiple items, it is attached only once.
    """
    if not DISCORD_WEBHOOK_URL:
        print("No Discord webhook URL provided.")
        return

    if not cheap_items:
        print("No cheap items to notify.")
        return

    embeds = []
    files = {}
    attached_files = {}  # Track which filenames have been attached.

    for idx, item in enumerate(cheap_items):
        realm_id = item["realm_id"]
        realm_name = relevant_realms.get(realm_id, realm_id)

        print(f"Processing item : {item}")
        # Get the icon's filename.
        filename = os.path.basename(item["icon"])
        attachment_url = f"attachment://{filename}"

        if filename not in attached_files:
            try:
                with open(item["icon"], "rb") as f:
                    file_bytes = f.read()
                    files[filename] = (filename, file_bytes)
                    attached_files[filename] = True
            except Exception as e:
                print(f"Error opening file {item['icon']}: {e}")
                attachment_url = ""
        
        embed = {
            "title": item["item_name"],
            "description": (
                f"**Item ID:** {item['item_id']}\n"
                f"**Realm:** {realm_name}\n"
                f"**Buyout:** {round(item['buyout']/10000)} (Avg Price: { round(item['avg_price']/10000,0)})"
            ),
            "footer": {"text": f"Auction ID: {item['auction_id']}"},
            "timestamp": item["timestamp"]
        }
        if attachment_url:
            embed["thumbnail"] = {"url": attachment_url}
        
        embeds.append(embed)

    payload = {"embeds": embeds}
    try:
        print(f"Payload is: {payload}")
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            data={"payload_json": json.dumps(payload)},
            files=files
        )
        if response.status_code in (200, 204):
            print("Notification sent successfully to Discord.")
        else:
            print(f"Failed to send notification, status code {response.status_code}")
    except Exception as e:
        print(f"Error sending Discord notification: {e}")

def main():
    conn = sqlite3.connect(DB_FILE)
    init_db(conn)
    relevant_realms = load_relevant_realms()
    print(f"Loaded {len(relevant_realms)} relevant realms.")

    expansion_data = load_expansion_data()
    expansion_presets = preprocess_presets(load_expansion_presets())
    latest_expansion = compute_latest_expansion(expansion_data)

    new_records = process_files(conn, relevant_realms)
    print(f"Processed {len(new_records)} auction records from relevant realms.")

    averages = get_historical_averages(conn)
    print(f"Computed historical averages for {len(averages)} items.")

    # Use partial to fix extra arguments.
    from functools import partial
    process_func = partial(process_record, averages=averages, 
                           special_items=load_special_items(), 
                           expansion_data=expansion_data, 
                           expansion_presets=expansion_presets,
                           latest_expansion=latest_expansion)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(process_func, new_records))
    
    candidates = {}
    for res in results:
        if res is None:
            continue
        realm, item_id, extended = res
        key = (realm, item_id)
        if key not in candidates or extended["buyout"] < candidates[key]["buyout"]:
            candidates[key] = extended

    candidate_list = list(candidates.values())
    candidate_list.sort(key=lambda x: x["buyout"])
    cheap_items = candidate_list[:5]
    print(f"Found {len(cheap_items)} candidate cheap items after filtering.")

    if cheap_items:
        notify_discord(cheap_items, relevant_realms)
    else:
        print("No qualifying cheap items to notify.")
    conn.close()

if __name__ == "__main__":
    main()
