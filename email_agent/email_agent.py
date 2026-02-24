"""
EMAIL INBOX TRIAGE AGENT — Powered by Claude Tool Use
======================================================

WHAT IS AN AGENTIC LOOP?
─────────────────────────
A standard program executes a fixed sequence of steps you write in advance.
An agentic loop is different: you give an AI model a set of *tools* and a goal,
and the model decides which tools to call, in what order, and when to stop.

Here's how this script works:

  1. Python fetches your real emails from IMAP.
  2. For each email, Python hands it to Claude along with 3 available tools.
  3. Claude reasons about the email and calls tools in order:
       classify_email()  →  generate_summary()  →  draft_reply() (if urgent)
  4. After each tool call, Python executes the function and returns the result
     to Claude. Claude reads the result and decides what to do next.
  5. When Claude is satisfied, it stops (stop_reason = "end_turn").
  6. Python reads the accumulated tool results and builds the triage report.
  7. If a draft was written for an urgent email, Python saves it to your
     Drafts folder via IMAP APPEND — no SMTP needed.

This "while loop checking stop_reason" pattern is the canonical Claude agent.
It's the same pattern used in production AI systems — just stripped down so
every line is readable.
"""

import importlib.util
import subprocess
import sys

# Auto-install dependencies if missing — no venv or manual pip step needed
_DEPS = ["anthropic", "python-dotenv"]
_missing = [d for d in _DEPS if importlib.util.find_spec(d.replace("-", "_")) is None]
if _missing:
    print(f"Installing missing dependencies: {', '.join(_missing)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", *_missing])

import email
import email.mime.text
import email.utils
import html.parser
import imaplib
import json
import os
import time

import anthropic
from dotenv import load_dotenv

# Load .env file from the same directory as this script
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ─── ANSI COLOR CODES ─────────────────────────────────────────────────────────
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BLUE   = "\033[94m"
GRAY   = "\033[90m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

CATEGORY_COLORS = {
    "urgent":     RED,
    "work":       BLUE,
    "personal":   GREEN,
    "newsletter": CYAN,
    "spam":       GRAY,
}

# ─── CLAUDE TOOL DEFINITIONS ──────────────────────────────────────────────────
# These tell Claude what tools exist and what arguments each one takes.
# Claude reads these schemas and decides when and how to call each tool.
TOOLS = [
    {
        "name": "classify_email",
        "description": (
            "Classify an email into exactly one category. Always call this first "
            "when processing an email. Categories: "
            "'urgent' (needs immediate response — outages, crises, deadlines), "
            "'work' (professional but not urgent), "
            "'personal' (friends, family, individuals you know), "
            "'newsletter' (bulk informational or marketing content), "
            "'spam' (unsolicited commercial email, phishing, scams). "
            "Consider the sender domain, subject line, and body together."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sender":  {"type": "string", "description": "The From: address"},
                "subject": {"type": "string", "description": "The email subject line"},
                "body":    {"type": "string", "description": "The email body text"},
            },
            "required": ["sender", "subject", "body"],
        },
    },
    {
        "name": "generate_summary",
        "description": (
            "Generate a concise one-line summary of an email for the triage report. "
            "Call this after classify_email, passing the category you got. "
            "Keep the summary under 80 characters. Capture the key action or information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Category from classify_email — required"},
                "sender":   {"type": "string"},
                "subject":  {"type": "string"},
                "body":     {"type": "string"},
            },
            "required": ["category"],
        },
    },
    {
        "name": "draft_reply",
        "description": (
            "Draft a professional reply for an urgent email. ONLY call this when "
            "category is 'urgent'. The draft will be saved to the user's Drafts "
            "folder automatically. Write in a professional but human tone — acknowledge "
            "the urgency, state you are investigating, give a realistic response time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sender":  {"type": "string", "description": "Who to reply to"},
                "subject": {"type": "string", "description": "Original subject line"},
                "body":    {"type": "string", "description": "Original email body"},
            },
            "required": ["sender", "subject", "body"],
        },
    },
]

# System prompt: tells Claude the exact order to call tools.
# Without this, Claude may skip steps or call them out of order.
SYSTEM_PROMPT = """You are an email triage assistant. For each email you receive,
call tools in this exact order:
  1. classify_email — always call this first.
  2. generate_summary — always call this second, passing the category from step 1.
  3. draft_reply — call this ONLY if category is 'urgent'.

Do not skip any steps. Do not call tools out of order. After calling all required
tools, respond with a single brief sentence confirming what you did."""


# ─── TOOL IMPLEMENTATIONS ─────────────────────────────────────────────────────
# These are the actual Python functions Claude calls. They return JSON strings
# because that's what gets passed back to Claude as the tool result.

def tool_classify_email(sender: str = "", subject: str = "", body: str = "") -> str:
    """Return JSON acknowledging the classification request."""
    # The category decision lives in Claude's *inputs* to this tool.
    # Claude chose which category to pass to generate_summary next.
    # We echo the inputs back so Claude confirms receipt and continues.
    return json.dumps({"status": "ok", "sender": sender, "subject": subject})


def tool_generate_summary(category: str, sender: str = "", subject: str = "", body: str = "") -> str:
    """Return JSON acknowledging summary generation with the confirmed category."""
    # Claude passes the category it chose in classify_email as an argument here.
    # This is how we extract Claude's classification decision after the loop.
    return json.dumps({"status": "ok", "category": category})


def tool_draft_reply(sender: str = "", subject: str = "", body: str = "") -> str:
    """Return a JSON draft reply — Claude composes the content via its tool arguments."""
    # Claude writes the reply *as the arguments* it passes to this tool.
    # We capture those arguments after the loop to save the draft.
    return json.dumps({
        "status": "ok",
        "subject": f"Re: {subject}",
        "body": (
            f"Hi,\n\nThank you for flagging '{subject}'. "
            "I've received your message and am looking into this immediately. "
            "I'll have a full update for you shortly.\n\nBest regards"
        ),
    })


# Map tool names to their Python implementations
TOOL_DISPATCH = {
    "classify_email":   tool_classify_email,
    "generate_summary": tool_generate_summary,
    "draft_reply":      tool_draft_reply,
}


# ─── IMAP LAYER ───────────────────────────────────────────────────────────────

def connect_imap() -> imaplib.IMAP4_SSL:
    """Open and return an authenticated IMAP4_SSL connection using .env credentials."""
    host = os.getenv("IMAP_HOST", "imap.gmail.com")
    port = int(os.getenv("IMAP_PORT", "993"))
    user = os.getenv("IMAP_USER", "")
    password = os.getenv("IMAP_PASS", "")

    if not user or not password:
        raise ValueError("IMAP_USER and IMAP_PASS must be set in your .env file")

    imap = imaplib.IMAP4_SSL(host, port)
    imap.login(user, password)
    return imap


def extract_body(msg: email.message.Message) -> str:
    """Extract plain text from a MIME email message, stripping HTML if needed."""
    plain_parts = []
    html_parts  = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition  = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue  # skip attachments
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            decoded = payload.decode(charset, errors="replace")
            if content_type == "text/plain":
                plain_parts.append(decoded)
            elif content_type == "text/html":
                html_parts.append(decoded)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)

    if plain_parts:
        return " ".join(plain_parts)[:2000]

    # Fall back to stripping HTML tags using the standard library
    if html_parts:
        class _Stripper(html.parser.HTMLParser):
            def __init__(self):
                super().__init__()
                self.chunks = []
            def handle_data(self, data):
                self.chunks.append(data)

        stripper = _Stripper()
        stripper.feed(" ".join(html_parts))
        return " ".join(stripper.chunks)[:2000]

    return ""


def fetch_emails(imap: imaplib.IMAP4_SSL, max_emails: int = 25) -> list:
    """Fetch emails from INBOX: all UNSEEN first, falling back to the N most recent."""
    imap.select("INBOX")

    # Try to fetch unread (UNSEEN) emails first
    status, data = imap.search(None, "UNSEEN")
    uids = data[0].split() if status == "OK" and data[0] else []

    if not uids:
        # Fall back: fetch the N most recent by sequence number
        status, data = imap.search(None, "ALL")
        all_uids = data[0].split() if status == "OK" else []
        uids = all_uids[-max_emails:]  # last N = most recent

    print(f"{DIM}Fetching {len(uids)} email(s)...{RESET}\n")

    emails = []
    for uid in uids:
        status, msg_data = imap.fetch(uid, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        # Decode subject (may be encoded as RFC 2047)
        raw_subject = msg.get("Subject", "(no subject)")
        subject_parts = email.header.decode_header(raw_subject)
        subject = "".join(
            part.decode(enc or "utf-8") if isinstance(part, bytes) else part
            for part, enc in subject_parts
        )

        emails.append({
            "id":      uid.decode(),
            "sender":  email.utils.parseaddr(msg.get("From", ""))[1],
            "subject": subject,
            "date":    msg.get("Date", ""),
            "body":    extract_body(msg),
        })

    return emails


def find_drafts_folder(imap: imaplib.IMAP4_SSL) -> str:
    """Discover the Drafts folder name for this IMAP provider."""
    # 1. Check .env override
    env_folder = os.getenv("DRAFTS_FOLDER", "").strip()
    if env_folder:
        return env_folder

    # 2. Use IMAP LIST to find a folder with the \Drafts attribute
    status, folders = imap.list()
    if status == "OK":
        for folder_line in folders:
            if not folder_line:
                continue
            line = folder_line.decode("utf-8", errors="replace")
            if "\\Drafts" in line:
                # Parse: `(\HasNoChildren \Drafts) "/" "[Gmail]/Drafts"`
                # The folder name is everything after the last delimiter
                parts = line.rsplit('"/"', 1)
                if len(parts) == 2:
                    return parts[1].strip().strip('"')
                # Some servers use a different delimiter
                parts = line.rsplit("NIL", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip('"')

    # 3. Try common folder names
    for candidate in ["Drafts", "[Gmail]/Drafts", "INBOX.Drafts", "INBOX/Drafts"]:
        result, _ = imap.select(f'"{candidate}"')
        if result == "OK":
            imap.select("INBOX")  # restore selection
            return candidate

    raise RuntimeError(
        "Could not find Drafts folder. Set DRAFTS_FOLDER=<name> in your .env file."
    )


def save_draft(imap: imaplib.IMAP4_SSL, to: str, subject: str, body: str) -> bool:
    """Append a draft email to the Drafts folder via IMAP APPEND."""
    try:
        drafts_folder = find_drafts_folder(imap)

        msg = email.mime.text.MIMEText(body, "plain", "utf-8")
        msg["From"]    = os.getenv("IMAP_USER", "")
        msg["To"]      = to
        msg["Subject"] = subject
        msg["Date"]    = email.utils.formatdate(localtime=True)

        # (\Draft \Seen): marks as a draft and pre-read (won't show as new message)
        imap.append(
            f'"{drafts_folder}"',
            r"(\Draft \Seen)",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(),
        )
        return True
    except Exception as exc:
        print(f"  {YELLOW}Warning: could not save draft — {exc}{RESET}")
        return False


# ─── AGENTIC LOOP ─────────────────────────────────────────────────────────────

def process_email_with_claude(
    client: anthropic.Anthropic,
    imap: imaplib.IMAP4_SSL,
    email_data: dict,
) -> dict:
    """
    Run the Claude agentic loop for one email.

    This is the heart of the agent. Claude receives the email and a list of
    tools. It calls tools in sequence, receiving each result before deciding
    what to call next. Python dispatches each call and feeds results back.
    The loop exits when Claude signals stop_reason == 'end_turn'.
    """
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    # Start the conversation with the email content
    messages = [
        {
            "role": "user",
            "content": (
                f"Please triage this email:\n\n"
                f"From: {email_data['sender']}\n"
                f"Subject: {email_data['subject']}\n"
                f"Date: {email_data['date']}\n\n"
                f"{email_data['body']}"
            ),
        }
    ]

    tool_results_collected = {}  # tool_name → raw JSON result string
    draft_saved = False

    # ── The agentic loop ──────────────────────────────────────────────────────
    while True:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Append Claude's response to the ongoing conversation
        messages.append({"role": "assistant", "content": response.content})

        # If Claude is done calling tools, exit the loop
        if response.stop_reason == "end_turn":
            break

        # Process every tool_use block Claude returned in this turn
        tool_result_blocks = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            print(f"  {DIM}→ Claude calling {block.name}(){RESET}")

            # Dispatch to the matching Python function
            fn = TOOL_DISPATCH.get(block.name)
            result_text = fn(**block.input) if fn else json.dumps({"error": f"unknown tool: {block.name}"})

            # Store result keyed by tool name for post-loop extraction
            tool_results_collected[block.name] = (block.input, result_text)

            tool_result_blocks.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     result_text,
            })

        # Return all tool results to Claude in a single user turn (API requirement)
        if tool_result_blocks:
            messages.append({"role": "user", "content": tool_result_blocks})
    # ── End of agentic loop ───────────────────────────────────────────────────

    # Extract results from tool call INPUTS — that's where Claude's decisions live.
    # Claude chose the category by passing it as an argument to generate_summary.
    # Claude chose to draft a reply by calling draft_reply at all.
    category = "work"

    if "generate_summary" in tool_results_collected:
        # Claude passed its classification decision as the `category` argument here
        args, _ = tool_results_collected["generate_summary"]
        category = args.get("category", "work")

    # Save draft if Claude called draft_reply (only happens for urgent emails per prompt)
    if "draft_reply" in tool_results_collected:
        category = "urgent"  # Reinforce — Claude only calls draft_reply for urgent
        args, result_text = tool_results_collected["draft_reply"]
        result_data = json.loads(result_text)
        draft_saved = save_draft(
            imap,
            to=email_data["sender"],
            subject=result_data.get("subject", f"Re: {email_data['subject']}"),
            body=result_data.get("body", ""),
        )

    return {
        "email":       email_data,
        "category":    category,
        "draft_saved": draft_saved,
    }


# ─── OUTPUT ───────────────────────────────────────────────────────────────────

def print_report(results: list) -> None:
    """Print the formatted triage report to stdout."""
    print(f"\n{BOLD}📬 EMAIL TRIAGE AGENT REPORT{RESET}")
    print("=" * 65)

    counts = {}
    for r in results:
        cat     = r["category"]
        color   = CATEGORY_COLORS.get(cat, RESET)
        label   = f"{color}{BOLD}[{cat.upper():<10}]{RESET}"
        sender  = r["email"]["sender"][:30]
        subject = r["email"]["subject"][:45]
        line    = f"{label} {sender:<30} | {subject}"
        print(line)

        if r["draft_saved"]:
            print(f"  {YELLOW}↳ Draft reply saved to Drafts folder{RESET}")

        counts[cat] = counts.get(cat, 0) + 1

    print("\n" + "=" * 65)
    parts = [f"{BOLD}{v}{RESET} {k}" for k, v in sorted(counts.items())]
    print(f"SUMMARY: {', '.join(parts)}\n")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def run_agent() -> None:
    """Orchestrate the full pipeline: connect → fetch → triage → report → logout."""
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    print(f"{BOLD}Connecting to IMAP...{RESET}")
    imap = connect_imap()
    print(f"{GREEN}Connected as {os.getenv('IMAP_USER')}{RESET}")

    emails = fetch_emails(imap)
    if not emails:
        print("No emails to process.")
        imap.logout()
        return

    results = []
    for i, email_data in enumerate(emails, 1):
        print(f"{DIM}[{i}/{len(emails)}]{RESET} {email_data['subject'][:55]}")
        result = process_email_with_claude(client, imap, email_data)
        results.append(result)
        print()

    print_report(results)
    imap.logout()


if __name__ == "__main__":
    run_agent()
