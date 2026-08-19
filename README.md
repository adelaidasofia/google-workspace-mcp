# google-workspace-mcp


<!-- mycelium-badges:start -->

<p>
  <a href="https://github.com/adelaidasofia/google-workspace-mcp/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/adelaidasofia/google-workspace-mcp?color=blue"></a>
  <a href="https://github.com/adelaidasofia/google-workspace-mcp/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/adelaidasofia/google-workspace-mcp?color=eab308"></a>
  <a href="https://github.com/adelaidasofia/google-workspace-mcp/commits/main"><img alt="Last commit" src="https://img.shields.io/github/last-commit/adelaidasofia/google-workspace-mcp"></a>
  <a href="https://github.com/adelaidasofia/google-workspace-mcp/issues"><img alt="Open issues" src="https://img.shields.io/github/issues/adelaidasofia/google-workspace-mcp"></a>
  <a href="https://pypi.org/project/adelaidasofia-google-workspace-mcp/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/adelaidasofia-google-workspace-mcp?color=blue&label=pypi"></a>
  <a href="https://pypi.org/project/adelaidasofia-google-workspace-mcp/"><img alt="PyPI downloads" src="https://img.shields.io/pypi/dm/adelaidasofia-google-workspace-mcp?color=blue&label=downloads"></a>
  <a href="https://myceliumai.co"><img alt="Built by Mycelium AI" src="https://img.shields.io/badge/built_by-Mycelium_AI-15B89A"></a>
</p>

<!-- mycelium-badges:end -->

Multi-account, token-efficient MCP for **Gmail + Calendar + Drive + Docs + Sheets**.
Built because the official Claude connector supports one account and returns full
message/file bodies by default.

## Why this exists

- **Multi-account**: OAuth multiple mailboxes (work + personal + co-founder). Every
  tool takes an `account` email; aliases are handled via Send-As identities.
- **Token-efficient**: Search/list returns compact shapes (`{id, from, subject,
  snippet, ...}` for mail, `{id, name, mime, modified, size, ...}` for Drive).
  Bodies and file content are opt-in.
- **Keyring-backed**: Refresh tokens live in the OS credential store — Keychain
  on macOS, Credential Manager on Windows, Secret Service on Linux — not in
  plaintext files. No tokens in the vault, no tokens in any repo.
- **macOS, Windows and Linux**: one installer per platform, same connector.

## Tools (v2, 63 tools)

### Account management (3)
- `gws_account_add` — browser OAuth flow, adds a new authorized mailbox
- `gws_account_list` — list authorized accounts + default
- `gws_account_remove` — remove local credential (doesn't revoke Google-side)

### Gmail (12)
- `gmail_search` — search with Gmail operators. Compact response.
- `gmail_read` — read one message or full thread. Bodies opt-in.
- `gmail_send` — send mail, optional `from_alias` for Send-As identities
- `gmail_draft` — create a draft
- `gmail_reply` — reply (preserves thread + headers), optional reply_all
- `gmail_labels_list` — list all labels
- `gmail_label_apply` — batch add/remove labels
- `gmail_archive` — batch archive (remove INBOX)
- `gmail_trash` — batch move to trash
- `gmail_sendas_list` — list Send-As identities on this mailbox
- `gmail_attachments_list` — list a message's attachments (filename, mime type, size)
- `gmail_attachment_save` — download one attachment to a local file, path returned for direct reading

### Calendar (7)
- `cal_list_calendars` — list all calendars
- `cal_list_events` — list upcoming events (compact by default, `verbose=True` for full)
- `cal_create_event` — create event, optional Google Meet link
- `cal_update_event` — partial-update fields
- `cal_delete_event` — delete
- `cal_freebusy` — check busy windows for scheduling
- `cal_respond` — accept/decline/tentative

### Drive (18)
- `drive_search` — free-text or raw Drive q-syntax. Metadata-only response.
- `drive_read_file` — metadata by default; `include_content=True` for body
- `drive_list_folder` — direct children of a folder (`'root'` for My Drive)
- `drive_create_folder` — create a folder under an optional parent
- `drive_upload` — upload a local file, optional `convert_to_google`
- `drive_move` — change parent folder
- `drive_rename` — rename a file or folder
- `drive_share` — grant reader/commenter/writer/etc. access by email
- `drive_trash` — soft delete (recoverable)
- `drive_untrash` — restore from Trash
- `drive_permission_list` — list everyone with access to a file
- `drive_permission_update` — change a grantee's role
- `drive_permission_delete` — revoke a permission
- `drive_shared_drives_list` — list shared drives this account accesses
- `drive_comments_list` — list comments on any Drive file (Doc/Sheet/Slide/upload)
- `drive_comment_add` — add a comment, optional anchor
- `drive_comment_reply` — reply to a comment
- `drive_comment_resolve` — mark a comment resolved

### Docs (9)
- `docs_create` — new Doc, optional initial body and parent folder
- `docs_read` — flat text by default; `structured=True` for full Docs API tree
- `docs_append` — append text to end of body
- `docs_insert_at` — insert text at a specific index
- `docs_replace_text` — find-and-replace, returns count replaced
- `docs_export` — export to markdown / pdf / docx / rtf / plain
- `docs_suggestions_list` — list pending tracked-change suggestions
- `docs_suggestions_accept_all` — accept all suggestions (rewrites Doc)
- `docs_suggestions_reject_all` — reject all suggestions (rewrites Doc)

### Sheets (14)
- `sheets_create` — new workbook, optional parent folder
- `sheets_list_sheets` — list tabs with row/col dimensions
- `sheets_add_sheet` — add a new tab to an existing workbook
- `sheets_read_range` — read A1 range. `FORMULA` / `UNFORMATTED_VALUE` options.
- `sheets_write_range` — overwrite a range. `USER_ENTERED` parses formulas.
- `sheets_append` — append rows below existing data
- `sheets_clear_range` — clear values (formatting preserved)
- `sheets_batch_read` — multi-range read in one API call
- `sheets_batch_write` — multi-range write in one API call
- `sheets_named_ranges_list` — list named ranges in a workbook
- `sheets_named_range_add` — create a named range
- `sheets_named_range_delete` — delete a named range
- `sheets_conditional_format_add` — add a conditional formatting rule
- `sheets_data_validation_add` — set dropdown / number / email / URL validation

## Install

**Fastest path — one script, no manual wiring.**

macOS and Linux:

```bash
git clone https://github.com/adelaidasofia/google-workspace-mcp.git
bash google-workspace-mcp/install.sh
```

Windows (PowerShell):

```powershell
git clone https://github.com/adelaidasofia/google-workspace-mcp.git
powershell -ExecutionPolicy Bypass -File .\google-workspace-mcp\install.ps1
```

The installer creates an isolated venv, installs dependencies, takes your
Google OAuth client (either interactively or via `GWS_CLIENT_ID` /
`GWS_CLIENT_SECRET` in the environment), and registers the server with
Claude Code. Safe to re-run. You still need a Google OAuth client first —
see [SETUP.md](SETUP.md) for the ~45 min one-time GCP setup. Then run
`gws_account_add` from Claude Code to authorize your first mailbox.

Both need Python 3.10 or newer and will find one you already have.

- **macOS / Linux** try `python3` first, then `python3.14` down to
  `python3.10`, so a Homebrew Python still counts when `python3` resolves to
  macOS's older system one.
- **Windows** tries `py -3` (the Python launcher) first, then `python`,
  `python3` and the versioned names. Every candidate has to run and report its
  own version before it counts, which is what keeps the Microsoft Store
  placeholder in `%LOCALAPPDATA%\Microsoft\WindowsApps` from being picked: it
  is a real `python.exe` on `PATH` that only opens the Store.

If yours lives somewhere no search would guess (pyenv, conda, a private
prefix), name it:

```bash
GWS_PYTHON=/full/path/to/python3 bash google-workspace-mcp/install.sh
```

```powershell
$env:GWS_PYTHON = 'C:\full\path\to\python.exe'
powershell -ExecutionPolicy Bypass -File .\google-workspace-mcp\install.ps1
```

<details>
<summary>Windows notes</summary>

**`-ExecutionPolicy Bypass` is in the command on purpose.** Windows blocks
downloaded scripts by default; this applies the exception to this one run
without changing the machine's policy.

**Run it from PowerShell, not Git Bash.** `install.sh` detects Git Bash / MSYS
and stops there with a pointer to `install.ps1`, rather than registering a
POSIX venv path that a native Claude Code cannot launch. WSL is real Linux and
keeps using `install.sh`.

**Time zone.** The connector reads the machine's IANA zone so that "3pm" in a
`cal_*` call means 3pm where you are. Windows names its zones its own way
(`SA Western Standard Time`), so this goes through `tzlocal`, which the
installer pulls in. Set `GWS_TIME_ZONE=America/Bogota` to override.

</details>

<details>
<summary>Plugin marketplace install</summary>

Open Claude Code, paste:

    /plugin marketplace add adelaidasofia/google-workspace-mcp
    /plugin install google-workspace-mcp@google-workspace-mcp

You still need to complete the one-time GCP setup in [SETUP.md](SETUP.md)
(~45 min for v1, ~5 min incremental for v2 Drive/Docs/Sheets) so the
server has a `client_secret.json` to OAuth against. Run `gws_account_add`
from Claude Code to authorize your first mailbox.

**On Windows, run `install.ps1` afterwards.** The plugin's `.mcp.json` launches
the server with `python3`, and a plugin manifest has no way to say "except on
Windows, use this instead". On Windows `python3` is either absent or the
Microsoft Store placeholder, so the connector is registered and never starts.
`install.ps1` re-registers it against an absolute interpreter path, which is
unambiguous on every platform.

</details>

<details>
<summary>Legacy install (manual <code>.mcp.json</code> wiring)</summary>

See [SETUP.md](SETUP.md) for the one-time GCP setup (~45 min for v1, ~5 min
incremental to enable Drive/Docs/Sheets for v2).

After setup:
```bash
pip3 install --break-system-packages -r requirements.txt
```

### Register with Claude Code

Add to your project's `.mcp.json` (or `~/.claude.json` for global access):
```json
"google-workspace": {
  "type": "stdio",
  "command": "python3",
  "args": ["/path/to/google-workspace-mcp/server.py"]
}
```

On Windows, give the full path to a real interpreter rather than a bare name —
`python3` there is usually the Microsoft Store placeholder, which resolves fine
and never runs Python:
```json
"google-workspace": {
  "type": "stdio",
  "command": "C:\\Users\\you\\google-workspace-mcp\\.venv\\Scripts\\python.exe",
  "args": ["C:\\Users\\you\\google-workspace-mcp\\server.py"]
}
```

</details>

## Upgrading from v1 → v2

v2 adds Drive + Docs + Sheets scopes. **Each authorized account must re-OAuth
once** so Google grants the new scopes. See SETUP.md step 6b.

## Roadmap

- **v3**: Gmail filters, vacation responder, push notifications (Gmail Watch),
  Calendar ACL/delegation, Slides, Forms, Tasks
- **v4**: Batch requests across services, Drive revisions

## Related MCPs

Same author, same architecture pattern (FastMCP, draft+confirm on writes where applicable, vault auto-export, MIT):

- [slack-mcp](https://github.com/adelaidasofia/slack-mcp) — multi-workspace Slack
- [imessage-mcp](https://github.com/adelaidasofia/imessage-mcp) — macOS iMessage
- [whatsapp-mcp](https://github.com/adelaidasofia/whatsapp-mcp) — WhatsApp via whatsmeow
- [apollo-mcp](https://github.com/adelaidasofia/apollo-mcp) — Apollo.io CRM + sequences
- [substack-mcp](https://github.com/adelaidasofia/substack-mcp) — Substack writing + analytics
- [luma-mcp](https://github.com/adelaidasofia/luma-mcp) — lu.ma events
- [parse-mcp](https://github.com/adelaidasofia/parse-mcp) — markitdown / Docling / LlamaParse router
- [rescuetime-mcp](https://github.com/adelaidasofia/rescuetime-mcp) — RescueTime productivity data
- [graph-query-mcp](https://github.com/adelaidasofia/graph-query-mcp) — vault knowledge graph queries
- [graph-autotagger-mcp](https://github.com/adelaidasofia/graph-autotagger-mcp) — wikilink suggestions from the graph
- [investor-relations-mcp](https://github.com/adelaidasofia/investor-relations-mcp) — seed-raise pipeline tracker
- [vault-sync-mcp](https://github.com/adelaidasofia/vault-sync-mcp) — bidirectional vault sync


## Telemetry

This plugin sends a single anonymous install signal to `myceliumai.co` the first time it loads in a Claude Code session on a given machine.

**What is sent:**
- Plugin name (e.g. `slack-mcp`)
- Plugin version (e.g. `0.1.0`)

**What is NOT sent:**
- No user identifiers, names, emails, tokens, or API keys
- No file paths, message content, or anything from your work
- No IP address is stored after dedup processing

**Why:** Helps the maintainer know which plugins people actually install, so attention goes to the ones that get used.

**Opt out:** Set the environment variable `MYCELIUM_NO_PING=1` before launching Claude Code. The hook will skip the network call entirely. Already-pinged installs leave a sentinel at `~/.mycelium/onboarded-<plugin>` — delete it if you want to reset state.

## License

MIT

---

Built by [Mycelium AI](https://myceliumai.co). Full install or team version at [diazroa.com](https://diazroa.com).
