"""Private-input cloud sign-in helper shared by Patchbay and the terminal.

The GUI supplies credentials over stdin, never argv, environment, or log output.
Only account status crosses back to the caller; the password is not persisted.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys

from .cloud import CloudClient, CloudError


def _client():
    # Friendly sign-in always uses the shared secure store, even when an old
    # shell configuration still contains a legacy plaintext session-file path.
    return CloudClient(use_environment=False)


def main(argv=None, *, client_factory=_client):
    parser = argparse.ArgumentParser(prog="qc-mcp cloud")
    parser.add_argument("action", choices=("login", "status", "logout"))
    parser.add_argument("--json", action="store_true", dest="json_mode",
                        help="private stdin JSON input for login; JSON status output")
    args = parser.parse_args(argv)
    try:
        if args.action == "login":
            if args.json_mode:
                try:
                    # Bound input from the local UI. A password never enters argv.
                    raw = sys.stdin.read(65537)
                    if len(raw) > 65536:
                        raise ValueError
                    credentials = json.loads(raw)
                    if (not isinstance(credentials, dict)
                            or not isinstance(credentials.get("email"), str)
                            or not isinstance(credentials.get("password"), str)
                            or credentials.get("remember", True) is not True):
                        raise ValueError
                    email = credentials["email"].strip()
                    password = credentials["password"]
                except (ValueError, TypeError):
                    raise CloudError("Enter an email and password to sign in securely.") from None
            else:
                if not sys.stdin.isatty():
                    raise CloudError("Run qc-mcp cloud login in a terminal to enter your password privately.")
                email = input("Cortex Cloud email: ").strip()
                password = getpass.getpass("Password: ")
            if not email or not password:
                raise CloudError("Email and password are required.")
            client = client_factory()
            try:
                result = client.login(email, password, persist=True)
            finally:
                password = ""
        else:
            client = client_factory()
            result = client.status() if args.action == "status" else client.logout()
    except (CloudError, EOFError, KeyboardInterrupt):
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, CloudError) else "Sign-in cancelled."
        result = {"authenticated": False, "session_persisted": False, "error": message}

    if args.json_mode:
        print(json.dumps(result))
    elif result.get("error"):
        print(result["error"], file=sys.stderr)
    elif result.get("authenticated"):
        account = result.get("email") or "Cortex Cloud"
        print(f"Signed in as {account}. Works with Patchbay closed.")
    else:
        print("Signed out. Run qc-mcp cloud login to sign in.")
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
