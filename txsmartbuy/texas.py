#!/usr/bin/env python3
"""Collect Texas ESBD solicitations through TXSmartBuy public JSON services."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

BASE_URL = "https://www.txsmartbuy.gov"
SERVICE_ROOT = BASE_URL + "/app/extensions/CPA/CPAMain/1.0.0/services/"
LIST_SERVICE = SERVICE_ROOT + "ESBD.Service.ss"
DETAIL_SERVICE = SERVICE_ROOT + "ESBD.Details.Service.ss"
STATUS_CODES = {
    "all": "", "posted": "1", "awarded": "2", "no-award": "11",
    "closed": "5", "cancelled": "3",
}
USER_AGENT = "Texas-ESBD-Collector/1.0 (+public procurement data collector)"


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    retries: int = 4,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, data=body, headers=headers, method=method)
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries:
                raise RuntimeError(f"Request failed after {retries} attempts: {url}") from exc
            wait = 2 ** (attempt - 1)
            logging.warning("Request failed (%s); retrying in %ss", exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def list_payload(args: argparse.Namespace, page: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "page": page, "urlRoot": "esbd", "status": STATUS_CODES[args.status]
    }
    filters = {
        "agencyNumber": args.agency_number,
        "keyword": args.keyword,
        "solicitationId": args.solicitation_id,
        "nigp": args.nigp,
        "startDate": args.start_date,
        "endDate": args.end_date,
    }
    payload.update({key: value for key, value in filters.items() if value})
    return payload


def fetch_list_page(args: argparse.Namespace, page: int) -> dict[str, Any]:
    return request_json(LIST_SERVICE, method="POST", payload=list_payload(args, page))


def fetch_detail(solicitation_id: str) -> dict[str, Any]:
    query = urlencode({
        "urlRoot": "esbd",
        "identification": solicitation_id,
    })
    detail = request_json(f"{DETAIL_SERVICE}?{query}")
    detail.pop("highwayDistricts", None)
    detail.pop("highwayDistrictList", None)
    detail["detail_url"] = f"{BASE_URL}/esbd/{solicitation_id}"
    for attachment in detail.get("attachments", []):
        if attachment.get("fileURL"):
            attachment["download_url"] = urljoin(BASE_URL, attachment["fileURL"])
    return detail


def enrich_page(
    rows: list[dict[str, Any]], workers: int
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    details: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_detail, str(row["solicitationId"])): str(
                row["solicitationId"]
            )
            for row in rows
        }
        for future in as_completed(futures):
            solicitation_id = futures[future]
            try:
                details[solicitation_id] = future.result()
            except Exception as exc:
                logging.error("Detail failed for %s: %s", solicitation_id, exc)
                failures.append({"solicitation_id": solicitation_id, "error": str(exc)})
    for row in rows:
        solicitation_id = str(row["solicitationId"])
        row["detail_url"] = f"{BASE_URL}/esbd/{solicitation_id}"
        row["detail"] = details.get(solicitation_id)
    return rows, failures


def write_snapshot(
    path: Path,
    rows: list[dict[str, Any]],
    failures: list[dict[str, str]],
    args: argparse.Namespace,
    total_available: int,
) -> None:
    output = {
        "source": f"{BASE_URL}/esbd",
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "status": args.status,
            "agency_number": args.agency_number,
            "keyword": args.keyword,
            "solicitation_id": args.solicitation_id,
            "nigp": args.nigp,
            "start_date": args.start_date,
            "end_date": args.end_date,
        },
        "total_available": total_available,
        "count": len(rows),
        "detail_failure_count": len(failures),
        "detail_failures": failures,
        "solicitations": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def collect(
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    first_page = fetch_list_page(args, 1)
    per_page = int(first_page.get("recordsPerPage") or 24)
    total_available = int(first_page.get("totalRecordsFound") or 0)
    total_pages = math.ceil(total_available / per_page) if total_available else 0
    if args.max_pages:
        total_pages = min(total_pages, args.max_pages)
    logging.info(
        "Texas ESBD reports %s matches across %s page(s)",
        total_available, total_pages,
    )
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    output_path = args.output_dir / "texas_solicitations.json"
    for page in range(1, total_pages + 1):
        logging.info("Processing page %s/%s", page, total_pages)
        response = first_page if page == 1 else fetch_list_page(args, page)
        page_rows = list(response.get("lines", []))
        if args.max_records:
            page_rows = page_rows[: max(0, args.max_records - len(rows))]
        if not page_rows:
            break
        if args.skip_details:
            for row in page_rows:
                row["detail_url"] = f"{BASE_URL}/esbd/{row['solicitationId']}"
        else:
            page_rows, page_failures = enrich_page(page_rows, args.workers)
            failures.extend(page_failures)
        rows.extend(page_rows)
        write_snapshot(output_path, rows, failures, args, total_available)
        if args.max_records and len(rows) >= args.max_records:
            break
        if args.delay:
            time.sleep(args.delay)
    return rows, failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Texas SmartBuy ESBD solicitations as JSON"
    )
    parser.add_argument(
        "--status", choices=STATUS_CODES, default="posted",
        help="Status (default: posted, including addenda)",
    )
    parser.add_argument("--agency-number", default="")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--solicitation-id", default="")
    parser.add_argument("--nigp", default="", help="NIGP class/item filter")
    parser.add_argument("--start-date", default="", help="Start date, MM/DD/YYYY")
    parser.add_argument("--end-date", default="", help="End date, MM/DD/YYYY")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--skip-details", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 10:
        parser.error("--workers must be between 1 and 10")
    return args


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    rows, failures = collect(args)
    logging.info(
        "Saved %s solicitations (%s detail failures) to %s",
        len(rows), len(failures), args.output_dir / "texas_solicitations.json",
    )


if __name__ == "__main__":
    main()
