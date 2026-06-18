import json
from pathlib import Path

import requests

from opengov.opengov_projects import fetch_project_ids
from opengov.opengov_projects import fetch_project_ids_for_agency


URL_TEMPLATE = "https://api.procurement.opengov.com/api/v1/project/{project_id}"
OUTPUT_FILE = Path("opengov_all_project_details.txt")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://procurement.opengov.com",
}


def headers_for_project(project_id):
    headers = HEADERS.copy()
    headers["Referer"] = f"https://procurement.opengov.com/projects/{project_id}"
    return headers


def fetch_project_detail(project_id):
    response = requests.get(
        URL_TEMPLATE.format(project_id=project_id),
        headers=headers_for_project(project_id),
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def fetch_all_project_details():
    #project_ids = fetch_project_ids()
    project_ids = fetch_project_ids_for_agency('wsscwater')
    project_details = []
    errors = []

    for index, project_id in enumerate(project_ids, start=1):
        print(f"[{index}/{len(project_ids)}] Fetching detail for project {project_id}")

        try:
            project_details.append(fetch_project_detail(project_id))
        except requests.RequestException as exc:
            errors.append({
                "project_id": project_id,
                "error": str(exc),
            })

    return project_details, errors


def build_txt_report(project_details, errors):
    lines = [
        "OpenGov All Project Details",
        "===========================",
        f"Project details fetched: {len(project_details)}",
        f"Errors: {len(errors)}",
        "",
        "Project Detail JSON",
        "-------------------",
        json.dumps(project_details, indent=2, ensure_ascii=False),
    ]

    if errors:
        lines.extend([
            "",
            "Errors",
            "------",
            json.dumps(errors, indent=2, ensure_ascii=False),
        ])

    return "\n".join(lines).strip() + "\n"


def main():
    project_details, errors = fetch_all_project_details()
    OUTPUT_FILE.write_text(build_txt_report(project_details, errors), encoding="utf-8-sig")

    print(f"Saved project detail report: {OUTPUT_FILE.resolve()}")
    print(f"Project details fetched: {len(project_details)}")
    print(f"Errors: {len(errors)}")


if __name__ == "__main__":
    main()
