import os
import json
import asyncio
import aiohttp
from collections import deque
# Configuration constants
REQUESTS_PER_SECOND = 90
DELAY = 1.0 / REQUESTS_PER_SECOND  # Dynamic delay between requests

class RateLimiter:
    def __init__(self, rate):
        self.rate = rate
        self.timestamps = deque()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = asyncio.get_event_loop().time()
            while self.timestamps and now - self.timestamps[0] > 1:
                self.timestamps.popleft()
            if len(self.timestamps) >= self.rate:
                sleep_time = 1 - (now - self.timestamps[0])
                await asyncio.sleep(sleep_time)
            self.timestamps.append(asyncio.get_event_loop().time())
rate_limiter = RateLimiter(REQUESTS_PER_SECOND)
# API Endpoints and Parameters
OAUTH_TOKEN_URL = "https://eu.battle.net/oauth/token"
# For items, we use the US domain and static namespace
ITEM_API_URL_TEMPLATE = "https://us.api.blizzard.com/data/wow/item/{item_id}?namespace=static-eu"

# File paths
ENCOUNTERED_ITEMS_FILE = "data/encountered_items.json"
ITEMS_SAVE_DIR = "data/items"
MEDIA_SAVE_DIR = "data/media"

async def get_oauth_token(session, client_id, client_secret):
    """
    Request an OAuth token using Blizzard client credentials.
    """
    data = {"grant_type": "client_credentials"}
    async with session.post(OAUTH_TOKEN_URL, data=data, auth=aiohttp.BasicAuth(client_id, client_secret)) as resp:
        resp.raise_for_status()
        token_data = await resp.json()
        token = token_data.get("access_token")
        if not token:
            raise Exception("Could not retrieve access token.")
        return token

async def fetch_item_data(session, item_id, headers):
    """
    Fetch the item data for a given item ID.
    """
    await rate_limiter.acquire()
    url = ITEM_API_URL_TEMPLATE.format(item_id=item_id)
    async with session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()

async def fetch_media_data(session, media_url, headers):
    """
    Fetch the media data from the provided media URL.
    """
    await rate_limiter.acquire()
    async with session.get(media_url, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()

def save_json(data, filename):
    """
    Save JSON data to a file with pretty formatting.
    """
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def load_encountered_items():
    """
    Load the encountered item IDs from file.
    The file is expected to contain a JSON list of item IDs.
    """
    if os.path.exists(ENCOUNTERED_ITEMS_FILE):
        with open(ENCOUNTERED_ITEMS_FILE, "r") as f:
            return json.load(f)
    else:
        print(f"No encountered items file found at {ENCOUNTERED_ITEMS_FILE}.")
        return []

async def process_item(session, item_id, headers):
    """
    Process a single item: fetch its data and associated media.
    Save the results to separate files.
    """
    try:
        # Fetch item data
        item_data = await fetch_item_data(session, item_id, headers)
        item_file = os.path.join(ITEMS_SAVE_DIR, f"{item_id}.json")
        save_json(item_data, item_file)
        print(f"Saved item data for item {item_id} to {item_file}")

        # Fetch media data if available in the item data
        media_info = item_data.get("media", {})
        media_key = media_info.get("key", {})
        media_url = media_key.get("href")
        if media_url:
            media_data = await fetch_media_data(session, media_url, headers)
            media_file = os.path.join(MEDIA_SAVE_DIR, f"{item_id}.json")
            save_json(media_data, media_file)
            print(f"Saved media data for item {item_id} to {media_file}")
        else:
            print(f"No media URL found for item {item_id}")
    except Exception as e:
        print(f"Error processing item {item_id}: {e}")

async def main():
    client_id = os.environ.get("BLIZZARD_CLIENT_ID")
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise Exception("Missing BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET environment variables.")
    
    # Load the encountered item IDs (list of integers or strings)
    encountered_items = load_encountered_items()
    if not encountered_items:
        print("No encountered items to process.")
        return

    async with aiohttp.ClientSession() as session:
        # Get OAuth token and prepare headers
        token = await get_oauth_token(session, client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}

        # Create tasks to process each item concurrently
        tasks = [
            asyncio.create_task(process_item(session, item_id, headers))
            for item_id in encountered_items
        ]
        
        # Gather and run tasks, handling exceptions individually
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Optionally, handle results or log exceptions if needed.
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"Error processing item {encountered_items[idx]}: {result}")

if __name__ == "__main__":
    asyncio.run(main())
