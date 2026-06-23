#!/usr/bin/env python3
"""Collect Pennsylvania eMarketplace RFP solicitations through public HTTP endpoints."""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, build_opener

BASE_URL = "https://www.emarketplace.state.pa.us/"
SEARCH_URL = urljoin(BASE_URL, "Search.aspx")
DETAIL_URL = urljoin(BASE_URL, "Solicitations.aspx")
SCRIPT_DIR = Path(__file__).resolve().parent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def fetch_text(opener: Any, url: str, *, data: bytes | None = None, retries: int = 4) -> str:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": USER_AGENT,
        "Referer": SEARCH_URL,
    }
    method = "POST" if data is not None else "GET"
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, data=data, headers=headers, method=method)
            with opener.open(request, timeout=60) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt == retries:
                raise RuntimeError(f"Request failed after {retries} attempts: {url}") from exc
            wait = 2 ** (attempt - 1)
            logging.warning("Request failed (%s); retrying in %ss", exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def hidden_fields(page: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r'<input[^>]+type=["\']hidden["\'][^>]*>', page, flags=re.I):
        tag = match.group(0)
        name = re.search(r'name=["\']([^"\']+)["\']', tag, flags=re.I)
        value = re.search(r'value=["\']([^"\']*)["\']', tag, flags=re.I)
        if name:
            fields[html.unescape(name.group(1))] = html.unescape(value.group(1) if value else "")
    return fields


def page_post_data(page: str, page_number: int) -> bytes:
    fields = hidden_fields(page)
    fields["__EVENTTARGET"] = "ctl00$MainBody$grdResults"
    fields["__EVENTARGUMENT"] = f"Page${page_number}"
    fields["__LASTFOCUS"] = ""
    return urlencode(fields).encode("utf-8")


def cell_values(row_html: str) -> list[str]:
    cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)
    return [clean_text(cell) for cell in cells]


def search_records(page: str, solicitation_type: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    rows = re.findall(r"<tr\b[^>]*class=[\"']Grid(?:Alt)?Item[\"'][^>]*>(.*?)</tr>", page, flags=re.I | re.S)
    for row in rows:
        link = re.search(r'href=["\']Solicitations\.aspx\?SID=([^"\']+)["\']', row, flags=re.I)
        if not link:
            continue
        sid = html.unescape(link.group(1))
        cells = cell_values(row)
        if len(cells) < 12:
            continue
        type_value = cells[1]
        if solicitation_type and type_value.upper() != solicitation_type.upper():
            continue
        records.append(
            {
                "solicitation_number": sid,
                "type": type_value,
                "title": cells[2],
                "description": cells[3],
                "agency": cells[4],
                "county": cells[5],
                "amended_date": cells[6],
                "solicitation_start_date": cells[7],
                "solicitation_due_date": cells[8],
                "bid_opening_date": cells[9],
                "status": cells[10],
                "contact_person": cells[11],
                "detail_url": DETAIL_URL + "?" + urlencode({"SID": sid}),
            }
        )
    return records


def span_text(page: str, element_id: str) -> str:
    pattern = rf'<span[^>]+id=["\']{re.escape(element_id)}["\'][^>]*>(.*?)</span>'
    match = re.search(pattern, page, flags=re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def all_file_links(page: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for match in re.finditer(r'<a[^>]+href=["\'](FileDownload\.aspx\?[^"\']+)["\'][^>]*>(.*?)</a>', page, flags=re.I | re.S):
        href = html.unescape(match.group(1))
        name = clean_text(match.group(2))
        files.append(
            {
                "name": name,
                "download_url": urljoin(BASE_URL, href),
            }
        )
    return files


def detail_values(page: str) -> dict[str, Any]:
    return {
        "procurement_type": span_text(page, "ctl00_MainBody_lblProcType"),
        "solicitation_number": span_text(page, "ctl00_MainBody_lblSolNo"),
        "title": span_text(page, "ctl00_MainBody_lblTitle"),
        "description": span_text(page, "ctl00_MainBody_lblDesc"),
        "agency": span_text(page, "ctl00_MainBody_lblAgency"),
        "address": span_text(page, "ctl00_MainBody_lblAddress"),
        "county": span_text(page, "ctl00_MainBody_lblCounty"),
        "duration": span_text(page, "ctl00_MainBody_lblDuration"),
        "contact_first_name": span_text(page, "ctl00_MainBody_lblContactFirstName"),
        "contact_last_name": span_text(page, "ctl00_MainBody_lblContactLastName"),
        "contact_phone": span_text(page, "ctl00_MainBody_lblContactPhone"),
        "contact_email": span_text(page, "ctl00_MainBody_lblContactEmail"),
        "solicitation_start_date": span_text(page, "ctl00_MainBody_lblStartDate"),
        "solicitation_due_date": span_text(page, "ctl00_MainBody_lblEndDate"),
        "solicitation_due_time": span_text(page, "ctl00_MainBody_lblDueTime"),
        "solicitation_opening_date": span_text(page, "ctl00_MainBody_lblOpenDate"),
        "solicitation_opening_time": span_text(page, "ctl00_MainBody_lblOpenTime"),
        "opening_location": span_text(page, "ctl00_MainBody_lblLoc"),
        "number_of_addendums": span_text(page, "ctl00_MainBody_lblAdd"),
        "amended_date": span_text(page, "ctl00_MainBody_lblAmendedDt"),
        "files": all_file_links(page),
    }


def fetch_detail(opener: Any, solicitation_number: str) -> dict[str, Any]:
    detail_url = DETAIL_URL + "?" + urlencode({"SID": solicitation_number})
    page = fetch_text(opener, detail_url)
    detail = detail_values(page)
    detail["detail_url"] = detail_url
    return detail


def collect(args: argparse.Namespace) -> dict[str, Any]:
    opener = build_opener()
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    seen: set[str] = set()

    page_html = fetch_text(opener, SEARCH_URL)
    for page_number in range(1, args.max_pages + 1):
        logging.info("Fetching Pennsylvania search page %s", page_number)
        if page_number > 1:
            page_html = fetch_text(opener, SEARCH_URL, data=page_post_data(page_html, page_number))

        for record in search_records(page_html, args.solicitation_type):
            sid = record["solicitation_number"]
            if sid in seen:
                continue
            seen.add(sid)
            if args.max_records and len(rows) >= args.max_records:
                break

            if not args.skip_details:
                try:
                    detail = fetch_detail(opener, sid)
                    record.update({key: value for key, value in detail.items() if value not in ("", [], None)})
                except Exception as exc:
                    logging.error("Detail failed for solicitation %s: %s", sid, exc)
                    failures.append({"solicitation_number": sid, "error": str(exc)})

            rows.append(record)
            if args.delay:
                time.sleep(args.delay)

        if args.max_records and len(rows) >= args.max_records:
            break
        if f"Page${page_number + 1}" not in page_html:
            break

    return {
        "source": SEARCH_URL,
        "endpoint_type": "ASP.NET WebForms HTTP endpoints returning HTML",
        "search_endpoint": SEARCH_URL,
        "detail_endpoint": DETAIL_URL + "?SID={solicitation_number}",
        "download_endpoint": urljoin(BASE_URL, "FileDownload.aspx"),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "solicitation_type_filter": args.solicitation_type,
        "count": len(rows),
        "detail_failure_count": len(failures),
        "detail_failures": failures,
        "solicitations": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Pennsylvania eMarketplace RFP details.")
    parser.add_argument("--solicitation-type", default="RFP", help='Default: "RFP". Use "" for all types.')
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--skip-details", action="store_true")
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--output", default=str(SCRIPT_DIR / "output" / "pennsylvania_rfps.json"))
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = collect(args)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logging.info("Saved %s solicitations to %s", data["count"], output_path)


if __name__ == "__main__":
    main()
