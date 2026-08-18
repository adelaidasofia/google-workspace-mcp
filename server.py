"""Google Workspace MCP — Gmail + Calendar + Drive + Docs + Sheets,
multi-account, token-efficient.

Registered in `~/.claude/.mcp.json` (or your project's `.mcp.json`) as `google-workspace`.

Two design goals:
1. Multi-account. Every tool takes `account` as an optional email; default is
   taken from GWS_DEFAULT_ACCOUNT or the first authorized mailbox.
2. Token-efficient. Search/list tools return compact shapes. Bodies and file
   content are opt-in via include_content / format params.

Keychain service name: `google-workspace-mcp` (see accounts.py KEYRING_SERVICE).
See SETUP.md for GCP OAuth setup; see README.md for day-to-day usage.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from fastmcp import FastMCP

import accounts
import calendar_tools
import docs_tools
import drive_tools
import gmail_tools
import sheets_tools

mcp = FastMCP("google-workspace")


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


@mcp.tool()
def gws_account_add() -> dict:
    """Authorize a new Google account via browser OAuth. Stores the refresh
    token in the macOS Keychain (service: onde-google-workspace-mcp).

    Requires client_secret.json at ~/.claude/google-workspace-mcp/client_secret.json.
    See SETUP.md.
    """
    return accounts.add_account()


@mcp.tool()
def gws_account_list() -> dict:
    """List all authorized Google accounts and which one is the default."""
    all_accounts = accounts.list_accounts()
    default = ""
    try:
        default = accounts.default_account()
    except accounts.AccountError:
        pass
    return {"accounts": all_accounts, "default": default, "count": len(all_accounts)}


@mcp.tool()
def gws_account_remove(email: str) -> dict:
    """Revoke local storage for an account. (Does not revoke Google-side; do
    that at https://myaccount.google.com/permissions.)"""
    return accounts.remove_account(email)


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------


@mcp.tool()
def gmail_search(
    query: str = "",
    account: str | None = None,
    limit: int = 10,
    include_spam_trash: bool = False,
    label_filter: list[str] | None = None,
) -> list[dict]:
    """Search Gmail messages. Returns compact shape (no body) — call gmail_read for body.

    Args:
        query: Gmail search operators, e.g. 'from:someone@example.com after:2026/04/01 is:unread'.
        account: Email address; defaults to configured default account.
        limit: 1-50. Default 10.
        include_spam_trash: Include SPAM/TRASH in results.
        label_filter: List of label names to filter by (e.g. ['INBOX', 'starred']).
    """
    return gmail_tools.search(
        query=query, account=account, limit=limit,
        include_spam_trash=include_spam_trash, label_filter=label_filter,
    )


@mcp.tool()
def gmail_read(
    message_id: str,
    account: str | None = None,
    keep_html: bool = False,
    full_thread: bool = False,
) -> dict:
    """Read a single message or full thread. Strips HTML to text by default.

    Args:
        message_id: Gmail message id from gmail_search.
        keep_html: If True, return raw HTML body. Default: strip to plain text.
        full_thread: If True, return all messages in the thread.
    """
    return gmail_tools.read(
        message_id=message_id, account=account,
        keep_html=keep_html, full_thread=full_thread,
    )


@mcp.tool()
def gmail_send(
    to: list[str],
    subject: str,
    body: str,
    account: str | None = None,
    from_alias: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Send an email. DESTRUCTIVE — irreversible once sent.

    Args:
        from_alias: Send-as identity (e.g. 'ops@example.com'). The
            authenticated `account` must have this alias configured in Gmail.
            Use gmail_sendas_list to see available aliases.
        dry_run: If True, return what WOULD be sent without calling the API.
            Use this to verify recipient, subject, and body before committing.
    """
    return gmail_tools.send(
        to=to, subject=subject, body=body, account=account,
        from_alias=from_alias, cc=cc, bcc=bcc, reply_to=reply_to,
        dry_run=dry_run,
    )


@mcp.tool()
def gmail_draft(
    to: list[str],
    subject: str,
    body: str,
    account: str | None = None,
    from_alias: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict:
    """Create a draft. Does not send."""
    return gmail_tools.draft(
        to=to, subject=subject, body=body, account=account,
        from_alias=from_alias, cc=cc, bcc=bcc,
    )


@mcp.tool()
def gmail_reply(
    message_id: str,
    body: str,
    account: str | None = None,
    reply_all: bool = False,
    dry_run: bool = False,
) -> dict:
    """Reply to a message. DESTRUCTIVE — sends immediately, same blast radius as gmail_send.
    Preserves thread + headers.

    Args:
        dry_run: If True, show what WOULD be sent without sending.
    """
    return gmail_tools.reply(
        message_id=message_id, body=body, account=account, reply_all=reply_all,
        dry_run=dry_run,
    )


@mcp.tool()
def gmail_labels_list(account: str | None = None) -> list[dict]:
    """List all labels on the account."""
    return gmail_tools.labels_list(account=account)


@mcp.tool()
def gmail_label_apply(
    message_ids: list[str],
    add: list[str] | None = None,
    remove: list[str] | None = None,
    account: str | None = None,
) -> dict:
    """Add or remove labels on messages. Batch up to 1000 per call."""
    return gmail_tools.label_apply(
        message_ids=message_ids, add=add, remove=remove, account=account,
    )


@mcp.tool()
def gmail_archive(message_ids: list[str], account: str | None = None) -> dict:
    """Archive messages: removes INBOX label but keeps them in All Mail, permanently searchable.
    No deletion. Use this to declutter without destroying.
    Use gmail_trash for soft-delete (messages auto-purged after 30 days)."""
    return gmail_tools.archive(message_ids=message_ids, account=account)


@mcp.tool()
def gmail_trash(message_ids: list[str], account: str | None = None) -> dict:
    """Move messages to Trash. Soft delete: auto-purged after 30 days, not easily searchable.
    Use gmail_archive to remove from inbox without any deletion risk."""
    return gmail_tools.trash(message_ids=message_ids, account=account)


@mcp.tool()
def gmail_attachments_list(message_id: str, account: str | None = None) -> list[dict]:
    """List a message's attachments: filename, mime type, size, and the
    attachment_id gmail_attachment_save needs. Metadata only, no download."""
    return gmail_tools.attachments_list(message_id=message_id, account=account)


@mcp.tool()
def gmail_attachment_save(
    message_id: str,
    attachment_id: str,
    filename: str | None = None,
    dest_dir: str | None = None,
    account: str | None = None,
) -> dict:
    """Download one attachment to a local file and return its path, so the
    caller can read it directly (PDF, image, docx, ...).

    Call gmail_attachments_list first for attachment_id and filename. Saves
    under ~/.claude/google-workspace-mcp/downloads unless dest_dir is given."""
    return gmail_tools.attachment_save(
        message_id=message_id, attachment_id=attachment_id,
        filename=filename, dest_dir=dest_dir, account=account,
    )


@mcp.tool()
def gmail_sendas_list(account: str | None = None) -> list[dict]:
    """List Send-As identities for this account (the aliases it can send as)."""
    return gmail_tools.sendas_list(account=account)


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


@mcp.tool()
def cal_list_calendars(account: str | None = None) -> list[dict]:
    """List all calendars visible to this account."""
    return calendar_tools.list_calendars(account=account)


@mcp.tool()
def cal_list_events(
    account: str | None = None,
    calendar_id: str = "primary",
    time_min: str = "now",
    time_max: str | None = None,
    days_ahead: int = 7,
    query: str | None = None,
    max_results: int = 25,
    verbose: bool = False,
    time_zone: str | None = None,
) -> list[dict]:
    """List events on a calendar.

    Args:
        time_min: ISO 8601 or 'now'/'today'/'tomorrow'. A time with no UTC
            offset, and 'today'/'tomorrow', mean local time in time_zone.
        time_max: ISO 8601. If omitted, uses time_min + days_ahead.
        query: Free-text search within event titles/descriptions.
        verbose: Include description, attendees, recurrence, conference data.
        time_zone: IANA name, e.g. 'America/Caracas'. Defaults to the machine's
            own zone (override with the GWS_TIME_ZONE environment variable).
    """
    return calendar_tools.list_events(
        account=account, calendar_id=calendar_id, time_min=time_min,
        time_max=time_max, days_ahead=days_ahead, query=query,
        max_results=max_results, verbose=verbose, time_zone=time_zone,
    )


@mcp.tool()
def cal_create_event(
    summary: str,
    start: str,
    end: str,
    account: str | None = None,
    calendar_id: str = "primary",
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    time_zone: str | None = None,
    send_updates: str = "all",
    add_meet: bool = False,
) -> dict:
    """Create a calendar event.

    Args:
        start, end: ISO 8601 (or 'now'/'today'/'tomorrow'). Written without a
            UTC offset ('2026-08-18T09:00:00') they mean that wall-clock time
            in time_zone, which is almost always what a person means. Include
            an offset ('...T09:00:00-04:00') to pin an exact instant.
        time_zone: IANA name, e.g. 'America/Caracas'. Defaults to the machine's
            own zone (override with the GWS_TIME_ZONE environment variable).
        send_updates: 'all' | 'externalOnly' | 'none'.
        add_meet: Attach a Google Meet link.
    """
    return calendar_tools.create_event(
        summary=summary, start=start, end=end, account=account,
        calendar_id=calendar_id, description=description, location=location,
        attendees=attendees, time_zone=time_zone, send_updates=send_updates,
        add_meet=add_meet,
    )


@mcp.tool()
def cal_update_event(
    event_id: str,
    account: str | None = None,
    calendar_id: str = "primary",
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees_add: list[str] | None = None,
    attendees_remove: list[str] | None = None,
    time_zone: str | None = None,
    send_updates: str = "all",
) -> dict:
    """Partial-update an event. Only pass fields you want to change.

    start/end follow the same rule as cal_create_event: no UTC offset means
    that wall-clock time in time_zone, which defaults to the machine's own zone.
    """
    return calendar_tools.update_event(
        event_id=event_id, account=account, calendar_id=calendar_id,
        summary=summary, start=start, end=end, description=description,
        location=location, attendees_add=attendees_add,
        attendees_remove=attendees_remove, time_zone=time_zone,
        send_updates=send_updates,
    )


@mcp.tool()
def cal_delete_event(
    event_id: str,
    account: str | None = None,
    calendar_id: str = "primary",
    send_updates: str = "all",
) -> dict:
    """Delete an event."""
    return calendar_tools.delete_event(
        event_id=event_id, account=account, calendar_id=calendar_id,
        send_updates=send_updates,
    )


@mcp.tool()
def cal_freebusy(
    time_min: str,
    time_max: str,
    account: str | None = None,
    emails: list[str] | None = None,
    time_zone: str | None = None,
) -> dict:
    """Check busy windows for one or more calendars (for scheduling).

    Args:
        emails: List of calendar IDs / emails. Defaults to ['primary'].
        time_zone: IANA name. Times with no UTC offset are read in this zone,
            which defaults to the machine's own.
    """
    return calendar_tools.freebusy(
        time_min=time_min, time_max=time_max, account=account, emails=emails,
        time_zone=time_zone,
    )


@mcp.tool()
def cal_respond(
    event_id: str,
    response: str,
    account: str | None = None,
    calendar_id: str = "primary",
) -> dict:
    """Respond to an event invite.

    Args:
        response: 'accepted' | 'declined' | 'tentative'.
    """
    return calendar_tools.respond(
        event_id=event_id, response=response, account=account, calendar_id=calendar_id,
    )


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------


@mcp.tool()
def drive_search(
    query: str = "",
    account: str | None = None,
    limit: int = 10,
    order_by: str = "modifiedTime desc",
    include_trashed: bool = False,
    drive_id: str | None = None,
) -> list[dict]:
    """Search Drive files. Returns compact metadata (no content).

    Args:
        query: Free text (wrapped as name/fullText contains) OR raw Drive
            q-syntax if it contains '=' or 'contains' (e.g.
            "mimeType='application/pdf' and modifiedTime > '2026-04-01T00:00:00'").
        limit: 1-100. Default 10.
        order_by: 'modifiedTime desc' | 'name' | 'createdTime desc' | etc.
        drive_id: Shared drive ID. Default: My Drive + shared-with-me.
    """
    return drive_tools.search(
        query=query, account=account, limit=limit, order_by=order_by,
        include_trashed=include_trashed, drive_id=drive_id,
    )


@mcp.tool()
def drive_read_file(
    file_id: str,
    account: str | None = None,
    include_content: bool = False,
    export_mime: str | None = None,
    max_chars: int = 50_000,
) -> dict:
    """Read a Drive file. Metadata by default; content is opt-in.

    Args:
        include_content: If True, fetch content. Google-native files export to
            text/csv/plain by default; override with export_mime.
            For Google Docs where you want markdown or docx output, use docs_export instead.
        export_mime: e.g. 'text/markdown' (Docs), 'application/pdf', or
            'text/csv' (Sheets). Ignored for non-Google files.
        max_chars: Cap on returned content. Default 50k.
    """
    return drive_tools.read_file(
        file_id=file_id, account=account, include_content=include_content,
        export_mime=export_mime, max_chars=max_chars,
    )


@mcp.tool()
def drive_list_folder(
    folder_id: str = "root",
    account: str | None = None,
    limit: int = 50,
    order_by: str = "name",
) -> list[dict]:
    """List direct children of a folder. Use 'root' for My Drive root."""
    return drive_tools.list_folder(
        folder_id=folder_id, account=account, limit=limit, order_by=order_by,
    )


@mcp.tool()
def drive_create_folder(
    name: str,
    parent_id: str | None = None,
    account: str | None = None,
) -> dict:
    """Create a new folder. If parent_id is None, creates at My Drive root."""
    return drive_tools.create_folder(name=name, parent_id=parent_id, account=account)


@mcp.tool()
def drive_upload(
    local_path: str,
    name: str | None = None,
    parent_id: str | None = None,
    mime: str | None = None,
    convert_to_google: bool = False,
    account: str | None = None,
) -> dict:
    """Upload a local file to Drive.

    Args:
        convert_to_google: If True, .docx/.csv/.xlsx/.pptx etc. convert to
            Google-native formats (Docs/Sheets/Slides) on upload.
    """
    return drive_tools.upload(
        local_path=local_path, name=name, parent_id=parent_id,
        mime=mime, convert_to_google=convert_to_google, account=account,
    )


@mcp.tool()
def drive_move(
    file_id: str,
    new_parent_id: str,
    account: str | None = None,
) -> dict:
    """Move a file to a different folder."""
    return drive_tools.move(
        file_id=file_id, new_parent_id=new_parent_id, account=account,
    )


@mcp.tool()
def drive_rename(
    file_id: str,
    new_name: str,
    account: str | None = None,
) -> dict:
    """Rename a file or folder."""
    return drive_tools.rename(file_id=file_id, new_name=new_name, account=account)


@mcp.tool()
def drive_share(
    file_id: str,
    email: str,
    role: str = "writer",
    send_notification: bool = False,
    message: str | None = None,
    account: str | None = None,
) -> dict:
    """Grant access to a file for someone who does not yet have a permission entry.
    Creates a new permission. Requires their email address.
    To change the role of someone who already has access, use drive_permission_update instead.

    Args:
        role: 'reader' | 'commenter' | 'writer' | 'fileOrganizer' |
            'organizer' | 'owner'.
        send_notification: If True, Google emails the grantee about the share.
    """
    return drive_tools.share(
        file_id=file_id, email=email, role=role,
        send_notification=send_notification, message=message, account=account,
    )


@mcp.tool()
def drive_trash(file_id: str, account: str | None = None, dry_run: bool = False) -> dict:
    """Move a file to Trash (soft delete, recoverable for 30 days). DESTRUCTIVE.

    Args:
        dry_run: If True, show what would be trashed without actually trashing it.
    """
    return drive_tools.trash(file_id=file_id, account=account, dry_run=dry_run)


@mcp.tool()
def drive_untrash(file_id: str, account: str | None = None) -> dict:
    """Restore a file from Trash."""
    return drive_tools.untrash(file_id=file_id, account=account)


@mcp.tool()
def drive_permission_list(file_id: str, account: str | None = None) -> list[dict]:
    """List all permissions (who has access) on a file."""
    return drive_tools.permission_list(file_id=file_id, account=account)


@mcp.tool()
def drive_permission_update(
    file_id: str,
    permission_id: str,
    role: str,
    account: str | None = None,
) -> dict:
    """Change the access role of someone who already has a permission on this file.
    Requires a permission_id — get it first from drive_permission_list.
    To grant access to someone new, use drive_share instead.

    Args:
        role: 'reader' | 'commenter' | 'writer' | 'fileOrganizer' |
            'organizer' | 'owner'.
    """
    return drive_tools.permission_update(
        file_id=file_id, permission_id=permission_id, role=role, account=account,
    )


@mcp.tool()
def drive_permission_delete(
    file_id: str,
    permission_id: str,
    account: str | None = None,
) -> dict:
    """Revoke a permission on a file. DESTRUCTIVE — removes someone's access immediately.
    Get permission_id from drive_permission_list first."""
    return drive_tools.permission_delete(
        file_id=file_id, permission_id=permission_id, account=account,
    )


@mcp.tool()
def drive_shared_drives_list(
    account: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List shared drives this account can access."""
    return drive_tools.shared_drives_list(account=account, limit=limit)


@mcp.tool()
def drive_comments_list(
    file_id: str,
    account: str | None = None,
    include_deleted: bool = False,
    limit: int = 50,
) -> list[dict]:
    """List comments on any Drive file (Doc/Sheet/Slide/uploaded)."""
    return drive_tools.comments_list(
        file_id=file_id, account=account,
        include_deleted=include_deleted, limit=limit,
    )


@mcp.tool()
def drive_comment_add(
    file_id: str,
    content: str,
    account: str | None = None,
    anchor: str | None = None,
) -> dict:
    """Add a sidebar comment (annotation) to a Drive file — not text to the document body.
    Works on Docs, Sheets, Slides, PDFs, etc.
    To add text to a Doc's body, use docs_append or docs_insert_at."""
    return drive_tools.comment_add(
        file_id=file_id, content=content, account=account, anchor=anchor,
    )


@mcp.tool()
def drive_comment_reply(
    file_id: str,
    comment_id: str,
    content: str,
    account: str | None = None,
) -> dict:
    """Reply to an existing comment."""
    return drive_tools.comment_reply(
        file_id=file_id, comment_id=comment_id, content=content, account=account,
    )


@mcp.tool()
def drive_comment_resolve(
    file_id: str,
    comment_id: str,
    account: str | None = None,
) -> dict:
    """Mark a comment as resolved."""
    return drive_tools.comment_resolve(
        file_id=file_id, comment_id=comment_id, account=account,
    )


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------


@mcp.tool()
def docs_create(
    title: str,
    body: str = "",
    parent_folder_id: str | None = None,
    account: str | None = None,
) -> dict:
    """Create a new Google Doc. Optional initial body text."""
    return docs_tools.create(
        title=title, body=body, parent_folder_id=parent_folder_id, account=account,
    )


@mcp.tool()
def docs_read(
    document_id: str,
    account: str | None = None,
    structured: bool = False,
    max_chars: int = 50_000,
) -> dict:
    """Read a Google Doc as flat plain text (formatting stripped) or as the full
    Docs API structural tree (paragraphs, text runs, tables) with structured=True.
    For formatted output (markdown, PDF, docx), use docs_export instead.
    Use structured=True for programmatic access to document structure.
    """
    return docs_tools.read(
        document_id=document_id, account=account,
        structured=structured, max_chars=max_chars,
    )


@mcp.tool()
def docs_append(
    document_id: str,
    text: str,
    account: str | None = None,
) -> dict:
    """Append text to the end of a Doc body."""
    return docs_tools.append(document_id=document_id, text=text, account=account)


@mcp.tool()
def docs_insert_at(
    document_id: str,
    text: str,
    index: int,
    account: str | None = None,
) -> dict:
    """Insert text at a specific character index within the Doc body. Index 1 = start of body.
    Use docs_append instead when adding to the end — it calculates the index automatically."""
    return docs_tools.insert_at(
        document_id=document_id, text=text, index=index, account=account,
    )


@mcp.tool()
def docs_replace_text(
    document_id: str,
    find: str,
    replace: str,
    match_case: bool = True,
    account: str | None = None,
) -> dict:
    """Find-and-replace text across the entire Doc. Returns count replaced."""
    return docs_tools.replace_text(
        document_id=document_id, find=find, replace=replace,
        match_case=match_case, account=account,
    )


@mcp.tool()
def docs_export(
    document_id: str,
    mime: str = "text/markdown",
    account: str | None = None,
    max_chars: int = 100_000,
) -> dict:
    """Export a Doc to a formatted output file. Default is markdown (headers, bold, lists preserved).
    Preferred over docs_read when you need formatted content rather than stripped plain text.
    For programmatic access to document structure (paragraphs, runs), use docs_read with structured=True.

    Args:
        mime: 'text/markdown' (default) | 'text/plain' | 'application/pdf' |
            'application/rtf' |
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document' (.docx)
    """
    return docs_tools.export(
        document_id=document_id, mime=mime, account=account, max_chars=max_chars,
    )


@mcp.tool()
def docs_suggestions_list(
    document_id: str,
    account: str | None = None,
) -> dict:
    """List pending suggestions (tracked changes) in a Doc. Per-suggestion
    accept/reject isn't supported by Docs API; use docs_suggestions_accept_all
    or docs_suggestions_reject_all."""
    return docs_tools.suggestions_list(document_id=document_id, account=account)


@mcp.tool()
def docs_suggestions_accept_all(
    document_id: str,
    account: str | None = None,
) -> dict:
    """Accept ALL pending suggestions by rewriting the Doc. DESTRUCTIVE — rewrites the entire
    document body; formatting metadata is lost. No per-suggestion accept via API.
    Call docs_suggestions_list first to review what will be accepted."""
    return docs_tools.accept_all_suggestions(document_id=document_id, account=account)


@mcp.tool()
def docs_suggestions_reject_all(
    document_id: str,
    account: str | None = None,
) -> dict:
    """Reject ALL pending suggestions by rewriting the Doc. DESTRUCTIVE — rewrites the entire
    document body; suggestions are permanently discarded. No per-suggestion reject via API.
    Call docs_suggestions_list first to review what will be discarded."""
    return docs_tools.reject_all_suggestions(document_id=document_id, account=account)


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------


@mcp.tool()
def sheets_create(
    title: str,
    parent_folder_id: str | None = None,
    account: str | None = None,
) -> dict:
    """Create a new Google Sheets workbook."""
    return sheets_tools.create_spreadsheet(
        title=title, parent_folder_id=parent_folder_id, account=account,
    )


@mcp.tool()
def sheets_list_sheets(
    spreadsheet_id: str,
    account: str | None = None,
) -> list[dict]:
    """List all tabs in a workbook with their dimensions."""
    return sheets_tools.list_sheets(spreadsheet_id=spreadsheet_id, account=account)


@mcp.tool()
def sheets_add_sheet(
    spreadsheet_id: str,
    title: str,
    rows: int = 1000,
    cols: int = 26,
    account: str | None = None,
) -> dict:
    """Add a new tab (sheet) to an existing workbook."""
    return sheets_tools.add_sheet(
        spreadsheet_id=spreadsheet_id, title=title,
        rows=rows, cols=cols, account=account,
    )


@mcp.tool()
def sheets_read_range(
    spreadsheet_id: str,
    range_a1: str,
    account: str | None = None,
    value_render: str = "FORMATTED_VALUE",
    date_render: str = "FORMATTED_STRING",
) -> dict:
    """Read a range of cells. Returns 2D array of values.

    Args:
        range_a1: A1 notation — 'Sheet1!A1:D100' or 'Sheet1' for full tab.
        value_render: 'FORMATTED_VALUE' | 'UNFORMATTED_VALUE' | 'FORMULA'.
    """
    return sheets_tools.read_range(
        spreadsheet_id=spreadsheet_id, range_a1=range_a1, account=account,
        value_render=value_render, date_render=date_render,
    )


@mcp.tool()
def sheets_write_range(
    spreadsheet_id: str,
    range_a1: str,
    values: list[list[Any]],
    account: str | None = None,
    value_input: str = "USER_ENTERED",
) -> dict:
    """Write a 2D array of values to a range. Overwrites existing cells.

    Args:
        value_input: 'USER_ENTERED' (parses formulas + dates) | 'RAW' (literal).
    """
    return sheets_tools.write_range(
        spreadsheet_id=spreadsheet_id, range_a1=range_a1, values=values,
        account=account, value_input=value_input,
    )


@mcp.tool()
def sheets_append(
    spreadsheet_id: str,
    range_a1: str,
    values: list[list[Any]],
    account: str | None = None,
    value_input: str = "USER_ENTERED",
    insert_data: str = "INSERT_ROWS",
) -> dict:
    """Append rows below the existing data in a range or sheet.

    Args:
        range_a1: Table range or sheet name (e.g. 'Sheet1!A:D' or 'Sheet1').
        insert_data: 'INSERT_ROWS' (shifts down) | 'OVERWRITE'.
    """
    return sheets_tools.append_rows(
        spreadsheet_id=spreadsheet_id, range_a1=range_a1, values=values,
        account=account, value_input=value_input, insert_data=insert_data,
    )


@mcp.tool()
def sheets_clear_range(
    spreadsheet_id: str,
    range_a1: str,
    account: str | None = None,
) -> dict:
    """Clear cell values in a range. Leaves formatting intact."""
    return sheets_tools.clear_range(
        spreadsheet_id=spreadsheet_id, range_a1=range_a1, account=account,
    )


@mcp.tool()
def sheets_batch_read(
    spreadsheet_id: str,
    ranges: list[str],
    account: str | None = None,
    value_render: str = "FORMATTED_VALUE",
) -> dict:
    """Read multiple ranges in one API call. Cheaper than N sheets_read_range."""
    return sheets_tools.batch_read(
        spreadsheet_id=spreadsheet_id, ranges=ranges,
        account=account, value_render=value_render,
    )


@mcp.tool()
def sheets_batch_write(
    spreadsheet_id: str,
    updates: list[dict],
    account: str | None = None,
    value_input: str = "USER_ENTERED",
) -> dict:
    """Write multiple ranges in one API call.

    Args:
        updates: List of {range: 'Sheet1!A1:B2', values: [[...], [...]]}.
    """
    return sheets_tools.batch_write(
        spreadsheet_id=spreadsheet_id, updates=updates,
        account=account, value_input=value_input,
    )


@mcp.tool()
def sheets_named_ranges_list(
    spreadsheet_id: str,
    account: str | None = None,
) -> list[dict]:
    """List all named ranges in a workbook."""
    return sheets_tools.named_ranges_list(spreadsheet_id=spreadsheet_id, account=account)


@mcp.tool()
def sheets_named_range_add(
    spreadsheet_id: str,
    name: str,
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    account: str | None = None,
) -> dict:
    """Create a named range (zero-based indices, end-exclusive). Get sheet_id from sheets_list_sheets."""
    return sheets_tools.named_range_add(
        spreadsheet_id=spreadsheet_id, name=name, sheet_id=sheet_id,
        start_row=start_row, end_row=end_row,
        start_col=start_col, end_col=end_col, account=account,
    )


@mcp.tool()
def sheets_named_range_delete(
    spreadsheet_id: str,
    named_range_id: str,
    account: str | None = None,
) -> dict:
    """Delete a named range by id."""
    return sheets_tools.named_range_delete(
        spreadsheet_id=spreadsheet_id, named_range_id=named_range_id, account=account,
    )


@mcp.tool()
def sheets_conditional_format_add(
    spreadsheet_id: str,
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    condition_type: str,
    condition_values: list[str],
    bg_color: dict | None = None,
    text_color: dict | None = None,
    bold: bool = False,
    account: str | None = None,
) -> dict:
    """Add a conditional formatting rule.

    Args:
        condition_type: 'NUMBER_GREATER' | 'NUMBER_LESS' | 'TEXT_CONTAINS' |
            'TEXT_EQ' | 'DATE_AFTER' | 'CUSTOM_FORMULA' | 'BLANK' | 'NOT_BLANK'.
        condition_values: Values for the condition (e.g. ['100'] or ['=A1>B1']).
        bg_color / text_color: {'red': 0-1, 'green': 0-1, 'blue': 0-1}.
    """
    return sheets_tools.conditional_format_add(
        spreadsheet_id=spreadsheet_id, sheet_id=sheet_id,
        start_row=start_row, end_row=end_row,
        start_col=start_col, end_col=end_col,
        condition_type=condition_type, condition_values=condition_values,
        bg_color=bg_color, text_color=text_color, bold=bold, account=account,
    )


@mcp.tool()
def sheets_data_validation_add(
    spreadsheet_id: str,
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    condition_type: str,
    condition_values: list[str],
    strict: bool = True,
    show_dropdown: bool = True,
    help_text: str | None = None,
    account: str | None = None,
) -> dict:
    """Set data validation on a range.

    Args:
        condition_type: 'ONE_OF_LIST' (dropdown) | 'NUMBER_GREATER' |
            'NUMBER_BETWEEN' | 'DATE_IS_VALID' | 'TEXT_IS_EMAIL' |
            'TEXT_IS_URL' | 'CUSTOM_FORMULA'.
        condition_values: e.g. ['yes','no','maybe'] for ONE_OF_LIST.
        strict: If True, reject invalid input; if False, warn only.
    """
    return sheets_tools.data_validation_add(
        spreadsheet_id=spreadsheet_id, sheet_id=sheet_id,
        start_row=start_row, end_row=end_row,
        start_col=start_col, end_col=end_col,
        condition_type=condition_type, condition_values=condition_values,
        strict=strict, show_dropdown=show_dropdown, help_text=help_text,
        account=account,
    )


if __name__ == "__main__":
    mcp.run()
