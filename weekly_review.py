"""Interactive weekly review coach.

Run an end-of-week conversation with Claude, grounded in priorities.json via
system_prompt.build_system_prompt(). Type "done" to end the conversation and
get a structured summary back.

Usage:
    python weekly_review.py
"""

import json
import os
import sys

from dotenv import load_dotenv
from anthropic import Anthropic

from system_prompt import build_system_prompt
from memory import save_review, get_all_reviews

sys.stdout.reconfigure(encoding="utf-8")

MODEL = "claude-opus-5"
MAX_TOKENS = 16000

# Server-side refusal fallback: if the model declines a turn, the API re-runs
# that same turn on a fallback model inside the same call. Drop `betas=` and
# `fallbacks=` below if you'd rather not use the beta endpoint.
BETAS = ["server-side-fallback-2026-07-01"]

EXIT_WORD = "done"

FIRST_QUESTION = "How did this week go against your top active priorities?"

# Seeds the history so FIRST_QUESTION is a real assistant turn Claude can see,
# without spending an API call to produce a question we already know we want.
KICKOFF = (
    "Start my weekly review. Ask me one question at a time, beginning with how "
    "the week went against my top active priorities."
)

FINAL_INSTRUCTION = (
    "That's the end of the review conversation. Now summarize the whole review "
    "using the weekly_review_summary tool. Base every field only on what I "
    "actually reported in this conversation, not on what you'd hope to see. "
    "Apply the drift and pivot rules from your instructions when deciding "
    "whether drift_detected is true."
)

SUMMARY_TOOL = {
    "name": "weekly_review_summary",
    "description": (
        "Record the structured outcome of this week's review. Call this exactly "
        "once, after the review conversation is complete."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "top_needle_movers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Up to 3 items (fewer is fine, including none) that actually "
                    "moved a top active priority forward this week. One short "
                    "phrase each. Do not pad the list to reach 3."
                ),
            },
            "drift_detected": {
                "type": "boolean",
                "description": (
                    "True if this week matches the drift pattern from my "
                    "priorities: no real movement on top priorities, or time "
                    "spent on busywork instead."
                ),
            },
            "drift_summary": {
                "type": "string",
                "description": (
                    "If drift_detected is true, a direct 1-3 sentence account of "
                    "the specific drift, separating what I reported from your "
                    "inference. Empty string if drift_detected is false."
                ),
            },
            "reflection_question": {
                "type": "string",
                "description": (
                    "One question for me to sit with this coming week, drawn from "
                    "the sharpest unresolved thing in this review."
                ),
            },
        },
        "required": [
            "top_needle_movers",
            "drift_detected",
            "drift_summary",
            "reflection_question",
        ],
        "additionalProperties": False,
    },
}


def _reply_text(response):
    """Join the text blocks of a response (thinking/tool blocks are skipped)."""
    return "\n\n".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


def _warn_on_odd_stop(response):
    if response.stop_reason == "refusal":
        details = response.stop_details
        category = getattr(details, "category", None) if details else None
        print(f"\n[The model declined this turn (category: {category}).]")
    elif response.stop_reason == "max_tokens":
        print("\n[Response hit max_tokens and may be cut off.]")


def run_weekly_review():
    """Run the interactive review, then return the structured summary dict."""
    load_dotenv()
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system = build_system_prompt()

    # Full message history — resent on every turn, since the API is stateless.
    messages = [
        {"role": "user", "content": KICKOFF},
        {"role": "assistant", "content": FIRST_QUESTION},
    ]

    # Plain-text transcript of just the visible Q&A, for storage — separate
    # from `messages`, which carries the full content-block structure the API
    # needs (thinking blocks, etc.).
    transcript_lines = [f"CLAUDE: {FIRST_QUESTION}"]

    print("=" * 70)
    print("WEEKLY REVIEW")
    print(f'Answer each question, then type "{EXIT_WORD}" when you want the summary.')
    print("=" * 70)
    print(f"\nCLAUDE: {FIRST_QUESTION}")

    while True:
        try:
            user_input = input("\nYOU > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nReview cancelled — no summary generated.")
            return None

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        transcript_lines.append(f"YOU: {user_input}")

        if user_input.lower() == EXIT_WORD:
            break

        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=BETAS,
            fallbacks="default",
            system=system,
            messages=messages,
        )
        _warn_on_odd_stop(response)

        # Append the full content list, not just the text, so thinking blocks
        # are echoed back unchanged on the next turn.
        messages.append({"role": "assistant", "content": response.content})
        reply = _reply_text(response)
        transcript_lines.append(f"CLAUDE: {reply}")
        print(f"\nCLAUDE: {reply}")

    transcript_text = "\n\n".join(transcript_lines)
    summary = _request_summary(client, system, messages)

    print("\n" + "=" * 70)
    print("STRUCTURED SUMMARY (weekly_review_summary tool output)")
    print("=" * 70)
    print(json.dumps(summary, indent=2))

    movers = summary.get("top_needle_movers", [])
    if len(movers) > 3:
        print(f"\n[Note: model returned {len(movers)} needle movers; expected up to 3.]")

    save_review(summary, transcript_text)
    print(f"\n[Saved to weekly_reviews table in {os.path.join(os.path.dirname(__file__) or '.', 'life_os.db')}]")

    _print_table_contents()

    return summary


def _print_table_contents():
    """Print every row currently in the weekly_reviews table."""
    rows = get_all_reviews()
    print("\n" + "=" * 70)
    print(f"WEEKLY_REVIEWS TABLE ({len(rows)} row{'s' if len(rows) != 1 else ''})")
    print("=" * 70)
    for row in rows:
        print(json.dumps(row, indent=2))
        print("-" * 70)


def _request_summary(client, system, messages):
    """One final call that forces the weekly_review_summary tool."""
    messages.append({"role": "user", "content": FINAL_INSTRUCTION})

    print("\n[Generating structured summary...]")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        betas=BETAS,
        fallbacks="default",
        system=system,
        messages=messages,
        tools=[SUMMARY_TOOL],
        tool_choice={"type": "tool", "name": SUMMARY_TOOL["name"]},
    )
    _warn_on_odd_stop(response)

    for block in response.content:
        if block.type == "tool_use" and block.name == SUMMARY_TOOL["name"]:
            return block.input

    raise RuntimeError(
        f"Expected a {SUMMARY_TOOL['name']} tool call; got stop_reason="
        f"{response.stop_reason!r} instead."
    )


if __name__ == "__main__":
    run_weekly_review()
