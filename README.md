# 🎮 Steam Guide Scraper

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Python script to download Steam Community Guides as Markdown files, preserving content and extracting comprehensive metadata into YAML frontmatter.

This script fetches guide content and metadata from Steam, parses complex formatting (including Steam's BBCode-like HTML), and saves the results locally.

## ✨ Core Features

*   **🎯 Flexible Input:**
    *   Download a single guide using its URL or unique ID.
    *   Download *all* available English guides for an entire game using its App ID.
*   **📚 Rich Metadata Extraction:** Saves comprehensive guide and game metadata (like IDs, titles, authors, dates, stats) to YAML frontmatter.
*   **📄 Content Conversion:** Converts Steam guide HTML/BBCode structure to clean Markdown.
*   **⚙️ Download Control:** Offers options for output location, limiting guides per game, sorting, overwriting files, request delay, retries, and error handling (`--fail-fast`).

## 🔧 Requirements

*   Python 3.x
*   Required Python libraries:
    *   `requests`
    *   `beautifulsoup4`
    *   `html2text`
    *   `PyYAML`
    *   `lxml` (Optional, but recommended for faster HTML parsing)

## 🚀 Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/glimgeist/steam-guide-scraper
    cd steam-guide-scraper
    ```
2.  **Set up a virtual environment (Recommended):**
    ```bash
    # Create the environment
    python -m venv venv

    # Activate it:
    # Windows
    venv\Scripts\activate
    # macOS/Linux
    source venv/bin/activate
    ```
3.  **Install dependencies using `requirements.txt`:**
    ```bash
    pip install -r requirements.txt
    ```
    *(Alternatively, install manually: `pip install requests beautifulsoup4 html2text PyYAML lxml`)*
    *(If you prefer not to use `lxml`, omit it; the script will use Python's built-in `html.parser`.)*

## 💡 Usage

Run the script from your terminal:

```bash
python steam_guide_scraper.py [MODE] [OPTIONS]
```

**Modes (Choose ONE):**

*   `--input <URL_OR_ID>` or `-i <URL_OR_ID>`:
    *   Process a single guide by its full URL or just the numeric ID.
    *   Example: `python steam_guide_scraper.py -i 3285858313`
    *   Example: `python steam_guide_scraper.py -i "https://steamcommunity.com/sharedfiles/filedetails/?id=3285858313"`
*   `--game-id <APP_ID>` or `-g <APP_ID>`:
    *   Fetch and process all available English guides for a specific game App ID.
    *   Example: `python steam_guide_scraper.py -g 2246340`

**Common Options:**

*   `-o <PATH>`, `--output <PATH>`:
    *   Specify the output location.
    *   For `--input`: Path to the output Markdown file (defaults to `<guide_id>.md` in the current directory).
    *   For `--game-id`: Path to the output directory (defaults to the current directory).
    *   Example (`--input`): `python steam_guide_scraper.py -i 123 -o ./output/my_guide.md`
    *   Example (`--game-id`): `python steam_guide_scraper.py -g 440 -o ./tf2_guides`
*   `-d <SECONDS>`, `--delay <SECONDS>`:
    *   Minimum delay between network requests (default: 1.0). Randomized slightly.
    *   Example: `python steam_guide_scraper.py -g 570 -d 2.5`
*   `-l N`, `--limit N`:
    *   Limit the number of guides processed when using `--game-id`. Fetches the first N guides based on sorting.
    *   Example: `python steam_guide_scraper.py -g 570 --limit 50`
*   `--sort-by {trend|toprated|mostrecent}`:
    *   Sorting order when using `--game-id` (default: `trend`).
    *   Example: `python steam_guide_scraper.py -g 570 --sort-by mostrecent`
*   `--overwrite`:
    *   Overwrite existing Markdown files (default is to skip).
    *   Example: `python steam_guide_scraper.py -g 440 --overwrite`
*   `--retries N`:
    *   Number of retry attempts for failed network requests (default: 1).
*   `--fail-fast`:
    *   Exit immediately if an error occurs when processing multiple guides (`--game-id`).
*   `-v`, `--verbose`:
    *   Enable detailed debug logging.
*   `-h`, `--help`:
    *   Show the full help message and exit.

## 📋 Examples

*   **Download a single guide by ID:**
    ```bash
    python steam_guide_scraper.py -i 3285858313
    ```
*   **Download a single guide by URL to a specific file:**
    ```bash
    python steam_guide_scraper.py -i "https://steamcommunity.com/sharedfiles/filedetails/?id=2008348525" -o ./output/woh_events.md
    ```
*   **Download the top 50 most recent guides for Dota 2 (App ID 570) with increased delay:**
    ```bash
    python steam_guide_scraper.py -g 570 --limit 50 --sort-by mostrecent -d 3 -o ./dota2_guides --verbose
    ```
*   **Download all guides for a game, overwriting any existing files:**
    ```bash
    python steam_guide_scraper.py -g 2246340 -o ./game_guides --overwrite
    ```

## 📄 Output Format

Each guide is saved as a Markdown file (`<guide_id>.md`). The file structure is:

1.  **YAML Frontmatter:** Contains all extracted metadata, enclosed in `---`.
2.  **Guide Title:** Formatted as a Markdown H1 (`# Title`).
3.  **Guide Description:** The top description section, converted to Markdown.
4.  **Main Content:** The guide's sections and content, converted to Markdown.

**Example YAML Frontmatter:**
```yaml
---
game_id: 913740
game_title: WORLD OF HORROR
guide_id: 2008348525
guide_title: Events Codex
authors:
- paperrabbit
post_date: '2020-02-27'
update_date: '2022-04-11'
category: Walkthroughs
rating: 4
num_ratings: 97
unique_visitors: 6269
current_favorites: 197
comments: 66
---
```

## ❓ Troubleshooting

*   **Rate Limiting/Blocks:** If you encounter frequent network errors (timeouts, 429, 403), Steam might be rate-limiting you. Increase the `--delay` (e.g., `-d 5`) and consider using `--retries`.
*   **Parsing Errors:** Steam occasionally updates its website layout. Since this script relies on specific HTML structures and CSS selectors, major changes by Steam could break the scraper. If extraction fails consistently, the selectors might need updating in the script. Report an issue if you suspect this!
*   **Missing Content:** Ensure the guide is public. Unusual formatting might challenge the conversion. Check the guide\'s source HTML on Steam if specific content is missing.
*   **Dependencies:** Ensure all required libraries are installed (see Installation).

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.