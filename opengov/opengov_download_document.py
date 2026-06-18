import argparse
import re
from pathlib import Path

import requests


PROJECT_URL_TEMPLATE = "https://api.procurement.opengov.com/api/v1/project/{project_id}"
DOWNLOAD_DIR = Path("opengov_downloads")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://procurement.opengov.com",
}


def safe_filename(value):
    name = re.sub(r'[<>:"/\\\\|?*]+', "_", str(value)).strip()
    return name or "opengov_document"


def fetch_project_detail(project_id):
    headers = HEADERS.copy()
    headers["Referer"] = f"https://procurement.opengov.com/projects/{project_id}"

    response = requests.get(
        PROJECT_URL_TEMPLATE.format(project_id=project_id),
        headers=headers,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def iter_downloadable_attachments(project):
    document_attachment = project.get("documentAttachment")
    if isinstance(document_attachment, dict) and document_attachment.get("url"):
        yield document_attachment

    for attachment in project.get("attachments") or []:
        if isinstance(attachment, dict) and attachment.get("url"):
            yield attachment


def attachment_matches(attachment, resource_id):
    if not resource_id:
        return True

    resource_id = str(resource_id)
    return resource_id in {
        str(attachment.get("id")),
        str(attachment.get("sharedId")),
        str(attachment.get("resource_id")),
    }


def find_attachment(project, resource_id=None):
    for attachment in iter_downloadable_attachments(project):
        if attachment_matches(attachment, resource_id):
            return attachment

    if resource_id:
        raise ValueError(f"No downloadable attachment found for resource/id: {resource_id}")

    raise ValueError("No downloadable attachment found for this project")


def download_attachment(attachment, output_dir=DOWNLOAD_DIR):
    output_dir.mkdir(exist_ok=True)

    filename = safe_filename(
        attachment.get("filename")
        or attachment.get("name")
        or f"attachment_{attachment.get('id')}"
    )
    output_path = output_dir / filename

    response = requests.get(attachment["url"], timeout=120)
    response.raise_for_status()
    output_path.write_bytes(response.content)

    return output_path


def download_project_document(project_id, resource_id=None):
    project = fetch_project_detail(project_id)
    attachment = find_attachment(project, resource_id)
    return download_attachment(attachment)


def main():
    parser = argparse.ArgumentParser(description="Download an OpenGov project attachment.")
    parser.add_argument("project_id", help="OpenGov project ID, for example 272093")
    parser.add_argument(
        "--resource-id",
        help="Optional attachment id or sharedId. If omitted, the first downloadable attachment is used."
    )
    args = parser.parse_args()

    output_path = download_project_document(args.project_id, args.resource_id)
    print(f"Downloaded: {output_path.resolve()}")


if __name__ == "__main__":
    main()
