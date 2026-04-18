"""Google Drive tool implementations — multi-account, token-efficient.

Design rules:
- Search returns metadata only: id, name, mime, modified, size, owner, parents.
  Never dumps content.
- `drive_read_file` downloads/exports content only when explicitly called.
- Google-native mime types (Docs, Sheets, Slides) are exported to text/csv/etc,
  not raw JSON blobs.
"""

from __future__ import annotations

import io
from typing import Any

from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

from accounts import service

# Map Google-native mime types to sensible export formats
GOOGLE_NATIVE_EXPORT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.drawing": "image/png",
}

# Trim what we send back from Drive API to save tokens
_BASE_FIELDS = "id,name,mimeType,modifiedTime,size,owners(emailAddress),parents,webViewLink,trashed"


def _summary(f: dict) -> dict:
    owners = f.get("owners") or []
    return {
        "id": f["id"],
        "name": f.get("name"),
        "mime": f.get("mimeType"),
        "modified": f.get("modifiedTime"),
        "size": int(f["size"]) if f.get("size") else None,
        "owner": owners[0]["emailAddress"] if owners else None,
        "parents": f.get("parents", []),
        "link": f.get("webViewLink"),
        "trashed": f.get("trashed", False),
    }


def search(
    query: str = "",
    account: str | None = None,
    limit: int = 10,
    order_by: str = "modifiedTime desc",
    include_trashed: bool = False,
    drive_id: str | None = None,
) -> list[dict]:
    """Search Drive files.

    Args:
        query: Either free-text (wrapped in `name contains '...' and fullText contains '...'`)
            or a raw Drive query using Drive query syntax
            (e.g. "mimeType='application/pdf' and modifiedTime > '2026-04-01T00:00:00'").
            Raw query is detected by presence of `=` or `contains`.
        order_by: e.g. 'modifiedTime desc', 'name', 'createdTime desc'.
        drive_id: Shared drive ID. Default: user's My Drive + visible shared files.
    """
    svc = service("drive", "v3", account=account)

    if query and ("=" in query or "contains" in query or "'" in query):
        q = query
    elif query:
        safe = query.replace("'", "\\'")
        q = f"(name contains '{safe}' or fullText contains '{safe}')"
    else:
        q = ""
    if not include_trashed:
        q = f"{q} and trashed = false" if q else "trashed = false"

    params: dict[str, Any] = {
        "q": q,
        "pageSize": min(max(limit, 1), 100),
        "orderBy": order_by,
        "fields": f"files({_BASE_FIELDS})",
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }
    if drive_id:
        params["driveId"] = drive_id
        params["corpora"] = "drive"

    resp = svc.files().list(**params).execute()
    return [_summary(f) for f in resp.get("files", [])]


def read_file(
    file_id: str,
    account: str | None = None,
    include_content: bool = False,
    export_mime: str | None = None,
    max_chars: int = 50_000,
) -> dict:
    """Read file metadata. Content is opt-in via include_content=True.

    Args:
        include_content: If True, fetch and return content. For Google-native
            files (Docs/Sheets/Slides), exports to text/csv/plain by default.
            For other files, returns raw bytes decoded as UTF-8 (binary files
            get a size-only placeholder).
        export_mime: Override the export target for Google-native files
            (e.g. 'application/pdf' for a Doc, 'text/markdown' for Docs).
            Ignored for non-Google files.
        max_chars: Truncate body to this many characters. Default 50k.
    """
    svc = service("drive", "v3", account=account)
    meta = (
        svc.files()
        .get(fileId=file_id, fields=_BASE_FIELDS, supportsAllDrives=True)
        .execute()
    )
    if not include_content:
        return _summary(meta)

    mime = meta.get("mimeType", "")

    if mime.startswith("application/vnd.google-apps"):
        target = export_mime or GOOGLE_NATIVE_EXPORT.get(mime, "text/plain")
        request = svc.files().export_media(fileId=file_id, mimeType=target)
    else:
        request = svc.files().get_media(fileId=file_id, supportsAllDrives=True)

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    raw = buf.getvalue()

    try:
        text = raw.decode("utf-8")
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
    except UnicodeDecodeError:
        text = f"(binary, {len(raw)} bytes — use export_mime or download directly)"
        truncated = False

    return {**_summary(meta), "content": text, "truncated": truncated, "bytes": len(raw)}


def list_folder(
    folder_id: str = "root",
    account: str | None = None,
    limit: int = 50,
    order_by: str = "name",
) -> list[dict]:
    svc = service("drive", "v3", account=account)
    resp = (
        svc.files()
        .list(
            q=f"'{folder_id}' in parents and trashed = false",
            pageSize=min(max(limit, 1), 200),
            orderBy=order_by,
            fields=f"files({_BASE_FIELDS})",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return [_summary(f) for f in resp.get("files", [])]


def create_folder(
    name: str,
    parent_id: str | None = None,
    account: str | None = None,
) -> dict:
    svc = service("drive", "v3", account=account)
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    created = (
        svc.files()
        .create(body=body, fields=_BASE_FIELDS, supportsAllDrives=True)
        .execute()
    )
    return _summary(created)


def upload(
    local_path: str,
    name: str | None = None,
    parent_id: str | None = None,
    mime: str | None = None,
    convert_to_google: bool = False,
    account: str | None = None,
) -> dict:
    """Upload a local file.

    Args:
        convert_to_google: If True and the file is a doc/csv/xlsx, convert to
            Docs/Sheets on upload.
    """
    svc = service("drive", "v3", account=account)
    body: dict[str, Any] = {"name": name or local_path.rsplit("/", 1)[-1]}
    if parent_id:
        body["parents"] = [parent_id]
    if convert_to_google:
        # Drive picks the right Google-native mime based on source
        if local_path.endswith((".docx", ".doc", ".txt", ".md", ".rtf")):
            body["mimeType"] = "application/vnd.google-apps.document"
        elif local_path.endswith((".xlsx", ".xls", ".csv", ".tsv")):
            body["mimeType"] = "application/vnd.google-apps.spreadsheet"
        elif local_path.endswith((".pptx", ".ppt")):
            body["mimeType"] = "application/vnd.google-apps.presentation"

    media = MediaFileUpload(local_path, mimetype=mime, resumable=True)
    created = (
        svc.files()
        .create(body=body, media_body=media, fields=_BASE_FIELDS, supportsAllDrives=True)
        .execute()
    )
    return _summary(created)


def move(
    file_id: str,
    new_parent_id: str,
    account: str | None = None,
) -> dict:
    svc = service("drive", "v3", account=account)
    current = (
        svc.files()
        .get(fileId=file_id, fields="parents", supportsAllDrives=True)
        .execute()
    )
    previous = ",".join(current.get("parents", []))
    updated = (
        svc.files()
        .update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=previous,
            fields=_BASE_FIELDS,
            supportsAllDrives=True,
        )
        .execute()
    )
    return _summary(updated)


def rename(file_id: str, new_name: str, account: str | None = None) -> dict:
    svc = service("drive", "v3", account=account)
    updated = (
        svc.files()
        .update(
            fileId=file_id,
            body={"name": new_name},
            fields=_BASE_FIELDS,
            supportsAllDrives=True,
        )
        .execute()
    )
    return _summary(updated)


def share(
    file_id: str,
    email: str,
    role: str = "writer",
    send_notification: bool = False,
    message: str | None = None,
    account: str | None = None,
) -> dict:
    """Share a file with someone.

    Args:
        role: 'reader' | 'commenter' | 'writer' | 'fileOrganizer' | 'organizer' | 'owner'.
    """
    svc = service("drive", "v3", account=account)
    permission = {"type": "user", "role": role, "emailAddress": email}
    created = (
        svc.permissions()
        .create(
            fileId=file_id,
            body=permission,
            sendNotificationEmail=send_notification,
            emailMessage=message,
            supportsAllDrives=True,
            fields="id,type,role,emailAddress",
        )
        .execute()
    )
    return {"file_id": file_id, **created}


def trash(file_id: str, account: str | None = None) -> dict:
    svc = service("drive", "v3", account=account)
    updated = (
        svc.files()
        .update(
            fileId=file_id,
            body={"trashed": True},
            fields=_BASE_FIELDS,
            supportsAllDrives=True,
        )
        .execute()
    )
    return _summary(updated)


def untrash(file_id: str, account: str | None = None) -> dict:
    svc = service("drive", "v3", account=account)
    updated = (
        svc.files()
        .update(
            fileId=file_id,
            body={"trashed": False},
            fields=_BASE_FIELDS,
            supportsAllDrives=True,
        )
        .execute()
    )
    return _summary(updated)


def permission_list(file_id: str, account: str | None = None) -> list[dict]:
    """List all permissions on a file."""
    svc = service("drive", "v3", account=account)
    resp = (
        svc.permissions()
        .list(
            fileId=file_id,
            fields="permissions(id,type,role,emailAddress,domain,displayName,deleted,pendingOwner)",
            supportsAllDrives=True,
        )
        .execute()
    )
    return resp.get("permissions", [])


def permission_update(
    file_id: str,
    permission_id: str,
    role: str,
    account: str | None = None,
) -> dict:
    """Change a grantee's role on a file.

    Args:
        role: 'reader' | 'commenter' | 'writer' | 'fileOrganizer' | 'organizer' | 'owner'.
    """
    svc = service("drive", "v3", account=account)
    updated = (
        svc.permissions()
        .update(
            fileId=file_id,
            permissionId=permission_id,
            body={"role": role},
            fields="id,type,role,emailAddress",
            supportsAllDrives=True,
        )
        .execute()
    )
    return {"file_id": file_id, **updated}


def permission_delete(
    file_id: str,
    permission_id: str,
    account: str | None = None,
) -> dict:
    """Revoke a permission on a file."""
    svc = service("drive", "v3", account=account)
    svc.permissions().delete(
        fileId=file_id,
        permissionId=permission_id,
        supportsAllDrives=True,
    ).execute()
    return {"file_id": file_id, "permission_id": permission_id, "status": "deleted"}


def comments_list(
    file_id: str,
    account: str | None = None,
    include_deleted: bool = False,
    limit: int = 50,
) -> list[dict]:
    """List comments on any Drive file (Doc, Sheet, Slide, or uploaded file)."""
    svc = service("drive", "v3", account=account)
    resp = (
        svc.comments()
        .list(
            fileId=file_id,
            pageSize=min(max(limit, 1), 100),
            includeDeleted=include_deleted,
            fields="comments(id,content,author(displayName,emailAddress),createdTime,modifiedTime,resolved,deleted,quotedFileContent,anchor,replies(id,content,author(displayName,emailAddress),createdTime))",
        )
        .execute()
    )
    return resp.get("comments", [])


def comment_add(
    file_id: str,
    content: str,
    account: str | None = None,
    anchor: str | None = None,
) -> dict:
    """Add a comment to a Drive file.

    Args:
        anchor: JSON string pinning the comment to a specific location
            (format depends on file type). Omit for document-level comment.
    """
    svc = service("drive", "v3", account=account)
    body: dict[str, Any] = {"content": content}
    if anchor:
        body["anchor"] = anchor
    created = (
        svc.comments()
        .create(
            fileId=file_id,
            body=body,
            fields="id,content,author(displayName,emailAddress),createdTime,resolved",
        )
        .execute()
    )
    return {"file_id": file_id, **created}


def comment_reply(
    file_id: str,
    comment_id: str,
    content: str,
    account: str | None = None,
) -> dict:
    """Reply to an existing comment."""
    svc = service("drive", "v3", account=account)
    created = (
        svc.replies()
        .create(
            fileId=file_id,
            commentId=comment_id,
            body={"content": content},
            fields="id,content,author(displayName,emailAddress),createdTime",
        )
        .execute()
    )
    return {"file_id": file_id, "comment_id": comment_id, **created}


def comment_resolve(
    file_id: str,
    comment_id: str,
    account: str | None = None,
) -> dict:
    """Mark a comment as resolved."""
    svc = service("drive", "v3", account=account)
    updated = (
        svc.comments()
        .update(
            fileId=file_id,
            commentId=comment_id,
            body={"resolved": True},
            fields="id,resolved",
        )
        .execute()
    )
    return {"file_id": file_id, **updated}


def shared_drives_list(account: str | None = None, limit: int = 50) -> list[dict]:
    """List shared drives this account has access to."""
    svc = service("drive", "v3", account=account)
    resp = (
        svc.drives()
        .list(
            pageSize=min(max(limit, 1), 100),
            fields="drives(id,name,createdTime,capabilities)",
        )
        .execute()
    )
    return [
        {
            "id": d["id"],
            "name": d.get("name"),
            "created": d.get("createdTime"),
            "can_edit": d.get("capabilities", {}).get("canEdit"),
        }
        for d in resp.get("drives", [])
    ]
