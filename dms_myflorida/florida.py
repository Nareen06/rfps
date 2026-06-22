#!/usr/bin/env python3

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://dms-media.ccplatform.net/api/page/data/bulletin/97609"
DMS_SITE_URL = "https://www.dms.myflorida.com"

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 100

OUTPUT_DIR = Path(__file__).resolve().parent
PRETTY_JSON_OUTPUT_FILE = OUTPUT_DIR / "florida_projects_pretty.json"
PDF_OUTPUT_DIR = OUTPUT_DIR / "florida_pdfs"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
}

INDIA_TIMEZONE = ZoneInfo("Asia/Kolkata")
INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)

    def get_text(self):
        return " ".join(self.parts)


class NextDataExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.in_next_data = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("id") == "__NEXT_DATA__":
            self.in_next_data = True

    def handle_endtag(self, tag):
        if tag == "script" and self.in_next_data:
            self.in_next_data = False

    def handle_data(self, data):
        if self.in_next_data:
            self.parts.append(data)

    def get_data(self):
        if not self.parts:
            raise ValueError("The detail page does not contain __NEXT_DATA__")
        return json.loads("".join(self.parts))


def repair_text(value):
    try:
        return value.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return value


def html_to_text(value):
    parser = TextExtractor()
    parser.feed(html.unescape(repair_text(value or "")))
    return " ".join(parser.get_text().split())


def split_subject(subject):
    for separator in (" - ", " – "):
        if separator in subject:
            agency, title = subject.split(separator, 1)
            return agency.strip(), title.strip()
    return "", subject.strip()


def build_url(offset, limit):
    return f"{BASE_URL}/{offset}/{limit}"


def build_session():
    session = requests.Session()
    session.headers.update(HEADERS)

    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def fetch_page(session, offset=DEFAULT_OFFSET, limit=DEFAULT_LIMIT, query=""):
    params = {"q": query} if query else None
    response = session.get(build_url(offset, limit), params=params, timeout=30)
    response.encoding = "utf-8"
    response.raise_for_status()
    return response.json()


def extract_next_data(document):
    parser = NextDataExtractor()
    parser.feed(document)
    return parser.get_data()


def build_detail_json_url(page_url, build_id):
    path = urlsplit(page_url).path.rstrip("/")
    return f"{DMS_SITE_URL}/_next/data/{build_id}{path}.json"


def format_timestamp(timestamp, target_timezone):
    if timestamp is None:
        return ""

    value = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return value.astimezone(target_timezone).isoformat()


def fetch_detail(session, page_url):
    response = session.get(page_url, timeout=30)
    response.encoding = "utf-8"
    response.raise_for_status()

    next_data = extract_next_data(response.text)
    page_props = (next_data.get("props") or {}).get("pageProps") or {}
    page_data = page_props.get("pageData") or {}
    detail_json_url = ""

    if not page_data:
        build_id = next_data.get("buildId")
        if not build_id:
            raise ValueError("The detail page has no pageData or Next.js build ID")

        detail_json_url = build_detail_json_url(page_url, build_id)
        detail_response = session.get(detail_json_url, timeout=30)
        detail_response.raise_for_status()

        page_data = (
            (detail_response.json().get("pageProps") or {}).get("pageData") or {}
        )

    if not page_data:
        raise ValueError("The detail response does not contain pageData")

    attachment = page_data.get("attachment") or {}
    original_site = page_data.get("link") or {}
    attachment_uri = attachment.get("uri") or ""
    archive_timestamp = (page_data.get("archiveOn") or {}).get("timestamp")

    return {
        "detailSourceUrl": page_url,
        "detailJsonUrl": detail_json_url,
        "archiveTimestamp": archive_timestamp,
        "archiveOnUtc": format_timestamp(archive_timestamp, timezone.utc),
        "archiveOnIndia": format_timestamp(archive_timestamp, INDIA_TIMEZONE),
        "attachment": {
            "fileName": attachment.get("fileName") or "",
            "fileSize": attachment.get("fileSize"),
            "mimeType": attachment.get("mimeType") or "",
            "downloadUrl": urljoin(DMS_SITE_URL, attachment_uri)
            if attachment_uri
            else "",
        },
        "originalSite": {
            "text": original_site.get("text") or "",
            "url": original_site.get("link") or "",
        },
    }


def build_pdf_path(pdf_dir, index, file_name):
    cleaned_name = INVALID_FILENAME_CHARACTERS.sub("_", file_name).strip(" .")

    if not cleaned_name:
        cleaned_name = "attachment.pdf"

    if not cleaned_name.lower().endswith(".pdf"):
        cleaned_name += ".pdf"

    return pdf_dir / f"{index:04d}_{cleaned_name}"


def download_pdf(session, attachment, pdf_dir, index):
    download_url = attachment.get("downloadUrl") or ""
    if not download_url:
        return

    file_name = attachment.get("fileName") or "attachment.pdf"
    mime_type = (attachment.get("mimeType") or "").lower()

    if mime_type != "application/pdf" and not file_name.lower().endswith(".pdf"):
        return

    pdf_dir.mkdir(parents=True, exist_ok=True)

    output_path = build_pdf_path(pdf_dir, index, file_name)
    temporary_path = output_path.with_suffix(output_path.suffix + ".part")

    try:
        with session.get(download_url, stream=True, timeout=60) as response:
            response.raise_for_status()

            with temporary_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)

        temporary_path.replace(output_path)

    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    attachment["localPath"] = str(output_path.resolve())


def normalize_bulletin(item):
    subject = repair_text(item.get("subject") or "")
    agency, title = split_subject(subject)

    return {
        "subject": subject,
        "agency": agency,
        "title": title,
        "category": repair_text(item.get("category") or ""),
        "publishOn": repair_text(item.get("publishOn") or ""),
        "modified": repair_text(item.get("modified") or ""),
        "created": repair_text(item.get("created") or ""),
        "url": item.get("url") or "",
        "message": html_to_text(item.get("message") or ""),
        "rawMessage": repair_text(item.get("message") or ""),
        "sticky": bool(item.get("sticky")),
    }


def split_csv(value):
    if not value:
        return []
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def record_search_text(record):
    return " ".join(
        [
            record.get("subject", ""),
            record.get("agency", ""),
            record.get("title", ""),
            record.get("category", ""),
            record.get("message", ""),
            record.get("publishOn", ""),
            record.get("modified", ""),
            record.get("created", ""),
        ]
    ).lower()


def matches_local_filters(record, args):
    text = record_search_text(record)

    if args.category:
        wanted_categories = split_csv(args.category)
        record_category = record.get("category", "").lower()

        if record_category not in wanted_categories:
            return False

    if args.agency:
        wanted_agencies = split_csv(args.agency)
        record_agency = record.get("agency", "").lower()

        if not any(agency in record_agency for agency in wanted_agencies):
            return False

    if args.include:
        include_terms = split_csv(args.include)

        if not any(term in text for term in include_terms):
            return False

    if args.require:
        required_terms = split_csv(args.require)

        if not all(term in text for term in required_terms):
            return False

    if args.exclude:
        exclude_terms = split_csv(args.exclude)

        if any(term in text for term in exclude_terms):
            return False

    return True


def fetch_bulletins(
    args,
):
    session = build_session()

    records = []
    scanned_count = 0
    total_count = None
    current_offset = args.offset
    next_progress = 100

    while True:
        data = fetch_page(session, current_offset, args.limit, args.query)
        bulletins = data.get("bulletins") or []
        total_count = data.get("bulletinsTotalCount", total_count)

        for item in bulletins:
            scanned_count += 1
            record = normalize_bulletin(item)

            # Local filtering happens BEFORE detail fetch and PDF download.
            if not matches_local_filters(record, args):
                continue

            if not args.no_details and record["url"]:
                try:
                    record.update(fetch_detail(session, record["url"]))
                except (requests.RequestException, ValueError, KeyError) as error:
                    record["detailError"] = str(error)

            attachment = record.get("attachment") or {}

            if attachment and not args.no_downloads:
                try:
                    download_pdf(session, attachment, args.pdf_dir, len(records) + 1)
                except (requests.RequestException, OSError) as error:
                    attachment["downloadError"] = str(error)

            records.append(record)

            if len(records) == next_progress:
                print(f"{next_progress} matching records completed")
                next_progress += 100

            if args.max_records and len(records) >= args.max_records:
                break

        if args.max_records and len(records) >= args.max_records:
            break

        if not args.all:
            break

        current_offset += args.limit

        if not bulletins or (total_count is not None and current_offset >= total_count):
            break

    return {
        "source": BASE_URL,
        "query": args.query,
        "offset": args.offset,
        "limit": args.limit,
        "scannedCount": scanned_count,
        "fetchedCount": len(records),
        "totalCount": total_count,
        "filters": {
            "category": args.category,
            "agency": args.agency,
            "include": args.include,
            "require": args.require,
            "exclude": args.exclude,
        },
        "bulletins": records,
    }


def write_json(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def build_pretty_json(data):
    projects = []

    for item in data["bulletins"]:
        projects.append(
            {
                "subject": item["subject"],
                "agency": item["agency"],
                "title": item["title"],
                "category": item["category"],
                "published": item["publishOn"],
                "modified": item["modified"],
                "dmsArchiveTime": {
                    "utc": item.get("archiveOnUtc") or "",
                    "india": item.get("archiveOnIndia") or "",
                },
                "message": item["message"],
                "dmsPageUrl": item["url"],
                "attachment": item.get("attachment") or {},
                "originalSite": item.get("originalSite") or {},
                "detailError": item.get("detailError", ""),
            }
        )

    return {
        "source": data["source"],
        "query": data["query"],
        "filters": data["filters"],
        "scannedCount": data["scannedCount"],
        "fetchedCount": data["fetchedCount"],
        "totalCount": data["totalCount"],
        "projects": projects,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description="Fetch Florida DMS current bid opportunities with local filters."
    )

    parser.add_argument("--offset", type=int, default=DEFAULT_OFFSET)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)

    # Weak server-side search from Florida's API.
    parser.add_argument(
        "--query",
        default="",
        help="Florida API q parameter. Use broad search only.",
    )

    parser.add_argument("--all", action="store_true", help="Fetch every available page")

    # Strong local filters.
    parser.add_argument(
        "--category",
        default="",
        help="Comma-separated exact categories, example: RFP,ITB,RFB",
    )

    parser.add_argument(
        "--agency",
        default="",
        help="Comma-separated agency/city names, example: St. Augustine,St. Cloud",
    )

    parser.add_argument(
        "--include",
        default="",
        help="Comma-separated terms. At least one must appear.",
    )

    parser.add_argument(
        "--require",
        default="",
        help="Comma-separated terms. All must appear.",
    )

    parser.add_argument(
        "--exclude",
        default="",
        help="Comma-separated terms. None can appear.",
    )

    parser.add_argument("--max-records", type=int, default=0)

    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip detail page requests.",
    )

    parser.add_argument(
        "--no-downloads",
        action="store_true",
        help="Do not download PDFs.",
    )

    parser.add_argument(
        "--pretty-json-output",
        type=Path,
        default=PRETTY_JSON_OUTPUT_FILE,
    )

    parser.add_argument("--pdf-dir", type=Path, default=PDF_OUTPUT_DIR)

    return parser


def main():
    args = build_parser().parse_args()

    data = fetch_bulletins(args)
    pretty_data = build_pretty_json(data)

    write_json(pretty_data, args.pretty_json_output)

    print(f"Scanned: {data['scannedCount']}")
    print(f"Matched: {data['fetchedCount']}")
    print(f"Saved to: {args.pretty_json_output}")


if __name__ == "__main__":
    main()