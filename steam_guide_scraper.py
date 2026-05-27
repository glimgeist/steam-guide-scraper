#!/usr/bin/env python3
"""Convert Steam Community Guides to Markdown with YAML frontmatter."""

import argparse
from datetime import datetime
import logging
import os
import random
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse


try:
    import bs4
    import html2text
    import requests
    import yaml
except ImportError as e:
    print(
        f"CRITICAL ERROR: Missing required library: {e.name}. "
        "Please install dependencies (e.g., pip install requests beautifulsoup4 html2text PyYAML).",
        file=sys.stderr,
    )
    sys.exit(2)

logging.basicConfig(
    level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr
)


MARKDOWN_CONVERTER = html2text.HTML2Text()
MARKDOWN_CONVERTER.body_width = 0
MARKDOWN_CONVERTER.unicode_snob = True
MARKDOWN_CONVERTER.ignore_links = False
MARKDOWN_CONVERTER.ignore_images = False
MARKDOWN_CONVERTER.images_as_html = False
MARKDOWN_CONVERTER.inline_links = True
MARKDOWN_CONVERTER.protect_links = True
MARKDOWN_CONVERTER.ignore_tables = False


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}

try:
    import lxml  # pylint: disable=unused-import
    HTML_PARSER = "lxml"
except ImportError:
    HTML_PARSER = "html.parser"


def is_valid_steam_guide_url(url: str) -> bool:
    """Check URL has valid scheme, steamcommunity.com domain, correct path, and 'id' param."""
    try:
        parsed = urlparse(url)
        is_valid = (
            parsed.scheme in ["http", "https"]
            and parsed.netloc == "steamcommunity.com"
            and "/sharedfiles/filedetails/" in parsed.path
            and "id=" in parsed.query
        )
        if not is_valid:
            logging.debug("URL validation failed for: %s", url)
        return is_valid
    except ValueError as e:
        logging.debug("URL parsing error: %s", e)
        return False


def get_guide_id_from_url(url: str) -> Optional[str]:
    """Extract the numeric guide ID from a Steam guide URL's query params.

    Returns the ID string, or None if missing/non-numeric.
    """
    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        guide_id = query_params.get("id", [None])[0]
        if guide_id and guide_id.isdigit():
            return guide_id
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        logging.debug("Could not extract guide ID from URL '%s': %s", url, e)
    return None


def safe_get_text(
    element: Optional[bs4.Tag], strip: bool = True, separator: str = " "
) -> Optional[str]:
    """Extract text from a BeautifulSoup element, returning None if element is None."""
    return element.get_text(strip=strip, separator=separator) if element else None


def safe_get_number(text: Optional[str]) -> Optional[int]:
    """Extract an integer from a string by stripping non-digit characters.

    Returns None if input is None or contains no digits.
    """
    if not text:
        return None
    try:
        cleaned = re.sub(r"[^\d]", "", text)
        return int(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def parse_steam_date(date_str: Optional[str]) -> Optional[str]:
    """Parse Steam's various date formats into YYYY-MM-DD.

    Handles formats like "Feb 27, 2020 @ 12:10am", "10 Jul, 2024 @ 2:07am",
    and "6 Apr @ 6:28pm" (assumes current year). Returns None on failure.
    """
    if not date_str:
        return None
    date_str = date_str.strip()

    # More specific patterns first
    patterns = [
        (r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4})\s+@.*", "%b %d, %Y"),        # "Feb 27, 2020 @ ..."
        (r"(\d{1,2}\s+[A-Za-z]{3},\s+\d{4})\s+@.*", "%d %b, %Y"),        # "10 Jul, 2024 @ ..."
        (r"^([A-Za-z]{3}\s+\d{1,2},\s+\d{4})$", "%b %d, %Y"),            # "Feb 27, 2020"
        (r"^(\d{1,2}\s+[A-Za-z]{3},\s+\d{4})$", "%d %b, %Y"),            # "10 Jul, 2024"
    ]

    for pattern, date_format in patterns:
        match = re.search(pattern, date_str, re.IGNORECASE)
        if match:
            date_part = match.group(1)
            try:
                dt_obj = datetime.strptime(date_part, date_format)
                return dt_obj.strftime("%Y-%m-%d")
            except ValueError as e:
                logging.debug(
                    "Failed to parse date '%s' with format '%s': %s",
                    date_part,
                    date_format,
                    e,
                )

    # Day Mon @ time without year (e.g., "6 Apr @ 6:28pm") — assume current year
    no_year_pattern_time = re.compile(
        r"^(\d{1,2}\s+[A-Za-z]{3})\s+@\s+\d{1,2}:\d{2}(?:am|pm)$", re.IGNORECASE
    )
    match_no_year_time = no_year_pattern_time.match(date_str)
    if match_no_year_time:
        date_part_no_year = match_no_year_time.group(1)
        current_year = datetime.now().year
        date_with_year = f"{date_part_no_year}, {current_year}"

        day_first_match = re.match(r"^\d{1,2}", date_part_no_year)
        date_format = "%d %b, %Y" if day_first_match else "%b %d, %Y"
        try:
            dt_obj = datetime.strptime(date_with_year, date_format)
            logging.debug(
                "Parsed date '%s' assuming current year: %s",
                date_str,
                current_year,
            )
            return dt_obj.strftime("%Y-%m-%d")
        except ValueError as e:
            logging.debug(
                "Failed to parse date '%s' with assumed year format '%s': %s",
                date_with_year,
                date_format,
                e,
            )

    logging.warning(
        "Could not parse date from string: '%s' using any known format.",
        date_str,
    )
    return None


def fetch_html(url: str, retries: int = 1, timeout: float = 30.0, cookies: Optional[dict] = None) -> Optional[str]:
    """Fetch HTML content from a URL with retry and exponential backoff.

    Returns HTML string or None on failure.
    """
    logging.debug("Fetching: %s (Timeout: %ss, Retries: %s)", url, timeout, retries)
    last_exception = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout, cookies=cookies or {})
            logging.debug(
                "Attempt %s/%s: Status %s for %s",
                attempt + 1, retries + 1, response.status_code, url,
            )
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            logging.info("Successfully fetched URL: %s", url)
            return response.text
        except requests.exceptions.RequestException as e:
            last_exception = e
            logging.warning(
                "Attempt %s/%s failed for %s: %s",
                attempt + 1, retries + 1, url, e,
            )
            if attempt < retries:
                sleep_time = min(10.0, (1.5 ** attempt) + random.uniform(0.1, 0.5))
                logging.debug("Waiting %.2fs before retrying...", sleep_time)
                time.sleep(sleep_time)
            else:
                logging.error(
                    "Fetching finally failed after %s attempts for URL %s.",
                    retries + 1, url,
                )
        except (LookupError, ValueError, TypeError, AttributeError) as e:
            logging.error(
                "Unexpected error during request/encoding for %s on attempt %s: %s",
                url, attempt + 1, e,
            )
            last_exception = e
            break

    if last_exception:
        logging.error("Final error fetching %s: %s", url, last_exception)
    return None


def parse_and_clean_soup(html_content: str) -> Optional[bs4.BeautifulSoup]:
    """Parses HTML and removes unwanted tags."""
    logging.debug("Parsing HTML content...")
    try:
        soup = bs4.BeautifulSoup(html_content, HTML_PARSER)
        for element in soup(["script", "style"]):
            element.decompose()
        for comment in soup.find_all(string=lambda text: isinstance(text, bs4.Comment)):
            comment.extract()
        return soup
    except (ValueError, TypeError, AttributeError) as e:
        logging.error("Failed to parse HTML: %s", e)
        return None


def _extract_game_id(soup: bs4.BeautifulSoup, guide_id: Optional[str]) -> Optional[Union[int, str]]:
    """Extract the Steam App ID from the guide page."""
    log_prefix = f"[Game ID (Guide {guide_id or 'N/A'})]"

    # Method 1: input[name="appid"]
    appid_inputs = soup.select('input[name="appid"]')
    for input_elem in appid_inputs:
        val = input_elem.get("value")
        if val and val.isdigit():
            logging.debug("%s Found via input[name=appid]: '%s'.", log_prefix, val)
            return int(val)

    # Method 2: store button link
    store_button = soup.select_one('a.btnv6_blue_hoverfade[data-appid][href*="/app/"]')
    if store_button:
        data_appid = store_button.get('data-appid')
        if data_appid and data_appid.isdigit():
            logging.debug("%s Found via store button data-appid: '%s'.", log_prefix, data_appid)
            return int(data_appid)

        href = store_button.get('href')
        if href:
            match = re.search(r"/app/(\d+)", href)
            if match:
                logging.debug("%s Found via store button href: '%s'.", log_prefix, match.group(1))
                return int(match.group(1))

    # Method 3: guide link data-appid
    any_guide_link = soup.select_one('a.workshopItemCollection[data-appid]')
    if any_guide_link:
        data_appid = any_guide_link.get('data-appid')
        if data_appid and data_appid.isdigit():
            logging.debug("%s Found via guide link data-appid: '%s'.", log_prefix, data_appid)
            return int(data_appid)

    # Method 4: JS variables
    html_content = str(soup)
    patterns = [
        r"g_steamIDAppID\s*=\s*['\"]?(\d+)['\"]?",
        r"ShowModalContent\s*\(\s*[^,]+,\s*['\"](\d+)['\"]",
        r'"appid"\s*:\s*(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_content)
        if match and match.group(1):
            logging.debug("%s Found via JS variable: '%s'.", log_prefix, match.group(1))
            return int(match.group(1))

    logging.warning("%s FAILED: Could not determine Game ID.", log_prefix)
    return None

def _extract_authors(soup: bs4.BeautifulSoup) -> Tuple[List[str], str]:
    """Extract author usernames from the creators block, with fallback to .guideAuthors."""
    authors = []
    created_by_section = soup.select_one(".rightDetailsBlock .creatorsBlock")
    if created_by_section:
        for author_link in created_by_section.select(".friendBlock a.friendBlockLinkOverlay"):
            author_url = author_link.get("href", "")
            if author_url:
                username = author_url.rstrip("/").split("/")[-1]
                if username:
                    authors.append(username)

    if authors:
        return authors, ", ".join(authors)

    # Fallback
    author_elem = soup.select_one(".guideAuthors")
    author_text = safe_get_text(author_elem)
    author_str = author_text.replace("By ", "").strip() if author_text else "Unknown Author"
    return [author_str], author_str

def _extract_tags_and_languages(soup: bs4.BeautifulSoup) -> Tuple[Any, Any]:
    """Extract category and language tags from the right details block."""
    categories, languages = [], []
    for tag_elem in soup.select(".rightDetailsBlock .workshopTags"):
        title_span = tag_elem.select_one(".workshopTagsTitle")
        if not title_span:
            continue

        title_text = safe_get_text(title_span, strip=True).lower()
        link_texts = [
            text
            for link in tag_elem.find_all("a")
            if (text := safe_get_text(link, strip=True))
        ]

        if "category" in title_text:
            categories.extend(link_texts)
        elif "languages" in title_text:
            languages.extend(link_texts)

    cat_result = categories[0] if len(categories) == 1 else (categories or None)
    lang_result = languages[0] if len(languages) == 1 else (languages or None)
    return cat_result, lang_result

def _extract_dates(soup: bs4.BeautifulSoup) -> Tuple[str, str]:
    """Extract post and update dates."""
    post_date, update_date = "", ""
    date_elements = soup.select(".rightDetailsBlock .detailsStatRight")
    label_elements = soup.select(".rightDetailsBlock .detailsStatLeft")

    if len(date_elements) == len(label_elements):
        for label_elem, date_elem in zip(label_elements, date_elements):
            label_text = " ".join(label_elem.stripped_strings).strip()
            date_text = " ".join(date_elem.stripped_strings).strip()

            if not date_text:
                continue

            parsed_date = parse_steam_date(date_text)
            if label_text == "Posted":
                if parsed_date:
                    post_date = parsed_date
                else:
                    logging.warning("Could not parse post date: %s", date_text)
            elif label_text == "Updated":
                if parsed_date:
                    update_date = parsed_date
                else:
                    logging.warning("Could not parse update date: %s", date_text)

    if update_date and not post_date:
        post_date = update_date
    elif post_date and not update_date:
        update_date = post_date

    return post_date, update_date


def _extract_statistics(soup: bs4.BeautifulSoup) -> Tuple[int, int, int, int, int]:
    """Extract rating, num_ratings, visitors, favorites, and comments."""
    rating = 0
    rating_elem = soup.select_one(".ratingSection .fileRatingDetails img")
    if rating_elem and rating_elem.get("src"):
        match = re.search(r"(\d)-star_large\.png", rating_elem["src"])
        if match:
            rating = int(match.group(1))

    num_ratings = safe_get_number(safe_get_text(soup.select_one(".ratingSection .numRatings"))) or 0

    unique_visitors, current_favorites = 0, 0
    stats_table = soup.select_one(".panel table.stats_table")
    if stats_table:
        for row in stats_table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) == 2:
                val = safe_get_text(cols[0])
                lbl = safe_get_text(cols[1])
                if lbl == "Unique Visitors":
                    unique_visitors = safe_get_number(val) or 0
                elif lbl == "Current Favorites":
                    current_favorites = safe_get_number(val) or 0

    comments_elem = soup.select_one('.commentthread_count_label span[id$="_totalcount"]')
    comments = safe_get_number(safe_get_text(comments_elem)) or 0

    return rating, num_ratings, unique_visitors, current_favorites, comments

def extract_metadata(
    soup: bs4.BeautifulSoup, guide_id: Optional[str] = None
) -> Dict[str, Any]:
    """Extract all metadata from a Steam guide page into a dictionary.

    Includes guide/game info, authors, dates, ratings, visitors, favorites,
    comments, and category/language tags.
    """
    logging.debug("Extracting metadata...")
    metadata = {}

    if guide_id and guide_id.isdigit():
        metadata["guide_id"] = int(guide_id)
    elif guide_id:
        metadata["guide_id"] = guide_id

    app_name_elem = soup.select_one(".apphub_AppName.ellipsis")
    metadata["game_title"] = safe_get_text(app_name_elem) or "Unknown Game"
    metadata["game_id"] = _extract_game_id(soup, guide_id)

    title_element = soup.select_one(".workshopItemTitle")
    if guide_title := safe_get_text(title_element):
        metadata["guide_title"] = guide_title

    metadata["authors"], metadata["author"] = _extract_authors(soup)
    metadata["category"], metadata["languages"] = _extract_tags_and_languages(soup)
    metadata["post_date"], metadata["update_date"] = _extract_dates(soup)

    stats = _extract_statistics(soup)
    metadata["rating"] = stats[0]
    metadata["num_ratings"] = stats[1]
    metadata["unique_visitors"] = stats[2]
    metadata["current_favorites"] = stats[3]
    metadata["comments"] = stats[4]

    logging.debug("Finished extracting metadata.")
    return metadata


def generate_frontmatter(metadata: Dict[str, Any]) -> str:
    """Generate YAML frontmatter from metadata, excluding 'author' and 'languages' keys.

    Returns a YAML block wrapped in '---' markers, or empty string on error.
    """
    try:
        metadata_to_dump = metadata.copy()
        metadata_to_dump.pop('author', None)
        metadata_to_dump.pop('languages', None)

        key_order = [
            'game_id', 'game_title', 'guide_id', 'guide_title', 'authors',
            'post_date', 'update_date', 'category', 'rating', 'num_ratings',
            'unique_visitors', 'current_favorites', 'comments'
        ]
        sorted_metadata = {k: metadata_to_dump[k] for k in key_order if k in metadata_to_dump}

        for k in sorted(metadata_to_dump.keys()):
            if k not in sorted_metadata:
                sorted_metadata[k] = metadata_to_dump[k]

        yaml_string = yaml.dump(
            sorted_metadata, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        logging.debug("Generated YAML frontmatter.")
        return f"---\n{yaml_string}---\n\n"
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as e:
        logging.error("Error generating YAML frontmatter: %s", e)
        return ""


def extract_title(soup: bs4.BeautifulSoup) -> Tuple[str, str]:
    """Extract guide title as (plain_text, markdown_h1) tuple."""
    title_element = soup.select_one(".workshopItemTitle")
    guide_title = safe_get_text(title_element) or "Untitled Guide"
    title_markdown = f"# {guide_title}\n\n"
    return guide_title, title_markdown


def extract_description(soup: bs4.BeautifulSoup) -> str:
    """Extract the guide description and convert to Markdown.

    Returns empty string if no description found or conversion fails.
    """
    desc_element = soup.select_one(".guideTopDescription")
    if not desc_element:
        logging.debug("Guide description element (.guideTopDescription) not found.")
        return ""

    try:
        desc_html = desc_element.decode_contents()
        temp_soup_desc = bs4.BeautifulSoup(desc_html, HTML_PARSER)

        # Remove link host spans
        for link_host_span in temp_soup_desc.find_all("span", class_="bb_link_host"):
            link_host_span.decompose()

        # Ensure images have alt text
        for img in temp_soup_desc.find_all("img"):
            alt_text = (
                img.get("alt", "").strip() or img.get("title", "").strip() or "Image"
            )
            img["alt"] = alt_text
            if "title" in img.attrs and img["alt"] == img["title"]:
                del img["title"]

        desc_html_cleaned = str(temp_soup_desc)
        description_markdown = MARKDOWN_CONVERTER.handle(desc_html_cleaned).strip()

        if description_markdown:
            logging.debug("Extracted and converted description.")
            return description_markdown + "\n\n"

        logging.debug("Description element found but resulted in empty Markdown.")
        return ""
    except (ValueError, TypeError, AttributeError, LookupError) as e:
        logging.error("Error processing description element: %s", e)
        return ""


def _preprocess_html_element(element: bs4.Tag, soup_instance: bs4.BeautifulSoup):
    """Transform Steam's custom HTML (BBCode divs, tables, etc.) into standard HTML in-place."""
    logging.debug("Preprocessing HTML element...")

    # Remove bb_link_host spans
    for link_host_span in element.find_all("span", class_="bb_link_host"):
        link_host_span.decompose()

    for img in element.find_all("img"):
        alt_text = img.get("alt", "").strip() or img.get("title", "").strip()
        src = img.get("src", "")
        if not alt_text:
            filename = urlparse(src).path.split("/")[-1] if src else ""
            alt_text = filename if filename else "Image"
        img["alt"] = alt_text

        if "title" in img.attrs and img["alt"] == img["title"]:
            del img["title"]

    tag_conversions = {
        "bb_h1": "h2",
        "bb_h3": "h4",
        "subSectionTitle": "h3",
    }
    for bb_class, html_tag in tag_conversions.items():
        for div in element.find_all("div", class_=bb_class):
            text = div.get_text(strip=True)
            if not text:
                div.decompose()
                continue
            new_tag = soup_instance.new_tag(html_tag)
            new_tag.string = text
            div.replace_with(new_tag)
            logging.debug("Converted %s to %s: %s...", bb_class, html_tag, text[:50])

    for list_tag in element.find_all(["ul", "ol"]):
        list_tag.attrs = {}

    for bb_code in element.find_all("div", class_="bb_code"):
        pre_tag = bb_code.find("pre")
        if pre_tag:
            bb_code.replace_with(pre_tag)
        else:
            new_pre = soup_instance.new_tag("pre")
            raw_content = bb_code.decode_contents(formatter=None).strip()
            new_pre.string = raw_content
            bb_code.replace_with(new_pre)

    for bb_table_div in element.find_all("div", class_="bb_table"):
        _convert_bb_table_to_html(bb_table_div, soup_instance)

    for clear_div in element.find_all("div", style="clear: both"):
        clear_div.decompose()

    logging.debug("Finished preprocessing HTML element.")


def _convert_bb_table_to_html(bb_table_div: bs4.Tag, soup_instance: bs4.BeautifulSoup):
    """Convert Steam's div-based table (.bb_table) to a standard HTML table in-place."""
    logging.debug("Processing bb_table div...")


    std_table = bb_table_div.find("table", recursive=False)
    if std_table:
        logging.debug("Found standard table inside bb_table. Replacing div.")
        bb_table_div.replace_with(std_table)
        return


    row_divs = bb_table_div.find_all("div", class_="bb_table_tr", recursive=False)
    if not row_divs:
        logging.warning(
            "bb_table div found but contains no .bb_table_tr rows. Skipping conversion."
        )
        bb_table_div.decompose()
        return

    logging.debug("Converting div-based table with %s rows...", len(row_divs))
    new_table = soup_instance.new_tag("table")
    tbody = soup_instance.new_tag("tbody")
    thead = None


    first_row_cells = row_divs[0].find_all(
        "div", class_=lambda c: c in ("bb_table_td", "bb_table_th"), recursive=False
    )
    is_header_row = any(
        "bb_table_th" in cell.get("class", []) for cell in first_row_cells
    )

    if is_header_row:
        logging.debug("First row identified as header.")
        thead = soup_instance.new_tag("thead")
        new_table.append(thead)
        header_row_div = row_divs.pop(0)
        new_header_row = soup_instance.new_tag("tr")
        header_cells_divs = header_row_div.find_all(
            "div", class_=lambda c: c in ("bb_table_td", "bb_table_th"), recursive=False
        )
        for cell_div in header_cells_divs:
            new_cell = soup_instance.new_tag("th")
            new_cell.extend(cell_div.contents)
            new_header_row.append(new_cell)
        thead.append(new_header_row)

    if row_divs:
        new_table.append(tbody)
        for row_div in row_divs:
            new_data_row = soup_instance.new_tag("tr")
            data_cells_divs = row_div.find_all(
                "div",
                class_=lambda c: c in ("bb_table_td", "bb_table_th"),
                recursive=False,
            )
            if not data_cells_divs:
                continue
            for cell_div in data_cells_divs:
                new_cell = soup_instance.new_tag("td")
                new_cell.extend(cell_div.contents)
                new_data_row.append(new_cell)
            tbody.append(new_data_row)


    bb_table_div.replace_with(new_table)
    logging.debug("Finished converting table.")


def process_main_content(soup: bs4.BeautifulSoup) -> str:
    """Extract the main guide content from div.guide.subSections and convert to Markdown.

    Returns empty string if content area not found or conversion fails.
    """
    main_content_selector = "div.guide.subSections"
    main_content_container = soup.select_one(main_content_selector)

    if not main_content_container:
        logging.error("Could not find the main guide content container.")
        return ""

    _preprocess_html_element(main_content_container, soup)

    main_content_html = main_content_container.decode_contents()

    if not main_content_html.strip():
        logging.warning(
            "Main content container found, but HTML is empty after preprocessing."
        )
        return ""


    try:
        main_content_markdown = MARKDOWN_CONVERTER.handle(main_content_html).strip()
        logging.debug("Main content conversion successful.")
        return main_content_markdown
    except (ValueError, TypeError, AttributeError, LookupError) as e:
        logging.error("Error during main content conversion: %s", e)
        return ""


def final_markdown_cleanup(markdown: str) -> str:
    """Normalize whitespace, fix heading formatting, and remove empty headings."""
    markdown = re.sub(r"\n\s*\n", "\n\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)

    markdown = re.sub(
        r"^(#+)(\*?_?)([^\s#*_].*)", r"\1 \2\3", markdown, flags=re.MULTILINE
    )
    markdown = re.sub(r"^(#+)\s{2,}(.*)", r"\1 \2", markdown, flags=re.MULTILINE)

    markdown = re.sub(r" +\n", "\n", markdown)
    markdown = re.sub(r"^#+\s*$", "", markdown, flags=re.MULTILINE)

    markdown = re.sub(r"\n{3,}", "\n\n", markdown)

    return markdown.strip()


def scrape_steam_guide(
    url: str, retries: int = 1, timeout: float = 30.0
) -> Tuple[Optional[str], Optional[str]]:
    """Scrape a single Steam guide and return (markdown_content, guide_id).

    Returns (None, guide_id) on failure. Falls back to partial content
    (frontmatter + title + description) if main content extraction fails.
    """

    guide_id = get_guide_id_from_url(url)
    if not is_valid_steam_guide_url(url):
        logging.error("Invalid Steam Community Guide URL format: %s", url)
        return None, guide_id

    html_content = fetch_html(url, retries=retries, timeout=timeout)
    if not html_content:
        return None, guide_id

    soup = parse_and_clean_soup(html_content)
    if not soup:
        return None, guide_id

    metadata = extract_metadata(soup, guide_id)
    yaml_frontmatter = generate_frontmatter(metadata)
    guide_title, title_markdown = extract_title(soup)
    description_markdown = extract_description(soup)
    main_content_markdown = process_main_content(soup)

    combined_markdown = (
        yaml_frontmatter + title_markdown + description_markdown + main_content_markdown
    )

    final_markdown = final_markdown_cleanup(combined_markdown)

    # Check if result has meaningful content beyond title/description
    base_length_estimate = len(title_markdown) + len(description_markdown) + 50
    if (
        len(final_markdown) <= base_length_estimate
        and not main_content_markdown.strip()
    ):
        logging.warning("Markdown appears empty or contains only title/description.")
        if guide_title != "Untitled Guide" or description_markdown.strip():
            logging.warning("Returning only frontmatter, title, and description.")
            return (
                final_markdown_cleanup(
                    yaml_frontmatter + title_markdown + description_markdown
                ),
                guide_id,
            )
        else:
            logging.error("Failed to extract any meaningful content.")
            return None, guide_id

    logging.info("Scraping finished successfully.")
    return final_markdown, guide_id


def construct_steam_guide_url(url_or_id: str) -> str:
    """Normalize a guide ID or URL to a full Steam Community Guide URL.

    Passes through URLs as-is; converts numeric IDs to the standard URL format.
    """
    # Check if input is already a URL
    if url_or_id.startswith(("http://", "https://")):
        return url_or_id
    if url_or_id.isdigit():
        return f"https://steamcommunity.com/sharedfiles/filedetails/?id={url_or_id}"

    logging.warning("Input '%s' doesn't appear to be a valid URL or guide ID.", url_or_id)
    return url_or_id

def fetch_guide_ids_for_game(
    game_id: str,
    delay: float,
    sort_by: str = 'trend',
    limit: Optional[int] = None,
    retries: int = 1,
    timeout: float = 30.0
) -> List[str]:
    """Fetch all English guide IDs for a game, with optional limit and pagination."""
    if limit is not None and limit <= 0:
        logging.warning("Limit provided is zero or negative, ignoring limit.")
        limit = None

    limit_str = f" (limit {limit})" if limit else ""
    logging.info(
        "Fetching guide list for game ID: %s%s...",
        game_id,
        limit_str,
    )
    guide_ids = set()
    page_num = 1
    max_pages = 1

    while page_num <= max_pages:

        if limit is not None and len(guide_ids) >= limit:
            logging.info("Reached guide limit (%s), stopping pagination.", limit)
            break


        index_url = (
            f"https://steamcommunity.com/app/{game_id}/guides/"
            f"?browsefilter={sort_by}&filetype=11&requiredtags[]=english"
            f"&numperpage=100&p={page_num}"
        )
        logging.info(
            "Fetching index page %s / %s (Sort: %s): %s",
            page_num,
            max_pages or "?",
            sort_by,
            index_url,
        )
        
        # Pass retries, timeout, and mature content cookie to fetch_html
        mature_cookies = {"wants_mature_content_apps": str(game_id), "mature_content": "1"}
        html_content = fetch_html(index_url, retries=retries, timeout=timeout, cookies=mature_cookies)
        if not html_content:
            logging.error(
                "Failed to fetch index page %s. Stopping guide ID collection.",
                page_num,
            )
            break

        soup = parse_and_clean_soup(html_content)
        if not soup:
            logging.error(
                "Failed to parse index page %s. Stopping.",
                page_num,
            )
            break

        guide_links = soup.select("a.workshopItemCollection")
        page_ids_found_this_loop = 0
        for link in guide_links:

            if limit is not None and len(guide_ids) >= limit:
                logging.info(
                    "Reached guide limit (%s) while processing page %s.",
                    limit,
                    page_num,
                )
                break

            guide_id = link.get('data-publishedfileid')
            if guide_id and guide_id.isdigit():
                if guide_id not in guide_ids:
                    guide_ids.add(guide_id)
                    page_ids_found_this_loop += 1
            else:
                # Fallback: extract from href
                href = link.get('href')
                if href:
                    extracted_id = get_guide_id_from_url(href)
                    if extracted_id and extracted_id not in guide_ids:
                        guide_ids.add(extracted_id)
                        page_ids_found_this_loop += 1

        logging.info(
            "Found %s new guide IDs on page %s. Total collected: %s",
            page_ids_found_this_loop,
            page_num,
            len(guide_ids),
        )
        if page_ids_found_this_loop == 0 and page_num > 1 and not guide_links:
            logging.warning(
                "No guides found on page %s, might indicate end or issue.",
                page_num,
            )

        if page_num == 1:
            pagination_links = soup.select("a.pagelink")
            if pagination_links:
                try:
                    last_page_text = safe_get_text(pagination_links[-1])
                    if last_page_text and last_page_text.isdigit():
                        max_pages = int(last_page_text)
                        logging.info("Determined total pages: %s", max_pages)
                    else:
                        logging.warning(
                            "Could not reliably determine max pages from pagination. Assuming 1."
                        )
                        max_pages = 1
                except (IndexError, ValueError, TypeError) as e:
                    logging.warning(
                        "Error parsing pagination, assuming 1 page: %s",
                        e,
                    )
                    max_pages = 1
            else:
                logging.info("No pagination found, assuming 1 page.")
                max_pages = 1

        if page_num >= max_pages or (limit is not None and len(guide_ids) >= limit):
            break

        page_num += 1

        sleep_time = delay * (0.5 + random.random())
        logging.debug("Waiting %.2fs before next index page request...", sleep_time)
        time.sleep(sleep_time)

    limit_str = f" (limited to {limit})" if limit else ""
    logging.info(
        "Finished fetching guide IDs. Found %s unique guides for game %s%s.",
        len(guide_ids),
        game_id,
        limit_str,
    )
    return list(guide_ids)


def process_guide_list(
    guide_inputs: List[str],
    output_dir: str,
    delay: float,
    overwrite: bool = False,
    fail_fast: bool = False,
    retries: int = 1,
    timeout: float = 30.0
) -> List[Dict[str, Any]]:
    """Scrape and save a list of guides. Skips existing files unless overwrite is True."""
    if not guide_inputs:
        logging.warning("No guide inputs provided to process.")
        return []

    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            logging.info("Created output directory: %s", output_dir)
        except OSError as e:
            logging.error("Could not create output directory %s: %s", output_dir, e)
            return []

    logging.info(
        "Processing %s guides. Output directory: %s",
        len(guide_inputs),
        output_dir,
    )
    results = []

    for i, guide_input in enumerate(guide_inputs, 1):

        potential_guide_id = None
        if isinstance(guide_input, str) and guide_input.isdigit():
            potential_guide_id = guide_input
        elif isinstance(guide_input, str) and guide_input.startswith(("http://", "https://")):
            potential_guide_id = get_guide_id_from_url(guide_input)

        if potential_guide_id:
            output_file_check = os.path.join(output_dir, f"{potential_guide_id}.md")
            if os.path.exists(output_file_check) and not overwrite:
                logging.info(
                    "Skipping guide %s: File already exists at %s",
                    potential_guide_id,
                    output_file_check,
                )
                results.append({
                    "input": guide_input,
                    "guide_id": (
                        int(potential_guide_id)
                        if potential_guide_id.isdigit()
                        else potential_guide_id
                    ),
                    "status": "skipped",
                    "output_file": output_file_check,
                })
                continue
            elif os.path.exists(output_file_check) and overwrite:
                logging.debug(
                    "File exists for guide %s but overwrite is enabled.",
                    potential_guide_id,
                )
        else:
            logging.debug(
                "Could not determine potential guide ID from input '%s' for pre-check.",
                guide_input,
            )


        url = construct_steam_guide_url(guide_input)
        logging.info("Processing guide %s/%s: %s", i, len(guide_inputs), url)

        try:
            markdown_result, guide_id_scraped = scrape_steam_guide(
                url, retries=retries, timeout=timeout
            )

            if markdown_result and guide_id_scraped:
                guide_id_str = str(guide_id_scraped)
                output_file = os.path.join(output_dir, f"{guide_id_str}.md")
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(markdown_result + "\n")
                results.append(
                    {
                        "input": guide_input,
                        "guide_id": guide_id_scraped,
                        "status": "success",
                        "output_file": output_file,
                    }
                )
                logging.info("Successfully saved guide to %s", output_file)
            else:
                results.append(
                    {
                        "input": guide_input,
                        "guide_id": guide_id_scraped,
                        "status": "failed",
                        "error": "Failed to extract guide content or guide ID",
                    }
                )
                logging.error("Failed to extract content/ID from %s", url)
        except (OSError, ValueError, TypeError, AttributeError, LookupError) as e:
            results.append(
                {
                    "input": guide_input,
                    "guide_id": None,
                    "status": "error",
                    "error": str(e),
                }
            )
            logging.error("Error processing %s: %s", url, e)

            if fail_fast:
                logging.critical("Fail-fast enabled: Exiting due to error.")
                sys.exit(1)

        if i < len(guide_inputs):
            sleep_time = delay * (0.5 + random.random())
            logging.debug("Waiting %.2fs before next guide request...", sleep_time)
            time.sleep(sleep_time)


    successful = sum(1 for r in results if r["status"] == "success")
    logging.info(
        "Processing complete. %s/%s guides successfully processed.",
        successful,
        len(guide_inputs),
    )
    return results


def main():
    """CLI entry point. Supports single guide (--input) or game-wide fetch (--game-id)."""
    parser = argparse.ArgumentParser(
        description="""
Steam Guide Scraper - Convert Steam Community Guides to Markdown with YAML frontmatter.

Modes:
  1. Single Guide: Provide a URL or guide ID using --input.
  2. Game Guides: Provide a game's App ID using --game-id to fetch all English guides.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--input",
        "-i",
        help="Single input: Steam Guide URL or guide ID.",
    )
    input_group.add_argument(
        "--game-id",
        "-g",
        help="Game input: Steam App ID of the game to fetch all English guides for.",
    )

    parser.add_argument(
        "-o",
        "--output",
        help="Output specification: "
        "With --input: Output file path (defaults to <guide_id>.md). "
        "With --game-id: Output directory (defaults to current directory).",
        default=None,
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=1.0,
        help="Minimum delay (seconds) between requests (index pages and guides). "
        "Randomized between 50%%-150%%. Default: 1.0",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help="Limit the number of guides processed when using --game-id. "
        "Fetches the first X guides found."
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing Markdown files. If not set, existing files are skipped."
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        metavar="N",
        help="Number of times to retry fetching a page on network errors (default: 1)."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Network request timeout in seconds (default: 30.0)."
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default=None,
        metavar="STRING",
        help="Custom User-Agent string for requests (overrides default)."
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Exit immediately if an error occurs during batch processing (--file or --game-id)."
    )
    parser.add_argument(
        "--sort-by",
        type=str,
        choices=['trend', 'toprated', 'mostrecent'],
        default='trend',
        metavar="FILTER",
        help="Sorting order for guides when using --game-id "
        "(choices: trend, toprated, mostrecent; default: trend)."
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Verbose logging enabled.")

    if args.user_agent:
        logging.info("Overriding User-Agent with: %s", args.user_agent)
        HEADERS["User-Agent"] = args.user_agent

    if args.game_id:
        if not args.game_id.isdigit():
            logging.error("Invalid Game ID provided: '%s'. Must be numeric.", args.game_id)
            sys.exit(1)

        output_dir = args.output if args.output else os.getcwd()
        guide_ids_to_process = fetch_guide_ids_for_game(
            args.game_id,
            args.delay,
            sort_by=args.sort_by,
            limit=args.limit,
            retries=args.retries,
            timeout=args.timeout
        )

        if not guide_ids_to_process:
            logging.warning("No guide IDs found for game %s. Exiting.", args.game_id)
            sys.exit(0)

        results = process_guide_list(
            guide_ids_to_process,
            output_dir,
            args.delay,
            overwrite=args.overwrite,
            fail_fast=args.fail_fast,
            retries=args.retries,
            timeout=args.timeout,
        )
        if not results or all(r['status'] != 'success' for r in results):
            sys.exit(1)
        sys.exit(0)

    elif args.input:
        url = construct_steam_guide_url(args.input)
        markdown_result, guide_id = scrape_steam_guide(
            url,
            retries=args.retries,
            timeout=args.timeout
        )

        if markdown_result is not None and markdown_result.strip():
            final_output = markdown_result + "\n"
            output_file = args.output
            guide_id_str = str(guide_id) if guide_id else None

            if not output_file:
                if guide_id_str:
                    output_file = f"{guide_id_str}.md"
                    logging.info("No output file specified, defaulting to: %s", output_file)
                else:
                    logging.error(
                        "Could not determine guide ID for default filename. Printing to console."
                    )
                    print("\n--- Markdown Output ---")
                    print(final_output)
                    sys.exit(0)

            logging.info("Attempting to write output to file: %s", output_file)
            try:

                output_dir = os.path.dirname(output_file)
                if output_dir and not os.path.exists(output_dir):
                    os.makedirs(output_dir)
                    logging.info("Created output directory: %s", output_dir)

                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(final_output)
                logging.info("Markdown content successfully saved to: %s", output_file)
                sys.exit(0)
            except IOError as e:
                logging.error("Error writing to file %s: %s", output_file, e)
                logging.info("Printing Markdown output to console as fallback.")
                print("\n--- Markdown Output (Fallback) ---")
                print(final_output)
                sys.exit(1)
        else:
            logging.error("Failed to scrape the guide or no meaningful content was found.")
            sys.exit(1)


if __name__ == "__main__":
    main()
