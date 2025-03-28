[![Discord][SVG-Discord]][Discord]
[![PayPal][SVG-PayPal]][PayPal]
[![LastRun][SVG-LastRun]][LastRun]


# Auction Sniper

Auction Sniper is a Github Actions Python-based tool designed to analyze World of Warcraft auction data and identify potential snipe opportunities.  
It aggregates historical pricing data and compares it with current auction listings to spot items with significantly low buyouts.  
When a qualifying auction is found, the tool sends a notification via Discord, making it easier to act quickly on underpriced deals.

## Features

- **Fully Github Hosted:**  
  Fully relies on Github Actions hosted runners for calculations. No Server Required!

- **Historical Analysis:**  
  Computes historical average buyout prices from a SQLite database, helping to spot deviations.

- **Auction Filtering:**  
  Identifies auction listings where the buyout is substantially lower (e.g., less than 20% of the historical average) than expected, suggesting a potential deal.

- **Notification System:**  
  Notifies you via Discord webhook about potential snipes, complete with item details and thumbnails.

- **Configuration:**  
  Allows you to configure multiple settings easily via JSON files.  
  - `expansionPresets.json` lets you filter item classes for specific expansions or rarities, even allowing for class-specific thresholds.  
  - `specialItems.json` lets you configure specific items with price thresholds, ignoring any class or general thresholds set.  
  - `relevantRealms.json` lets you configure specific realms to care about so you can easily filter out realms you do not play (or have characters) on.

## Usage

1. **Fork the repository:**

2. **Create and get a Blizzard client to interact with their web API:**  
   - See [Blizzard Docs](https://develop.battle.net/documentation/guides/getting-started) on how to create one.

3. **Set the required Github Secrets:**

   - `BLIZZARD_CLIENT_ID`: Your Blizzard Client ID.  
   - `BLIZZARD_CLIENT_SECRET`: Your Blizzard Client Secret.  
   - `DISCORD_WEBHOOK_URL`: A Discord webhook URL (see [Discord Docs](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks) for more information).

4. **(Optional) Configure the config files:**
   - `data/expansionPresets.json`: Filter item classes/expansions/rarities.
   - `data/specialItems.json`: Set specific items with thresholds.
   - `data/relevantRealms.json`: Set realms to search.

   ```
   Auction-Sniper/
   ├── data/
   │   ├── auctions/         # Auction JSON files
   │   ├── items/            # Item JSON files
   │   ├── relevantRealms.json
   │   ├── BonusIds.json
   │   ├── specialItems.json
   │   ├── ItemSearchName.json
   │   └── expansionPresets.json
   ```

5. **(Optional) Modify the run times:**  
   Currently, the main workflow only runs between 07:00 and 21:00 UTC. Modify that as best for your online times/timezone.

## Contributing

Contributions are welcome! If you have suggestions or improvements, feel free to open an issue or submit a pull request.

## Support / Donations

If you find Auction Sniper useful, please consider supporting the project:

- [Ko-fi](https://ko-fi.com/Jodsderechte)
- [PayPal](https://www.paypal.com/donate/?hosted_button_id=PSQ4D3HXNZKMG)

## Disclaimer

This tool is provided "as-is" without any warranty. Use it at your own risk.


[//]: # (Links)

[Discord]: https://discord.com/invite/v3gYmYamGJ (Join the Discord)
[PayPal]: https://ko-fi.com/jodsderechte (Donate via PayPal)
[LastRun]: https://github.com/Jodsderechte/Auction-Sniper/actions/workflows/realmAuctionData.yml (Latest workflow run)

[//]: # (Images)
[SVG-Discord]: https://img.shields.io/badge/Discord-7289da?logo=discord&logoColor=fff&style=flat-square
[SVG-PayPal]: https://custom-icon-badges.demolab.com/badge/-Support-lightgrey?style=flat-square&logo=kofi&color=222222
[SVG-LastRun]: https://github.com/Jodsderechte/Auction-Sniper/actions/workflows/realmAuctionData.yml/badge.svg
