import sys
from pathlib import Path

from .config import BASE_URL, GIGAMOVE_HOST


def parse_cookie_file(path: Path) -> dict:
    """Parse a Netscape-format cookies.txt and return Gigamove cookies as a dict."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError as e:
        sys.exit(f"Failed to read cookie file: {e}")

    cookies = {}
    for line in lines:
        # Exporters mark HttpOnly cookies with this prefix; strip it before
        # treating '#' lines as comments.
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue

        fields = line.rstrip("\r\n").split("\t")
        if len(fields) != 7:
            continue
        domain, _, _, _, _, name, value = fields
        if _domain_matches(domain):
            cookies[name] = value

    if not cookies:
        sys.exit(
            f"No cookies for {GIGAMOVE_HOST} found in {path}.\n"
            "Make sure the file is in Netscape format and was exported while "
            "logged into Gigamove."
        )
    return cookies


def _domain_matches(domain: str) -> bool:
    domain = domain.lstrip(".").lower()
    return GIGAMOVE_HOST == domain or GIGAMOVE_HOST.endswith("." + domain)


def make_session(cookies: dict):
    """Create a requests session whose cookies are only ever sent to Gigamove itself."""
    import requests

    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=GIGAMOVE_HOST, path="/")
    return session


def is_login_url(url: str) -> bool:
    return "shibboleth" in url.lower() or "login" in url.lower()


def check_session(cookies: dict, verbose: bool = False) -> bool:
    """Return True if the session cookies are valid (not redirected to login)."""
    import requests

    session = make_session(cookies)
    try:
        resp = session.get(f"{BASE_URL}/en/provide", allow_redirects=True, timeout=15)
    except requests.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return False

    # If redirected to SSO/login page, session is expired
    if is_login_url(resp.url):
        if verbose:
            print(f"Session invalid — redirected to: {resp.url}", file=sys.stderr)
        return False

    if resp.status_code == 200 and "csrf-token" in resp.text:
        if verbose:
            print("Session is valid.", file=sys.stderr)
        return True

    if verbose:
        print(f"Unexpected response: HTTP {resp.status_code}", file=sys.stderr)
    return False
