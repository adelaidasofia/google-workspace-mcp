"""Google Docs tool implementations.

Docs API is structural (paragraphs, text runs, tables). For most Claude use
cases, plain text in/out is what's useful. `docs_read` returns text; structural
reads are opt-in via structured=True.
"""

from __future__ import annotations

import datetime
import pathlib
from typing import Any

from accounts import service

_AUDIT_LOG = pathlib.Path.home() / ".claude" / "google-workspace-mcp" / "audit.log"


def _audit(action: str, detail: str) -> None:
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    with open(_AUDIT_LOG, "a") as f:
        f.write(f"{ts}\t{action}\t{detail}\n")


def _flatten(body: dict) -> str:
    """Walk the Docs structural tree and pull plain text."""
    out_lines: list[str] = []
    for element in body.get("content", []):
        para = element.get("paragraph")
        if para:
            line = ""
            for run in para.get("elements", []):
                tr = run.get("textRun")
                if tr:
                    line += tr.get("content", "")
            out_lines.append(line.rstrip("\n"))
            continue
        table = element.get("table")
        if table:
            for row in table.get("tableRows", []):
                cells = []
                for cell in row.get("tableCells", []):
                    cells.append(_flatten({"content": cell.get("content", [])}).replace("\n", " "))
                out_lines.append(" | ".join(cells))
        # skip sectionBreaks and tableOfContents
    return "\n".join(out_lines)


def create(
    title: str,
    body: str = "",
    parent_folder_id: str | None = None,
    account: str | None = None,
) -> dict:
    """Create a new Google Doc. Optionally drop initial body text."""
    docs = service("docs", "v1", account=account)
    drive = service("drive", "v3", account=account)

    created = docs.documents().create(body={"title": title}).execute()
    doc_id = created["documentId"]

    if body:
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": body}}]},
        ).execute()

    if parent_folder_id:
        # Move into specified folder
        current = drive.files().get(fileId=doc_id, fields="parents").execute()
        previous = ",".join(current.get("parents", []))
        drive.files().update(
            fileId=doc_id,
            addParents=parent_folder_id,
            removeParents=previous,
            fields="id,parents",
        ).execute()

    return {"id": doc_id, "title": title, "link": f"https://docs.google.com/document/d/{doc_id}/edit"}


def read(
    document_id: str,
    account: str | None = None,
    structured: bool = False,
    max_chars: int = 50_000,
) -> dict:
    """Read a Google Doc.

    Args:
        structured: If True, return the full Docs API structural tree. Default
            is to return flat text.
        max_chars: Truncate flat text to this many chars. Default 50k.
    """
    svc = service("docs", "v1", account=account)
    doc = svc.documents().get(documentId=document_id).execute()

    if structured:
        return {
            "id": doc["documentId"],
            "title": doc.get("title"),
            "revision_id": doc.get("revisionId"),
            "body": doc.get("body"),
        }

    text = _flatten(doc.get("body", {}))
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return {
        "id": doc["documentId"],
        "title": doc.get("title"),
        "revision_id": doc.get("revisionId"),
        "text": text,
        "truncated": truncated,
        "char_count": len(text),
    }


def append(document_id: str, text: str, account: str | None = None) -> dict:
    """Append text to the end of a Doc."""
    svc = service("docs", "v1", account=account)
    doc = svc.documents().get(documentId=document_id, fields="body(content)").execute()

    # End index is the last content element's endIndex
    end_index = 1
    for element in doc.get("body", {}).get("content", []):
        end_index = max(end_index, element.get("endIndex", 1))
    # Insert BEFORE the trailing newline Google adds
    insert_at = max(1, end_index - 1)

    svc.documents().batchUpdate(
        documentId=document_id,
        body={"requests": [{"insertText": {"location": {"index": insert_at}, "text": text}}]},
    ).execute()
    return {"id": document_id, "appended_chars": len(text), "status": "ok"}


def replace_text(
    document_id: str,
    find: str,
    replace: str,
    match_case: bool = True,
    account: str | None = None,
) -> dict:
    """Find-and-replace text across the entire Doc. DESTRUCTIVE — cannot be undone via API."""
    svc = service("docs", "v1", account=account)
    resp = svc.documents().batchUpdate(
        documentId=document_id,
        body={
            "requests": [
                {
                    "replaceAllText": {
                        "containsText": {"text": find, "matchCase": match_case},
                        "replaceText": replace,
                    }
                }
            ]
        },
    ).execute()
    occurrences = 0
    for r in resp.get("replies", []):
        occurrences += r.get("replaceAllText", {}).get("occurrencesChanged", 0)
    _audit("docs_replace_text", f"account={account or 'default'} doc={document_id} find={find!r} replace={replace!r} occurrences={occurrences}")
    return {"id": document_id, "occurrences_replaced": occurrences}


def insert_at(
    document_id: str,
    text: str,
    index: int,
    account: str | None = None,
) -> dict:
    """Insert text at a specific index. Index 1 = start of body."""
    svc = service("docs", "v1", account=account)
    svc.documents().batchUpdate(
        documentId=document_id,
        body={"requests": [{"insertText": {"location": {"index": index}, "text": text}}]},
    ).execute()
    return {"id": document_id, "inserted_chars": len(text), "at_index": index}


def suggestions_list(
    document_id: str,
    account: str | None = None,
) -> dict:
    """List pending suggestions (tracked changes) in a Doc.

    Note: Google Docs API does not support programmatically accepting/rejecting
    suggestions. This tool surfaces what's pending; acceptance must be done via
    the Docs UI, or by reading with PREVIEW_SUGGESTIONS_ACCEPTED and rewriting.
    """
    svc = service("docs", "v1", account=account)
    doc = svc.documents().get(documentId=document_id).execute()

    insertions: list[dict] = []
    deletions: list[dict] = []

    def walk(content_elements):
        for element in content_elements:
            para = element.get("paragraph")
            if para:
                for run in para.get("elements", []):
                    tr = run.get("textRun")
                    if not tr:
                        continue
                    text = tr.get("content", "")
                    for sid in tr.get("suggestedInsertionIds", []) or []:
                        insertions.append({"suggestion_id": sid, "text": text})
                    for sid in tr.get("suggestedDeletionIds", []) or []:
                        deletions.append({"suggestion_id": sid, "text": text})
            table = element.get("table")
            if table:
                for row in table.get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        walk(cell.get("content", []))

    walk(doc.get("body", {}).get("content", []))

    return {
        "id": doc["documentId"],
        "title": doc.get("title"),
        "pending_insertions": insertions,
        "pending_deletions": deletions,
        "total_pending": len(insertions) + len(deletions),
    }


def accept_all_suggestions(
    document_id: str,
    account: str | None = None,
) -> dict:
    """Accept ALL pending suggestions by rewriting the Doc. DESTRUCTIVE — rewrites the entire
    document body; formatting metadata is lost. No per-suggestion accept via API.
    Call docs_suggestions_list first to review what will be accepted.
    """
    svc = service("docs", "v1", account=account)
    doc = svc.documents().get(
        documentId=document_id,
        suggestionsViewMode="PREVIEW_SUGGESTIONS_ACCEPTED",
    ).execute()
    accepted_text = _flatten(doc.get("body", {}))

    current = svc.documents().get(documentId=document_id, fields="body(content)").execute()
    end_index = 1
    for element in current.get("body", {}).get("content", []):
        end_index = max(end_index, element.get("endIndex", 1))

    requests = []
    if end_index > 2:
        requests.append({
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": end_index - 1}
            }
        })
    if accepted_text:
        requests.append({
            "insertText": {"location": {"index": 1}, "text": accepted_text}
        })

    if requests:
        svc.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()

    _audit("docs_suggestions_accept_all", f"account={account or 'default'} doc={document_id}")
    return {"id": document_id, "status": "all suggestions accepted", "char_count": len(accepted_text)}


def reject_all_suggestions(
    document_id: str,
    account: str | None = None,
) -> dict:
    """Reject ALL pending suggestions by rewriting the Doc. DESTRUCTIVE — rewrites the entire
    document body; suggestions are permanently discarded.
    Call docs_suggestions_list first to review what will be discarded.
    """
    svc = service("docs", "v1", account=account)
    doc = svc.documents().get(
        documentId=document_id,
        suggestionsViewMode="PREVIEW_WITHOUT_SUGGESTIONS",
    ).execute()
    rejected_text = _flatten(doc.get("body", {}))

    current = svc.documents().get(documentId=document_id, fields="body(content)").execute()
    end_index = 1
    for element in current.get("body", {}).get("content", []):
        end_index = max(end_index, element.get("endIndex", 1))

    requests = []
    if end_index > 2:
        requests.append({
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": end_index - 1}
            }
        })
    if rejected_text:
        requests.append({
            "insertText": {"location": {"index": 1}, "text": rejected_text}
        })

    if requests:
        svc.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()

    _audit("docs_suggestions_reject_all", f"account={account or 'default'} doc={document_id}")
    return {"id": document_id, "status": "all suggestions rejected", "char_count": len(rejected_text)}


def export(
    document_id: str,
    mime: str = "text/markdown",
    account: str | None = None,
    max_chars: int = 100_000,
) -> dict:
    """Export a Doc to plain text, markdown, pdf, etc. via Drive export.

    Args:
        mime: 'text/plain' | 'text/markdown' | 'application/pdf' | 'application/rtf' |
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document' (.docx)
    """
    drive = service("drive", "v3", account=account)
    request = drive.files().export_media(fileId=document_id, mimeType=mime)
    data = request.execute()
    try:
        text = data.decode("utf-8")
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        return {"id": document_id, "mime": mime, "content": text, "truncated": truncated}
    except (UnicodeDecodeError, AttributeError):
        return {"id": document_id, "mime": mime, "bytes": len(data), "content": None,
                "note": "Binary export — download via drive_read_file or save locally"}
