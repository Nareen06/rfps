import json
import re
import sys
from datetime import date
from pathlib import Path

import requests


SEARCH_URL = "https://api.instantmarkets.com/api/v2/opp/searchOnWeb2/"
DETAILS_URL = "https://api.instantmarkets.com/api/v2/opp/details/"
PDF_BASE_URL = "https://d3ot3obhbewajn.cloudfront.net/sshot/prod"

API_KEY = "bXmwcgINGQWWNIwKjpjm4O"
USER_EMAIL = ""

PDF_DIR = Path("instantmarkets_pdfs")
DETAILS_OUTPUT_FILE = "instantmarkets_details_with_pdfs_pretty.txt"
ROWS_PER_PAGE = 20

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": "https://www.instantmarkets.com",
    "Referer": "https://www.instantmarkets.com/",
    "x-api-key": API_KEY,
}


def build_search_payload():
    return {
        "customerType": "",
        "dueDateFilter": "",
        "location": "",
        "naicsList": [],
        "oppAgency": "",
        "oppStatus": "Active",
        "oppType": "Bid Notification,Pre-Bid Notification",
        "opportunityCount": ROWS_PER_PAGE,
        "pageNum": 1,
        "postDateFilter": "",
        "pscList": [],
        "searchName": "Information Technology",
        "searchDisplayName": "Information Technology",
        "searchDisplayName2": "",
        "setAside": "",
        "siteCity": "",
        "siteCountry": "",
        "siteState": "",
        "sortBy": "",
        "sourceName": "",
        "sourceType": "",
        "startDateFilter": "",
        "timezone": "Asia/Calcutta",
        "clientLocalDate": date.today().isoformat(),
        "getLimitedResults": False,
        "defaultSearch": True,
    }


def build_details_payload(doc_id):
    return {
        "authenticationToken": "",
        "currentTime": date.today().isoformat(),
        "opportunityId": doc_id,
        "userEmail": USER_EMAIL,
        "timezone": "Asia/Calcutta",
        "lowerLimiDescriptionCount": 0,
        "loginToken": "",
        "upperLimiDescriptionCount": 1000,
    }


def get_rows(data):
    response = data.get("response") or {}
    return response.get("opps") or response.get("results") or []


def safe_filename(value):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return value[:180] or "download"


def pdf_url_for(site_id, doc_id):
    return f"{PDF_BASE_URL}/{site_id}/{doc_id}.pdf"


def download_pdf(session, site_id, doc_id):
    if not site_id or not doc_id:
        return {
            "downloaded": False,
            "pdfUrl": "",
            "pdfPath": "",
            "error": "Missing siteId or docID",
        }

    PDF_DIR.mkdir(exist_ok=True)

    pdf_url = pdf_url_for(site_id, doc_id)
    pdf_path = PDF_DIR / f"{safe_filename(site_id)}_{safe_filename(doc_id)}.pdf"

    response = session.get(pdf_url, timeout=60)
    content_type = response.headers.get("content-type", "")

    if response.status_code != 200:
        return {
            "downloaded": False,
            "pdfUrl": pdf_url,
            "pdfPath": str(pdf_path),
            "status": response.status_code,
            "error": response.text[:300],
        }

    if "pdf" not in content_type.lower() and not response.content.startswith(b"%PDF"):
        return {
            "downloaded": False,
            "pdfUrl": pdf_url,
            "pdfPath": str(pdf_path),
            "status": response.status_code,
            "contentType": content_type,
            "error": "Response did not look like a PDF",
        }

    pdf_path.write_bytes(response.content)

    return {
        "downloaded": True,
        "pdfUrl": pdf_url,
        "pdfPath": str(pdf_path),
        "bytes": len(response.content),
        "contentType": content_type,
    }


session = requests.Session()
session.headers.update(headers)

search_response = session.post(SEARCH_URL, json=build_search_payload(), timeout=30)
print("SEARCH STATUS:", search_response.status_code)
search_response.raise_for_status()

search_data = search_response.json()
rows = get_rows(search_data)
print("ROWS:", len(rows))

results = []

for index, item in enumerate(rows, start=1):
    doc_id = item.get("docID")
    site_id = item.get("siteId")

    print(f"{index}/{len(rows)} DETAILS:", doc_id)
    details_response = session.post(
        DETAILS_URL,
        json=build_details_payload(doc_id),
        timeout=30,
    )

    try:
        details_data = details_response.json()
    except ValueError:
        details_data = {
            "error": "Non-JSON details response",
            "text": details_response.text[:1000],
        }

    print(f"{index}/{len(rows)} PDF:", pdf_url_for(site_id, doc_id))
    pdf_result = download_pdf(session, site_id, doc_id)

    results.append(
        {
            "docID": doc_id,
            "siteId": site_id,
            "title": item.get("title"),
            "detailsStatus": details_response.status_code,
            "details": details_data,
            "pdf": pdf_result,
        }
    )

with open(DETAILS_OUTPUT_FILE, "w", encoding="utf-8") as file:
    json.dump(results, file, indent=2, ensure_ascii=False)

print("DETAILS SAVED TO:", DETAILS_OUTPUT_FILE)
print("PDF FOLDER:", PDF_DIR)
