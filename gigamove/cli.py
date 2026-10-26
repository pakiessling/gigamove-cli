import argparse
import getpass
import os
import sys
from pathlib import Path

from . import __version__
from .config import MAX_EXPIRY_DAYS


def _expiry_days(value: str) -> int:
    n = int(value)
    if not 1 <= n <= MAX_EXPIRY_DAYS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_EXPIRY_DAYS}")
    return n


def cmd_upload(args):
    from .api import upload_file
    from .auth import make_session
    from .config import load_session

    filepath = Path(args.file)
    if not filepath.exists():
        sys.exit(f"File not found: {filepath}")
    if not filepath.is_file():
        sys.exit(f"Not a file: {filepath}")

    if args.password is not None:
        password = args.password
    elif args.ask_password:
        password = getpass.getpass("Download password: ")
    else:
        password = os.environ.get("GIGAMOVE_PASSWORD", "")

    session = make_session(load_session(args.cookie_file))

    url = upload_file(
        session,
        filepath,
        comment=args.comment,
        password=password,
        expiry_days=args.expires,
        recipients=args.email,
        notify=not args.no_notify,
        md5=args.md5,
        verbose=args.verbose,
    )

    # Only URL on stdout
    print(url)


def cmd_auth(args):
    from .auth import check_session, parse_cookie_file
    from .config import load_session, save_session

    if args.check:
        cookies = load_session()
        valid = check_session(cookies, verbose=True)
        sys.exit(0 if valid else 1)

    if not args.cookie_file:
        sys.exit("Provide a Netscape-format cookie file: gigamove auth COOKIES.txt")

    cookies = parse_cookie_file(args.cookie_file)
    print(f"Found {len(cookies)} Gigamove cookie(s).", file=sys.stderr)

    # Validate before saving
    print("Checking session validity...", file=sys.stderr)
    if not check_session(cookies, verbose=True):
        sys.exit("Session appears invalid. Log into Gigamove, re-export cookies and try again.")

    save_session(cookies)


def main():
    parser = argparse.ArgumentParser(
        prog="gigamove",
        description="Upload files via RWTH Gigamove 2.0",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    # upload subcommand
    up = sub.add_parser("upload", help="Upload a file and print the share link")
    up.add_argument("file", metavar="FILE", help="Path to file to upload")
    up.add_argument("--comment", metavar="TEXT", default="", help="Upload description")
    pw = up.add_mutually_exclusive_group()
    pw.add_argument("--ask-password", action="store_true",
                    help="Prompt for a download password (or set GIGAMOVE_PASSWORD)")
    pw.add_argument("--password", metavar="TEXT",
                    help="Download password; visible to other users via `ps`, "
                         "prefer --ask-password or GIGAMOVE_PASSWORD")
    up.add_argument("--email", metavar="ADDR", action="append", default=[],
                    help="Send the download link to this address (can be repeated)")
    up.add_argument("--expires", metavar="DAYS", type=_expiry_days, default=MAX_EXPIRY_DAYS,
                    help=f"Expiry in days (1-{MAX_EXPIRY_DAYS}, default {MAX_EXPIRY_DAYS})")
    up.add_argument("--no-notify", action="store_true",
                    help="Don't email me a reminder before the file expires")
    up.add_argument("--md5", action="store_true",
                    help="Compute the file's MD5 checksum before uploading and add it "
                         "to the description")
    up.add_argument("--cookie-file", metavar="PATH",
                    help="Path to Netscape-format cookies.txt (overrides ~/.config/gigamove/cookies.txt)")
    up.add_argument("-v", "--verbose", action="store_true", help="Verbose output on stderr")

    # auth subcommand
    au = sub.add_parser("auth", help="Save or check session cookies")
    au.add_argument("cookie_file", metavar="COOKIES.txt", nargs="?",
                    help="Netscape-format cookie file exported from your browser")
    au.add_argument("--check", action="store_true",
                    help="Check if saved session is still valid")

    args = parser.parse_args()

    if args.command == "upload":
        handler = cmd_upload
    elif args.command == "auth":
        handler = cmd_auth
    else:
        parser.print_help()
        sys.exit(1)

    import requests
    from .api import GigamoveError

    try:
        handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except GigamoveError as e:
        sys.exit(f"Error: {e}")
    except requests.RequestException as e:
        sys.exit(f"Network error: {e}")


if __name__ == "__main__":
    main()
