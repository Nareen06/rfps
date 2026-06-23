#!/usr/bin/env python3
"""Collect Connecticut CTSource solicitations through public Proactis APIs."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PORTAL_URL = "https://portal.ct.gov/DAS/CTSource/BidBoard"
APP_BASE_URL = "https://webprocure.proactiscloud.com/wp-web-public/"
RESOURCE_URL = APP_BASE_URL + "en/resource/"
CUSTOMER_ID = "51"
OID = "-1"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
SCRIPT_DIR = Path(__file__).resolve().parent


def request_json(url: str, retries: int = 4) -> dict[str, Any]:
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": USER_AGENT,
        "Referer": APP_BASE_URL,
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


def load_resource_config() -> dict[str, Any]:
    return request_json(RESOURCE_URL + "?" + urlencode({"eboId": CUSTOMER_ID, "oid": OID}))


def search_url(api_base_url: str, args: argparse.Namespace, page: int) -> str:
    start = (page - 1) * args.rows
    facets: list[str] = []
    if args.status:
        facets.append("ps=" + args.status)
    if args.solicitation_type:
        facets.append("solType=" + args.solicitation_type)
    params = {
        "customerid": CUSTOMER_ID,
        "q": args.keyword or "*",
        "from": start,
        "sort": args.sort,
        "f": "~".join(facets),
        "oids": "",
        "oid": OID,
    }
    return api_base_url.rstrip("/") + "/search/sols?" + urlencode(params)


def bid_detail_api_url(api_base_url: str, bid_id: Any) -> str:
    return api_base_url.rstrip("/") + f"/soldetail/{bid_id}?" + urlencode({"customerid": CUSTOMER_ID, "oid": OID})


def document_info_api_url(api_base_url: str, wp_base_url: str, bid: dict[str, Any]) -> str:
    params = {
        "headOid": str((bid.get("ownerOrg") or {}).get("oid") or ""),
        "creatorOrgId": str((bid.get("creatorOrg") or {}).get("oid") or ""),
        "oid": OID,
        "bidId": str(bid.get("bidid") or ""),
        "baseUrl": wp_base_url,
    }
    return api_base_url.rstrip("/") + "/soldetail/sd/getSolicitationInfo?" + urlencode(params)


def public_detail_url(bid_id: Any) -> str:
    return APP_BASE_URL + f"#/bidboard/bid/{bid_id}?" + urlencode(
        {"customerid": CUSTOMER_ID, "searchterm": "*", "pagenumber": 1, "oid": OID}
    )


def protected_doc_download_url(wp_base_url: str, doc_id: Any, owner_id: Any, bid_id: Any) -> str:
    if not doc_id or not owner_id or not bid_id:
        return ""
    return wp_base_url.rstrip("/") + "/MainBidBoard?" + urlencode(
        {"ac": 4, "docid": doc_id, "owner_id": owner_id, "bid": bid_id}
    )


def solicitation_summary_url(wp_base_url: str, bid: dict[str, Any]) -> str:
    params = {
        "ac": 2,
        "bidid": bid.get("bidid") or "",
        "oid": (bid.get("ownerOrg") or {}).get("oid") or "",
        "bidoid": (bid.get("creatorOrg") or {}).get("oid") or "",
    }
    return wp_base_url.rstrip("/") + "/MainBidBoard/solicitation_document.pdf?" + urlencode(params)


def line_items_download_url(wp_base_url: str, bid: dict[str, Any]) -> str:
    return wp_base_url.rstrip("/") + f"/public/bb/{CUSTOMER_ID}/{bid.get('bidid')}/lineitem-spreadsheet/download.do"


def clean_contact_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def parse_contact_info(value: Any) -> dict[str, str]:
    lines = clean_contact_text(value).splitlines()
    name_address_lines: list[str] = []
    phone = ""
    fax = ""
    email = ""

    for line in lines:
        tel_match = re.match(r"(?i)^tel:\s*(.*)$", line)
        fax_match = re.match(r"(?i)^fax:\s*(.*)$", line)
        email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", line)

        if tel_match:
            phone = tel_match.group(1).strip()
        elif fax_match:
            fax = fax_match.group(1).strip()
        elif email_match:
            email = email_match.group(0).strip()
        else:
            name_address_lines.append(line)

    name = name_address_lines[0] if name_address_lines else ""
    address = ", ".join(name_address_lines[1:]) if len(name_address_lines) > 1 else ""

    return {
        "contact_name": name,
        "contact_address": address,
        "contact_phone": phone,
        "contact_fax": fax,
        "contact_email": email,
    }


def normalize_documents(wp_base_url: str, bid: dict[str, Any], document_info: dict[str, Any] | None) -> dict[str, Any]:
    bid_id = bid.get("bidid")
    owner_id = (bid.get("ownerOrg") or {}).get("oid")
    bid_docs_out = []
    for doc in bid.get("bidDocs") or []:
        doc_doc = ((doc.get("docAssoc") or {}).get("docDoc") or {})
        doc_id = doc_doc.get("docid") or doc.get("docid") or ""
        doc_owner = doc_doc.get("ownerId") or doc.get("ownerId") or owner_id
        bid_docs_out.append(
            {
                "name": doc_doc.get("name") or doc.get("name") or "",
                "docid": doc_id,
                "owner_id": doc_owner,
                "protected_download_url": protected_doc_download_url(wp_base_url, doc_id, doc_owner, bid_id),
            }
        )

    mandatory_docs = []
    if document_info:
        for doc in document_info.get("mandatoryDocuments") or []:
            doc_id = doc.get("docid") or ""
            mandatory_docs.append(
                {
                    "docid": doc_id,
                    "protected_download_url": protected_doc_download_url(wp_base_url, doc_id, owner_id, bid_id),
                }
            )
    return {"bid_documents": bid_docs_out, "mandatory_documents": mandatory_docs}


def normalize_bid(
    bid: dict[str, Any],
    *,
    api_base_url: str,
    wp_base_url: str,
    detail: dict[str, Any] | None,
    document_info: dict[str, Any] | None,
) -> dict[str, Any]:
    owner_org = bid.get("ownerOrg") or {}
    creator_org = bid.get("creatorOrg") or {}
    status = bid.get("ctBidstatus") or {}
    sol_type = bid.get("orgBidClassType") or {}
    contacts = bid.get("bidContacts") or []
    contact_detail = (contacts[0].get("bidContactDetail") if contacts else {}) or {}
    contact_info = contact_detail.get("contactinfo", "")
    parsed_contact = parse_contact_info(contact_info)
    docs = normalize_documents(wp_base_url, bid, document_info)

    return {
        "bid_id": bid.get("bidid"),
        "bid_number": bid.get("bidNumber", ""),
        "title": bid.get("title", ""),
        "description": bid.get("description", ""),
        "status": status.get("publicStatus", ""),
        "solicitation_type": sol_type.get("description", ""),
        "start_date": bid.get("startDate"),
        "open_date": bid.get("openDate"),
        "end_date": bid.get("endDate"),
        "status_date": bid.get("statusDate"),
        "owner_org": owner_org.get("name", ""),
        "owner_org_id": owner_org.get("oid", ""),
        "creator_org": creator_org.get("name", ""),
        "creator_org_id": creator_org.get("oid", ""),
        "contact_info": contact_info,
        "contact_name": parsed_contact["contact_name"],
        "contact_address": parsed_contact["contact_address"],
        "contact_phone": parsed_contact["contact_phone"],
        "contact_fax": parsed_contact["contact_fax"],
        "contact_email": parsed_contact["contact_email"],
        "has_addendums": bid.get("hasAddendums", False),
        "bid_respond_access_type": (bid.get("bidRespondAccessType") or {}).get("description", ""),
        "public_detail_url": public_detail_url(bid.get("bidid")),
        "detail_api_url": bid_detail_api_url(api_base_url, bid.get("bidid")),
        "summary_pdf_url": solicitation_summary_url(wp_base_url, bid),
        "line_items_download_url": line_items_download_url(wp_base_url, bid),
        "document_info": document_info or {},
        "bid_documents": docs["bid_documents"],
        "mandatory_documents": docs["mandatory_documents"],
    }


def fetch_detail_and_documents(api_base_url: str, wp_base_url: str, bid_id: Any) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    detail_response = request_json(bid_detail_api_url(api_base_url, bid_id))
    records = detail_response.get("records") or []
    detail_bid = records[0] if records else None
    document_info = request_json(document_info_api_url(api_base_url, wp_base_url, detail_bid)) if detail_bid else None
    return detail_response, detail_bid, document_info


def collect(args: argparse.Namespace) -> dict[str, Any]:
    resource = load_resource_config()
    api_base_url = resource["apiBaseURL"]
    wp_base_url = resource["wpBaseURL"]
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    total_available = 0

    for page in range(1, args.max_pages + 1):
        logging.info("Fetching Connecticut search page %s", page)
        data = request_json(search_url(api_base_url, args, page))
        records = data.get("records") or []
        total_available = int(data.get("hits") or total_available or 0)
        if not records:
            break
        for bid in records:
            if args.max_records and len(rows) >= args.max_records:
                break
            detail_response = None
            document_info = None
            if not args.skip_details:
                try:
                    detail_response, detail_bid, document_info = fetch_detail_and_documents(api_base_url, wp_base_url, bid.get("bidid"))
                    bid = detail_bid or bid
                except Exception as exc:
                    logging.error("Detail failed for bid %s: %s", bid.get("bidid"), exc)
                    failures.append({"bid_id": str(bid.get("bidid")), "error": str(exc)})
            rows.append(normalize_bid(bid, api_base_url=api_base_url, wp_base_url=wp_base_url, detail=detail_response, document_info=document_info))
        if args.max_records and len(rows) >= args.max_records:
            break
        if page * args.rows >= total_available:
            break
        if args.delay:
            time.sleep(args.delay)

    return {
        "source": PORTAL_URL,
        "app": APP_BASE_URL,
        "resource": resource,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "keyword": args.keyword,
            "status": args.status,
            "solicitation_type": args.solicitation_type,
            "sort": args.sort,
            "rows": args.rows,
            "max_pages": args.max_pages,
            "max_records": args.max_records,
            "skip_details": args.skip_details,
        },
        "total_available": total_available,
        "count": len(rows),
        "detail_failure_count": len(failures),
        "detail_failures": failures,
        "solicitations": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Connecticut CTSource solicitations as JSON")
    parser.add_argument("--keyword", default="*")
    parser.add_argument("--status", default="Open")
    parser.add_argument("--solicitation-type", default="Request for Proposal")
    parser.add_argument("--sort", default="r")
    parser.add_argument("--rows", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--skip-details", action="store_true")
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR / "output")
    args = parser.parse_args()
    if not 1 <= args.rows <= 100:
        parser.error("--rows must be between 1 and 100")
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    return args


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    data = collect(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "connecticut_solicitations.json"
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, output_path)
    logging.info("Saved %s solicitations to %s", data["count"], output_path)


if __name__ == "__main__":
    main()
