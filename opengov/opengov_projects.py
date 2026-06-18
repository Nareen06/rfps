import requests

from opengov.opengov_governments import fetch_agency_codes


URL_TEMPLATE = "https://api.procurement.opengov.com/api/v1/government/{agency_code}/project/public"

PAYLOAD = {
    "filters": [
        {
            "type": "status",
            "value": "open"
        }
    ],
    "quickSearchQuery": None,
    "limit": 10,
    "page": 1,
    "sortField": "proposalDeadline",
    "sortDirection": "DESC"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://procurement.opengov.com",
}


def headers_for_agency(agency_code):
    headers = HEADERS.copy()
    headers["Referer"] = f"https://procurement.opengov.com/portal/{agency_code}/"
    return headers


def payload_for_page(page):
    payload = PAYLOAD.copy()
    payload["page"] = page
    return payload


def find_projects(data):
    if isinstance(data, dict):
        return data.get("rows", [])
    return []


def fetch_project_ids_for_agency(agency_code):
    project_ids = []
    page = 1
    total_count = None

    while True:
        response = requests.post(
            URL_TEMPLATE.format(agency_code=agency_code),
            headers=headers_for_agency(agency_code),
            json=payload_for_page(page),
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        projects = find_projects(data)
        project_ids.extend(
            project["id"]
            for project in projects
            if isinstance(project, dict) and project.get("id") is not None
        )

        if isinstance(data, dict):
            total_count = data.get("count", total_count)

        if not projects:
            break

        limit = payload_for_page(page).get("limit", len(projects))
        if total_count is not None and len(project_ids) >= total_count:
            break

        if len(projects) < limit:
            break

        page += 1

    return project_ids


def fetch_project_ids():
    agency_codes = fetch_agency_codes()
    project_ids = []

    for index, agency_code in enumerate(agency_codes, start=1):
        print(f"[{index}/{len(agency_codes)}] Fetching project IDs for {agency_code}")

        try:
            project_ids.extend(fetch_project_ids_for_agency(agency_code))
        except requests.RequestException as exc:
            print(f"Skipping {agency_code}: {exc}")

    return project_ids


def main():
    project_ids = fetch_project_ids()
    print(project_ids)
    print(f"Total project IDs: {len(project_ids)}")


if __name__ == "__main__":
    main()
