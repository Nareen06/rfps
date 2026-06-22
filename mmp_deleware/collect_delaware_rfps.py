#!/usr/bin/env python3
"""Collect Delaware bid/RFP data using the public endpoints used by mmp.delaware.gov."""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


BASE_URL = "https://mmp.delaware.gov"
LIST_ENDPOINT = "/Bids/GetBids"
DETAIL_ENDPOINT = "/Bids/GetBidDetail"
DOCUMENT_ENDPOINT = "/Bids/GetBidDocumentList"
VALID_STATUSES = ("Open", "RecentClosed", "ClosedNotAwarded")
USER_AGENT = "Delaware-RFP-Collector/1.0 (+public procurement data collector)"


class FragmentParser(HTMLParser):
    """Extract text elements and links from the API's HTML fragments."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, Any]] = []
        self.links: list[dict[str, str]] = []
        self._captures: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag in {"p", "strong", "h1"}:
            self._captures.append({"tag": tag, "attrs": attributes, "parts": []})
        if tag == "a":
            capture = {"tag": tag, "attrs": attributes, "parts": []}
            self._captures.append(capture)

    def handle_data(self, data: str) -> None:
        for capture in self._captures:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._captures) - 1, -1, -1):
            capture = self._captures[index]
            if capture["tag"] != tag:
                continue
            self._captures.pop(index)
            text = " ".join("".join(capture["parts"]).split())
            item = {"tag": tag, "text": text, "attrs": capture["attrs"]}
            self.elements.append(item)
            if tag == "a" and capture["attrs"].get("href"):
                self.links.append({"text": text, "href": capture["attrs"]["href"]})
            break


def request_text(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    retries: int = 3,
) -> str:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json, text/html", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"

    for attempt in range(1, retries + 1):
        try:
            request = Request(url, data=data, headers=headers, method=method)
            with urlopen(request, timeout=45) as response:
                return response.read().decode("utf-8-sig")
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt == retries:
                raise RuntimeError(f"Request failed after {retries} attempts: {url}") from exc
            wait = 2 ** (attempt - 1)
            logging.warning("Request failed (%s); retrying in %ss", exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def fetch_bid_page(status: str, page: int, rows: int) -> dict[str, Any]:
    query = urlencode({"status": status})
    payload = {
        "page": page,
        "rows": rows,
        "sidx": "OpenDate",
        "sord": "desc",
        "_search": False,
    }
    raw = request_text(
        f"{BASE_URL}{LIST_ENDPOINT}?{query}", method="POST", payload=payload
    )
    return json.loads(raw)


def parse_detail(fragment: str, bid_id: int) -> dict[str, Any]:
    parser = FragmentParser()
    parser.feed(fragment)

    field_labels = {
        "Solicitation Ad Date": "advertised_date",
        "Deadline for Bid Responses": "deadline_detail",
        "Contact Information": "contact_detail",
    }
    details: dict[str, Any] = {}
    elements = parser.elements
    for index, element in enumerate(elements):
        field_name = field_labels.get(element["text"])
        if not field_name:
            continue
        for candidate in elements[index + 1 :]:
            if candidate["tag"] == "p" and candidate["text"]:
                details[field_name] = candidate["text"]
                break

    messages = []
    for element in elements:
        classes = element["attrs"].get("class", "").split()
        if element["tag"] == "p" and "text-danger" in classes:
            text = element["text"]
            if text and text != "Important Specific Message" and text not in messages:
                messages.append(text)

    for link in parser.links:
        href = link["href"]
        if href.startswith("mailto:"):
            details["contact_email"] = href.removeprefix("mailto:")

    details["important_messages"] = messages
    details["detail_url"] = f"{BASE_URL}/Bids/Details/{bid_id}"
    return details


def fetch_detail(bid_id: int) -> dict[str, Any]:
    query = urlencode({"id": bid_id})
    fragment = request_text(f"{BASE_URL}{DETAIL_ENDPOINT}?{query}")
    return parse_detail(fragment, bid_id)


def fetch_documents(bid_id: int) -> list[dict[str, str]]:
    query = urlencode({"id": bid_id, "currentCount": 0})
    fragment = request_text(f"{BASE_URL}{DOCUMENT_ENDPOINT}?{query}")
    parser = FragmentParser()
    parser.feed(fragment)
    documents = []
    for link in parser.links:
        if not link["href"] or "load more" in link["text"].lower():
            continue
        documents.append(
            {"name": link["text"], "url": urljoin(BASE_URL, link["href"])}
        )
    return documents


def collect_status(status: str, rows_per_page: int, delay: float) -> list[dict[str, Any]]:
    bids: list[dict[str, Any]] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        logging.info("Fetching %s bids page %s/%s", status, page, total_pages)
        response = fetch_bid_page(status, page, rows_per_page)
        total_pages = int(response.get("total", 0))
        page_rows = response.get("rows", [])
        for bid in page_rows:
            bid_id = int(bid["Id"])
            logging.info("Fetching detail for bid %s", bid_id)
            bid["Status"] = status
            bid["Detail"] = fetch_detail(bid_id)
            bid["Documents"] = fetch_documents(bid_id)
            bids.append(bid)
            if delay:
                time.sleep(delay)
        page += 1
    return bids


def write_json(path: Path, bids: list[dict[str, Any]], statuses: list[str]) -> None:
    output = {
        "source": f"{BASE_URL}/Bids/",
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "statuses": statuses,
        "count": len(bids),
        "bids": bids,
    }
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Delaware public bid/RFP data")
    parser.add_argument(
        "--status", nargs="+", choices=VALID_STATUSES, default=["Open"],
        help="Bid statuses to collect (default: Open)",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--rows-per-page", type=int, default=100)
    parser.add_argument(
        "--delay", type=float, default=0.15,
        help="Seconds between bid detail requests (default: 0.15)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    bids: list[dict[str, Any]] = []
    for status in args.status:
        bids.extend(collect_status(status, args.rows_per_page, args.delay))

    json_path = args.output_dir / "delaware_bids.json"
    write_json(json_path, bids, args.status)
    logging.info("Saved %s bids to %s", len(bids), json_path)


if __name__ == "__main__":
    main()
