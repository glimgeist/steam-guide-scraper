#!/usr/bin/env python3

import requests
import bs4
import html2text
import yaml
import argparse
import sys
import re
import logging
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from typing import Optional, Tuple, Dict, Any, List, Union
import time
import random
import os

# --- Configuration ---

logging.basicConfig(
    level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr
)

# html2text configuration
MARKDOWN_CONVERTER = html2text.HTML2Text()
MARKDOWN_CONVERTER.body_width = 0
MARKDOWN_CONVERTER.unicode_snob = True
MARKDOWN_CONVERTER.ignore_links = False
MARKDOWN_CONVERTER.ignore_images = False
MARKDOWN_CONVERTER.images_as_html = False
MARKDOWN_CONVERTER.inline_links = True
MARKDOWN_CONVERTER.protect_links = True
MARKDOWN_CONVERTER.ignore_tables = False

# Request Headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# Try to import lxml for faster parsing
try:
    import lxml
    HTML_PARSER = "lxml"
except ImportError:
    HTML_PARSER = "html.parser"

# --- Helper Functions ---

# NOTE: This script relies heavily on the HTML structure and CSS selectors
# of the Steam Community guide pages. Changes to the Steam website may
# break the scraper, requiring updates to the selectors and parsing logic.

def is_valid_steam_guide_url(url: str) -> bool:
    """Validate if a URL is a properly formatted Steam Community Guide URL.

    This function checks if a URL matches the expected format for Steam Community
    Guides. It validates the URL structure but does not check if the guide
    actually exists on Steam.

    Args:
        url: The URL to validate

    Returns:
        True if the URL matches the expected format for Steam Community Guides,
        False otherwise.

    The function checks for:
    - Valid URL scheme (http:// or https://)
    - Correct domain (steamcommunity.com)
    - Correct path (/sharedfiles/filedetails/)
    - Presence of an 'id' query parameter

    Examples:
        Valid URLs:
        - https://steamcommunity.com/sharedfiles/filedetails/?id=2008348525
        - http://steamcommunity.com/sharedfiles/filedetails/?id=2008348525

        Invalid URLs:
        - https://steamcommunity.com/ (wrong path)
        - https://example.com/sharedfiles/filedetails/?id=2008348525 (wrong domain)
        - https://steamcommunity.com/sharedfiles/filedetails/ (missing id)
    """
    try:
        parsed = urlparse(url)
        is_valid = (
            parsed.scheme in ["http", "https"]
            and parsed.netloc == "steamcommunity.com"
            and "/sharedfiles/filedetails/" in parsed.path
            and "id=" in parsed.query
        )
        if not is_valid:
            logging.debug(f"URL validation failed for: {url}")
        return is_valid
    except ValueError as e:
        logging.debug(f"URL parsing error: {e}")
        return False


def get_guide_id_from_url(url: str) -> Optional[str]:
    """Extract the guide ID from a Steam Community Guide URL.

    This function parses a Steam Community Guide URL and extracts the numeric
    guide ID from the query parameters. It handles various URL formats and
    includes error handling for malformed URLs.

    Args:
        url: A Steam Community Guide URL containing a guide ID

    Returns:
        The extracted guide ID as a string, or None if:
        - The URL is malformed
        - The 'id' parameter is missing
        - The 'id' parameter is not numeric

    Examples:
        Input: "https://steamcommunity.com/sharedfiles/filedetails/?id=2008348525"
        Output: "2008348525"

        Input: "https://steamcommunity.com/sharedfiles/filedetails/"
        Output: None

    Note:
        This function assumes the URL is already validated as a Steam Community
        Guide URL. For validation, use is_valid_steam_guide_url() first.
    """
    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        guide_id = query_params.get("id", [None])[0]
        if guide_id and guide_id.isdigit():
            return guide_id
    except Exception as e:
        logging.debug(f"Could not extract guide ID from URL '{url}': {e}")
    return None


def safe_get_text(
    element: Optional[bs4.Tag], strip: bool = True, separator: str = " "
) -> Optional[str]:
    """Safely extract text content from a BeautifulSoup element.

    This utility function handles the common case of extracting text from
    BeautifulSoup elements while gracefully handling None values and
    providing options for text formatting.

    Args:
        element: A BeautifulSoup Tag element, or None
        strip: Whether to strip whitespace from the result (default: True)
        separator: String to use when joining multiple text nodes (default: ' ')

    Returns:
        The extracted text as a string, or None if:
        - The element is None
        - The element has no text content

    This function is a safe alternative to directly calling get_text()
    on elements that might be None, preventing AttributeError exceptions.
    """
    return element.get_text(strip=strip, separator=separator) if element else None


def safe_get_number(text: Optional[str]) -> Optional[int]:
    """Extract the first integer from a string, handling various formats.

    This utility function extracts numeric values from strings that might
    contain additional text or formatting. It's useful for parsing Steam's
    various numeric statistics that are often displayed with labels or
    formatting.

    Args:
        text: A string potentially containing a number, or None

    Returns:
        The first integer found in the string, or None if:
        - The input is None
        - No numeric value is found
        - The numeric value cannot be converted to an integer

    Examples:
        Input: "1,234 ratings"
        Output: 1234

        Input: "Favorite (5)"
        Output: 5

        Input: "No ratings yet"
        Output: None
    """
    if not text:
        return None
    try:
        cleaned = re.sub(r"[^\d]", "", text)
        return int(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def parse_steam_date(date_str: Optional[str]) -> Optional[str]:
    """Parse Steam's date format into a standardized YYYY-MM-DD string.

    This function handles various date formats used by Steam Community Guides,
    converting them to a consistent YYYY-MM-DD format. It includes support for
    multiple date formats and handles edge cases like missing years.

    Args:
        date_str: A string containing a date in Steam's format, or None

    Returns:
        A string in YYYY-MM-DD format, or None if parsing fails.

    Supported Formats (Case-insensitive):
    - "Feb 27, 2020 @ 12:10am"
    - "Apr 11, 2022 @ 12:50pm"
    - "10 Jul, 2024 @ 2:07am"
    - "Oct 26, 2023 @ 5:01pm"
    - "6 Apr @ 6:28pm" (Assumes current year)
    - "Feb 27, 2020" (Date only)
    """
    if not date_str:
        return None
    date_str = date_str.strip()

    # Define patterns and their corresponding strptime formats
    # Order matters: More specific patterns first
    patterns = [
        # Format: Month Day, Year @ Time (e.g., "Feb 27, 2020 @ 12:10am")
        (r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4})\s+@.*", "%b %d, %Y"),
        # Format: Day Month, Year @ Time (e.g., "10 Jul, 2024 @ 2:07am")
        (r"(\d{1,2}\s+[A-Za-z]{3},\s+\d{4})\s+@.*", "%d %b, %Y"),
        # Format: Month Day, Year (Date only)
        (r"^([A-Za-z]{3}\s+\d{1,2},\s+\d{4})$", "%b %d, %Y"),
        # Format: Day Month, Year (Date only)
        (r"^(\d{1,2}\s+[A-Za-z]{3},\s+\d{4})$", "%d %b, %Y"),
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
                    f"Failed to parse date '{date_part}' with format '{date_format}': {e}"
                )
                # Continue to next pattern if this one fails

    # Format: Day Mon @ time (e.g., "6 Apr @ 6:28pm", assume current year)
    no_year_pattern_time = re.compile(
        r"^(\d{1,2}\s+[A-Za-z]{3})\s+@\s+\d{1,2}:\d{2}(?:am|pm)$", re.IGNORECASE
    )
    match_no_year_time = no_year_pattern_time.match(date_str)
    if match_no_year_time:
        date_part_no_year = match_no_year_time.group(1)
        current_year = datetime.now().year
        date_with_year = f"{date_part_no_year}, {current_year}"
        # Determine format based on whether day or month comes first
        day_first_match = re.match(r"^\d{1,2}", date_part_no_year)
        date_format = "%d %b, %Y" if day_first_match else "%b %d, %Y"
        try:
            dt_obj = datetime.strptime(date_with_year, date_format)
            logging.debug(f"Parsed date '{date_str}' assuming current year: {current_year}")
            return dt_obj.strftime("%Y-%m-%d")
        except ValueError as e:
            logging.debug(
                f"Failed to parse date '{date_with_year}' with assumed year format '{date_format}': {e}"
            )

    logging.warning(f"Could not parse date from string: '{date_str}' using any known format.")
    return None


# --- Core Scraping Logic ---


def fetch_html(url: str, retries: int = 1, timeout: float = 30.0) -> Optional[str]:
    """Fetch HTML content from the given URL with retries.

    Args:
        url: The URL to fetch.
        retries: Number of times to retry on network errors (0 means 1 attempt).
        timeout: Request timeout in seconds.

    Returns:
        HTML content as string or None if fetching fails after retries.
    """
    logging.debug(f"Fetching: {url} (Timeout: {timeout}s, Retries: {retries})")
    last_exception = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout)
            logging.debug(f"Attempt {attempt + 1}/{retries + 1}: Status {response.status_code} for {url}")
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            logging.info(f"Successfully fetched URL: {url}")
            return response.text
        except requests.exceptions.RequestException as e:
            last_exception = e
            logging.warning(f"Attempt {attempt + 1}/{retries + 1} failed for {url}: {e}")
            if attempt < retries:
                sleep_time = min(10.0, (1.5 ** attempt) + random.uniform(0.1, 0.5))
                logging.debug(f"Waiting {sleep_time:.2f}s before retrying...")
                time.sleep(sleep_time)
            else:
                logging.error(f"Fetching finally failed after {retries + 1} attempts for URL {url}.")
        except Exception as e:
            logging.error(f"Unexpected error during request/encoding for {url} on attempt {attempt + 1}: {e}")
            last_exception = e
            break

    if last_exception:
        logging.error(f"Final error fetching {url}: {last_exception}")
    return None


def parse_and_clean_soup(html_content: str) -> Optional[bs4.BeautifulSoup]:
    """Parses HTML and removes unwanted tags."""
    logging.debug("Parsing HTML content...")
    try:
        soup = bs4.BeautifulSoup(html_content, HTML_PARSER)
        logging.debug(f"HTML parsing successful using '{HTML_PARSER}'.")
        # Remove scripts, styles, and comments
        for element in soup(["script", "style"]):
            element.decompose()
        comments = soup.find_all(string=lambda text: isinstance(text, bs4.Comment))
        for comment in comments:
            comment.extract()
        logging.debug("Removed script/style tags and comments.")
        return soup
    except Exception as e:
        logging.error(f"Failed to parse HTML: {e}")
        return None


def _extract_game_id(soup: bs4.BeautifulSoup, guide_id: Optional[str]) -> Optional[Union[int, str]]:
    """Extract the Steam App ID from the guide page."""
    log_prefix = f"[Game ID Extraction (Guide {guide_id or 'N/A'})]"
    
    # 1. Try input[name="appid"]
    appid_inputs = soup.select('input[name="appid"]')
    for input_elem in appid_inputs:
        val = input_elem.get("value")
        if val and val.isdigit():
            logging.debug(f"{log_prefix} Success (Method 1): Found game_id '{val}'.")
            return int(val)
            
    # 2. Try store button link
    store_button = soup.select_one('a.btnv6_blue_hoverfade[data-appid][href*="/app/"]')
    if store_button:
        data_appid = store_button.get('data-appid')
        if data_appid and data_appid.isdigit():
            logging.debug(f"{log_prefix} Success (Method 2): Found game_id '{data_appid}'.")
            return int(data_appid)
        
        href = store_button.get('href')
        if href:
            match = re.search(r"/app/(\d+)", href)
            if match:
                logging.debug(f"{log_prefix} Success (Method 2 fallback): Found game_id '{match.group(1)}'.")
                return int(match.group(1))

    # 3. Try guide link
    any_guide_link = soup.select_one('a.workshopItemCollection[data-appid]')
    if any_guide_link:
        data_appid = any_guide_link.get('data-appid')
        if data_appid and data_appid.isdigit():
            logging.debug(f"{log_prefix} Success (Method 3): Found game_id '{data_appid}'.")
            return int(data_appid)

    # 4. Try JS variables
    logging.debug(f"{log_prefix} Trying Method 4: JS variables.")
    html_content = str(soup)
    for pattern in [r"g_steamIDAppID\s*=\s*['\"]?(\d+)['\"]?", r"ShowModalContent\s*\(\s*[^,]+,\s*['\"](\d+)['\"]", r'"appid"\s*:\s*(\d+)']:
        match = re.search(pattern, html_content)
        if match and match.group(1):
            logging.debug(f"{log_prefix} Success (Method 4): Found game_id '{match.group(1)}'.")
            return int(match.group(1))

    logging.warning(f"{log_prefix} FAILED: Could not determine Game ID.")
    return None

def _extract_authors(soup: bs4.BeautifulSoup) -> Tuple[List[str], str]:
    """Extract the author list and fallback author string."""
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

    # Fallback to old method
    author_elem = soup.select_one(".guideAuthors")
    author_text = safe_get_text(author_elem)
    author_str = author_text.replace("By ", "").strip() if author_text else "Unknown Author"
    return [author_str], author_str

def _extract_tags_and_languages(soup: bs4.BeautifulSoup) -> Tuple[Any, Any]:
    """Extract categories and languages tags."""
    categories, languages = [], []
    for tag_elem in soup.select(".rightDetailsBlock .workshopTags"):
        title_span = tag_elem.select_one(".workshopTagsTitle")
        if not title_span:
            continue
            
        title_text = safe_get_text(title_span, strip=True).lower()
        link_texts = [text for link in tag_elem.find_all("a") if (text := safe_get_text(link, strip=True))]
        
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
                if parsed_date: post_date = parsed_date
                else: logging.warning(f"Could not parse post date: {date_text}")
            elif label_text == "Updated":
                if parsed_date: update_date = parsed_date
                else: logging.warning(f"Could not parse update date: {date_text}")

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
        match = re.search(r"(\\d)-star_large\\.png", rating_elem["src"])
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

    comments = safe_get_number(safe_get_text(soup.select_one('.commentthread_count_label span[id$="_totalcount"]'))) or 0
    
    return rating, num_ratings, unique_visitors, current_favorites, comments

def extract_metadata(
    soup: bs4.BeautifulSoup, guide_id: Optional[str] = None
) -> Dict[str, Any]:
    """Extract metadata from a Steam Community Guide page.

    This function parses the HTML of a Steam Community Guide page and extracts
    all available metadata into a structured dictionary. The metadata includes
    information about the guide, its authors, the associated game, and various
    statistics.

    Args:
        soup: A BeautifulSoup object containing the parsed HTML of the guide page
        guide_id: Optional guide ID to include in the metadata. If not provided,
                 it will be extracted from the page if possible.

    Returns:
        A dictionary containing all extracted metadata fields. The dictionary
        includes the following fields (all fields are optional and may be None
        if not found):

        Basic Information:
        - guide_id: The unique identifier for the guide (as int if possible)
        - guide_title: The title of the guide
        - game_title: The title of the associated game
        - game_id: The Steam App ID of the associated game (as string)

        Author Information:
        - authors: List of all authors' names

        Dates:
        - post_date: The date the guide was originally posted (YYYY-MM-DD)
        - update_date: The date the guide was last updated (YYYY-MM-DD)

        Statistics:
        - rating: The guide's star rating (0-5)
        - num_ratings: The number of ratings received
        - unique_visitors: The number of unique visitors
        - current_favorites: The number of current favorites
        - comments: The number of comments

        Classification:
        - category: The guide's category/categories (string or list)
        - languages: List of languages the guide is available in (string or list)

    The function uses multiple methods to extract each piece of metadata,
    with fallback strategies when the primary method fails. It includes
    extensive error handling and logging to help diagnose extraction issues.
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
    """Generate YAML frontmatter from metadata dictionary.

    This function converts a dictionary of metadata into a YAML-formatted
    string suitable for use as frontmatter in Markdown files. It handles
    various data types and includes error handling for invalid data. It excludes
    the redundant 'author' field.

    Args:
        metadata: A dictionary containing guide metadata. Keys should be
                 strings, and values can be strings, numbers, lists, or None.
                 Common keys include:
                 - guide_id, guide_title, game_title, game_id
                 - authors, post_date, update_date
                 - rating, num_ratings, unique_visitors
                 - current_favorites, comments, category, languages

    Returns:
        A string containing the YAML frontmatter, wrapped in '---' markers.
        The string will be empty if there's an error generating the YAML.

    The generated YAML will:
    - Preserve Unicode characters
    - Use block style for lists and dictionaries
    - Sort keys alphabetically (by default, override if needed)
    - Handle None values appropriately (represented as null or empty)
    - Exclude the 'author' field.

    Example output:
        ---
        authors:
        - paperrabbit
        category: Walkthroughs
        comments: 66
        current_favorites: 197
        game_id: '913740'
        game_title: WORLD OF HORROR
        guide_id: 2008348525 # Note: guide_id is now numeric
        guide_title: Events Codex
        languages: English
        num_ratings: 97
        post_date: '2020-02-27'
        rating: 4
        unique_visitors: 6269
        update_date: '2022-04-11'
        ---
    """
    try:
        # Create a copy to avoid modifying the original dict
        metadata_to_dump = metadata.copy()
        # Remove the 'author' and 'languages' keys if they exist before dumping
        metadata_to_dump.pop('author', None)
        metadata_to_dump.pop('languages', None)

        # Optional: Define the desired order of keys for consistent output
        # Remove 'languages' from the desired order
        key_order = [
            'game_id', 'game_title', 'guide_id', 'guide_title', 'authors',
            'post_date', 'update_date', 'category', 'rating', 'num_ratings',
            'unique_visitors', 'current_favorites', 'comments' # Removed 'languages'
        ]
        # Sort the dictionary based on the desired key order
        sorted_metadata = {k: metadata_to_dump[k] for k in key_order if k in metadata_to_dump}
        # Add any remaining keys not in the order list (maintains them alphabetically)
        for k in sorted(metadata_to_dump.keys()):
             if k not in sorted_metadata:
                 sorted_metadata[k] = metadata_to_dump[k]


        yaml_string = yaml.dump(
            sorted_metadata, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        logging.debug("Generated YAML frontmatter.")
        return f"---\n{yaml_string}---\n\n"
    except Exception as e:
        logging.error(f"Error generating YAML frontmatter: {e}")
        return ""


def extract_title(soup: bs4.BeautifulSoup) -> Tuple[str, str]:
    """Extract and format the guide title from the HTML.

    This function extracts the guide's title from the HTML and formats it
    as both a plain string and a Markdown H1 heading. It includes fallback
    behavior for cases where the title cannot be found.

    Args:
        soup: A BeautifulSoup object containing the parsed guide HTML

    Returns:
        A tuple containing:
        1. The plain title text (or "Untitled Guide" if not found)
        2. The title formatted as a Markdown H1 heading

    The function:
    - Looks for the title in the .workshopItemTitle element
    - Strips any leading/trailing whitespace
    - Formats the title as a Markdown H1 heading (# Title)
    - Provides a default value if the title cannot be found

    Example:
        Input HTML: <div class="workshopItemTitle">My Guide Title</div>
        Returns: ("My Guide Title", "# My Guide Title\n\n")
    """
    title_element = soup.select_one(".workshopItemTitle")
    guide_title = safe_get_text(title_element) or "Untitled Guide"
    title_markdown = f"# {guide_title}\n\n"
    return guide_title, title_markdown


def extract_description(soup: bs4.BeautifulSoup) -> str:
    """Extract and convert the guide description to Markdown.

    This function processes the guide's description section, handling Steam's
    custom HTML formatting and converting it to clean Markdown. It includes
    special handling for various HTML elements and formatting.

    Args:
        soup: A BeautifulSoup object containing the parsed guide HTML

    Returns:
        A string containing the description in Markdown format, or an empty
        string if no description is found or if conversion fails.

    The function:
    1. Locates the description in the .guideTopDescription element
    2. Cleans up the HTML by:
       - Removing link host spans
       - Ensuring images have proper alt text
       - Handling Steam's custom formatting
    3. Converts the cleaned HTML to Markdown
    4. Adds appropriate spacing and formatting

    The resulting Markdown will:
    - Preserve links and images
    - Maintain basic formatting (bold, italic, lists)
    - Include proper spacing between elements
    - Handle nested HTML structures appropriately
    """
    desc_element = soup.select_one(".guideTopDescription")
    if not desc_element:
        logging.debug("Guide description element (.guideTopDescription) not found.")
        return ""

    try:
        # Create a temporary soup for description cleanup
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
            # Remove title if it's the same as alt to avoid redundancy in Markdown
            if "title" in img.attrs and img["alt"] == img["title"]:
                del img["title"]

        desc_html_cleaned = str(temp_soup_desc)
        description_markdown = MARKDOWN_CONVERTER.handle(desc_html_cleaned).strip()

        if description_markdown:
            logging.debug("Extracted and converted description.")
            return description_markdown + "\n\n"
        else:
            logging.debug("Description element found but resulted in empty Markdown.")
            return ""
    except Exception as e:
        logging.error(f"Error processing description element: {e}")
        return ""


def _preprocess_html_element(element: bs4.Tag, soup_instance: bs4.BeautifulSoup):
    """Preprocess an HTML element before Markdown conversion.

    This internal function applies a series of transformations to an HTML
    element to prepare it for conversion to Markdown. It handles Steam's
    custom HTML formatting and converts it to more standard HTML that
    can be better converted to Markdown.

    Args:
        element: The BeautifulSoup Tag element to preprocess
        soup_instance: The BeautifulSoup instance used to create new tags

    The preprocessing steps include:
    1. Link handling:
       - Removing link host spans
       - Preserving link text and URLs
    2. Image processing:
       - Ensuring images have proper alt text
       - Removing redundant title attributes
    3. Heading conversion:
       - Converting Steam's custom heading divs to standard HTML headings
       - Preserving heading hierarchy
    4. List standardization:
       - Removing custom list classes
       - Ensuring proper list structure
    5. Code block handling:
       - Converting Steam's code blocks to standard <pre> tags
       - Preserving code formatting
    6. Table conversion:
       - Converting Steam's custom table divs to standard HTML tables
       - Preserving table structure and content
    7. Cleanup:
       - Removing unnecessary divs
       - Cleaning up whitespace

    This function modifies the element in place and does not return a value.
    """
    logging.debug("Preprocessing HTML element...")

    # Remove bb_link_host spans
    for link_host_span in element.find_all("span", class_="bb_link_host"):
        link_host_span.decompose()

    # Pre-process images (ensure alt text)
    for img in element.find_all("img"):
        alt_text = img.get("alt", "").strip() or img.get("title", "").strip()
        src = img.get("src", "")
        if not alt_text:
            filename = urlparse(src).path.split("/")[-1] if src else ""
            alt_text = f"{filename}" if filename else "Image"
        img["alt"] = alt_text
        # Remove title if it's the same as alt
        if "title" in img.attrs and img["alt"] == img["title"]:
            del img["title"]

    # Convert Steam BBCode divs to standard HTML tags
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
            logging.debug(f"Converted {bb_class} to {html_tag}: {text[:50]}...")

    # Standardize lists (remove bb_ classes)
    for list_tag in element.find_all(["ul", "ol"]):
        list_tag.attrs = {}

    # Convert bb_code blocks to <pre>
    for bb_code in element.find_all("div", class_="bb_code"):
        pre_tag = bb_code.find("pre")
        if pre_tag:
            bb_code.replace_with(pre_tag)  # Use existing <pre>
        else:
            # Create new <pre> and preserve raw content
            new_pre = soup_instance.new_tag("pre")
            raw_content = bb_code.decode_contents(formatter=None).strip()
            new_pre.string = raw_content
            bb_code.replace_with(new_pre)

    # Convert div-based tables (bb_table) to standard HTML tables
    for bb_table_div in element.find_all("div", class_="bb_table"):
        _convert_bb_table_to_html(bb_table_div, soup_instance)

    # Remove clear:both divs
    for clear_div in element.find_all("div", style="clear: both"):
        clear_div.decompose()

    logging.debug("Finished preprocessing HTML element.")


def _convert_bb_table_to_html(bb_table_div: bs4.Tag, soup_instance: bs4.BeautifulSoup):
    """Convert Steam's custom table div structure to standard HTML table.

    This internal function converts Steam's custom div-based table structure
    to a standard HTML table element. It handles various table formats and
    preserves the table's structure, content, and formatting.

    Args:
        bb_table_div: The BeautifulSoup Tag containing the Steam table div
        soup_instance: The BeautifulSoup instance used to create new tags

    The conversion process:
    1. Checks for existing standard table (uses it if found)
    2. Identifies table rows and cells
    3. Determines header row (if present)
    4. Creates standard HTML table structure:
       - <table>, <thead>, <tbody>, <tr>, <th>, <td>
    5. Preserves cell content and formatting
    6. Handles edge cases:
       - Empty rows
       - Mixed header/data cells
       - Nested content

    This function modifies the bb_table_div in place, replacing it with
    a standard HTML table structure.
    """
    logging.debug("Processing bb_table div...")

    # If a standard <table> already exists inside, use it directly
    std_table = bb_table_div.find("table", recursive=False)
    if std_table:
        logging.debug("Found standard table inside bb_table. Replacing div.")
        bb_table_div.replace_with(std_table)
        return

    # Find direct child rows (.bb_table_tr)
    row_divs = bb_table_div.find_all("div", class_="bb_table_tr", recursive=False)
    if not row_divs:
        logging.warning(
            "bb_table div found but contains no .bb_table_tr rows. Skipping conversion."
        )
        bb_table_div.decompose()  # Remove the empty table structure
        return

    logging.debug(
        f"Found div-based table structure with {len(row_divs)} rows. Converting..."
    )
    new_table = soup_instance.new_tag("table")
    tbody = soup_instance.new_tag("tbody")
    thead = None

    # Check if the first row is a header row (contains .bb_table_th)
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
        header_row_div = row_divs.pop(0)  # Remove header row from list
        new_header_row = soup_instance.new_tag("tr")
        header_cells_divs = header_row_div.find_all(
            "div", class_=lambda c: c in ("bb_table_td", "bb_table_th"), recursive=False
        )
        for cell_div in header_cells_divs:
            new_cell = soup_instance.new_tag("th")
            new_cell.extend(cell_div.contents)
            new_header_row.append(new_cell)
        thead.append(new_header_row)

    # Process remaining rows as data rows (or all rows if no header)
    if row_divs:  # Check if there are data rows left
        new_table.append(tbody)
        for row_div in row_divs:
            new_data_row = soup_instance.new_tag("tr")
            data_cells_divs = row_div.find_all(
                "div",
                class_=lambda c: c in ("bb_table_td", "bb_table_th"),
                recursive=False,
            )
            if not data_cells_divs:
                continue  # Skip empty rows
            for cell_div in data_cells_divs:
                new_cell = soup_instance.new_tag("td")
                new_cell.extend(cell_div.contents)
                new_data_row.append(new_cell)
            tbody.append(new_data_row)

    # Replace the original bb_table div with the new standard table
    bb_table_div.replace_with(new_table)
    logging.debug("Finished converting table.")


def process_main_content(soup: bs4.BeautifulSoup) -> str:
    """Process the main content of the guide and convert it to Markdown.

    This function handles the core content of the Steam guide, converting
    Steam's custom HTML formatting to clean Markdown while preserving the
    structure and formatting of the original content.

    Args:
        soup: A BeautifulSoup object containing the parsed guide HTML

    Returns:
        A string containing the main content in Markdown format, or an empty
        string if the content cannot be found or processed.

    The function:
    1. Locates the main content in the .guide.subSections element
    2. Applies preprocessing to handle Steam's custom formatting:
       - Converts Steam's BBCode-like divs to standard HTML
       - Processes tables and code blocks
       - Handles images and links
       - Manages lists and headings
    3. Converts the processed HTML to Markdown
    4. Applies final cleanup to ensure consistent formatting

    The resulting Markdown will:
    - Preserve the original content structure
    - Convert Steam's custom formatting to standard Markdown
    - Handle complex elements like tables and code blocks
    - Maintain proper heading hierarchy
    - Include appropriate spacing and formatting

    Note:
        This function relies on the html2text converter configured at the
        module level (MARKDOWN_CONVERTER) for the actual HTML-to-Markdown
        conversion.
    """
    main_content_selector = "div.guide.subSections"
    logging.debug(
        f"Attempting to find main content container: '{main_content_selector}'"
    )
    main_content_container = soup.select_one(main_content_selector)

    if not main_content_container:
        logging.error("Could not find the main guide content container.")
        return ""  # Return empty string if content area not found

    # Preprocess the HTML within the container
    _preprocess_html_element(main_content_container, soup)

    # Get the processed HTML string
    main_content_html = main_content_container.decode_contents()

    if not main_content_html.strip():
        logging.warning(
            "Main content container found, but HTML is empty after preprocessing."
        )
        return ""

    # Convert the processed HTML to Markdown
    logging.debug("Converting main content HTML to Markdown...")
    try:
        main_content_markdown = MARKDOWN_CONVERTER.handle(main_content_html).strip()
        logging.debug("Main content conversion successful.")
        return main_content_markdown
    except Exception as e:
        logging.error(f"Error during main content conversion: {e}")
        return ""  # Return empty on conversion error


def final_markdown_cleanup(markdown: str) -> str:
    """Apply final cleanup operations to the generated Markdown.

    This function performs a series of cleanup operations on the Markdown
    to ensure consistent formatting and remove any artifacts from the
    HTML-to-Markdown conversion process.

    Args:
        markdown: The Markdown string to clean up

    Returns:
        A cleaned-up version of the input Markdown string

    The cleanup operations include:
    1. Collapsing excessive newlines:
       - Multiple blank lines -> two newlines
       - Lines with only spaces -> single newline
    2. Fixing heading formatting:
       - Ensuring space after # markers
       - Removing extra spaces in headings
       - Removing empty headings
    3. Removing trailing whitespace from lines
    4. Ensuring consistent spacing around elements

    The goal is to produce clean, consistent Markdown that:
    - Is easy to read in source form
    - Renders correctly in Markdown viewers
    - Follows common Markdown style conventions
    - Has minimal unnecessary whitespace
    """
    logging.debug("Performing final Markdown cleanup...")
    # Collapse excessive newlines (more robust)
    markdown = re.sub(r"\n\s*\n", "\n\n", markdown)  # Blanks lines with spaces
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)  # 3+ newlines -> 2

    # Ensure space after heading markers (e.g., #Heading)
    # Handles optional bold/italic markers immediately after #
    markdown = re.sub(
        r"^(#+)(\*?_?)([^\s#*_].*)", r"\1 \2\3", markdown, flags=re.MULTILINE
    )
    # Reduce multiple spaces after heading marker (e.g., #   Heading)
    markdown = re.sub(r"^(#+)\s{2,}(.*)", r"\1 \2", markdown, flags=re.MULTILINE)

    # Remove trailing whitespace from lines
    markdown = re.sub(r" +\n", "\n", markdown)

    # Remove empty headings (e.g., ### on a line by itself)
    markdown = re.sub(r"^#+\s*$", "", markdown, flags=re.MULTILINE)
    # Collapse newlines again after removing empty headings
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)

    return markdown.strip()  # Return stripped final result


def scrape_steam_guide(url: str, retries: int = 1, timeout: float = 30.0) -> Tuple[Optional[str], Optional[str]]:
    """Scrape a Steam Community Guide and convert it to Markdown.

    This is the core function that handles the entire scraping process for a single guide.
    It fetches the guide's HTML, extracts metadata and content, and converts everything
    to a well-formatted Markdown file with YAML frontmatter.

    Args:
        url: The Steam Community Guide URL to scrape. This can be either a full URL
             or a numeric guide ID (which will be converted to a URL).
        retries: Number of times to retry on network errors (0 means 1 attempt).
        timeout: Request timeout in seconds.

    Returns:
        A tuple containing:
        - The complete Markdown content as a string, or None if scraping failed
        - The guide ID as a string, or None if it couldn't be extracted

    The function performs the following steps:
    1. Validates the input URL and extracts the guide ID
    2. Fetches the guide's HTML content
    3. Parses and cleans the HTML
    4. Extracts all available metadata
    5. Converts the content to Markdown
    6. Combines everything into a single Markdown document with YAML frontmatter

    Error Handling:
    - Invalid URLs are logged and return (None, None)
    - Network errors are caught and logged
    - Parsing errors are caught and logged
    - If content extraction fails, the function will return partial results
      (frontmatter and title) if available

    The function includes extensive logging to help diagnose issues.
    """

    guide_id = get_guide_id_from_url(url)
    if not is_valid_steam_guide_url(url):
        logging.error(f"Invalid Steam Community Guide URL format: {url}")
        return (
            None,
            guide_id,
        )  # Return ID even if URL is invalid for potential filename use

    html_content = fetch_html(url, retries=retries, timeout=timeout)
    if not html_content:
        return None, guide_id  # Error already logged in fetch_html

    soup = parse_and_clean_soup(html_content)
    if not soup:
        return None, guide_id  # Error already logged in parse_and_clean_soup

    metadata = extract_metadata(soup, guide_id)
    yaml_frontmatter = generate_frontmatter(metadata)
    guide_title, title_markdown = extract_title(soup)
    description_markdown = extract_description(soup)
    main_content_markdown = process_main_content(soup)

    # Combine the parts
    combined_markdown = (
        yaml_frontmatter + title_markdown + description_markdown + main_content_markdown
    )

    # Final cleanup
    final_markdown = final_markdown_cleanup(combined_markdown)

    # Check if the result is meaningful (more than just headers/empty content)
    # Consider base length slightly differently, frontmatter might be empty on error
    base_length_estimate = (
        len(title_markdown) + len(description_markdown) + 50
    )  # Base + buffer
    if (
        len(final_markdown) <= base_length_estimate
        and not main_content_markdown.strip()
    ):
        logging.warning(
            "Resulting markdown appears empty or contains only title/description."
        )
        # Return title/desc if available and main content failed, otherwise None
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


# --- Main Execution ---


def construct_steam_guide_url(url_or_id: str) -> str:
    """Convert a guide ID or URL to a full Steam Community Guide URL.

    This function handles both URL and ID inputs, ensuring that a valid
    Steam Community Guide URL is returned. It's used to normalize input
    before processing.

    Args:
        url_or_id: Either a full Steam Community Guide URL or a numeric guide ID.
                  Examples:
                  - URL: "https://steamcommunity.com/sharedfiles/filedetails/?id=2008348525"
                  - ID: "2008348525"

    Returns:
        A complete Steam Community Guide URL. If the input is already a URL,
        it's returned as-is. If it's a numeric ID, it's converted to the
        appropriate URL format.

    Note:
        The function does not validate whether the guide actually exists
        on Steam - it only ensures the URL format is correct.
    """
    # Check if input is already a URL
    if url_or_id.startswith(("http://", "https://")):
        return url_or_id

    # Check if input is a numeric ID
    if url_or_id.isdigit():
        return f"https://steamcommunity.com/sharedfiles/filedetails/?id={url_or_id}"

    # If it's neither a URL nor a numeric ID, return as is (will fail validation later)
    logging.warning(
        f"Input '{url_or_id}' doesn't appear to be a valid URL or guide ID."
    )
    return url_or_id


# New function to fetch all guide IDs for a game
def fetch_guide_ids_for_game(
    game_id: str, 
    delay: float, 
    sort_by: str = 'trend', 
    limit: Optional[int] = None, 
    retries: int = 1, 
    timeout: float = 30.0
) -> List[str]:
    """Fetches all English guide IDs for a given Steam game ID, up to an optional limit.

    Handles pagination on the Steam Community guides page.

    Args:
        game_id: The Steam App ID of the game.
        delay: Minimum delay between fetching index pages.
        sort_by: Sorting order for guides.
        limit: Optional maximum number of guide IDs to fetch.
        retries: Number of times to retry on network errors (0 means 1 attempt).
        timeout: Request timeout in seconds.

    Returns:
        A list of unique guide IDs (as strings).
    """
    if limit is not None and limit <= 0:
        logging.warning("Limit provided is zero or negative, ignoring limit.")
        limit = None

    logging.info(f"Fetching guide list for game ID: {game_id}{f' (limit {limit})' if limit else ''}...")
    guide_ids = set()
    page_num = 1
    max_pages = 1 # Start assuming one page

    while page_num <= max_pages:
        # Check if limit is already reached before fetching the page
        if limit is not None and len(guide_ids) >= limit:
            logging.info(f"Reached guide limit ({limit}), stopping pagination.")
            break

        # Construct URL with sort_by
        index_url = (
            f"https://steamcommunity.com/app/{game_id}/guides/"
            f"?browsefilter={sort_by}&filetype=11&requiredtags[]=english"
            f"&numperpage=100&p={page_num}"
        )
        logging.info(f"Fetching index page {page_num} / {max_pages or '?'} (Sort: {sort_by}): {index_url}")

        # Pass retries and timeout to fetch_html
        html_content = fetch_html(index_url, retries=retries, timeout=timeout) 
        if not html_content:
            logging.error(f"Failed to fetch index page {page_num}. Stopping guide ID collection.")
            break # Stop if a page fails
            
        soup = parse_and_clean_soup(html_content)
        if not soup:
            logging.error(f"Failed to parse index page {page_num}. Stopping.")
            break # Stop if parsing fails

        # Find guide links on the current page
        guide_links = soup.select("a.workshopItemCollection")
        page_ids_found_this_loop = 0
        for link in guide_links:
            # Check if limit is reached within the page loop
            if limit is not None and len(guide_ids) >= limit:
                logging.info(f"Reached guide limit ({limit}) while processing page {page_num}.")
                break # Stop processing links on this page

            guide_id = link.get('data-publishedfileid')
            if guide_id and guide_id.isdigit():
                if guide_id not in guide_ids:
                    guide_ids.add(guide_id)
                    page_ids_found_this_loop += 1
            else:
                # Fallback to extracting from href if data attribute missing
                href = link.get('href')
                if href:
                    extracted_id = get_guide_id_from_url(href)
                    if extracted_id and extracted_id not in guide_ids:
                        guide_ids.add(extracted_id)
                        page_ids_found_this_loop += 1

        logging.info(f"Found {page_ids_found_this_loop} new guide IDs on page {page_num}. Total collected: {len(guide_ids)}")
        if page_ids_found_this_loop == 0 and page_num > 1 and not guide_links:
             # Only warn if no links were present at all on a later page
             logging.warning(f"No guides found on page {page_num}, might indicate end or issue.")

        # Determine max pages only on the first iteration
        if page_num == 1:
            pagination_links = soup.select("a.pagelink")
            if pagination_links:
                try:
                    # Get the text (page number) of the last pagination link
                    last_page_text = safe_get_text(pagination_links[-1])
                    if last_page_text and last_page_text.isdigit():
                        max_pages = int(last_page_text)
                        logging.info(f"Determined total pages: {max_pages}")
                    else:
                        logging.warning("Could not reliably determine max pages from pagination. Assuming 1.")
                        max_pages = 1 # Fallback if last link isn't a number
                except (IndexError, ValueError, TypeError) as e:
                    logging.warning(f"Error parsing pagination, assuming 1 page: {e}")
                    max_pages = 1
            else:
                logging.info("No pagination found, assuming 1 page.")
                max_pages = 1 # No pagination links, only one page

        # Check if we need to continue pagination (and limit not reached)
        if page_num >= max_pages or (limit is not None and len(guide_ids) >= limit):
            break

        page_num += 1

        # Apply delay before fetching the next page
        sleep_time = delay * (0.5 + random.random()) # Randomize delay
        logging.debug(f"Waiting {sleep_time:.2f}s before next index page request...")
        time.sleep(sleep_time)

    logging.info(f"Finished fetching guide IDs. Found {len(guide_ids)} unique guides for game {game_id}{f' (limited to {limit})' if limit else ''}.")
    return list(guide_ids)


# Refactored processing function
def process_guide_list(
    guide_inputs: List[str], 
    output_dir: str, 
    delay: float, 
    overwrite: bool = False,
    fail_fast: bool = False,
    retries: int = 1,
    timeout: float = 30.0
) -> List[Dict[str, Any]]:
    """Processes a list of guide URLs or IDs, scraping and saving them.

    Skips existing files by default unless overwrite is True.

    Args:
        guide_inputs: List of guide URLs or IDs.
        output_dir: Directory to save output files.
        delay: Minimum delay between scraping individual guides.
        overwrite: Whether to overwrite existing Markdown files.
        fail_fast: Whether to exit immediately if an error occurs.
        retries: Number of times to retry on network errors.
        timeout: Request timeout in seconds.

    Returns:
        A list of dictionaries containing processing results for each guide.
    """
    if not guide_inputs:
        logging.warning("No guide inputs provided to process.")
        return []

    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            logging.info(f"Created output directory: {output_dir}")
        except OSError as e:
            logging.error(f"Could not create output directory {output_dir}: {e}")
            return []

    logging.info(f"Processing {len(guide_inputs)} guides. Output directory: {output_dir}")
    results = []

    for i, guide_input in enumerate(guide_inputs, 1):
        # --- Check for existing file before scraping --- 
        potential_guide_id = None
        if isinstance(guide_input, str) and guide_input.isdigit():
            potential_guide_id = guide_input
        elif isinstance(guide_input, str) and guide_input.startswith(("http://", "https://")):
            potential_guide_id = get_guide_id_from_url(guide_input)
        
        if potential_guide_id:
            output_file_check = os.path.join(output_dir, f"{potential_guide_id}.md")
            if os.path.exists(output_file_check) and not overwrite:
                logging.info(f"Skipping guide {potential_guide_id}: File already exists at {output_file_check}")
                results.append({
                    "input": guide_input,
                    "guide_id": int(potential_guide_id) if potential_guide_id.isdigit() else potential_guide_id,
                    "status": "skipped",
                    "output_file": output_file_check,
                })
                continue # Move to the next guide input
            elif os.path.exists(output_file_check) and overwrite:
                 logging.debug(f"File exists for guide {potential_guide_id} but overwrite is enabled.")
        else:
            logging.debug(f"Could not determine potential guide ID from input '{guide_input}' for pre-check.")
        # --- End check for existing file --- 

        url = construct_steam_guide_url(guide_input)
        logging.info(f"Processing guide {i}/{len(guide_inputs)}: {url}")

        try:
            markdown_result, guide_id_scraped = scrape_steam_guide(url, retries=retries, timeout=timeout)

            if markdown_result and guide_id_scraped:
                # Ensure guide_id is string for filename
                guide_id_str = str(guide_id_scraped)
                output_file = os.path.join(output_dir, f"{guide_id_str}.md")
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(markdown_result + "\n")  # Add trailing newline
                results.append(
                    {
                        "input": guide_input,
                        "guide_id": guide_id_scraped, # Keep original type (int/str)
                        "status": "success",
                        "output_file": output_file,
                    }
                )
                logging.info(f"Successfully saved guide to {output_file}")
            else:
                results.append(
                    {
                        "input": guide_input,
                        "guide_id": guide_id_scraped, # Keep original type (int/str)
                        "status": "failed",
                        "error": "Failed to extract guide content or guide ID",
                    }
                )
                logging.error(f"Failed to extract content/ID from {url}")
        except Exception as e:
            results.append(
                {
                    "input": guide_input,
                    "guide_id": None,
                    "status": "error",
                    "error": str(e),
                }
            )
            logging.error(f"Error processing {url}: {e}")
            # Implement fail-fast logic
            if fail_fast:
                logging.critical("Fail-fast enabled: Exiting due to error.")
                sys.exit(1) 

        # Apply rate limiting delay between scraping guides
        if i < len(guide_inputs):  # Don't delay after the last guide
            sleep_time = delay * (0.5 + random.random())  # Randomize delay
            logging.debug(f"Waiting {sleep_time:.2f}s before next guide request...")
            time.sleep(sleep_time)

    # Report summary
    successful = sum(1 for r in results if r["status"] == "success")
    logging.info(
        f"Processing complete. {successful}/{len(guide_inputs)} guides successfully processed."
    )
    return results


def main():
    """Parses arguments, runs the scraper, and handles output.

    Supports single guide, batch file, or fetching all guides for a game.
    """
    parser = argparse.ArgumentParser(
        description="""
Steam Guide Scraper - Convert Steam Community Guides to Markdown with YAML frontmatter.

This tool fetches guides from the Steam Community, extracts their content and metadata,
and saves them as clean Markdown files with YAML frontmatter.

Modes:
1. Single Guide: Provide a URL or guide ID using --input.
2. Batch File: Provide a text file with URLs/IDs (one per line) using --file.
3. Game Guides: Provide a game's App ID using --game-id to fetch all its English guides.

The script includes comprehensive metadata extraction, including guide IDs, game information,
author details, dates, ratings, visitor statistics, and more. All metadata is preserved
in the YAML frontmatter of the output Markdown files.
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
        help="Limit the number of guides processed when using --game-id. Fetches the first X guides found."
    )
    parser.add_argument(
        "--overwrite",
        action="store_true", # Default is False
        help="Overwrite existing Markdown files. If not set, existing files are skipped."
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1, # Default to 1 retry (2 total attempts)
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
        action="store_true", # Default is False
        help="Exit immediately if an error occurs during batch processing (--file or --game-id)."
    )
    parser.add_argument(
        "--sort-by",
        type=str,
        choices=['trend', 'toprated', 'mostrecent'],
        default='trend',
        metavar="FILTER",
        help="Sorting order for guides when using --game-id (choices: trend, toprated, mostrecent; default: trend)."
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Verbose logging enabled.")

    # --- User-Agent Override ---
    if args.user_agent:
        logging.info(f"Overriding User-Agent with: {args.user_agent}")
        HEADERS["User-Agent"] = args.user_agent
    # --- End User-Agent Override ---

    # --- Mode Handling ---

    # 1. Process guides by Game ID
    if args.game_id:
        if not args.game_id.isdigit():
            logging.error(f"Invalid Game ID provided: '{args.game_id}'. Must be numeric.")
            sys.exit(1)

        output_dir = args.output if args.output else os.getcwd()
        # Pass the limit, sort_by, retries, timeout
        guide_ids_to_process = fetch_guide_ids_for_game(
            args.game_id, 
            args.delay, 
            sort_by=args.sort_by,
            limit=args.limit, 
            retries=args.retries,
            timeout=args.timeout
        )

        if not guide_ids_to_process:
            logging.warning(f"No guide IDs found for game {args.game_id}. Exiting.")
            sys.exit(0)

        # Pass the overwrite, fail_fast, retries, timeout
        results = process_guide_list(
            guide_ids_to_process, output_dir, args.delay, 
            overwrite=args.overwrite, 
            fail_fast=args.fail_fast,
            retries=args.retries,
            timeout=args.timeout
        )
        if not results or all(r['status'] != 'success' for r in results):
             sys.exit(1) # Exit with error if no guides succeeded
        sys.exit(0)

    # 3. Process single guide input
    elif args.input:
        url = construct_steam_guide_url(args.input)
        # Pass retries and timeout
        markdown_result, guide_id = scrape_steam_guide(
            url, 
            retries=args.retries,
            timeout=args.timeout
        )

        if markdown_result is not None and markdown_result.strip():
            final_output = markdown_result + "\n"  # Ensure trailing newline
            output_file = args.output
            guide_id_str = str(guide_id) if guide_id else None

            if not output_file:
                if guide_id_str:
                    output_file = f"{guide_id_str}.md"
                    logging.info(f"No output file specified, defaulting to: {output_file}")
                else:
                    logging.error(
                        "Could not determine guide ID for default filename. Printing to console."
                    )
                    print("\n--- Markdown Output ---")
                    print(final_output)
                    sys.exit(0)  # Exit cleanly after printing

            logging.info(f"Attempting to write output to file: {output_file}")
            try:
                # Create output directory if it doesn't exist for single file output
                output_dir = os.path.dirname(output_file)
                if output_dir and not os.path.exists(output_dir):
                     os.makedirs(output_dir)
                     logging.info(f"Created output directory: {output_dir}")

                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(final_output)
                logging.info(f"Markdown content successfully saved to: {output_file}")
                sys.exit(0)  # Success
            except IOError as e:
                logging.error(f"Error writing to file {output_file}: {e}")
                logging.info("Printing Markdown output to console as fallback.")
                print("\n--- Markdown Output (Fallback) ---")
                print(final_output)
                sys.exit(1)  # Indicate error after fallback print
        else:
            logging.error("Failed to scrape the guide or no meaningful content was found.")
            sys.exit(1)


if __name__ == "__main__":
    # Check essential dependencies early
    try:
        import requests
        import bs4
        import html2text
        import yaml
    except ImportError as e:
        print(
            f"CRITICAL ERROR: Missing required library: {e.name}. Please install dependencies (e.g., pip install requests beautifulsoup4 html2text PyYAML).",
            file=sys.stderr,
        )
        sys.exit(2)

    main()
