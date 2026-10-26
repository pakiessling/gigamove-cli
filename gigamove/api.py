import math
import re
import secrets
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

from .auth import is_login_url
from .config import BASE_URL, MAX_EXPIRY_DAYS

CHUNK_SIZE = 18 * 1024 * 1024  # same as the web client
MAX_FILE_SIZE = 100 * 1000**3  # fallback if the page doesn't state the per-file limit

MAX_RETRIES = 8
RETRY_BASE_DELAY = 2.0  # seconds; doubles each attempt
RETRY_MAX_DELAY = 60.0

POLL_INTERVAL = 3.0  # seconds, same as the web client
PROCESSING_TIMEOUT = 6 * 3600

_AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}


class GigamoveError(Exception):
    pass


class SessionExpired(GigamoveError):
    def __init__(self, msg: str = ""):
        super().__init__(
            msg or "Gigamove session expired or invalid. Log in again in your browser, "
            "re-export the cookies and run `gigamove auth COOKIES.txt`."
        )


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def _chunk_timeout(chunk_size: int) -> int:
    """Scale timeout with chunk size; assume at least 50 KB/s, minimum 120s."""
    return max(120, chunk_size // 50_000)


def _retry(fn, what: str, log=_log, verbose: bool = False):
    """Call fn(), retrying network errors and server-side (5xx) failures with backoff."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
            log(f"  {what}: retry {attempt}/{MAX_RETRIES - 1} in {delay:.0f}s...")
            time.sleep(delay)
        try:
            return fn()
        except (requests.RequestException, OSError) as e:
            last_exc = e
            if verbose:
                log(f"  {what} attempt {attempt + 1} failed: {e}")
    raise GigamoveError(f"{what} failed after {MAX_RETRIES} attempts: {last_exc}") from last_exc


class _Progress:
    """Upload progress on stderr: a tqdm bar on a terminal if available,
    otherwise plain lines every 5% so batch logs (e.g. Slurm) stay readable."""

    def __init__(self, total: int, desc: str):
        self.total = total
        self.done = 0
        self._lock = threading.Lock()
        self._tty = sys.stderr.isatty()
        self._last_pct = -1
        self._width = 0
        self._bar = None
        if self._tty:
            try:
                from tqdm import tqdm
                self._bar = tqdm(total=total, unit="B", unit_scale=True, file=sys.stderr, desc=desc)
            except ImportError:
                pass

    def update(self, n: int) -> None:
        with self._lock:
            self.done += n
            if self._bar is not None:
                self._bar.update(n)
                return
            pct = int(self.done * 100 / self.total)
            status = f"{pct}% ({_human(self.done)} / {_human(self.total)})"
            if self._tty:
                # Pad to the longest line so far: \r doesn't erase leftovers of a longer one.
                line = f"  {status}"
                self._width = max(self._width, len(line))
                print("\r" + line.ljust(self._width), end="", file=sys.stderr, flush=True)
            elif pct // 5 > self._last_pct // 5:
                print(f"  {status}", file=sys.stderr, flush=True)
            self._last_pct = pct

    def write(self, msg: str) -> None:
        with self._lock:
            if self._bar is not None:
                self._bar.write(msg, file=sys.stderr)
                return
            if self._tty and self._last_pct >= 0:
                print(file=sys.stderr)  # move off the \r status line
            print(msg, file=sys.stderr)

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()
        elif self._tty and self._last_pct >= 0:
            print(file=sys.stderr)


def _error_message(resp: requests.Response) -> str:
    """Extract Gigamove's error message (and suggestion, if any) from a response."""
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text.strip()[:300]}"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict) and err.get("message"):
        msg = err["message"]
        if err.get("suggestion"):
            msg += f" ({err['suggestion']})"
        return f"HTTP {resp.status_code}: {msg}"
    if isinstance(data, dict) and data.get("message"):
        return f"HTTP {resp.status_code}: {data['message']}"
    return f"HTTP {resp.status_code}: {str(data)[:300]}"


def _check(resp: requests.Response) -> requests.Response:
    """Raise SessionExpired for login redirects, HTTPError (retried) for transient
    server errors and GigamoveError (not retried) for other rejections."""
    if resp.is_redirect:
        if is_login_url(resp.headers.get("Location", "")):
            raise SessionExpired()
        raise GigamoveError(f"Unexpected redirect to {resp.headers.get('Location')}")
    if resp.status_code in (401, 419):
        raise SessionExpired()
    if resp.status_code >= 500 or resp.status_code in (408, 429):
        raise requests.HTTPError(_error_message(resp), response=resp)
    if resp.status_code >= 400:
        raise GigamoveError(_error_message(resp))
    return resp


def _json(resp: requests.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise GigamoveError(f"Expected JSON from Gigamove, got: {resp.text.strip()[:300]}")
    return data if isinstance(data, dict) else {}


class UploadPage:
    """Values the upload page hands to its own JavaScript."""

    def __init__(self, html: str):
        m = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', html)
        if not m:
            raise SessionExpired("Could not find the CSRF token on the Gigamove page — "
                                 "the session may be expired. Run `gigamove auth` again.")
        self.csrf = m.group(1)

        def number(pattern):
            m = re.search(pattern, html)
            return int(m.group(1)) if m else None

        m = re.search(r"var maxExpiryDate = '(\d{4}-\d{2}-\d{2})'", html)
        self.max_expiry = m.group(1) if m else None
        self.current_size = number(r"var currentSize = (\d+);")
        gb = number(r"\$\('#userQuota'\)\.text\((\d+)\)")
        self.user_quota = gb * 1000**3 if gb else None
        gb = number(r"\$\('#fileQuota'\)\.text\((\d+)\)")
        self.file_quota = gb * 1000**3 if gb else MAX_FILE_SIZE


def get_upload_page(session: requests.Session) -> UploadPage:
    # A redirect here means the session is gone (Gigamove sends us to SSO).
    resp = session.get(f"{BASE_URL}/en/provide", timeout=30, allow_redirects=False)
    if resp.is_redirect:
        raise SessionExpired()
    resp.raise_for_status()
    return UploadPage(resp.text)


def get_csrf_token(session: requests.Session) -> str:
    return get_upload_page(session).csrf


def _send_chunk(session: requests.Session, csrf: str, fields: dict, index: int,
                data: bytes, timeout: int) -> dict:
    fields = dict(fields, chunk_index=str(index))
    # The web client sends every field both in the query string and the form body;
    # only `notify` differs ('1'/'0' in the query, 'true'/'false' in the body).
    query = dict(fields, notify="1" if fields["notify"] == "true" else "0")
    resp = session.post(
        f"{BASE_URL}/en/upload/upload-chunk",
        params=query,
        data=fields,
        files={"chunk": ("blob", data, "application/octet-stream")},
        headers=dict(_AJAX_HEADERS, **{"X-CSRF-TOKEN": csrf}),
        timeout=timeout,
        allow_redirects=False,
    )
    _check(resp)
    if index < int(fields["total_chunks"]) - 1:
        return {}  # the web client ignores responses to all but the last chunk
    return _json(resp)


def wait_for_processing(session: requests.Session, s3_key: str, verbose: bool = False) -> str:
    """Poll until Gigamove has finished processing the upload; return the download URL."""
    url = f"{BASE_URL}/en/upload/status/{quote(s3_key, safe='')}"
    start = last_note = time.monotonic()
    while True:
        data = _json(_retry(
            lambda: _check(session.get(url, headers=_AJAX_HEADERS, timeout=30,
                                       allow_redirects=False)),
            "Checking upload status", verbose=verbose,
        ))
        status = data.get("status")
        if status == "completed" and data.get("download_url"):
            return data["download_url"]
        if status in ("failed", "error"):
            raise GigamoveError(f"Gigamove could not process the upload: {data}")

        elapsed = time.monotonic() - start
        if elapsed > PROCESSING_TIMEOUT:
            raise GigamoveError(
                f"Gigamove is still processing the upload after {elapsed / 3600:.0f} h. "
                "It may still finish; check 'Provided' on the Gigamove website."
            )
        if verbose or time.monotonic() - last_note >= 60:
            _log(f"  Still processing ({elapsed / 60:.0f} min, status: {status})...")
            last_note = time.monotonic()
        time.sleep(POLL_INTERVAL)


def upload_file(
    session: requests.Session,
    filepath: Path,
    comment: str = "",
    password: str = "",
    expiry_days: int = MAX_EXPIRY_DAYS,
    recipients: "list | None" = None,
    notify: bool = True,
    verbose: bool = False,
) -> str:
    if recipients is None:
        recipients = []
    if not 1 <= expiry_days <= MAX_EXPIRY_DAYS:
        raise GigamoveError(f"Expiry must be between 1 and {MAX_EXPIRY_DAYS} days.")

    file_size = filepath.stat().st_size
    if file_size == 0:
        raise GigamoveError(f"{filepath.name} is empty; Gigamove cannot share empty files.")

    # Step 1: CSRF token, limits and quota from the upload page
    _log("Checking session and quota...")
    page = _retry(lambda: get_upload_page(session), "Loading upload page", verbose=verbose)

    if file_size > page.file_quota:
        raise GigamoveError(
            f"{filepath.name} is {_human(file_size)}; Gigamove accepts at most "
            f"{_human(page.file_quota)} per file. Split it first, e.g.\n"
            f"  split -b {page.file_quota // 1000**3 - 5}G {filepath} {filepath.name}.part-"
        )
    if page.user_quota and page.current_size is not None \
            and page.current_size + file_size > page.user_quota:
        raise GigamoveError(
            f"{filepath.name} ({_human(file_size)}) does not fit in your Gigamove quota: "
            f"{_human(page.current_size)} of {_human(page.user_quota)} used. "
            "Delete old files under 'Provided' on the Gigamove website."
        )

    expiry_date = (date.today() + timedelta(days=expiry_days)).isoformat()
    if page.max_expiry and expiry_date > page.max_expiry:
        expiry_date = page.max_expiry  # the server's "today" may differ from ours

    total_chunks = math.ceil(file_size / CHUNK_SIZE)
    upload_id = secrets.token_hex(12)
    fields = {
        "upload_id": upload_id,
        "total_chunks": str(total_chunks),
        "original_filename": filepath.name,
        "comment": comment,
        "password": password,
        "recipients": ",".join(recipients),
        "expiryDate": expiry_date,
        "notify": "true" if notify else "false",
        "total_size": str(file_size),
    }
    if verbose:
        _log(f"File: {filepath.name} ({file_size} bytes)")
        _log(f"Expiry: {expiry_date}")
        _log(f"Upload ID: {upload_id}")

    # Step 2: send the file in chunks, one after the other like the web client
    _log(f"Uploading {filepath.name} ({_human(file_size)} in {total_chunks} "
         f"chunk{'s' if total_chunks != 1 else ''})...")
    timeout = _chunk_timeout(CHUNK_SIZE)
    progress = _Progress(file_size, filepath.name)
    result = {}
    try:
        with open(filepath, "rb") as f:
            for index in range(total_chunks):
                data = f.read(CHUNK_SIZE)
                expected = min(CHUNK_SIZE, file_size - index * CHUNK_SIZE)
                if len(data) != expected:
                    raise GigamoveError(f"{filepath} changed while uploading")
                result = _retry(
                    lambda: _send_chunk(session, page.csrf, fields, index, data, timeout),
                    f"Chunk {index + 1}/{total_chunks}", progress.write, verbose,
                )
                progress.update(len(data))
    finally:
        progress.close()

    # Step 3: the last chunk's response has the link, or says the server is still processing
    if verbose:
        _log(f"Final response: {result}")
    if result.get("status") == "processing" and result.get("s3_key"):
        _log("Upload complete; waiting for Gigamove to process the file...")
        url = wait_for_processing(session, result["s3_key"], verbose)
    elif result.get("download_url"):
        url = result["download_url"]
    else:
        raise GigamoveError(f"Unexpected response after the last chunk: {result}")

    if url.startswith("/"):
        url = BASE_URL + url
    return url
