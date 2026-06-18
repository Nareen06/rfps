import json
import re
import textwrap
from html import unescape
from pathlib import Path


INPUT_FILE = Path("opengov_all_project_details.txt")
OUTPUT_FILE = Path("opengov_db_column_report.txt")


def clean_html(value):
    if not value:
        return "N/A"

    text = re.sub(r"<br\s*/?>", "\n", str(value), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip() or "N/A"


def clean(value):
    if value in (None, ""):
        return "N/A"
    return clean_html(value) if isinstance(value, str) else str(value)


def wrap_field(label, value, width=110):
    text = clean(value)
    wrapped = textwrap.wrap(
        text,
        width=width,
        initial_indent=f"{label}: ",
        subsequent_indent=" " * (len(label) + 2),
    )
    return "\n".join(wrapped) if wrapped else f"{label}: N/A"


def load_project_details(path):
    text = path.read_text(encoding="utf-8-sig")
    marker = "Project Detail JSON\n-------------------\n"

    if marker not in text:
        raise ValueError(f"Could not find JSON section marker in {path}")

    json_text = text.split(marker, 1)[1]
    if "\n\nErrors\n------\n" in json_text:
        json_text = json_text.split("\n\nErrors\n------\n", 1)[0]

    return json.loads(json_text)


def first_category(project):
    categories = project.get("categories") or []
    if categories and isinstance(categories[0], dict):
        return categories[0]
    return {}


def map_opportunity(project):
    organization = project.get("government", {}).get("organization", {})
    addendums = project.get("addendums") or []

    return {
        "id": project.get("id"),
        "title": project.get("title"),
        "solicitation_number": project.get("financialId"),
        "notice_type": project.get("type"),
        "publish_date": project.get("postedAt") or project.get("releaseProjectDate"),
        "modified_date": project.get("lastUpdatedAt") or project.get("updated_at"),
        "is_active": project.get("status") in ("open", "pending", "evaluation"),
        "is_canceled": project.get("status") == "canceled" or bool(project.get("closeOutReason")),
        "modifications_count": len(addendums),
        "department": project.get("departmentName") or project.get("department", {}).get("name"),
        "agency": organization.get("name"),
        "office_name": project.get("departmentName") or project.get("department", {}).get("name"),
        "office_city": organization.get("city"),
        "office_state": organization.get("state"),
        "office_zip": organization.get("zipCode"),
        "office_country": organization.get("country"),
        "response_date": project.get("proposalDeadline"),
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
        "full_description": project.get("rawSummary") or project.get("summary"),
        "poc_full_name": project.get("contactFullName") or project.get("contactDisplayName"),
        "poc_title": project.get("contactTitle"),
        "poc_phone": project.get("contactPhoneComplete") or project.get("contactPhone"),
        "poc_email": project.get("contactEmail"),
        "poc_type": "project_contact",
    }


def iter_attachments(project):
    document_attachment = project.get("documentAttachment")
    if isinstance(document_attachment, dict) and document_attachment.get("url"):
        yield "project_document_snapshot", document_attachment
        return

    for attachment in project.get("attachments") or []:
        if isinstance(attachment, dict) and attachment.get("url"):
            yield "attachment", attachment
            return


def map_attachment(project, attachment_type, attachment):
    return {
        "id": attachment.get("id"),
        "opportunity_id": project.get("id"),
        "resource_id": attachment.get("sharedId") or attachment.get("id"),
        "file_name": attachment.get("filename") or attachment.get("name"),
        "file_type": attachment.get("fileExtension"),
        "posted_date": attachment.get("created_at") or project.get("postedAt"),
        "created_at": attachment.get("created_at"),
        "updated_at": attachment.get("updated_at"),
        "external_url": attachment.get("url"),
        "attachment_type": attachment.get("type") or attachment_type,
    }


def format_dict(title, values):
    lines = [title, "-" * len(title)]
    for key, value in values.items():
        if key == "external_url":
            lines.append(f"{key}: {clean(value)}")
        else:
            lines.append(wrap_field(key, value))
    return "\n".join(lines)


def build_report(projects):
    lines = [
        "OpenGov Fields Matching Database Columns",
        "========================================",
        f"Projects found: {len(projects)}",
        "",
    ]

    attachment_count = 0

    for index, project in enumerate(projects, start=1):
        lines.append(f"PROJECT {index}")
        lines.append("=" * len(f"PROJECT {index}"))
        lines.append(format_dict("Opportunity Columns", map_opportunity(project)))
        lines.append("")

        attachments = list(iter_attachments(project))
        attachment_count += len(attachments)

        if attachments:
            lines.append("Attachment Columns")
            lines.append("------------------")
            for attachment_index, (attachment_type, attachment) in enumerate(attachments, start=1):
                lines.append(format_dict(
                    f"Attachment {attachment_index}",
                    map_attachment(project, attachment_type, attachment)
                ))
                lines.append("")
        else:
            lines.append("Attachment Columns")
            lines.append("------------------")
            lines.append("No matching attachment records found.")
            lines.append("")

    lines.insert(3, f"Attachments found: {attachment_count}")
    return "\n".join(lines).strip() + "\n"


def main():
    projects = load_project_details(INPUT_FILE)
    OUTPUT_FILE.write_text(build_report(projects), encoding="utf-8-sig")
    print(f"Saved mapped report: {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
