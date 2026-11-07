
#!/usr/bin/env python3

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, unquote


# ============================================================
# Configuration
# ============================================================

ROOT = Path(__file__).resolve().parents[2]
JSON_PATH = ROOT / "rss" / "entries.json"
FEED_PATH = ROOT / "feed.xml"

# ============================================================
# Validation helpers
# ============================================================


def fail(message):
    raise ValueError(message)


def validate_string(value, name, allow_empty=False):
    if not isinstance(value, str):
        fail(f"{name}: must be a string")

    if not allow_empty and not value.strip():
        fail(f"{name}: must not be empty")

    # XML 1.0 character validation
    for char in value:
        cp = ord(char)
        if not (
            cp in (0x09, 0x0A, 0x0D)
            or 0x20 <= cp <= 0xD7FF
            or 0xE000 <= cp <= 0xFFFD
            or 0x10000 <= cp <= 0x10FFFF
        ):
            fail(f"{name}: contains an invalid XML character")

    return value


def validate_site_url(value):
    validate_string(value, "site_url")

    parsed = urlsplit(value)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        fail("site_url: must be an origin such as https://example.com")

    return value.rstrip("/")


def validate_entry_url(value):
    validate_string(value, "entry.url")

    parsed = urlsplit(value)

    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        fail(
            f"Invalid entry URL: {value!r}. "
            "Use a root-relative path such as /articles/example or /articles/example.html"
        )

    decoded = unquote(parsed.path)

    if "\\" in decoded or "\x00" in decoded:
        fail(f"Invalid URL path: {value}")

    parts = decoded.lstrip("/").split("/")

    if any(part in ("", ".", "..") for part in parts):
        fail(f"Invalid URL path: {value}")

    file_path = (ROOT / decoded.lstrip("/")).resolve()

    if not file_path.is_relative_to(ROOT):
        fail(f"URL points outside the repository: {value}")

    if file_path.suffix.lower() not in (".html", ".htm"):
        if file_path.suffix:
            fail(f"URL must point to an HTML file: {value}")

        html_paths = [
            Path(f"{file_path}.html").resolve(),
            Path(f"{file_path}.htm").resolve(),
        ]
        file_path = next(
            (path for path in html_paths if path.is_file() and path.is_relative_to(ROOT)),
            None,
        )

        if file_path is None:
            fail(f"HTML file does not exist: {value}")

    if not file_path.is_file():
        fail(f"HTML file does not exist: {value}")

    return value, file_path


# ============================================================
# Git timestamp
# ============================================================


def get_git_timestamp(file_path):
    """
    Get the last commit timestamp for a file.

    Git does not preserve filesystem mtime.
    Use the committer timestamp of the latest commit
    that modified this file.
    """

    relative_path = file_path.relative_to(ROOT)

    result = subprocess.run(
        [
            "git",
            "-C", str(ROOT),
            "log",
            "-1",
            "--format=%cI",
            "--",
            relative_path.as_posix(),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        fail(
            f"Failed to get Git timestamp for {relative_path}: "
            f"{result.stderr.strip()}"
        )

    value = result.stdout.strip()

    if not value:
        fail(
            f"No Git history found for {relative_path}. "
            "Make sure the file is committed and full Git history is available."
        )

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        fail(f"Invalid Git timestamp: {value}")

    if dt.tzinfo is None:
        fail(f"Git timestamp has no timezone: {value}")

    return dt


# ============================================================
# Existing feed
# ============================================================


def load_existing_dates():
    """
    Return:
        { absolute_url: datetime }

    Existing publication dates are preserved,
    even if the corresponding HTML file changes later.
    """

    if not FEED_PATH.exists():
        return {}

    try:
        tree = ET.parse(FEED_PATH)
    except ET.ParseError as e:
        fail(f"Existing feed.xml is invalid: {e}")

    root = tree.getroot()

    if root.tag != "rss":
        fail("Existing feed.xml is not RSS 2.0")

    channel = root.find("channel")

    if channel is None:
        fail("Existing feed.xml has no channel element")

    dates = {}

    for item in channel.findall("item"):
        link = item.findtext("link")
        guid = item.findtext("guid")
        pub_date = item.findtext("pubDate")

        if not link or not guid or not pub_date:
            fail("Existing feed contains an item missing link, guid or pubDate")

        if link != guid:
            fail(f"Existing feed has inconsistent link/guid: {link}")

        if link in dates:
            fail(f"Duplicate URL in existing feed: {link}")

        try:
            dt = parsedate_to_datetime(pub_date)
        except (TypeError, ValueError, OverflowError):
            fail(f"Invalid pubDate in existing feed: {pub_date}")

        if dt.tzinfo is None:
            fail(f"pubDate has no timezone: {pub_date}")

        dates[link] = dt

    return dates


# ============================================================
# JSON loading and validation
# ============================================================


def load_entries():
    if not JSON_PATH.is_file():
        fail(f"JSON file not found: {JSON_PATH}")

    try:
        with JSON_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON: {e}")

    if not isinstance(data, dict):
        fail("JSON root must be an object")

    title = validate_string(data.get("title"), "title")
    site_url = validate_site_url(data.get("site_url"))
    description = validate_string(
        data.get("description"), "description"
    )

    language = data.get("language", "ja")
    validate_string(language, "language")

    entries = data.get("entries")

    if not isinstance(entries, list):
        fail("entries must be an array")

    if not entries:
        fail("entries must contain at least one item")

    existing_dates = load_existing_dates()

    seen_urls = set()
    result = []

    for index, entry in enumerate(entries):
        label = f"entries[{index}]"

        if not isinstance(entry, dict):
            fail(f"{label}: must be an object")

        entry_title = validate_string(
            entry.get("title"), f"{label}.title"
        )

        raw_url = entry.get("url")
        relative_url, file_path = validate_entry_url(raw_url)

        absolute_url = site_url + relative_url

        if absolute_url in seen_urls:
            fail(f"Duplicate URL in JSON: {relative_url}")

        seen_urls.add(absolute_url)

        entry_description = validate_string(
            entry.get("description", ""),
            f"{label}.description",
            allow_empty=True,
        )

        # Preserve the original publication date if already published.
        if absolute_url in existing_dates:
            published = existing_dates[absolute_url]
        else:
            published = get_git_timestamp(file_path)

        result.append({
            "title": entry_title,
            "url": absolute_url,
            "description": entry_description,
            "published": published,
        })

    # Newest first
    result.sort(
        key=lambda item: item["published"],
        reverse=True,
    )

    return {
        "title": title,
        "site_url": site_url,
        "description": description,
        "language": language,
        "entries": result,
    }


# ============================================================
# RSS generation
# ============================================================


def generate_rss(data):
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = data["title"]
    ET.SubElement(channel, "link").text = data["site_url"] + "/"
    ET.SubElement(channel, "description").text = data["description"]
    ET.SubElement(channel, "language").text = data["language"]

    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc)
    )

    for entry in data["entries"]:
        item = ET.SubElement(channel, "item")

        ET.SubElement(item, "title").text = entry["title"]
        ET.SubElement(item, "link").text = entry["url"]

        guid = ET.SubElement(item, "guid", isPermaLink="true")
        guid.text = entry["url"]

        ET.SubElement(item, "pubDate").text = format_datetime(
            entry["published"]
        )

        ET.SubElement(item, "description").text = (
            entry["description"]
        )

    ET.indent(rss, space="  ")

    tree = ET.ElementTree(rss)
    tree.write(
        FEED_PATH,
        encoding="utf-8",
        xml_declaration=True,
    )

    print(f"Generated: {FEED_PATH}")
    print(f"Entries: {len(data['entries'])}")


# ============================================================
# Main
# ============================================================


def main():
    try:
        data = load_entries()
        generate_rss(data)

    except (ValueError, OSError, subprocess.SubprocessError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
