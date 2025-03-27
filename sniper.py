#!/usr/bin/env python3
import os
import json
import glob
import sqlite3
import datetime
import requests
import concurrent.futures

# Path to the folder containing auction JSON files.
AUCTIONS_DIR = "data/auctions"

# SQLite database file to store historical auction data.
DB_FILE = "auctions.db"

# Discord webhook URL (set as environment variable in GitHub Actions)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def init_db(conn):
    """
    Initialize the database by creating an auctions table if it doesn't exist.
    The table stores realm info, auction id, item id, buyout price, quantity, time_left and timestamp.
    """
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
    Parse a single JSON file and return a list of auction records.
    Each record is a tuple ready for bulk insertion.
    """
    records = []
    try:
        with open(file, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error processing {file}: {e}")
        return records

    # Determine realm using connected_realm href, or fallback to filename.
    realm = data.get("connected_realm", {}).get("href", "")
    if realm:
        realm = realm.split("/")[-1]
    else:
        realm = os.path.basename(file).split(".")[0]

    # Use current UTC timestamp for all records from this file.
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
    Returns a flat list of new auction records that were inserted.
    Uses bulk insertion for speed.
    """
    files = glob.glob(os.path.join(AUCTIONS_DIR, "*.json"))
    all_records = []
    # Use a thread pool to parse files concurrently
    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = executor.map(parse_file, files)
        for record_list in results:
            all_records.extend(record_list)

    # Perform a bulk insert in a single transaction
    if all_records:
        conn.executemany("""
            INSERT INTO auctions (realm, auction_id, item_id, buyout, quantity, time_left, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """, all_records)
        conn.commit()

    return all_records  # Returning the list of records inserted

def get_historical_averages(conn):
    """
    Calculate historical market values for each item by averaging the buyout prices.
    Returns a dictionary mapping item_id to average buyout.
    """
    cursor = conn.execute("""
        SELECT item_id, AVG(buyout) as avg_price
        FROM auctions
        GROUP BY item_id;
    """)
    averages = {row[0]: row[1] for row in cursor.fetchall()}
    return averages

def find_cheap_items(new_records, averages):
    """
    From the list of new auction records, return those where the buyout price is less than 20%
    of the historical average and at least 10,000.
    Each record is a tuple with structure:
      (realm, auction_id, item_id, buyout, quantity, time_left, timestamp)
    """
    cheap_items = []
    for record in new_records:
        realm, auction_id, item_id, buyout, quantity, time_left, timestamp = record
        avg = averages.get(item_id)
        if avg and buyout < (0.20 * avg) and buyout >= 10000:
            cheap_items.append({
                "realm": realm,
                "auction_id": auction_id,
                "item_id": item_id,
                "buyout": buyout,
                "quantity": quantity,
                "time_left": time_left
            })
    return cheap_items

def notify_discord(cheap_items):
    """
    Notify via Discord webhook if any cheap items are found.
    The message includes details of the cheap auctions.
    """
    if not DISCORD_WEBHOOK_URL:
        print("No Discord webhook URL provided.")
        return

    content = "**Cheap Auction Alert!**\n"
    for item in cheap_items:
        content += f"- Realm: {item['realm']}, Item ID: {item['item_id']}, Buyout: {item['buyout']}, Auction ID: {item['auction_id']}\n"

    payload = {"content": content}
    try:
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

    # Process auction JSON files in parallel and bulk insert new records.
    new_records = process_files(conn)
    print(f"Processed {len(new_records)} new auction records.")

    # Calculate historical averages based on the entire database.
    averages = get_historical_averages(conn)

    # Identify auctions that are considered cheap.
    cheap_items = find_cheap_items(new_records, averages)
    print(f"Found {len(cheap_items)} cheap items.")

    # Notify via Discord if cheap items are found.
    if cheap_items:
        notify_discord(cheap_items)
    else:
        print("No cheap items found.")

    conn.close()

if __name__ == "__main__":
    main()
