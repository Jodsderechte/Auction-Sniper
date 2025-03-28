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
RELEVANT_REALMS_FILE = "config/relevantRealms.json"
SPECIAL_ITEMS_FILE = "config/specialItems.json"
ITEM_CLASSES_FILE = "config/itemClasses.json"
RAIDERIO_BONUS_FILE = "data/BonusIds.json"

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
            bonus_key TEXT,
            min_buyout INTEGER,
            timestamp TEXT,
            PRIMARY KEY (realm, item_id, bonus_key, timestamp)
        );
    """)
    conn.commit()

def init_announced_db(conn):
    """Create a table to store auction IDs that have been announced."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS announced_auctions (
            auction_id TEXT PRIMARY KEY
        );
    """)
    conn.commit()

def load_announced_auctions(conn):
    """Return a set of auction IDs that have already been announced."""
    cursor = conn.execute("SELECT auction_id FROM announced_auctions;")
    return {row[0] for row in cursor.fetchall()}    

def save_announced_auctions(conn, items):
    """Insert the auction IDs of the items that have been announced."""
    for item in items:
        auction_id = str(item["auction_id"])
        try:
            conn.execute("INSERT INTO announced_auctions (auction_id) VALUES (?);", (auction_id,))
        except sqlite3.IntegrityError:
            # Auction ID already exists; skip it.
            pass
    conn.commit()

def process_files(conn, relevant_realms):
    """
    Process auction JSON files, filtering by relevant realms.
    Aggregates data by (realm, item_id, bonus_key) to get the minimum buyout.
    Returns the temporary list of full auction records.
    """
    files = glob.glob(os.path.join(AUCTIONS_DIR, "*.json"))
    batch_data = {}   # Key: (realm, item_id, bonus_key)
    full_records = [] # List of full records (only from relevant realms)
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    for file in files:
        records = parse_file(file)
        for record in records:
            realm, auction_id, item_id, buyout, quantity, time_left, bonus_lists, ts = record
            if realm not in relevant_realms:
                continue
            full_records.append(record)
            bonus_key = get_bonus_key(bonus_lists)
            key = (realm, item_id, bonus_key)
            if key not in batch_data or buyout < batch_data[key]:
                batch_data[key] = buyout
    
    # Insert aggregated data into the database.
    for (realm, item_id, bonus_key), min_buyout in batch_data.items():
        conn.execute("""
            INSERT INTO item_prices (realm, item_id, bonus_key, min_buyout, timestamp)
            VALUES (?, ?, ?, ?, ?);
        """, (realm, item_id, bonus_key, min_buyout, timestamp))
    conn.commit()
    return full_records

def parse_file(file):
    """
    Parse a single JSON file from AUCTIONS_DIR and return a list of auction records.
    Each record is a tuple: (realm, auction_id, item_id, buyout, quantity, time_left, bonus_lists, timestamp)
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
        bonus_lists = item.get("bonus_lists", [])  # Capture bonus IDs
        record = (
            realm,
            auction.get("id"),
            item.get("id"),
            auction.get("buyout", 0),
            auction.get("quantity", 1),
            auction.get("time_left", ""),
            bonus_lists,  # New field
            timestamp
        )
        records.append(record)
    return records
def get_historical_averages(conn):
    """
    Calculate historical average buyout per item variant (grouped by item_id and bonus_key)
    from the entire database.
    Returns a dictionary mapping (item_id, bonus_key) to average buyout.
    """
    cursor = conn.execute("""
        SELECT item_id, bonus_key, AVG(min_buyout) as avg_price
        FROM item_prices
        GROUP BY item_id, bonus_key;
    """)
    averages = { (row[0], row[1]): row[2] for row in cursor.fetchall() }
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
        with open(SPECIAL_ITEMS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading special items: {e}")
        return {}

def load_raiderio_bonuses():
    try:
        with open(RAIDERIO_BONUS_FILE, "r") as f:
            bonuses = json.load(f)
        return bonuses  
    except Exception as e:
        print(f"Error loading relevant realms: {e}")
        return {}

def get_bonus_key(bonus_lists):
    """
    Generate a normalized bonus key using RaiderIO's bonus mapping.
    Only include bonus IDs that affect pricing (like ilvl changes, sockets, extra stats).
    """
    if not bonus_lists:
        return ""
    relevant_bonuses = []
    for bid in bonus_lists:
        bonus_info = RAIDERIO_BONUSES.get(str(bid))
        if bonus_info:
            # Assume bonus_info contains a field 'affectsPricing' or similar,
            # or you could check if bonus_info includes known keys like 'ilvl_up' or 'socket'.
            if bonus_info.get("affectsPricing", False) or bonus_info.get("category") in ("ilvl", "socket", "tertiary"):
                # For clarity, you might use a simplified tag:
                tag = bonus_info.get("tag", str(bid))
                relevant_bonuses.append(tag)
    if not relevant_bonuses:
        return ""
    return "-".join(sorted(relevant_bonuses))

def calculate_effective_ilvl(base_ilvl, bonus_lists):
    """
    Calculate the effective item level by adding bonus level adjustments.
    
    base_ilvl: the base item level (as an int).
    bonus_lists: a list of bonus IDs (numbers).
    
    The function looks up each bonus ID in the RAIDERIO_BONUSES mapping (loaded from BonusIds.json)
    and, if the bonus contains a "level" field, adds that value to the base_ilvl.
    """
    try:
        effective_ilvl = int(base_ilvl) if base_ilvl is not None else 0
    except ValueError:
        effective_ilvl = 0

    for bid in bonus_lists:
        bonus_info = RAIDERIO_BONUSES.get(str(bid))
        if bonus_info and "level" in bonus_info:
            effective_ilvl += bonus_info["level"]
    return effective_ilvl
def load_expansion_data():
    try:
        with open(ITEM_CLASSES_FILE, "r") as f:
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
        with open(ITEM_CLASSES_FILE, "r") as f:
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

def get_localized_value(val, lang="en_US"):
    if isinstance(val, dict):
        return val.get(lang, "")
    elif isinstance(val, str):
        return val
    return ""


def cross_reference_item(record, avg, special_items, expansion_data, presets, latest_expansion):
    realm, auction_id, item_id, buyout, quantity, time_left, bonus_lists, timestamp = record
    item_data = load_item_data(item_id)
    if not item_data:
        return None

    exp_info = expansion_data.get(str(item_id))
    expansion_id = exp_info.get("ExpansionID", 0) if exp_info else 0

    item_class_raw = item_data.get("item_class", {}).get("name", "")
    item_class = get_localized_value(item_class_raw).lower()

    preset = presets.get(item_class)
    if preset:
        allowed_expansions = preset.get("allowed_expansions", "all")
        allowed_qualities = preset.get("allowed_qualities", "all")
        allowed_subclasses = preset.get("allowed_subclass", "all")
        
        if allowed_expansions == "latest":
            allowed_expansions = {latest_expansion}
        
        if allowed_expansions != "all" and expansion_id not in allowed_expansions:
            return None

        if allowed_subclasses != "all":
            item_subclass_raw = item_data.get("item_subclass", "").get("name", "")
            item_subclass = get_localized_value(item_subclass_raw).lower()
            if item_subclass not in allowed_subclasses:
                return None

        quality = get_localized_value(item_data.get("quality", "").get("name", "")).upper()
        if allowed_qualities != "all" and quality not in allowed_qualities:
            return None
    else:
        print(f"Item class for item {item_id} is: {item_class} and subclass is {get_localized_value(item_data.get('item_subclass', ''))}")
        quality = get_localized_value(item_data.get("quality", "").get("name", "")).upper()

    threshold = preset.get("min_buyout_overwrite", MIN_BUYOUT) if preset else MIN_BUYOUT
    ratio = preset.get("threshold_ratio_overwrite", THRESHOLD_RATIO) if preset else THRESHOLD_RATIO

    special_threshold = special_items.get(str(auction_id))
    if special_threshold is not None and buyout < special_threshold:
        qualifies = True
    elif buyout < (ratio * avg) and buyout >= threshold:
        qualifies = True
    else:
        qualifies = False

    if not qualifies:
        return None

    # Retrieve base ilvl and calculate effective ilvl using bonus modifications.
    base_ilvl = item_data.get("level") or item_data.get("item_level") or 0
    effective_ilvl = calculate_effective_ilvl(base_ilvl, bonus_lists)

    item_name = get_localized_value(item_data.get("name", "Unknown Item"))
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
        "avg_price": avg,
        "ilvl": effective_ilvl  # Include the computed effective ilvl
    }


def process_record(record, averages, special_items, expansion_data, expansion_presets, latest_expansion):
    realm, auction_id, item_id, buyout, quantity, time_left, bonus_lists, timestamp = record
    bonus_key = get_bonus_key(bonus_lists)
    avg = averages.get((item_id, bonus_key))
    if not avg:
        return None
    extended = cross_reference_item(record, avg, special_items, expansion_data, expansion_presets, latest_expansion)
    if extended:
        extended["bonus_key"] = bonus_key
        return (realm, item_id, extended)
    return None


def find_cheap_items(new_records, averages, relevant_realms, expansion_data, expansion_presets, announced_ids, latest_expansion):
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
                             expansion_data=expansion_data, expansion_presets=expansion_presets, latest_expansion=latest_expansion)
    
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
    filtered_candidates = [item for item in candidate_list if str(item["auction_id"]) not in announced_ids]

    return filtered_candidates[:5]

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
        icon_path = item["icon"].replace("\\", os.sep)
        filename = os.path.basename(os.path.normpath(icon_path))
        normalized_icon_path = os.path.normpath(icon_path)
        attachment_url = f"attachment://{filename}"

        if filename not in attached_files:
            try:
                with open(normalized_icon_path, "rb") as f:
                    file_bytes = f.read()
                    files[filename] = (filename, file_bytes)
                    attached_files[filename] = True
            except Exception as e:
                print(f"Error opening file {normalized_icon_path}: {e}")
                attachment_url = ""
        
        embed = {
            "title": item["item_name"],
            "description": (
                f"**Item ID:** {item['item_id']}\n"
                f"**Realm:** {realm_name}\n"
                f"**Buyout:** {round(item['buyout']/10000)} (Avg Price: { round(item['avg_price']/10000,0)}) \n"
                f"**Item Level:** {item.get('ilvl', 'N/A')}\n"
                f"**Bonuses:** {item.get('bonus_key', 'None')}" 
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

RAIDERIO_BONUSES  = load_raiderio_bonuses()
print(f"Loaded {len(RAIDERIO_BONUSES)} bonus ids.")

def main():
    conn = sqlite3.connect(DB_FILE)
    init_db(conn)
    init_announced_db(conn)  # initialize announced auctions table
    relevant_realms = load_relevant_realms()
    print(f"Loaded {len(relevant_realms)} relevant realms.")

    expansion_data = load_expansion_data()
    print(f"Loaded {len(expansion_data)} expansion infos.")
    expansion_presets = preprocess_presets(load_expansion_presets())
    print(f"Calculated {len(expansion_presets)} expansion presets.")
    latest_expansion = compute_latest_expansion(expansion_data)
    print(f"Calculated latest expansion and it's {latest_expansion}.")

    new_records = process_files(conn, relevant_realms)
    print(f"Processed {len(new_records)} auction records from relevant realms.")

    averages = get_historical_averages(conn)
    print(f"Computed historical averages for {len(averages)} items.")

    announced_ids = load_announced_auctions(conn)
    cheap_items = find_cheap_items(new_records, averages, relevant_realms, expansion_data, expansion_presets, announced_ids, latest_expansion)
    print(f"Found {len(cheap_items)} candidate cheap items after filtering.")

    if cheap_items:
        notify_discord(cheap_items, relevant_realms)
        save_announced_auctions(conn, cheap_items)
    else:
        print("No qualifying cheap items to notify.")
    conn.close()

if __name__ == "__main__":
    main()
