# Steam Guide Scraper

Command-line utility to scrape Steam Community Guides and convert them to Markdown with YAML frontmatter.

**Note:** Steam Community now applies aggressive anti-automation protections. Bulk scraping will likely fail with HTTP 429 errors after a relatively small number of requests (~20), and increasing delays does not reliably prevent temporary IP throttling/blocking.

## Requirements

- Python 3.x
- Dependencies: `requests`, `beautifulsoup4`, `html2text`, `PyYAML` (optional: `lxml`)

## Installation

```bash
git clone https://github.com/glimgeist/steam-guide-scraper
cd steam-guide-scraper
pip install -r requirements.txt
```

## Usage

```text
Usage:
  python steam_guide_scraper.py [MODES] [OPTIONS]

Modes (one required):
  -i, --input <URL_OR_ID>        Download a single guide
  -g, --game-id <APP_ID>         Download guides for a game

Options:
  -o, --output <PATH>            Output file (single mode) or directory (game mode)
  -d, --delay <SECONDS>          Minimum delay between requests (default: 1.0)
  -l, --limit <N>                Limit guides processed (game mode only)
      --sort-by <MODE>           trend | toprated | mostrecent (default: trend)
      --overwrite                Overwrite existing files
      --retries <N>              Retry failed requests (default: 1)
      --timeout <SECONDS>        Request timeout (default: 30.0)
      --user-agent <STRING>      Override default HTTP User-Agent header
      --fail-fast                Exit immediately on first error
  -v, --verbose                  Enable verbose logging
  -h, --help                     Show help message
```

### Examples

```bash
# Scrape a single guide by ID
python steam_guide_scraper.py -i 3285858313 -o guide.md

# Scrape up to 50 most recent guides for Dota 2 (App ID 570)
python steam_guide_scraper.py -g 570 --limit 50 --sort-by mostrecent -o ./dota2_guides
```

## Output Format

Guides are saved as `<guide_id>.md` files with a YAML frontmatter block containing metadata, followed by the guide title, description, and converted Markdown content.

### YAML Frontmatter Schema

```yaml
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
```

## License

MIT License. See [LICENSE](LICENSE) for details.