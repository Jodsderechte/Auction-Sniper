import os
import json
import glob
import sqlite3
import datetime
import requests
import concurrent.futures

# Directories and files
AUCTIONS_DIR = "data/auctions"
ITEMS_DIR = "data/items"
RELEVANT_REALMS_FILE = "data/relevantRealms.json"

# SQLite database file to store historical auction data.
DB_FILE = "auctions.db"

# Discord webhook URL (set as environment variable in GitHub Actions)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Threshold constants
MIN_BUYOUT = 10000       # Only consider auctions with buyout at least 10k
THRESHOLD_RATIO = 0.05   # Auction is a "snipe" if buyout is less than 5% of historical average

def init_db(conn):
    """Initialize the database by creating the auctions table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            realm TEXT,
            auction_id INTEGER,
            item_id INTEGER,
            buyout INTEGER,
            quantity INTEGER,
            time_left TEXT,
            timestamp TEXT
        );
    """)
    conn.commit()

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
    else:
        realm = os.path.basename(file).split(".")[0]

    timestamp = datetime.datetime.utcnow().isoformat()
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

def process_files(conn):
    """
    Process all JSON files in AUCTIONS_DIR in parallel.
    Bulk insert the auction records into the SQLite database.
    Returns a list of new records (tuples) that were inserted.
    """
    files = glob.glob(os.path.join(AUCTIONS_DIR, "*.json"))
    all_records = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = executor.map(parse_file, files)
        for record_list in results:
            all_records.extend(record_list)

    if all_records:
        conn.executemany("""
            INSERT INTO auctions (realm, auction_id, item_id, buyout, quantity, time_left, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """, all_records)
        conn.commit()

    return all_records

def get_historical_averages(conn):
    """
    Calculate historical average buyout per item from the entire database.
    Returns a dictionary mapping item_id to average buyout.
    """
    cursor = conn.execute("""
        SELECT item_id, AVG(buyout) as avg_price
        FROM auctions
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

def cross_reference_item(record, avg, special_items):
    """
    For a given auction record and its historical average, cross reference item data.
    Returns a dict with extended info (name, icon, quality) if the item quality is not COMMON
    and the record qualifies (buyout < THRESHOLD_RATIO * avg and >= MIN_BUYOUT).
    Otherwise, returns None.
    record: tuple (realm, auction_id, item_id, buyout, quantity, time_left, timestamp)
    avg: historical average price for this item
    """
    realm, auction_id, item_id, buyout, quantity, time_left, timestamp = record
    special_threshold = special_items.get(str(auction_id))
    if special_threshold is not None and buyout < special_threshold:
        qualifies = True
    elif buyout < (THRESHOLD_RATIO * avg) and buyout >= MIN_BUYOUT:
        qualifies = True
    else:
        qualifies = False
    
    if not qualifies:
        return None
    item_data = load_item_data(item_id)
    if not item_data:
        return None
    # Check quality; skip if quality is COMMON
    quality = item_data.get("quality", {}).get("type", "").upper()
    if quality == "COMMON":
        return None
    # Get item name and icon
    item_name = item_data.get("name", "Unknown Item")
    icon = item_data.get("icon_path", "")
    # Calculate saving percentage
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
        "saving_pct": saving_pct
    }

def find_cheap_items(new_records, averages, relevant_realms):
    """
    From the new auction records, filter for auctions that:
      - Are from a realm listed in relevant_realms,
      - Have buyout less than THRESHOLD_RATIO * historical average and at least MIN_BUYOUT,
      - Are not of "COMMON" quality (based on item JSON data).
    Returns a list of dictionaries with extended auction/item data.
    Only the top 5 auctions with the highest saving percentage are returned.
    """
    candidates = []
    special_items = load_special_items()
    # Process each new record
    for record in new_records:
        realm = record[0]
        # Only consider if realm is in our relevant realms list
        if realm not in relevant_realms:
            continue
        avg = averages.get(record[2])
        if not avg:
            continue
        extended = cross_reference_item(record, avg, special_items)
        if extended:
            candidates.append(extended)
    # Sort by saving percentage descending
    candidates.sort(key=lambda x: x["saving_pct"], reverse=True)
    # Return top 5
    return candidates[:5]

def notify_discord(cheap_items, relevant_realms):
    """
    Send a Discord webhook notification listing the top cheap items.
    The message includes the item name, icon, realm (friendly name), buyout, and saving percentage.
    """
    if not DISCORD_WEBHOOK_URL:
        print("No Discord webhook URL provided.")
        return

    if not cheap_items:
        print("No cheap items to notify.")
        return
    print(f"Notifying about item {cheap_items}")
    message = "**Top Auction Snipes!**\n"
    for item in cheap_items:
        realm_id = item["realm_id"]
        realm_name = relevant_realms.get(realm_id, realm_id)
        message += (f"- **{item['item_name']}** (ID: {item['item_id']})\n"
                    f"  - Icon: {item['icon']}\n"
                    f"  - Realm: {realm_name}\n"
                    f"  - Buyout: {item['buyout']} (Saving: {item['saving_pct']:.1f}%)\n"
                    f"  - Auction ID: {item['auction_id']}\n")
    payload = {"content": message}
    try:
        print(f"Payload is: {payload}")
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if response.status_code == 204:
            print("Notification sent successfully to Discord.")
        else:
            print(f"Failed to send notification, status code {response.status_code}")
    except Exception as e:
        print(f"Error sending Discord notification: {e}")

def main():
    # Connect to (or create) the SQLite database
    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    # Process auction JSON files in parallel and insert new records
    new_records = process_files(conn)
    print(f"Processed {len(new_records)} new auction records.")

    # Compute historical averages
    averages = get_historical_averages(conn)
    print(f"Computed historical averages for {len(averages)} items.")

    # Load relevant realms mapping (realm_id -> friendly realm name)
    relevant_realms = load_relevant_realms()
    print(f"Loaded {len(relevant_realms)} relevant realms.")

    # Identify cheap items using the new threshold (5%) and cross-reference item data
    cheap_items = find_cheap_items(new_records, averages, relevant_realms)
    print(f"Found {len(cheap_items)} candidate cheap items after filtering.")

    # Notify Discord if any cheap items are found
    if cheap_items:
        notify_discord(cheap_items, relevant_realms)
    else:
        print("No qualifying cheap items to notify.")

    conn.close()

if __name__ == "__main__":
    main()
