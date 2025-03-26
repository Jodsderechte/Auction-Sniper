import os
import requests
import json
import re

# Base endpoint configuration
BASE_URL = "https://eu.api.blizzard.com"
NAMESPACE = "dynamic-eu"
OAUTH_TOKEN_URL = "https://eu.battle.net/oauth/token"

def get_oauth_token(client_id, client_secret):
    """
    Request an OAuth token using the Blizzard client credentials.
    """
    data = {"grant_type": "client_credentials"}
    response = requests.post(OAUTH_TOKEN_URL, data=data, auth=(client_id, client_secret))
    response.raise_for_status()
    token_data = response.json()
    token = token_data.get("access_token")
    if not token:
        raise Exception("Could not retrieve access token.")
    return token

def get_connected_realms(headers):
    """
    Fetches the connected realm index from the Blizzard API.
    """
    url = f"{BASE_URL}/data/wow/connected-realm/?namespace={NAMESPACE}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
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

def get_auctions_for_realm(realm_id, headers):
    """
    Fetches the auction data for a given realm.
    """
    url = f"{BASE_URL}/data/wow/connected-realm/{realm_id}/auctions?namespace={NAMESPACE}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def save_json(data, filename):
    """
    Saves JSON data to a file with pretty formatting.
    """
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def main():
    # Get Blizzard client credentials from environment variables
    client_id = os.environ.get("BLIZZARD_CLIENT_ID")
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise Exception("Missing BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET environment variables.")
    
    # Get OAuth token and set up the header
    token = get_oauth_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}
    
    # Fetch connected realms
    realms = get_connected_realms(headers)
    if not realms:
        print("No connected realms found.")
        return
    
    # Process each realm: extract realm id, query auctions and save data
    for realm in realms:
        href = realm.get("href")
        realm_id = extract_realm_id(href)
        if realm_id:
            print(f"Processing realm: {realm_id}")
            auctions_data = get_auctions_for_realm(realm_id, headers)
            filename = f"data/realm_{realm_id}.json"
            save_json(auctions_data, filename)
            print(f"Saved auctions data for realm {realm_id} to {filename}")
        else:
            print(f"Could not extract realm id from href: {href}")

if __name__ == "__main__":
    main()
