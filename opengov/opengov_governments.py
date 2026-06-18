import requests


URL = "https://api.procurement.opengov.com/api/v1/government"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://procurement.opengov.com",
    "Referer": "https://procurement.opengov.com/",
}


def get_agency_code(record):
    government = record.get("government") or {}
    return government.get("code") or record.get("code")


def extract_agency_codes(records):
    agency_codes = []

    for record in records:
        code = get_agency_code(record)
        if code:
            agency_codes.append(str(code).strip())

    return sorted(agency_codes, key=str.lower)


def fetch_agency_codes():
    response = requests.get(URL, headers=HEADERS, timeout=60)
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, list):
        raise TypeError(f"Expected a list from OpenGov, got {type(data).__name__}")

    return extract_agency_codes(data)


def main():
    agency_codes = fetch_agency_codes()
    print(agency_codes)
    print(f"Total agency codes: {len(agency_codes)}")


if __name__ == "__main__":
    main()
