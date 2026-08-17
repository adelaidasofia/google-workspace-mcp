#!/usr/bin/env bash
#
# One-command install for google-workspace-mcp.
#
#   cd ~ && git clone https://github.com/adelaidasofia/google-workspace-mcp.git
#   bash ~/google-workspace-mcp/install.sh
#
# Builds an isolated venv next to this script, installs the dependencies, asks
# for the OAuth client, and registers the server with Claude Code. Nothing is
# written outside this directory and Claude Code's own config. Safe to re-run.
#
# Targets macOS's stock bash 3.2, so no 4.x-only syntax.

set -euo pipefail

SERVER_NAME="google-workspace"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$(printf '\033[1m'); DIM=$(printf '\033[2m'); R=$(printf '\033[0m')
  GRN=$(printf '\033[32m'); RED=$(printf '\033[31m'); YLW=$(printf '\033[33m')
else
  B=""; DIM=""; R=""; GRN=""; RED=""; YLW=""
fi

step() { printf '\n%s==>%s %s%s%s\n' "$GRN" "$R" "$B" "$1" "$R"; }
ok()   { printf '    %s+%s %s\n' "$GRN" "$R" "$1"; }
warn() { printf '    %s!%s %s\n' "$YLW" "$R" "$1"; }
die()  { printf '\n%sX  %s%s\n\n' "$RED" "$1" "$R" >&2; exit 1; }

# Prompts must read from the terminal, not stdin: stdin may be the script
# itself when this is piped, and then every read would silently consume the
# script's own remaining lines instead of waiting for the person.
if [ -r /dev/tty ]; then TTY=/dev/tty; else TTY=/dev/stdin; fi

ask() { # ask <prompt> -> echoes the entered value
  local prompt="$1" value=""
  while [ -z "$value" ]; do
    printf '\n    %s%s%s\n    > ' "$B" "$prompt" "$R" > /dev/tty
    IFS= read -r value < "$TTY" || die "No input received. Run the script from a terminal."
    # Trim spaces people pick up when copying out of a browser.
    value="$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "$value" ] && printf '    %sThat was empty. Try again.%s\n' "$YLW" "$R" > /dev/tty
  done
  printf '%s' "$value"
}

confirm_yes() { # confirm_yes <question> -> 0 if yes
  local reply=""
  printf '\n    %s%s%s [y/N] ' "$B" "$1" "$R" > /dev/tty
  IFS= read -r reply < "$TTY" || reply=""
  case "$reply" in [yY]*) return 0 ;; *) return 1 ;; esac
}

printf '\n%s  Google Workspace MCP  %s\n' "$B" "$R"
printf '%s  Gmail, Calendar, Drive, Docs and Sheets, connected to Claude Code.%s\n' "$DIM" "$R"

# ---------------------------------------------------------------- 1. tooling

step "Checking what you already have"

command -v git >/dev/null 2>&1 || die \
"git is not installed.
   Run  xcode-select --install  , let it finish, then run this script again."

command -v python3 >/dev/null 2>&1 || die \
"python3 is not installed.
   Run  xcode-select --install  , let it finish, then run this script again."

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3,10) else 0)' 2>/dev/null || echo 0)
[ "$PY_OK" = "1" ] || die \
"python3 is version $(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo unknown), but 3.10 or newer is needed.
   Install a newer Python from python.org, then run this script again."
ok "python3 $(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"

command -v claude >/dev/null 2>&1 || die \
"Claude Code is not installed, or its 'claude' command is not on your PATH.
   Install Claude Code first, quit and reopen Terminal, then run this again."
ok "claude"

[ -f "$SCRIPT_DIR/server.py" ] || die \
"This script is not sitting next to server.py, so the clone looks incomplete.
   Delete the folder and clone it again."

# --------------------------------------------------------------- 2. install

step "Installing the connector (about a minute)"

# A venv is not optional here: Homebrew and python.org interpreters are marked
# externally-managed (PEP 668), so a plain `pip install` refuses outright.
if [ ! -x "$VENV_PY" ]; then
  python3 -m venv "$VENV_DIR" || die \
"Could not create the virtual environment in $VENV_DIR.
   If that folder half-exists, delete it and run this script again."
fi
ok "isolated environment ready"

"$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$VENV_PY" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt" || die \
"Could not install the dependencies.
   Check your internet connection and run this script again."
ok "dependencies installed"

# Import the real module. A dependency that resolves at install time but not at
# import time (a missing transitive, a version clash) otherwise shows up much
# later as a connector that is simply absent from /mcp, with nothing to read.
LOAD_ERR="$(cd "$SCRIPT_DIR" && "$VENV_PY" -c 'import server' 2>&1)" || die \
"The connector installed but did not load. Please send this to the cohort channel:

$LOAD_ERR"
ok "connector loads"

# ------------------------------------------------------------ 3. credentials

step "Your Google OAuth client"

# Two ways in. A person running this by hand gets prompted. An agent (Claude
# Code) driving it passes both values in the environment and is never asked a
# question, because a prompt it cannot answer would hang the whole install.
CLIENT_ID="${GWS_CLIENT_ID:-}"
CLIENT_SECRET="${GWS_CLIENT_SECRET:-}"
NONINTERACTIVE=0
if [ -n "$CLIENT_ID" ] && [ -n "$CLIENT_SECRET" ]; then
  NONINTERACTIVE=1
  ok "using the client passed in the environment"
elif [ -n "$CLIENT_ID" ] || [ -n "$CLIENT_SECRET" ]; then
  die \
"Only one of GWS_CLIENT_ID / GWS_CLIENT_SECRET was set.
   Set both, or neither and this script will ask you for them."
else
  cat <<EOF

    You need two values from the Google Cloud Console, from the screen
    shown when you created your Desktop OAuth client:

      $B Client ID $R      ends with .apps.googleusercontent.com
      $B Client secret $R  usually starts with GOCSPX-

    Do not have them yet? Press Ctrl-C, finish the console steps in the
    guide, then run this script again. Nothing done so far is lost.
EOF
  CLIENT_ID="$(ask 'Paste your Client ID, then press Return')"
  CLIENT_SECRET="$(ask 'Paste your Client secret, then press Return')"
fi

# Shape checks. A value can be present and still be the wrong thing: pasting
# the ID into both boxes is the single most common way this goes wrong, and it
# fails much later with an opaque OAuth error instead of here.
[ "$CLIENT_SECRET" != "$CLIENT_ID" ] || die \
"The Client ID and Client secret you gave are identical.
   The ID ends with .apps.googleusercontent.com; the secret usually starts with GOCSPX-."

case "$CLIENT_SECRET" in
  *.apps.googleusercontent.com) die \
"The Client secret you gave is actually a Client ID (it ends with .apps.googleusercontent.com).
   The secret usually starts with GOCSPX-." ;;
esac

odd_shape() { # odd_shape <label> <hint>
  warn "$1"
  warn "$2"
  if [ "$NONINTERACTIVE" = "1" ]; then
    warn "Continuing, because both values were supplied deliberately."
  else
    confirm_yes "Use it anyway?" || die "Nothing was changed. Run the script again with the right value."
  fi
}

case "$CLIENT_ID" in
  *.apps.googleusercontent.com) ok "client ID looks right" ;;
  *) odd_shape "That client ID does not end with .apps.googleusercontent.com, which every Google client ID does." \
               "It is easy to paste the project name here by mistake." ;;
esac

case "$CLIENT_SECRET" in
  GOCSPX-*) ok "client secret looks right" ;;
  *) odd_shape "That client secret does not start with GOCSPX-, which most Google client secrets do." \
               "Older clients can differ, so this may be fine." ;;
esac

# -------------------------------------------------------------- 4. register

step "Connecting it to Claude Code"

# Re-running should heal a bad value rather than fail on "already exists". The
# remove is unconditional and its failure ignored, so this does not depend on
# parsing `claude mcp list` output, which is a display format, not a contract.
claude mcp remove "$SERVER_NAME" -s user >/dev/null 2>&1 || true

claude mcp add "$SERVER_NAME" -s user \
  -e "GWS_CLIENT_ID=$CLIENT_ID" \
  -e "GWS_CLIENT_SECRET=$CLIENT_SECRET" \
  -- "$VENV_PY" "$SCRIPT_DIR/server.py" >/dev/null || die \
"Could not register the connector with Claude Code.
   Run this to see the error:
     claude mcp add $SERVER_NAME -s user -e GWS_CLIENT_ID=... -e GWS_CLIENT_SECRET=... -- $VENV_PY $SCRIPT_DIR/server.py"

ok "registered as \"$SERVER_NAME\""

cat <<EOF

$GRN  Installed.$R

  ${B}Two things left, both inside Claude Code:${R}

    1. Quit Claude Code completely and open it again.
       It only picks up new connectors when it starts.

    2. Send it this message:

           Call gws_account_add

       Your browser opens. Sign in as yourself and approve the access.
       Seeing "Google hasn't verified this app"? That is expected, it is
       your own app. Click Advanced, then the link to continue.

  Then try:  Call gmail_search with query "is:unread" and limit 5

EOF
