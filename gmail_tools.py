"""Gmail tool implementations — token-efficient wrappers around the Gmail API.

Design rules:
- Search returns minimal shape: id, thread_id, from, subject, snippet, date, unread, labels.
  ~80% token savings vs. pulling full messages.
- `gmail_read` is the only tool that returns bodies, and it strips HTML to text
  unless keep_html=True.
- All tools take `account` as first kwarg; None means "use default".
- Label names are strings on the way in and out; Gmail's internal label IDs
  are hidden from the caller.
"""

from __future__ import annotations

import base64
import datetime
import pathlib
import re
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

from accounts import service

_AUDIT_LOG = pathlib.Path.home() / ".claude" / "google-workspace-mcp" / "audit.log"


def _audit(action: str, detail: str) -> None:
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    with open(_AUDIT_LOG, "a") as f:
        f.write(f"{ts}\t{action}\t{detail}\n")


# ---------------------------------------------------------------------------
# Label name <-> id helpers
# ---------------------------------------------------------------------------


def _label_map(svc) -> dict[str, dict]:
    """Return {name_lower: {id, name, type}} for all labels on the account."""
    resp = svc.users().labels().list(userId="me").execute()
    return {lbl["name"].lower(): lbl for lbl in resp.get("labels", [])}


def _resolve_label_ids(svc, names: list[str]) -> list[str]:
    lbls = _label_map(svc)
    ids = []
    missing = []
    for n in names:
        lbl = lbls.get(n.lower())
        if lbl:
            ids.append(lbl["id"])
        else:
            missing.append(n)
    if missing:
        raise ValueError(f"Unknown labels: {missing}. Use gmail_labels_list to see existing.")
    return ids


def _names_for(label_ids: list[str], lbls: dict[str, dict]) -> list[str]:
    by_id = {v["id"]: v["name"] for v in lbls.values()}
    return [by_id.get(i, i) for i in label_ids]


# ---------------------------------------------------------------------------
# Message parsing helpers
# ---------------------------------------------------------------------------


def _headers_dict(msg: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_body(payload: dict, keep_html: bool) -> str:
    """Walk MIME parts and pull the best available body."""
    if not payload:
        return ""

    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")
    parts = payload.get("parts") or []

    if data and mime == "text/plain":
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    if data and mime == "text/html":
        html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return html if keep_html else _strip_html(html)

    plain = None
    html = None
    for p in parts:
        sub = _extract_body(p, keep_html)
        if not sub:
            continue
        if p.get("mimeType", "").startswith("text/plain") and plain is None:
            plain = sub
        elif p.get("mimeType", "").startswith("text/html") and html is None:
            html = sub
        elif plain is None and html is None:
            plain = sub
    return plain or html or ""


def _summary(msg: dict, lbls: dict[str, dict]) -> dict:
    h = _headers_dict(msg)
    _, from_email = parseaddr(h.get("from", ""))
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "from": h.get("from", ""),
        "from_email": from_email,
        "to": h.get("to", ""),
        "subject": h.get("subject", "(no subject)"),
        "date": h.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "unread": "UNREAD" in msg.get("labelIds", []),
        "labels": _names_for(msg.get("labelIds", []), lbls),
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def search(
    query: str = "",
    account: str | None = None,
    limit: int = 10,
    include_spam_trash: bool = False,
    label_filter: list[str] | None = None,
) -> list[dict]:
    """Gmail search. See https://support.google.com/mail/answer/7190 for operators."""
    svc = service("gmail", "v1", account=account)
    lbls = _label_map(svc)

    page_size = min(max(limit, 1), 100)
    params: dict[str, Any] = {
        "userId": "me",
        "q": query,
        "maxResults": page_size,
        "includeSpamTrash": include_spam_trash,
    }
    if label_filter:
        params["labelIds"] = _resolve_label_ids(svc, label_filter)

    # Collect IDs, following pagination until we have enough
    message_ids: list[str] = []
    while len(message_ids) < limit:
        resp = svc.users().messages().list(**params).execute()
        page_ids = [m["id"] for m in resp.get("messages", [])]
        message_ids.extend(page_ids)
        if "nextPageToken" not in resp or not page_ids:
            break
        params["pageToken"] = resp["nextPageToken"]
    message_ids = message_ids[:limit]

    if not message_ids:
        return []

    # Batch-fetch metadata. Gmail's batch endpoint hard-caps inner requests
    # at 100 ("Inner request count exceeds the limit. Received: N, Limit: 100"
    # — observed 2026-05-25 when email-triage requested PER_ACCOUNT_LIMIT=120).
    # Chunk into pages of <=100 and concatenate the results.
    fetched: dict[str, dict] = {}

    def _collect(req_id: str, response: dict | None, exception: Exception | None) -> None:
        if exception is None and response is not None:
            fetched[req_id] = response

    GMAIL_BATCH_MAX = 100
    for i in range(0, len(message_ids), GMAIL_BATCH_MAX):
        chunk = message_ids[i : i + GMAIL_BATCH_MAX]
        batch = svc.new_batch_http_request(callback=_collect)
        for mid in chunk:
            batch.add(
                svc.users().messages().get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                ),
                request_id=mid,
            )
        batch.execute()

    return [_summary(fetched[mid], lbls) for mid in message_ids if mid in fetched]


def read(
    message_id: str,
    account: str | None = None,
    keep_html: bool = False,
    full_thread: bool = False,
) -> dict:
    """Read one message or full thread. Returns body as text (HTML stripped by default)."""
    svc = service("gmail", "v1", account=account)
    lbls = _label_map(svc)

    if full_thread:
        first = svc.users().messages().get(userId="me", id=message_id, format="metadata").execute()
        thread_id = first["threadId"]
        thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        messages = []
        for m in thread.get("messages", []):
            messages.append(
                {
                    **_summary(m, lbls),
                    "body": _extract_body(m.get("payload", {}), keep_html),
                }
            )
        return {"thread_id": thread_id, "message_count": len(messages), "messages": messages}

    msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    return {**_summary(msg, lbls), "body": _extract_body(msg.get("payload", {}), keep_html)}


def _build_raw(
    to: list[str],
    subject: str,
    body: str,
    from_alias: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    thread_message_id: str | None = None,
) -> tuple[str, str | None]:
    msg = EmailMessage()
    if from_alias:
        msg["From"] = from_alias
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return raw, thread_message_id


def sendas_list(account: str | None = None) -> list[dict]:
    """List all 'Send mail as' identities configured on this mailbox.

    Use this to discover which aliases (e.g. ops@example.com) a given
    authenticated account is allowed to send from.
    """
    svc = service("gmail", "v1", account=account)
    resp = svc.users().settings().sendAs().list(userId="me").execute()
    return [
        {
            "email": s.get("sendAsEmail"),
            "display_name": s.get("displayName"),
            "is_primary": s.get("isPrimary", False),
            "is_default": s.get("isDefault", False),
            "treat_as_alias": s.get("treatAsAlias", False),
            "verification_status": s.get("verificationStatus"),
        }
        for s in resp.get("sendAs", [])
    ]


def send(
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
    """Send mail. DESTRUCTIVE — irreversible once sent.

    `from_alias` lets you send as a configured Send-As identity.
    If dry_run=True, returns what WOULD be sent without calling the API.
    """
    raw, _ = _build_raw(to, subject, body, from_alias=from_alias, cc=cc, bcc=bcc, reply_to=reply_to)
    if dry_run:
        return {
            "dry_run": True,
            "to": to,
            "subject": subject,
            "from_alias": from_alias,
            "cc": cc,
            "body_preview": body[:500],
            "status": "NOT SENT — dry_run=True",
        }
    svc = service("gmail", "v1", account=account)
    sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    _audit("gmail_send", f"account={account or 'default'} to={to} subject={subject!r}")
    return {"id": sent["id"], "thread_id": sent.get("threadId"), "status": "sent"}


def draft(
    to: list[str],
    subject: str,
    body: str,
    account: str | None = None,
    from_alias: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict:
    svc = service("gmail", "v1", account=account)
    raw, _ = _build_raw(to, subject, body, from_alias=from_alias, cc=cc, bcc=bcc)
    created = (
        svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    )
    return {"draft_id": created["id"], "message_id": created["message"]["id"], "status": "draft"}


def reply(
    message_id: str,
    body: str,
    account: str | None = None,
    reply_all: bool = False,
    dry_run: bool = False,
) -> dict:
    """Reply to a message. DESTRUCTIVE — sends immediately, same blast radius as gmail_send.

    If dry_run=True, shows what WOULD be sent without sending.
    """
    svc = service("gmail", "v1", account=account)
    original = svc.users().messages().get(userId="me", id=message_id, format="metadata").execute()
    h = _headers_dict(original)

    from_hdr = h.get("from", "")
    to_hdr = h.get("to", "")
    cc_hdr = h.get("cc", "") if reply_all else ""
    in_reply_to = h.get("message-id", "")
    references = (h.get("references", "") + " " + in_reply_to).strip()
    subject = h.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    to_list = [from_hdr] if from_hdr else []
    if reply_all and to_hdr:
        to_list += [t.strip() for t in to_hdr.split(",") if t.strip()]
    cc_list = [c.strip() for c in cc_hdr.split(",") if c.strip()] if cc_hdr else None

    if dry_run:
        return {
            "dry_run": True,
            "to": to_list,
            "subject": subject,
            "body_preview": body[:500],
            "in_reply_to_thread": original["threadId"],
            "status": "NOT SENT — dry_run=True",
        }

    raw, _ = _build_raw(
        to=to_list,
        subject=subject,
        body=body,
        cc=cc_list,
        in_reply_to=in_reply_to,
        references=references,
    )
    sent = (
        svc.users()
        .messages()
        .send(userId="me", body={"raw": raw, "threadId": original["threadId"]})
        .execute()
    )
    _audit("gmail_reply", f"account={account or 'default'} reply_to={message_id}")
    return {"id": sent["id"], "thread_id": sent["threadId"], "status": "sent"}


def labels_list(account: str | None = None) -> list[dict]:
    svc = service("gmail", "v1", account=account)
    resp = svc.users().labels().list(userId="me").execute()
    return [
        {"id": l["id"], "name": l["name"], "type": l["type"]}
        for l in resp.get("labels", [])
    ]


def label_apply(
    message_ids: list[str],
    add: list[str] | None = None,
    remove: list[str] | None = None,
    account: str | None = None,
) -> dict:
    svc = service("gmail", "v1", account=account)
    add_ids = _resolve_label_ids(svc, add) if add else []
    remove_ids = _resolve_label_ids(svc, remove) if remove else []
    svc.users().messages().batchModify(
        userId="me",
        body={"ids": message_ids, "addLabelIds": add_ids, "removeLabelIds": remove_ids},
    ).execute()
    return {"count": len(message_ids), "added": add or [], "removed": remove or []}


def archive(message_ids: list[str], account: str | None = None) -> dict:
    svc = service("gmail", "v1", account=account)
    svc.users().messages().batchModify(
        userId="me", body={"ids": message_ids, "removeLabelIds": ["INBOX"]}
    ).execute()
    return {"count": len(message_ids), "status": "archived"}


def trash(message_ids: list[str], account: str | None = None) -> dict:
    svc = service("gmail", "v1", account=account)
    for mid in message_ids:
        svc.users().messages().trash(userId="me", id=mid).execute()
    return {"count": len(message_ids), "status": "trashed"}
