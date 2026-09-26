"""AI developments digest, researched with Claude's server-side web search.

Asks Claude to search the web for recent AI developments relevant to my
goals, filter and tag them using a fixed evaluation framework, and write the
result as a plain-text email body. The digest is printed to the terminal and
then sent via Resend to NOTIFY_EMAIL (same recipient variable the scheduled
nudge-email workflow uses).

Usage:
    python ai_digest.py            # research, print, and send
    python ai_digest.py --dry-run  # research and print only; no email
"""

import argparse
import json
import os
import sys
from datetime import date

from dotenv import load_dotenv
from anthropic import Anthropic
import resend

from system_prompt import PRIORITIES_PATH

sys.stdout.reconfigure(encoding="utf-8")

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000

# Server-side refusal fallback, same as weekly_review.py.
BETAS = ["server-side-fallback-2026-07-01"]

# Server-side web search: Anthropic runs the searches, no client loop needed.
WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 15,
}

# If the server-side search loop hits its iteration limit, the response comes
# back with stop_reason "pause_turn" and we re-send to let it resume.
MAX_CONTINUATIONS = 5

SYSTEM_PROMPT_TEMPLATE = """You research recent AI developments and write a personal digest email for me. I'm pivoting my career into AI and am targeting AI Solutions Engineer / Growth Engineer roles. The job search is my actual priority.

CONTEXT ON MY PROJECTS (use this to judge relevance accurately; never overstate their state):
- PracFit PWA: a fitness coaching progressive web app, currently v0.3 in coach testing, not client-facing yet.
- PracFit studio: I am not currently investing in growing the studio beyond baseline duties; it is in maintenance mode. Do not justify an item's relevance by "grows the studio" or similar business growth for PracFit, UNLESS my active priorities below specifically list a PracFit-growth-related item, in which case that overrides this default. Improving the PracFit PWA itself is separate from growing the studio and stays in scope.
- Ark Training OS: PRD-only, build never started, and back-burnered indefinitely. It is not a near-term restart. Do not justify an item's relevance by a hypothetical Ark restart or future Ark build.

MY CURRENT ACTIVE PRIORITIES (from my priorities file):
{active_priorities}

WHAT TO SEARCH FOR (priorities):
1. AI agent development tools and models
2. AI developments relevant to a career pivot into AI Solutions Engineering: primarily health tech/fitness AI, but also AI safety/anti-misuse work (my long-term interest area), and broader AI industry developments worth being aware of in case they reveal a new area of interest I haven't considered yet
3. Anthropic-specific updates: new Claude models, API changes, MCP updates
4. Anything that could accelerate the PracFit PWA build
5. Anything shifting the competitive landscape for AI Solutions Engineer / Growth Engineer roles

HOW TO EVALUATE WHAT YOU FIND:
- Tag every item as exactly one of: Direct, Adjacent, Stretch, or Market awareness.
- Apply a 30-minute rule: if fully evaluating something would take longer than 30 minutes, just flag it for later rather than going deep.
- Skip pure hype with no substance.
- Treat unconfirmed reports and rumors as hype and skip them. If the only support for an item is language like "reportedly", "rumored", "anticipated", "contemplating", or "sources say", it is not news; leave it out.
- One story per item by default. You may group stories under one item only if they are substantively related in subject matter (the same product, the same underlying event, or the same specific problem). Stories that merely launched or were published on the same day, or only share a broad topic, are not related enough: give each its own item.
- A grouped item's "What it is:" field must name and briefly describe every story it covers. Never state or imply a count of stories higher than the number the "What it is:" field actually describes, anywhere in the item, including passing phrases like "one of five incidents" or "coverage of several such stories". If there are more related stories you aren't describing, either describe them or don't mention them.
- Freshness: today is {today}. Only include developments from roughly the past two weeks. Drop anything older, unless you explicitly mark it as background by starting its "What it is:" field with "Background (not current news):". Use web search to find developments; do not rely on memory for what is recent.

OUTPUT FORMAT:
Write the digest as a plain-text email body (no Markdown headings, tables, or bold markers; it will be sent as plain text). Do not include a subject line. Do not include any intro line such as "Here is this week's AI digest." The very first line of your output must be the first item's "Tool/Development:" line.

Every item uses exactly these seven field labels, in this order, spelled exactly like this:
Tool/Development:
Category:
Relevance:
What it is:
Why it matters:
Should I test it:
Distraction risk:

Follow this format exactly, with no deviation in field names or wording:

Tool/Development: <name>
Category: <category>
Relevance: <Direct | Adjacent | Stretch | Market awareness>
What it is: <1-2 sentences, plain language>
Why it matters: <tied specifically to the PWA, resume, LinkedIn, or job search>
Should I test it: <yes | no | later> - <one-sentence reason>
Distraction risk: <low | medium | high>

Separate items with a blank line. After the last item, end the email with exactly one sentence: the single most important thing from this digest and why it matters for my path.

Write the digest only in your final message, after you've finished searching. Don't include commentary about your search process in it."""


def build_system_prompt():
    """Fill the prompt template with today's date and my active priorities."""
    with open(PRIORITIES_PATH, encoding="utf-8") as f:
        active_priorities = json.load(f)["active_priorities"]
    return SYSTEM_PROMPT_TEMPLATE.format(
        today=date.today().isoformat(),
        active_priorities="\n".join(f"- {item}" for item in active_priorities),
    )


def _check_stop_reason(response):
    if response.stop_reason == "refusal":
        details = response.stop_details
        category = getattr(details, "category", None) if details else None
        raise RuntimeError(f"The model declined to write the digest (category: {category}).")
    if response.stop_reason == "max_tokens":
        print("[Response hit max_tokens and may be cut off.]")


def _digest_text(content_blocks):
    """Return the text written after the last tool block.

    Web search responses interleave text ("Let me search for...") with tool
    blocks; the digest itself is the text that follows the final search.
    Text blocks are joined without separators because citations split one
    passage of prose into several adjacent blocks.
    """
    last_tool_index = -1
    for i, block in enumerate(content_blocks):
        if block.type not in ("text", "thinking", "redacted_thinking"):
            last_tool_index = i
    return "".join(
        block.text
        for block in content_blocks[last_tool_index + 1:]
        if block.type == "text"
    ).strip()


def generate_digest():
    """Research recent AI developments and return the digest email body."""
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    today = date.today().isoformat()
    system = build_system_prompt()

    messages = [{
        "role": "user",
        "content": f"Today is {today}. Research and write this week's AI digest.",
    }]
    content_blocks = []

    for _ in range(MAX_CONTINUATIONS + 1):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=BETAS,
            fallbacks="default",
            system=system,
            messages=messages,
            tools=[WEB_SEARCH_TOOL],
        )
        _check_stop_reason(response)
        content_blocks.extend(response.content)

        if response.stop_reason != "pause_turn":
            break
        # Re-send with the paused turn appended; the server resumes on its own.
        messages.append({"role": "assistant", "content": response.content})
    else:
        raise RuntimeError(f"Web search still paused after {MAX_CONTINUATIONS} continuations.")

    digest = _digest_text(content_blocks)
    if not digest:
        raise RuntimeError("The model finished without writing a digest.")
    return digest


def send_digest(digest):
    """Email the digest via Resend to NOTIFY_EMAIL."""
    resend.api_key = os.environ["RESEND_API_KEY"]
    resend.Emails.send({
        "from": os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev"),
        "to": os.environ["NOTIFY_EMAIL"],
        "subject": f"AI Digest - {date.today():%b %d, %Y}",
        "text": digest,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Research and email an AI developments digest.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the digest but don't send it.",
    )
    args = parser.parse_args()

    load_dotenv()

    required = ["ANTHROPIC_API_KEY"]
    if not args.dry_run:
        required += ["RESEND_API_KEY", "NOTIFY_EMAIL"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        sys.exit(f"Missing environment variable(s): {', '.join(missing)}. Add them to .env.")

    print("[Researching recent AI developments - this can take a few minutes...]")
    digest = generate_digest()

    print("=" * 70)
    print("AI DIGEST")
    print("=" * 70)
    print(digest)
    print("=" * 70)

    if args.dry_run:
        print("[Dry run: email not sent.]")
        sys.exit(0)

    send_digest(digest)
    print(f"[Sent to {os.environ['NOTIFY_EMAIL']}.]")
