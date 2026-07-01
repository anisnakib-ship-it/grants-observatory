"""One-time Google consent to let the app SEND mail via the Gmail API.

Run once:  python gmail_auth.py
It opens your browser, you click "Allow" for info@sunandsun.com.tr, and it writes
token.json (a refresh token). After that, the app sends without any password.
Re-run only if you revoke access or delete token.json.
"""
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

import config

sys.stdout.reconfigure(encoding="utf-8")


def main():
    if not os.path.exists(config.GMAIL_CLIENT_SECRET_FILE):
        print("ERROR: client_secret.json not found at", config.GMAIL_CLIENT_SECRET_FILE)
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(
        config.GMAIL_CLIENT_SECRET_FILE, config.GMAIL_SCOPES
    )
    print("Opening your browser for Google consent (sign in as the sender account)...")
    creds = flow.run_local_server(port=0, prompt="consent")
    with open(config.GMAIL_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print("SUCCESS: authorized. Token saved to", config.GMAIL_TOKEN_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
