"""Set the dashboard password.

    python set_password.py

Only a PBKDF2 hash is written, to settings.json (git-ignored) — the plaintext is
never stored anywhere. getpass keeps it off the screen and out of shell history,
which is why there is no --password flag: an argument would land in ~/.bash_history
and in the process list.

Restart the app afterwards so it reloads settings.json:
    pm2 restart grants-monitor
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

import config

# The dashboard is internet-facing, so a short password is a real risk rather
# than a style preference. Long beats complex: a passphrase is fine.
MIN_LENGTH = 12


def main():
    print("Set the Grants Observatory dashboard password.")
    print(f"Minimum {MIN_LENGTH} characters — a passphrase of a few words is ideal.\n")

    try:
        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Repeat password: ")
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; nothing changed.")
        return 1

    if password != confirm:
        print("Passwords do not match; nothing changed.")
        return 1
    if len(password) < MIN_LENGTH:
        print(f"Too short ({len(password)} chars, need {MIN_LENGTH}); nothing changed.")
        return 1

    config.save_settings({
        "auth_password_hash": generate_password_hash(password),
        "auth_enabled": True,
    })
    print(f"\nPassword set. Hash written to {config.BASE_DIR}\\settings.json")
    print("Restart the app to load it:  pm2 restart grants-monitor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
