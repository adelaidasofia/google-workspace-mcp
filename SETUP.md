# Setup — Google Workspace MCP

One-time setup: ~45-60 minutes. Do it once, every account you add after takes ~90 seconds.

## 0. What you need

- A Google account that can create Google Cloud projects (personal gmail works fine; no Workspace required to create the project).
- macOS (the MCP stores tokens in the Apple Keychain).

## 1. Create a Google Cloud project

1. Open https://console.cloud.google.com/projectcreate
2. Project name: `onde-workspace-mcp` (or whatever). Location: No organization.
3. Click **Create**. Wait ~30 seconds for the project to spin up.
4. Make sure the new project is selected in the top-left dropdown.

## 2. Enable the APIs

Open each link, confirm the right project is selected, click **Enable**:

- Gmail API: https://console.cloud.google.com/apis/library/gmail.googleapis.com
- Google Calendar API: https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
- Google Drive API: https://console.cloud.google.com/apis/library/drive.googleapis.com
- Google Docs API: https://console.cloud.google.com/apis/library/docs.googleapis.com
- Google Sheets API: https://console.cloud.google.com/apis/library/sheets.googleapis.com

## 3. OAuth consent screen

https://console.cloud.google.com/apis/credentials/consent

1. User type: **External**. (Internal only works with paid Workspace orgs.)
2. App name: `Google Workspace MCP`. User support email: your email. Developer email: same.
3. **Scopes** — click **Add or Remove Scopes**, add:
   - `.../auth/gmail.modify`
   - `.../auth/gmail.send`
   - `.../auth/gmail.settings.basic`
   - `.../auth/calendar`
   - `.../auth/drive`
   - `.../auth/documents`
   - `.../auth/spreadsheets`
   - `.../auth/userinfo.email`
   - `openid`
4. **Test users** — add every email you plan to OAuth:
   - you@yourcompany.com
   - you@gmail.com
   - (any other real mailboxes you want to authorize)
5. Publishing status: stays **Testing**. That is fine — test users can use it indefinitely. Only requires verification if you ship to >100 users.

## 4. Create an OAuth client ID

https://console.cloud.google.com/apis/credentials

1. Click **Create Credentials → OAuth client ID**.
2. Application type: **Desktop app**.
3. Name: `onde-workspace-mcp-desktop`.
4. Click **Create**. A dialog shows the client ID + secret.
5. Click **Download JSON**. Save the file — this is what the MCP reads.

## 5. Drop the credential file in place

```bash
mv ~/Downloads/client_secret_*.json ~/.claude/google-workspace-mcp/client_secret.json
```

Must be named exactly `client_secret.json`. The `.gitignore` keeps it out of any repo.

## 6. Authorize each real mailbox

In Claude Code (in this vault), run:

```
/mcp
```

Confirm `google-workspace` appears. Then, in a message:

> Call gws_account_add

A browser window opens. Sign in with the first account (e.g. `adelaida@planwithonde.com`), grant all requested scopes, wait for the "authentication flow complete" page, come back to Claude.

Repeat for each real mailbox you want connected:

```
Call gws_account_add again
```

Sign in with the next account.

**Which accounts to OAuth**: only the mailboxes where mail actually lands. Aliases do not need their own OAuth. For your setup:

| Address | Type | OAuth it? |
|---|---|---|
| adelaida@planwithonde.com | Real Workspace mailbox | Yes |
| adelaida@diazroa.com | Real personal mailbox | Yes |
| tech@onde-event.com | Alias / routing rule | **No** — it routes to a real mailbox; OAuth that real one instead. Use it as a Send-As identity. |
| sergio@planwithonde.com | Alias to Sergio's account | **No** — only OAuth if you have full access to Sergio's real mailbox. |

## 6b. Upgrading from v1 → v2 (re-OAuth required)

v2 adds Drive + Docs + Sheets. Each account that was authorized under v1 has
an older, narrower scope grant in Google's records — it must be re-consented
so Google issues a refresh token that covers the new scopes.

For each already-authorized mailbox:

1. Go to https://myaccount.google.com/permissions, find **Onde Workspace MCP**
   (or whatever you named the OAuth app), click **Remove Access**. This forces
   Google to show a fresh consent screen on next OAuth.
2. In Claude Code, call `gws_account_add` and sign in again as that mailbox.
   The consent screen now lists Drive, Docs, Sheets in addition to Gmail and
   Calendar. Approve all.
3. Repeat for every account in `gws_account_list`.

(Alternatively, GCP's OAuth consent screen editor sometimes preserves refresh
tokens if you *add* scopes without removing any — but in practice the safer
path is to revoke and re-consent. Google's token endpoint will not expand a
refresh token's scope silently.)

## 7. Set a default account (optional)

The first authorized account becomes default automatically. To override, add to `~/.zshrc`:

```bash
export GWS_DEFAULT_ACCOUNT="adelaida@planwithonde.com"
```

Every `gmail_*` and `cal_*` tool takes an optional `account` param that overrides this.

## 8. Verify

```
Call gws_account_list
```

Should return `{"accounts": ["adelaida@planwithonde.com", "adelaida@diazroa.com"], "default": "adelaida@planwithonde.com", "count": 2}`.

Then:

```
Call gmail_search with query "is:unread" and limit 5
```

Should return up to 5 compact message summaries.

## Troubleshooting

**"No refresh token for X"** — you authorized the account but Google didn't return a refresh token. Go to https://myaccount.google.com/permissions, revoke "Onde Workspace MCP", then re-run `gws_account_add`. The `prompt=consent` flag forces a fresh token on re-auth.

**"Access blocked: onde-workspace-mcp has not completed verification"** — the email you're signing in with is not on the test-users list in step 3.4. Add it and retry.

**"Invalid scope" on OAuth** — the scopes listed in step 3.3 don't match what `accounts.py` requests. Re-check the consent screen scopes.

**Keychain password prompts every tool call** — macOS is treating the stored secret as "read-only to the calling app." Open Keychain Access, search for `onde-google-workspace-mcp`, right-click the entry, **Access Control**, allow `python3` / `fastmcp`. Or allow "all applications" (less strict).

**Sending from an alias fails with 400** — the alias isn't configured in Gmail's "Send mail as" settings for the authenticated mailbox. Open https://mail.google.com/mail/u/0/#settings/accounts and add it, or use `gmail_sendas_list` to see what's currently allowed.
