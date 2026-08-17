# Setup — Google Workspace MCP

Two ways in:

- **Your own client (solo)** — you create your own Google Cloud project and OAuth app. **This is the path for anyone using this MCP on their own — no one needs to hand you anything.** Follow §0–§8 below. ~45–60 minutes once; every account you add after takes ~90 seconds.
- **Shared client (team / cohort)** — *only* if your program or admin already created the OAuth app and handed you a `client_secret.json`. You skip Google Cloud Console entirely: jump down to [Shared-client mode](#shared-client-mode-the-2-minute-path). ~2 minutes.

## 0. What you need (solo path)

- A Google account that can create Google Cloud projects (personal gmail works fine; no Workspace required to create the project).
- macOS (the MCP stores tokens in the Apple Keychain).

## 1. Create a Google Cloud project

1. Open https://console.cloud.google.com/projectcreate
2. Project name: `google-workspace-mcp` (or whatever). Location: No organization.
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
5. Publishing status: **Testing** is fine for a small, fixed group you'll list as test users — but note refresh tokens for external users expire **7 days** after issuance in Testing mode (you'll re-run `gws_account_add` weekly). If that's not what you want, **Publish app** (Audience → Publish app) instead: no test-user list, no 7-day expiry, works for any Google account, reversible via "Back to testing". Verification is only required to remove the "unverified" warning or exceed ~100 total unverified-app users — not to get non-expiring tokens.

## 4. Create an OAuth client ID

https://console.cloud.google.com/apis/credentials

1. Click **Create Credentials → OAuth client ID**.
2. Application type: **Desktop app**.
3. Name: `google-workspace-mcp-desktop`.
4. Click **Create**. A dialog shows the client ID + secret — copy **both** now, Google no longer offers a JSON download or a way to view the secret again later (if you lose it, click **Add secret** on the client's detail page to mint a new one).

## 5. Hand the client to the server

Two ways. **Env vars are the shorter path and need no file at all** — the same shape [microsoft-365-mcp](https://github.com/adelaidasofia/microsoft-365-mcp) uses for `M365_CLIENT_ID`.

**Option A — env vars (recommended).** Register the server with both values inline; nothing lands on disk:

```bash
claude mcp add google-workspace -s user \
  -e GWS_CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com \
  -e GWS_CLIENT_SECRET=YOUR_CLIENT_SECRET \
  -- /path/to/google-workspace-mcp/.venv/bin/python /path/to/google-workspace-mcp/server.py
```

`GWS_CLIENT_ID` and `GWS_CLIENT_SECRET` must be set **together**. Setting one alone is a hard error rather than a silent fall-back to the file, so a typo surfaces immediately instead of authorizing against the wrong client.

**Option B — credential file.** Create `client_secret.json` next to `server.py` (env vars, when set, win over it):

```json
{
  "installed": {
    "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
    "client_secret": "YOUR_CLIENT_SECRET",
    "redirect_uris": ["http://localhost"],
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token"
  }
}
```

Must be named exactly `client_secret.json`. The `.gitignore` keeps it out of any repo.

For a Desktop OAuth client the `client_secret` is a public-client identifier, not a confidential user secret: it cannot be kept secret on an end user's machine and Google treats it that way. Either option carries the same exposure.

## 6. Authorize each real mailbox

In Claude Code (in this vault), run:

```
/mcp
```

Confirm `google-workspace` appears. Then, in a message:

> Call gws_account_add

A browser window opens. Sign in with the first account (e.g. `you@yourcompany.com`), grant all requested scopes, wait for the "authentication flow complete" page, come back to Claude.

Repeat for each real mailbox you want connected:

```
Call gws_account_add again
```

Sign in with the next account.

**Which accounts to OAuth**: only the mailboxes where mail actually lands. Aliases do not need their own OAuth.

| Address type | OAuth it? |
|---|---|
| Real mailbox (Workspace or Gmail) | Yes |
| Alias / routing address that forwards to a real mailbox | **No** — OAuth the real mailbox instead. Use the alias as a Send-As identity in Gmail settings. |
| Shared mailbox you don't own / control | **No** — only OAuth if you have explicit full access. |

## 6b. Upgrading from v1 → v2 (re-OAuth required)

v2 adds Drive + Docs + Sheets. Each account that was authorized under v1 has
an older, narrower scope grant in Google's records — it must be re-consented
so Google issues a refresh token that covers the new scopes.

For each already-authorized mailbox:

1. Go to https://myaccount.google.com/permissions, find **Google Workspace MCP**
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
export GWS_DEFAULT_ACCOUNT="you@yourcompany.com"
```

Every `gmail_*` and `cal_*` tool takes an optional `account` param that overrides this.

## 8. Verify

```
Call gws_account_list
```

Should return `{"accounts": ["you@yourcompany.com", "you@gmail.com"], "default": "you@yourcompany.com", "count": 2}`.

Then:

```
Call gmail_search with query "is:unread" and limit 5
```

Should return up to 5 compact message summaries.

---

## Shared-client mode (the 2-minute path)

Your team or program already created one OAuth app and will send you its `client_secret.json`. You do **not** touch Google Cloud Console. Three steps:

**What you need**

- The `client_secret.json` your program/admin sends you (over the private channel they specify — a group chat or DM, never a public link).
- macOS (tokens live in the Apple Keychain by default).

**1. Drop the file in place**

```bash
mkdir -p ~/.claude/google-workspace-mcp
mv ~/Downloads/client_secret*.json ~/.claude/google-workspace-mcp/client_secret.json
```

Must be named exactly `client_secret.json` (same location §5 uses). The `.gitignore` keeps it out of any repo.

**2. Authorize your own account**

In Claude Code, run `/mcp` and confirm `google-workspace` is listed. Then, in a message:

> Call gws_account_add

A browser opens. Sign in with **your** Google account and grant every scope.

**3. Clear the "Google hasn't verified this app" screen**

The shared app runs in Testing mode, so Google shows an "unverified app" warning on first sign-in. This is expected — it is your program's own app, not a third party.

- **Corporate Google account:** the warning disappears once your IT admin marks the app's **Client ID** as *Trusted* — that is the one pre-workshop request IT needs, and the program sends you the exact Client ID to hand them. If IT just approved it, give it ~15 minutes to propagate, then retry.
- **Personal Gmail:** click **Advanced → Go to _(app name)_ (unsafe)** and continue. "Unverified" here only means the app is in Testing mode; you are authorizing your own account into your program's own app.

Done — go to [§7](#7-set-a-default-account-optional) and [§8](#8-verify) above to set a default and confirm it works. You never needed §1–§6.

> **"Access blocked: … has not completed verification"** with no Advanced link → the app is still in **Testing** mode and your email is not on its test-user list. Ask your program/admin to either add you, or move the app's publishing status to **In production** (Google Cloud Console → Google Auth Platform → Audience → **Publish app**) — that drops the test-user list entirely, works for any Google account, and removes the 7-day refresh-token expiry Testing mode carries. Confirmed working: a non-test-user account can complete consent and use every scope, including Gmail and Drive, on an unverified production app (only the "Advanced → Continue" click changes, not the outcome). Reversible via "Back to testing" if you'd rather not.

**Why sharing one `client_secret.json` is safe.** For a Desktop OAuth client the `client_secret` is a public-client identifier, not a confidential user secret — it cannot be kept secret on an end-user's machine, and Google treats it that way. Every member authorizes only their own Google account and gets their own refresh token stored locally; the shared client never grants cross-account access. Keep the file to your team's private channel regardless.

### Running the shared client for a team (admin)

One person creates the app once, then everyone else uses shared-client mode above:

1. Do §1–§4 above (create project, enable APIs, consent screen, Desktop client).
2. For a small, fixed group, **Testing** mode works: in §3 step 4 (**Test users**), add every member's email (up to 100). Members who are not listed get "Access blocked".
3. For an open or growing group (a public repo, a cohort with late signups, strangers you don't want to track by email), skip the test-user list — **publish the app** instead (Google Auth Platform → Audience → **Publish app**). Any Google account can then authorize with no roster to maintain and no 100-user cap on who's authorized (Google does cap unverified apps around 100 *total* grantees — verify if you expect to exceed that). The one-time "Google hasn't verified this app" click-through is unchanged either way.
4. Distribute the credential file (see §4/§5 — Google removed client-secret download; you may need to reconstruct the JSON) to members over a private channel, and send the **Client ID** alongside it — corporate members forward that Client ID to their IT to mark *Trusted*. IT-Trust matters independent of Testing vs. production: it's what gets your app past a locked-down org's third-party-app policy, not what removes the unverified warning.

## Troubleshooting

**"No refresh token for X"** — you authorized the account but Google didn't return a refresh token. Go to https://myaccount.google.com/permissions, revoke "Google Workspace MCP", then re-run `gws_account_add`. The `prompt=consent` flag forces a fresh token on re-auth.

**"Access blocked: google-workspace-mcp has not completed verification"** — the email you're signing in with is not on the test-users list in step 3.4. Add it and retry.

**"Invalid scope" on OAuth** — the scopes listed in step 3.3 don't match what `accounts.py` requests. Re-check the consent screen scopes.

**Keychain password prompts every tool call** — macOS anchors an "Always Allow" grant to a stable code signature. On an ad-hoc-signed Python (e.g. a `uv`-managed interpreter — `codesign -dv` shows `Signature=adhoc`, no Team ID) the grant can't persist, so keychain reads re-prompt. Credential caching already cuts this to at most one prompt per account per server start (instead of one per call).

**Definitive fix — skip the Keychain.** Set `GWS_TOKEN_FILE`, or just create `tokens.json` next to `accounts.py`, to store tokens in a chmod-600 JSON file instead of the OS keyring. A file has no per-app ACL or partition-list machinery, so no process ever prompts. Migrate existing tokens once:

```bash
cd ~/.claude/google-workspace-mcp   # your install dir
python3 - <<'PY'
import json, subprocess, pathlib
SVC = "google-workspace-mcp"
out = {}
for e in json.loads(pathlib.Path("accounts_index.json").read_text()):
    tok = subprocess.run(["security","find-generic-password","-s",SVC,"-a",e,"-w"],
                         capture_output=True, text=True).stdout.strip()
    if tok:
        out[e] = tok
p = pathlib.Path("tokens.json"); p.write_text(json.dumps(out, indent=2)); p.chmod(0o600)
print(f"migrated {len(out)} token(s)")
PY
# then remove the now-unused keychain items (optional, stops residual prompts)
for e in $(python3 -c 'import json;print(" ".join(json.load(open("accounts_index.json"))))'); do
  security delete-generic-password -s google-workspace-mcp -a "$e" >/dev/null 2>&1
done
```

`tokens.json` is gitignored; keep it chmod 600 (readable only by your user — same practical exposure as an allow-all keychain item, minus the prompts).

**Keychain-stays alternative** — re-store a token allow-all: `security add-generic-password -s google-workspace-mcp -a you@example.com -w "$TOKEN" -A`. Note `-A` sets the app ACL but *not* the partition list, so on some macOS versions an ad-hoc binary can still prompt on first access per process — the file backend avoids that entirely. The Keychain Access GUI route ("allow `python3` / `fastmcp`") only sticks for a stably-signed Python.

**Sending from an alias fails with 400** — the alias isn't configured in Gmail's "Send mail as" settings for the authenticated mailbox. Open https://mail.google.com/mail/u/0/#settings/accounts and add it, or use `gmail_sendas_list` to see what's currently allowed.
