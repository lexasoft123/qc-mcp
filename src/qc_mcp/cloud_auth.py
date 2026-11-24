"""Private-input cloud sign-in helper shared by Patchbay and the terminal.

The GUI supplies credentials over stdin, never argv, environment, or log output.
Account status and selected capture metadata can be returned to the caller.
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


def _product_page(client, category, query, page):
    result = client.search(category, query, page=page, size=10)
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise CloudError("Cortex Cloud returned an invalid product search response")
    products = []
    for row in result["data"][:10]:
        if not isinstance(row, dict):
            continue
        product_id = row.get("productId")
        name = row.get("name")
        if not isinstance(product_id, str) or not isinstance(name, str):
            continue
        metadata = row.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        product = {"productId": product_id[:200], "name": name[:200]}
        for key in ("authorUsername", "description", "createdAt", "hash", "creatorType", "creatorVersion"):
            if isinstance(row.get(key), str):
                product[key] = row[key][:1500]
        for key in ("version", "instrumentType", "deviceType"):
            if isinstance(metadata.get(key), str) or type(metadata.get(key)) is int:
                product[key] = str(metadata[key])[:100]
        if type(metadata.get("gainType")) is int:
            product["gainType"] = metadata["gainType"]
        if type(row.get("downloads")) is int:
            product["downloads"] = row["downloads"]
        if isinstance(row.get("tags"), list):
            product["tags"] = [tag[:100] for tag in row["tags"][:20] if isinstance(tag, str)]
        products.append(product)
    pages = result.get("numberOfPages")
    return {"products": products, "hasNext": type(pages) is int and page + 1 < pages}


def _preset_details(client, product_id):
    item = client.product(product_id)
    if not isinstance(item, dict) or item.get("type") != "preset":
        raise CloudError("Cortex Cloud did not return a preset for this product ID.")
    detail = item.get("details")
    detail = detail if isinstance(detail, dict) else {}

    def names(key, limit):
        rows = detail.get(key)
        if not isinstance(rows, list):
            return []
        return [row["name"][:150] for row in rows[:limit]
                if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"]]

    compatibility = item.get("compatibility")
    return {
        "compatibility": [value[:100] for value in compatibility[:12] if isinstance(value, str)]
        if isinstance(compatibility, list) else [],
        "blocks": names("blocks", 32),
        "scenes": names("scenes", 8),
    }


def main(argv=None, *, client_factory=_client):
    parser = argparse.ArgumentParser(prog="qc-mcp cloud")
    parser.add_argument("action", choices=("login", "status", "logout", "search", "preset-details"))
    parser.add_argument("--json", action="store_true", dest="json_mode",
                        help="private stdin JSON input for login; JSON status output")
    parser.add_argument("--category", choices=("captures", "presets"))
    parser.add_argument("--product-id", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--page", type=int, default=0)
    args = parser.parse_args(argv)
    if args.action in {"search", "preset-details"} and not args.json_mode:
        parser.error(f"{args.action} requires --json")
    try:
        if args.action == "search":
            query = args.query.strip()
            if not args.category or not query or len(query) > 120 or not 0 <= args.page <= 1000:
                raise CloudError("Enter a Cloud search of 1 to 120 characters and a valid page.")
            result = _product_page(client_factory(), args.category, query, args.page)
        elif args.action == "preset-details":
            result = _preset_details(client_factory(), args.product_id)
        elif args.action == "login":
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
