# Email Inbox Triage Agent

A minimal Python script that connects to your real email inbox and uses **Claude's tool-use API** as an autonomous agent to classify, summarize, and draft replies for your emails — no ML models, no databases, no frameworks.

---

## What this demonstrates

This project showcases the **agentic loop** pattern — the core building block of AI agents in production. Instead of Python hardcoding every step, Claude is given a set of tools and decides what to call, in what order, and when to stop.

### How the agentic loop works

```
┌─────────────────────────────────────────────────────────────────┐
│  Python fetches emails from IMAP                                │
│                                                                 │
│  For each email:                                                │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  1. Python sends the email to Claude with 3 tools         │  │
│  │                                                           │  │
│  │  2. Claude calls classify_email()                         │  │
│  │     Python executes it, returns result to Claude          │  │
│  │                                                           │  │
│  │  3. Claude calls generate_summary(category=...)           │  │
│  │     Python executes it, returns result to Claude          │  │
│  │                                                           │  │
│  │  4. If urgent: Claude calls draft_reply()                 │  │
│  │     Python saves the draft to your Drafts folder via IMAP │  │
│  │                                                           │  │
│  │  5. Claude signals stop_reason="end_turn" → loop exits    │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Python prints the triage report                                │
└─────────────────────────────────────────────────────────────────┘
```

The key insight: **Claude decides which tools to call and in what order.** Python's job is to dispatch the calls and feed results back. This "while loop checking `stop_reason`" pattern is identical to how Claude agents work in production systems.

### Why this is different from prompt engineering

A standard LLM call: Python sends a prompt → Claude responds with text → done.

An agentic loop: Python sends a prompt + tools → Claude calls a tool → Python executes it → Python returns result → Claude calls another tool → ... → Claude stops.

Claude can inspect the result of each tool call before deciding what to do next. This enables multi-step reasoning that wouldn't fit in a single prompt.

---

## Setup

### 1. Prerequisites

- Python 3.8+
- An Anthropic API key — get one at [console.anthropic.com](https://console.anthropic.com)
- An email account with IMAP access

### 2. Get your email credentials

**Gmail** (recommended):
1. Enable 2-Step Verification at [myaccount.google.com/security](https://myaccount.google.com/security)
2. Go to Security → 2-Step Verification → **App passwords**
3. Create a new app password for "Mail" — you'll get a 16-character password
4. Use that as `IMAP_PASS` below (spaces are fine to include or omit)

> Gmail blocks regular account passwords over IMAP. You must use an App Password.

**Other providers** (Outlook, Fastmail, iCloud, etc.): use your regular password, or an app-specific password if 2FA is on. See [IMAP server list](#imap-server-settings) below.

### 3. Configure your `.env` file

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```env
ANTHROPIC_API_KEY=sk-ant-...

IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=you@gmail.com
IMAP_PASS=your-app-password
```

**Required variables:**

| Variable | Description | Example |
|---|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | `sk-ant-...` |
| `IMAP_HOST` | Your provider's IMAP hostname | `imap.gmail.com` |
| `IMAP_PORT` | IMAP SSL port | `993` |
| `IMAP_USER` | Your full email address | `you@gmail.com` |
| `IMAP_PASS` | Your password or app password | `abcd efgh ijkl mnop` |

**Optional variables:**

| Variable | Description | Default |
|---|---|---|
| `DRAFTS_FOLDER` | Override Drafts folder name if auto-detection fails | Auto-detected |
| `CLAUDE_MODEL` | Claude model to use | `claude-haiku-4-5-20251001` |

### 4. Run

```bash
python3 email_agent.py
```

That's it. The script auto-installs `anthropic` and `python-dotenv` on first run if they're not already present.

The agent will:
1. Connect to your inbox over IMAP
2. Fetch all **unread** emails (falls back to the 25 most recent if no unread)
3. Run each email through Claude's agentic loop (classify → summarize → draft if urgent)
4. Print a color-coded triage report to the terminal
5. Save draft replies for urgent emails directly to your Drafts folder

---

## Expected output

```
Connecting to IMAP...
Connected as you@gmail.com
Fetching 6 email(s)...

[1/6] URGENT: Server is down
  → Claude calling classify_email()
  → Claude calling generate_summary()
  → Claude calling draft_reply()
  ↳ Draft reply saved to Drafts folder

[2/6] Weekly standup notes
  → Claude calling classify_email()
  → Claude calling generate_summary()

...

📬 EMAIL TRIAGE AGENT REPORT
=================================================================
[URGENT    ] boss@company.com               | URGENT: Server is down
  ↳ Draft reply saved to Drafts folder
[WORK      ] team@company.com               | Weekly standup notes
[NEWSLETTER] news@substack.com              | 5 things to know this week
[SPAM      ] promo@deals.com                | You've been selected!
[PERSONAL  ] friend@gmail.com               | Coffee this week?

SUMMARY: 1 urgent, 1 work, 1 newsletter, 1 personal, 1 spam
```

---

## Project structure

```
mingo-mail/
  .gitignore
  email_agent/
    email_agent.py    # the entire agent in one file
    .env.example      # copy this to .env and fill in your credentials
    .env              # your credentials — never committed
    requirements.txt  # anthropic, python-dotenv
    README.md         # this file
```

---

## Tool reference

Three tools are defined for Claude to call:

| Tool | When Claude calls it | What Python does |
|---|---|---|
| `classify_email(sender, subject, body)` | First, for every email | Acknowledges; Claude's category is captured from the args |
| `generate_summary(category, ...)` | Second, for every email | Acknowledges; Python reads `category` from the args |
| `draft_reply(sender, subject, body)` | Only for urgent emails | Builds the draft; Python saves it via IMAP APPEND |

Claude's classification decision is extracted from the `category` argument it passes to `generate_summary` — that's where the model's reasoning becomes observable in Python.

---

## IMAP server settings

| Provider | IMAP Host | Port |
|---|---|---|
| Gmail | `imap.gmail.com` | `993` |
| Outlook / Office 365 | `outlook.office365.com` | `993` |
| iCloud | `imap.mail.me.com` | `993` |
| Fastmail | `imap.fastmail.com` | `993` |
| Yahoo | `imap.mail.yahoo.com` | `993` |

---

## Troubleshooting

**`IMAP_USER and IMAP_PASS must be set`** — Your `.env` file is missing or not in the same directory as `email_agent.py`. Make sure you ran `cp .env.example .env` and filled it in.

**Gmail login failure** — You must use an App Password. Regular Gmail passwords are rejected over IMAP when 2FA is enabled.

**`Could not find Drafts folder`** — Set `DRAFTS_FOLDER` in your `.env`. Use `[Gmail]/Drafts` for Gmail, `Drafts` for most other providers.

**`pip install` fails on first run** — Your system Python may be managed (e.g. Homebrew on macOS). Run manually: `pip3 install --user anthropic python-dotenv` then retry.

**No emails processed** — You may have no unread mail. The agent falls back to the 25 most recent — if your inbox is empty, there's nothing to process.
