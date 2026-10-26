import os
import sys
from pathlib import Path

GIGAMOVE_HOST = "gigamove.rwth-aachen.de"
BASE_URL = f"https://{GIGAMOVE_HOST}"
MAX_EXPIRY_DAYS = 14  # Gigamove limit

CONFIG_DIR = Path.home() / ".config" / "gigamove"
COOKIE_FILE = CONFIG_DIR / "cookies.txt"


def load_session(path: "Path | None" = None) -> dict:
    from .auth import parse_cookie_file

    path = Path(path) if path else COOKIE_FILE
    if not path.exists():
        sys.exit(
            f"No cookie file found at {path}.\n"
            "Run `gigamove auth COOKIES.txt` to save your session cookies."
        )
    return parse_cookie_file(path)


def save_session(cookies: dict) -> None:
    """Write only the Gigamove cookies, in Netscape format, readable by the owner only."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File", "# Gigamove session cookies saved by `gigamove auth`"]
    for name, value in cookies.items():
        # Expiry 0 = session cookie; the server decides when the session ends.
        lines.append("\t".join([GIGAMOVE_HOST, "FALSE", "/", "TRUE", "0", name, value]))

    tmp = COOKIE_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, COOKIE_FILE)
    print(f"Cookies saved to {COOKIE_FILE}", file=sys.stderr)
