import os
import sys

from dotenv import load_dotenv
import resend

load_dotenv()

resend.api_key = os.environ["RESEND_API_KEY"]

if len(sys.argv) > 1:
    to_address = sys.argv[1]
else:
    to_address = input("Recipient email address: ").strip()

from_address = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")

try:
    resend.Emails.send({
        "from": from_address,
        "to": to_address,
        "subject": "Life OS Agent Test",
        "text": "This is a test email from your Life OS Agent.",
    })
    print("Success: test email sent.")
except Exception:
    print("Failure: could not send test email.")
