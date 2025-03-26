import os
import json
import re
import asyncio
import aiohttp

# Base endpoint configuration
BASE_URL = "https://eu.api.blizzard.com"
NAMESPACE = "dynamic-eu"
OAUTH_TOKEN_URL = "https://eu.battle.net/oauth/token"
SAVE_FOLDER = "data/auctions/"
REQUESTS_PER_SECOND = 90

async def get_oauth_token(session, client_id, client_secret):
    """
    Request an OAuth token using the Blizzard client credentials.
    """
    data = {"grant_type": "client_credentials"}
    async with session.post(OAUTH_TOKEN_URL, data=data, auth=aiohttp.BasicAuth(client_id, client_secret)) as resp:
        resp.raise_for_status()
        token_data = await resp.json()
        token = token_data.get("access_token")
        if not token:
            raise Exception("Could not retrieve access token.")
        return token

async def get_connected_realms(session, headers):
    """
    Fetches the connected realm index from the Blizzard API.
    """
    url = f"{BASE_URL}/data/wow/connected-realm/?namespace={NAMESPACE}"
    async with session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        data = await resp.json()
        return data.get("connected_realms", [])

def extract_realm_id(href):
    """
    Extracts the realm id from the provided URL.
    Expected URL format:
    https://eu.api.blizzard.com/data/wow/connected-realm/1080?namespace=dynamic-eu
    """
    match = re.search(r"/connected-realm/(\d+)\?", href)
    if match:
        return match.group(1)
    return None

DELAY = 1.0/REQUESTS_PER_SECOND
async def get_auctions_for_realm(session, realm_id, headers):
    """
    Fetches the auction data for a given realm.
    Adds a small delay to keep the request rate under 100 requests per second.
    """
    # Delay 0.01 seconds before each request to stay within the rate limit.
    await asyncio.sleep(DELAY)
    url = f"{BASE_URL}/data/wow/connected-realm/{realm_id}/auctions?namespace={NAMESPACE}"
    print(f"Fetching auctions for realm {realm_id} from {url}")
    async with session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()

async def main():
    client_id = os.environ.get("BLIZZARD_CLIENT_ID")
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise Exception("Missing BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET environment variables.")
        
    async with aiohttp.ClientSession() as session:
        # Request OAuth token.
        token = await get_oauth_token(session, client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}
        
        # Get connected realms.
        connected_realms = await get_connected_realms(session, headers)
        if not connected_realms:
            print("No connected realms found.")
            return

        # Extract realm IDs.
        realm_ids = []
        for realm in connected_realms:
            href = realm.get("href")
            realm_id = extract_realm_id(href)
            if realm_id:
                realm_ids.append(realm_id)
            else:
                print(f"Could not extract realm id from href: {href}")

        # Create a task for each realm request.
        tasks = [
            asyncio.create_task(get_auctions_for_realm(session, realm_id, headers))
            for realm_id in realm_ids
        ]
        
        # Gather all results concurrently.
        auctions_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Save auction data per realm.
        for realm_id, auctions_data in zip(realm_ids, auctions_results):
            if isinstance(auctions_data, Exception):
                print(f"Error fetching realm {realm_id}: {auctions_data}")
            else:
                filename = f"{SAVE_FOLDER}{realm_id}.json"
                os.makedirs(os.path.dirname(filename), exist_ok=True)
                with open(filename, "w") as f:
                    json.dump(auctions_data, f, indent=2)
                print(f"Saved auctions data for realm {realm_id} to {filename}")

if __name__ == "__main__":
    asyncio.run(main())