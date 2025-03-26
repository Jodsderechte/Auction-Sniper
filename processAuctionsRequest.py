import os
import re
import json
import asyncio
import aiohttp
from glob import glob

# --- Configuration Constants ---
REQUESTS_PER_SECOND = 90
DELAY = 1.0 / REQUESTS_PER_SECOND  # Delay based on rate limit
HOURLY_LIMIT_STATUS = {429}

# Endpoints and namespaces for item requests
OAUTH_TOKEN_URL = "https://eu.battle.net/oauth/token"
ITEM_API_URL_TEMPLATE = "https://us.api.blizzard.com/data/wow/item/{item_id}?namespace=static-eu&locale=en_US"

# File paths
AUCTIONS_DIR = os.path.join("data", "auctions") # Auctions files named like data/realm_{realm_id}.json
ENCOUNTERED_ITEMS_FILE = os.path.join("data", "encountered_items.json")
ITEMS_SAVE_DIR = os.path.join("data", "items")
MEDIA_SAVE_DIR = os.path.join("data", "media")

def save_json(data, filename):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def load_encountered_items():
    if os.path.exists(ENCOUNTERED_ITEMS_FILE):
        with open(ENCOUNTERED_ITEMS_FILE, "r") as f:
            return set(json.load(f))
    else:
        print(f"No encountered items file found at {ENCOUNTERED_ITEMS_FILE}.")
        return set()

def load_auctions_item_ids():
    """
    Reads all auction data files from the auctions directory and
    extracts the set of item IDs.
    """
    item_ids = set()
    # Assume auctions files match the pattern "realm_*.json"
    for filepath in glob(os.path.join(AUCTIONS_DIR, "*.json")):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
                auctions = data.get("auctions", [])
                for auction in auctions:
                    item = auction.get("item", {})
                    item_id = item.get("id")
                    if item_id:
                        item_ids.add(str(item_id))
        except Exception as e:
            print(f"Error reading {filepath}: {e}")
    return item_ids

async def get_oauth_token(session, client_id, client_secret):
    data = {"grant_type": "client_credentials"}
    async with session.post(OAUTH_TOKEN_URL, data=data, auth=aiohttp.BasicAuth(client_id, client_secret)) as resp:
        resp.raise_for_status()
        token_data = await resp.json()
        token = token_data.get("access_token")
        if not token:
            raise Exception("Could not retrieve access token.")
        return token

async def fetch_item_data(session, item_id, headers):
    await asyncio.sleep(DELAY)
    url = ITEM_API_URL_TEMPLATE.format(item_id=item_id)
    async with session.get(url, headers=headers) as resp:
        if resp.status in HOURLY_LIMIT_STATUS:
            raise aiohttp.ClientResponseError(
                status=resp.status,
                request_info=resp.request_info,
                history=resp.history,
                message=f"Rate limit hit for item {item_id}"
            )
        resp.raise_for_status()
        return await resp.json()

async def fetch_media_data(session, media_url, headers):
    await asyncio.sleep(DELAY)
    async with session.get(media_url, headers=headers) as resp:
        if resp.status in HOURLY_LIMIT_STATUS:
            raise aiohttp.ClientResponseError(
                status=resp.status,
                request_info=resp.request_info,
                history=resp.history,
                message="Rate limit hit for media"
            )
        resp.raise_for_status()
        return await resp.json()

async def process_new_item(session, item_id, headers):
    """
    Process a single new item: fetch item data and its media.
    Returns True if successful, False if a rate limit error is encountered.
    """
    try:
        item_data = await fetch_item_data(session, item_id, headers)
        item_file = os.path.join(ITEMS_SAVE_DIR, f"{item_id}.json")
        save_json(item_data, item_file)
        print(f"Saved item data for item {item_id}")

        media_info = item_data.get("media", {})
        media_key = media_info.get("key", {})
        media_url = media_key.get("href")
        if media_url:
            media_data = await fetch_media_data(session, media_url, headers)
            media_file = os.path.join(MEDIA_SAVE_DIR, f"{item_id}.json")
            save_json(media_data, media_file)
            print(f"Saved media data for item {item_id}")
        else:
            print(f"No media URL for item {item_id}")
        return True

    except aiohttp.ClientResponseError as e:
        if e.status in HOURLY_LIMIT_STATUS:
            print(f"Rate limit hit while processing item {item_id}: {e}")
            return False
        else:
            print(f"HTTP error for item {item_id}: {e}")
            return False
    except Exception as e:
        print(f"Error processing item {item_id}: {e}")
        return False

async def process_new_items(new_item_ids, headers, client_id, client_secret):
    async with aiohttp.ClientSession() as session:
        token = await get_oauth_token(session, client_id, client_secret)
        headers["Authorization"] = f"Bearer {token}"

        tasks = [
            asyncio.create_task(process_new_item(session, item_id, headers))
            for item_id in new_item_ids
        ]
        results = await asyncio.gather(*tasks)
        processed_items = {item_id for item_id, success in zip(new_item_ids, results) if success}
        return processed_items

async def main():
    # Get client credentials from environment
    client_id = os.environ.get("BLIZZARD_CLIENT_ID")
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise Exception("Missing BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET.")

    # Load item IDs from committed auctions data
    current_item_ids = load_auctions_item_ids()
    print(f"Found {len(current_item_ids)} unique item IDs in auctions data.")

    # Load previously encountered items
    old_items = load_encountered_items()
    print(f"{len(old_items)} items were previously encountered.")

    # Determine new items to process
    new_items = current_item_ids - old_items
    print(f"{len(new_items)} new items to process.")

    headers = {}
    processed_new_items = set()
    if new_items:
        processed_new_items = await process_new_items(list(new_items), headers, client_id, client_secret)
        print(f"Successfully processed {len(processed_new_items)} new items.")

    # Update encountered items file (only add items that were successfully processed)
    final_encountered = list(old_items.union(processed_new_items))
    save_json(final_encountered, ENCOUNTERED_ITEMS_FILE)
    print(f"Encountered items file updated with {len(final_encountered)} items.")

if __name__ == "__main__":
    asyncio.run(main())
