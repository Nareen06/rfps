#!/usr/bin/env python3
"""Collect Virginia eVA public opportunities through its JSON Solr endpoint."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote, urljoin
from urllib.request import Request, urlopen

BASE_URL = "https://mvendor.cgieva.com"
PUBLIC_BASE = BASE_URL + "/Vendor/public/"
SOURCE_PAGE = PUBLIC_BASE + "AllOpportunities.jsp"
SEARCH_ENDPOINT = PUBLIC_BASE + "solrconnect.jsp"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
SCRIPT_DIR = Path(__file__).resolve().parent


def request_json(url: str, retries: int = 4) -> dict[str, Any]:
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": USER_AGENT,
        "Referer": SOURCE_PAGE,
    }
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers=headers, method="GET")
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries:
                raise RuntimeError(f"Request failed after {retries} attempts: {url}") from exc
            wait = 2 ** (attempt - 1)
            logging.warning("Request failed (%s); retrying in %ss", exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def solr_text_filter(field: str, value: str) -> str:
    value = value.strip()
    if " " in value:
        value = value.replace(" ", "* AND *")
    return f"{field}:(*{value}*)"


def solr_list_filter(field: str, values: list[str]) -> str:
    quoted = " OR ".join(f'"{value}"' for value in values if value)
    return f"{field}:({quoted})"


def build_filters(args: argparse.Namespace) -> list[str]:
    filters: list[str] = []
    if args.keyword:
        filters.append(solr_text_filter("sosearch", args.keyword))
    if args.status:
        filters.append(solr_list_filter("status", [args.status]))
    if args.opportunity_type:
        filters.append(solr_list_filter("doccddesc", [args.opportunity_type]))
    if args.exclude_opportunity_type:
        filters.append("-" + solr_list_filter("doccddesc", [args.exclude_opportunity_type]))
    if args.agency_name:
        filters.append(solr_list_filter("agencyname", [args.agency_name]))
    if args.category:
        filters.append(solr_list_filter("category", [args.category]))
    if args.set_aside:
        filters.append(solr_list_filter("setasideshortdesc", [args.set_aside]))
    return filters


def build_url(args: argparse.Namespace, cursor_mark: str) -> str:
    params: list[tuple[str, str | int]] = [
        ("q", "*:*"),
        ("sort", args.sort),
        ("rows", args.rows),
        ("facet.limit", 600),
        ("facet.sort", "count"),
        ("facet", "on"),
        ("facet.mincount", 1),
        ("wt", "json"),
        ("cursorMark", cursor_mark),
    ]
    for field in [
        "status",
        "agencyname",
        "doccddesc",
        "category",
        "setasideshortdesc",
        "pubdate",
        "closedate",
    ]:
        params.append(("facet.field", field))
    for filter_query in build_filters(args):
        params.append(("fq", filter_query))
    return SEARCH_ENDPOINT + "?" + urlencode(params, quote_via=quote)


def detail_url(doc: dict[str, Any]) -> str:
    app = doc.get("app") or ""
    doccd = doc.get("doccd") or ""
    dept = doc.get("docdeptcd") or ""
    external_id = doc.get("externalid") or ""
    internal_id = doc.get("internalid") or ""
    version = doc.get("version") or ""

    if app == "QQ":
        path = "QQDetails.jsp"
        query = {
            "PageTitle": "QQ Details",
            "REQUEST_ID": external_id,
        }
    elif app == "ADV":
        path = "ADVSODetails.jsp"
        query = {
            "PageTitle": "SO Details",
            "DOC_CD": doccd,
            "Details_Page": "ADVSODetails.jsp",
            "DEPT_CD": dept,
            "BID_INTRNL_NO": internal_id,
            "BID_NO": external_id,
            "BID_VERS_NO": version,
        }
    elif app == "VBO":
        path = "VBODetails.jsp"
        query = {
            "PageTitle": "SO Details",
            "DOC_CD": doccd,
            "Details_Page": "VBOSODetails.jsp",
            "DEPT_CD": dept,
            "BID_INTRNL_NO": internal_id,
            "BID_NO": external_id,
            "BID_VERS_NO": version,
        }
    elif app == "IV":
        path = "IVDetails.jsp"
        query = {
            "PageTitle": "SO Details",
            "rfp_id_lot": internal_id,
            "rfp_id_round": version,
        }
    else:
        return ""

    return urljoin(PUBLIC_BASE, path) + "?" + urlencode(query)


def normalize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc.get("id", ""),
        "external_id": doc.get("externalid", ""),
        "internal_id": doc.get("internalid", ""),
        "version": doc.get("version", ""),
        "app": doc.get("app", ""),
        "doc_code": doc.get("doccd", ""),
        "doc_type": doc.get("doctype", ""),
        "opportunity_type": doc.get("doccddesc", ""),
        "title": doc.get("shortdesc", ""),
        "description": doc.get("longdesc", ""),
        "status": doc.get("status", ""),
        "agency": doc.get("agency", ""),
        "agency_name": doc.get("agencyname", ""),
        "department_code": doc.get("docdeptcd", ""),
        "category": doc.get("category", ""),
        "category_short_description": doc.get("categoryshortdesc", ""),
        "set_aside": doc.get("setaside", ""),
        "set_aside_description": doc.get("setasideshortdesc", ""),
        "published_at": doc.get("pubdate", ""),
        "close_date": doc.get("closedate", ""),
        "expiration_date": doc.get("expirationdate", ""),
        "future_procurement_estimated_issue_date": doc.get("fpestissuedate", ""),
        "future_procurement_estimated_price_range": doc.get("fpestpricerange", ""),
        "work_location": doc.get("workloc", ""),
        "commodity_codes": doc.get("commcode", []),
        "commodity_descriptions": doc.get("commdesc", []),
        "contact_name": doc.get("preparername", ""),
        "contact_email": doc.get("prepareremail", ""),
        "contact_phone": doc.get("preparerphonenumber", ""),
        "raw": doc,
        "detail_url": detail_url(doc),
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    cursor_mark = "*"
    total_available = 0
    pages_fetched = 0

    while True:
        pages_fetched += 1
        logging.info("Fetching page %s (cursor=%s)", pages_fetched, cursor_mark)
        data = request_json(build_url(args, cursor_mark))
        response = data.get("response", {})
        docs = response.get("docs", [])
        total_available = int(response.get("numFound") or total_available or 0)
        rows.extend(normalize_doc(doc) for doc in docs)

        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor_mark:
            break
        cursor_mark = next_cursor
        if args.max_pages and pages_fetched >= args.max_pages:
            break
        if args.max_records and len(rows) >= args.max_records:
            rows = rows[: args.max_records]
            break
        if args.delay:
            time.sleep(args.delay)

    return {
        "source": SOURCE_PAGE,
        "api": SEARCH_ENDPOINT,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "keyword": args.keyword,
            "status": args.status,
            "opportunity_type": args.opportunity_type,
            "exclude_opportunity_type": args.exclude_opportunity_type,
            "agency_name": args.agency_name,
            "category": args.category,
            "set_aside": args.set_aside,
            "sort": args.sort,
            "rows": args.rows,
            "max_pages": args.max_pages,
            "max_records": args.max_records,
        },
        "total_available": total_available,
        "count": len(rows),
        "opportunities": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Virginia eVA opportunities as JSON")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--status", default="")
    parser.add_argument(
        "--opportunity-type",
        default="",
        help="Filter to one exact eVA opportunity type. Default: no include filter.",
    )
    parser.add_argument(
        "--exclude-opportunity-type",
        default="Future Procurement (FPR)",
        help='Opportunity type to exclude. Default: "Future Procurement (FPR)". Use "" to include future procurements too.',
    )
    parser.add_argument("--agency-name", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--set-aside", default="")
    parser.add_argument("--sort", default="pubdate desc,id desc")
    parser.add_argument("--rows", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR / "output")
    args = parser.parse_args()
    if not 1 <= args.rows <= 500:
        parser.error("--rows must be between 1 and 500")
    return args


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    data = collect(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "virginia_opportunities.json"
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, output_path)
    logging.info("Saved %s opportunities to %s", data["count"], output_path)


if __name__ == "__main__":
    main()
