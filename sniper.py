#!/usr/bin/env python3
import os
import json
import glob
import sqlite3
import datetime
import requests

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

def insert_auction(conn, realm, auction):
    """
    Insert a single auction record into the database.
    """
    timestamp = datetime.datetime.utcnow().isoformat()
    item = auction.get("item", {})
    conn.execute("""
        INSERT INTO auctions (realm, auction_id, item_id, buyout, quantity, time_left, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?);
    """, (
        realm,
        auction.get("id"),
        item.get("id"),
        auction.get("buyout", 0),
        auction.get("quantity", 1),
        auction.get("time_left", ""),
        timestamp
    ))
    conn.commit()

def process_files(conn):
    """
    Process all JSON files in the AUCTIONS_DIR.
    Assumes each file is one realm's auctions.
    Uses the file name or a field from the JSON as the realm identifier.
    """
    files = glob.glob(os.path.join(AUCTIONS_DIR, "*.json"))
    new_auctions = []  # keep track of auctions processed in this run
    for file in files:
        with open(file, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                print(f"Error decoding {file}")
                continue

        # Use the realm id from the connected_realm href or fallback to the file name.
        realm = data.get("connected_realm", {}).get("href", "")
        if realm:
            realm = realm.split("/")[-1]
        else:
            realm = os.path.basename(file).split(".")[0]

        auctions = data.get("auctions", [])
        for auction in auctions:
            insert_auction(conn, realm, auction)
            new_auctions.append({
                "realm": realm,
                "auction_id": auction.get("id"),
                "item_id": auction.get("item", {}).get("id"),
                "buyout": auction.get("buyout", 0),
                "quantity": auction.get("quantity", 1),
                "time_left": auction.get("time_left", "")
            })
    return new_auctions

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
    averages = {}
    for row in cursor.fetchall():
        averages[row[0]] = row[1]
    return averages

def find_cheap_items(new_auctions, averages):
    """
    From the list of new auctions, return those where the buyout price is less than 20%
    of the historical average and at least 10,000.
    """
    cheap_items = []
    for auction in new_auctions:
        item_id = auction["item_id"]
        buyout = auction["buyout"]
        avg = averages.get(item_id)
        if avg and buyout < (0.20 * avg) and buyout >= 10000:
            cheap_items.append(auction)
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

    # Process auction JSON files and store new auctions in the DB
    new_auctions = process_files(conn)
    print(f"Processed {len(new_auctions)} new auctions.")

    # Calculate historical averages for each item based on all data in the DB.
    averages = get_historical_averages(conn)

    # Identify auctions that are considered cheap.
    cheap_items = find_cheap_items(new_auctions, averages)
    print(f"Found {len(cheap_items)} cheap items.")

    # If any cheap items found, notify via Discord webhook.
    if cheap_items:
        notify_discord(cheap_items)
    else:
        print("No cheap items found.")

    conn.close()

if __name__ == "__main__":
    main()
