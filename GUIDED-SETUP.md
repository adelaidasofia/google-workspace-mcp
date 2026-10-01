# Guided setup with Claude Code and Claude in Chrome

This is the setup path for someone who wants Claude to connect their Gmail, Calendar and Drive for them. Claude Code runs the terminal steps. Claude in Chrome (the Claude extension for Google Chrome, [claude.com/chrome](https://claude.com/chrome)) does the browser steps. The person does three things: pastes the browser messages into Claude in Chrome, types their own password when a page asks for it, and restarts Claude when told. [SETUP.md](SETUP.md) is the full manual for doing it by hand.

## How the session goes

- In the person's language: short sentences, no jargon, one step at a time.
- Claude Code runs every terminal step. The person never types a command, edits a file or opens Terminal. When a command needs their approval, one plain sentence says what it does.
- Each browser task is one message for the person to paste into Claude in Chrome, introduced with "Copy this and paste it into Claude in Chrome:" and followed by one line saying what to bring back.
- Passwords and 2-step codes are always typed by the person and never shared. Every browser message says to stop at a password or code field and let the person type it.
- The person keeps the Claude Code window open until setup is finished. Closing it halfway loses the progress.
- The Google app is the person's own, in their own Google Cloud project. SETUP.md's shared-client mode is only for someone who was handed a client by their team.

## Security model

- The Google app is theirs, inside their own Google account. Once it is published, anyone who has its Client ID and secret can open a sign-in screen for it, so both stay private.
- Tokens stay on their computer, in the operating system's credential store. No third-party server is involved.
- "Google hasn't verified this app" is expected for a personal app. Continuing past it on their own app is safe.
- Publishing the app ("In production") removes the 7-day logout that Testing mode imposes. It does not list the app anywhere, but any Google account can then authorize it, after the "Google hasn't verified this app" click-through.
- A Desktop client's "client secret" identifies the app. Google treats it as non-confidential for desktop apps, so passing it to the installer is fine.

## Steps

### 1. Get the code (Claude Code)

```bash
cd ~ && if [ -d google-workspace-mcp/.git ]; then git -C google-workspace-mcp pull --ff-only; else git clone https://github.com/adelaidasofia/google-workspace-mcp.git; fi
```

On Windows, see [Windows](#windows) below.

### 2. Create their Google app (Claude in Chrome)

The person gets this message, translated into their language, with the scope lines kept exactly as written:

```text
Help me create my own Google app so a program on my computer can use my Gmail, Calendar, Drive, Docs and Sheets. Do all the clicking for me in Google Cloud Console (console.cloud.google.com), signed in as me. Ask me before you accept any terms. If a page asks for my password or a code from my phone, stop and let me type it.

1. Create a new project called "My email with Claude" and make sure it is selected.
2. Enable these 5 APIs in that project: Gmail API, Google Calendar API, Google Drive API, Google Docs API, Google Sheets API.
3. Open Google Auth Platform. If it offers "Get started": app name "My email with Claude", support email = my email, Audience = External, contact email = my email, agree to the policy, Create.
4. Audience: if the publishing status is "Testing", click "Publish app" and confirm, so it says "In production".
5. Data Access: click "Add or remove scopes", paste these lines into the box for adding scopes manually, then click "Add to table", "Update" and "Save":
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/gmail.settings.basic
https://www.googleapis.com/auth/calendar
https://www.googleapis.com/auth/drive
https://www.googleapis.com/auth/documents
https://www.googleapis.com/auth/spreadsheets
https://www.googleapis.com/auth/userinfo.email
openid
6. Clients: "Create client", application type "Desktop app", name "My email with Claude", Create.
7. A window shows my Client ID and Client secret. Do not close it. Show me both values so I can copy them. If you cannot show the secret, tell me it is on the screen so I copy it myself.
```

When Claude in Chrome finishes, the person pastes the Client ID and the Client secret back into Claude Code.

**If creating the project is blocked** (common on work accounts): the same message works signed in to a personal Gmail. The account that owns the app and the mailbox they connect do not have to be the same.

### 3. Install (Claude Code)

```bash
GWS_CLIENT_ID='<client id>' GWS_CLIENT_SECRET='<client secret>' bash ~/google-workspace-mcp/install.sh
```

With both values set the installer asks no questions: it builds its own environment, installs dependencies and registers the connector with Claude Code. The Client ID ends in `.apps.googleusercontent.com`; the secret usually starts with `GOCSPX-`.

Inside the Claude desktop app the installer finds the app's own copy of Claude Code by itself. If it still stops, with a message that names `CLAUDE_CODE_EXECPATH`, it could not confirm that copy. Put the app's own copy of Claude Code on `PATH` and run it again; the installer uses a `claude` on `PATH` without that check:

```bash
PATH="$(dirname "$CLAUDE_CODE_EXECPATH"):$PATH" GWS_CLIENT_ID='<client id>' GWS_CLIENT_SECRET='<client secret>' bash ~/google-workspace-mcp/install.sh
```

### 4. Restart and connect their account (the person, helped by Claude in Chrome)

A running Claude only loads new connectors when it starts. The next session will not have this context, so the person hears all of this before restarting:

1. Quit Claude completely (Cmd + Q on a Mac) and open it again.
2. Send: "Connect my Google account and show me my last 5 emails." (in their language)
3. A Google page opens. They type their password themselves. If it says Google hasn't verified the app, that is expected: it is their own app. They can tell Claude in Chrome: "Help me approve this screen, it's my own app. I'll type my password myself."

Claude in Chrome then clicks Advanced, continues to the app, ticks every permission and presses Continue. The page ends with "The authentication flow has completed."

## If something goes wrong

| What they see | What to do |
|---|---|
| "Access blocked" on a work account | The company must trust the app. The IT message below, with their Client ID filled in, asks for that. A personal Gmail can be connected in the meantime. |
| "Error 400: invalid_scope" | One of the scope lines in step 2.5 is missing. Send Claude in Chrome back to Data Access to add it. |
| "Error 400: redirect_uri_mismatch" | The client is not a "Desktop app". Create a Desktop app client and install again with its values. |
| Worked, then stopped about a week later | The app is still in Testing. Publish it (step 2.4), then run `gws_account_add` again. |
| macOS asks for the keychain password on every call | See SETUP.md, "Keychain password prompts every tool call". |

**IT message** (translate to their language and fill in the Client ID):

> Hello. I am connecting my work Google account to an AI assistant that runs on my own computer. Could you mark this app as Trusted in the Google Admin console (Security, then Access and data control, then API controls, then Manage app access)? Client ID: `<their Client ID>`. It is a desktop app: I sign in with my own account, access stays on my computer, and no outside company receives our email. It requests Gmail, Calendar, Drive, Docs and Sheets for my account only.

After IT approves, wait about 15 minutes and run `gws_account_add` again.

## Windows

Run the installer from PowerShell, not Git Bash:

```powershell
cd ~; git clone https://github.com/adelaidasofia/google-workspace-mcp.git
$env:GWS_CLIENT_ID = '<client id>'; $env:GWS_CLIENT_SECRET = '<client secret>'
powershell -ExecutionPolicy Bypass -File .\google-workspace-mcp\install.ps1
```

Everything else is the same.
