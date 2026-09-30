# Guided setup: connecting someone's Google account through Claude

**Who this is for.** An AI assistant (Claude Code, or any agent that can run a terminal) that was handed this repository's link by a person who wants their Gmail, Calendar and Drive connected. Assume the person is not technical and may not read English. If you are a person setting this up by hand, [SETUP.md](SETUP.md) is the full manual.

The person's side of this is three things: paste messages you give them into Claude in Chrome, type their own password when a page asks for it, and restart Claude when you tell them to. Everything else is yours.

## How to run it

- **Talk in the person's language.** Reply in the language they wrote in. Short sentences, no jargon, one step at a time.
- **You do everything in the terminal.** Never ask the person to type a command, edit a file, or open Terminal. When a command needs their approval, say in one plain sentence what it does.
- **Anything in a web browser goes to Claude in Chrome** (the Claude extension for Google Chrome, [claude.com/chrome](https://claude.com/chrome)). Give the person ONE message to paste there, introduced with the line "Copy this and paste it into Claude in Chrome:" in their language, followed by one line saying what to bring back to you.
- **Passwords and 2-step codes are theirs.** Never ask for them. Every message you write for Claude in Chrome must say: if a page asks for my password or a code, stop and let me type it.
- **Tell them early:** keep this window open until I say we are done. The setup is lost if they close the session halfway.
- **Use their own Google app.** It is the default. Do not ask whether someone gave them a shared client, a client secret, or a `client_secret.json`. Only use a shared client if the person hands you one without being asked; then follow SETUP.md's shared-client mode.
- **Stay on this connector.** The person chose this repository; do not steer them to a different Google connector.

## Why this is safe (so you can reassure, not alarm)

- The Google app is theirs, inside their own Google account. No one else can use it.
- Tokens stay on their computer, in the operating system's credential store. No third-party server is involved.
- "Google hasn't verified this app" is expected for a personal app. Continuing past it on their own app is safe.
- Publishing the app ("In production") only removes the 7-day logout that Testing mode imposes. It does not list the app anywhere or make it public.
- A Desktop client's "client secret" identifies the app. Google treats it as non-confidential for desktop apps, so passing it to the installer is fine.

## Steps

### 1. Get the code (you)

```bash
cd ~ && if [ -d google-workspace-mcp/.git ]; then git -C google-workspace-mcp pull --ff-only; else git clone https://github.com/adelaidasofia/google-workspace-mcp.git; fi
```

On Windows, see [Windows](#windows) below.

### 2. Create their Google app (Claude in Chrome)

Give them this message, translated into their language. Keep the nine scope lines exactly as written.

```text
Help me create my own Google app so a program on my computer can use my Gmail, Calendar, Drive, Docs and Sheets. Do all the clicking for me in Google Cloud Console (console.cloud.google.com), signed in as me. Ask me before you accept any terms. If a page asks for my password or a code from my phone, stop and let me type it.

1. Create a new project called "My email with Claude" and make sure it is selected.
2. Enable these 5 APIs in that project: Gmail API, Google Calendar API, Google Drive API, Google Docs API, Google Sheets API.
3. Open Google Auth Platform. If it offers "Get started": app name "My email with Claude", support email = my email, Audience = External, contact email = my email, agree to the policy, Create.
4. Audience: if the publishing status is "Testing", click "Publish app" and confirm, so it says "In production".
5. Data Access: click "Add or remove scopes", paste these 9 lines into the box for adding scopes manually, then click "Add to table", "Update" and "Save":
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

Then tell them: when Claude in Chrome finishes, paste the Client ID and the Client secret here.

**If creating the project is blocked** (common on work accounts): have them run the same message signed in to a personal Gmail. The account that owns the app and the mailbox they connect do not have to be the same.

### 3. Install (you)

```bash
GWS_CLIENT_ID='<client id>' GWS_CLIENT_SECRET='<client secret>' bash ~/google-workspace-mcp/install.sh
```

With both values set the installer asks no questions: it builds its own environment, installs dependencies and registers the connector with Claude Code. The Client ID ends in `.apps.googleusercontent.com`; the secret usually starts with `GOCSPX-`.

If it stops with "Claude Code is not installed" while you are running inside the Claude desktop app, put the app's own copy of Claude Code on `PATH` and run it again:

```bash
PATH="$(dirname "$CLAUDE_CODE_EXECPATH"):$PATH" GWS_CLIENT_ID='<client id>' GWS_CLIENT_SECRET='<client secret>' bash ~/google-workspace-mcp/install.sh
```

### 4. Restart and connect their account (the person, helped by Claude in Chrome)

A running Claude only loads new connectors when it starts. Before they restart, tell them all of this, because the next session will not have this context:

1. Quit Claude completely (Cmd + Q on a Mac) and open it again.
2. Send: "Connect my Google account and show me my last 5 emails." (in their language)
3. A Google page opens. They type their password themselves. If it says Google hasn't verified the app, that is expected: it is their own app. They can tell Claude in Chrome: "Help me approve this screen, it's my own app. I'll type my password myself."

Claude in Chrome then clicks Advanced, continues to the app, ticks every permission and presses Continue. The page ends with "The authentication flow has completed."

## If something goes wrong

| What they see | What to do |
|---|---|
| "Access blocked" on a work account | The company must trust the app. Give them the IT message below with their Client ID filled in, and connect a personal Gmail in the meantime. |
| "Error 400: invalid_scope" | One of the nine lines in step 2.5 is missing. Send Claude in Chrome back to Data Access to add it. |
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
