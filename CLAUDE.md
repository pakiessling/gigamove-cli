# Gigamove CLI — Project Reference

## Goal
Python CLI tool (`gigamove`) to upload files via RWTH Aachen's Gigamove 2.0 and get share links, usable from HPC nodes without a browser.

## API Flow (from the live /en/provide page, Oct 2026)

Base URL: `https://gigamove.rwth-aachen.de`. The upload logic is inline JS in the logged-in
`/en/provide` page (not in the public app.js). Saved copy: `_private/provide.html`.
The old S3 multipart flow (register at `/en/upload/provide`, `/en/url` signing, finalize) was
removed by Gigamove: `POST /en/upload/provide` now returns 405.

### 1. Upload page
```
GET /en/provide (session cookies; a redirect means the session expired)
→ <meta name="csrf-token" content="...">
→ inline JS vars: currentSize = <bytes used>; maxExpiryDate = 'YYYY-MM-DD';
  $('#userQuota').text(1000) [GB]; $('#fileQuota').text(100) [GB]
```

### 2. Chunks (sequential, 18 MiB each, chunk_index 0-based)
```
POST /en/upload/upload-chunk?<all fields below, notify=1|0>
Headers: X-CSRF-TOKEN, X-Requested-With: XMLHttpRequest
multipart/form-data: chunk (blob, filename "blob"), upload_id (client-generated random),
  chunk_index, total_chunks, original_filename, comment, password,
  recipients (comma-separated), expiryDate (YYYY-MM-DD), notify (true|false), total_size
Errors: JSON {"error": {"message", "suggestion"}}
```
The browser ignores responses to all chunks but the last.

### 3. Result (response to the last chunk)
- `{"message": "File uploaded successfully", "status": "completed", "s3_key", "download_url",
  "download_action_url"}` → done (confirmed live for a 40 MB file, Oct 2026;
  `download_action_url` is a short-lived signed link, so print `download_url`), or
- `{"status": "processing", "s3_key": ...}` → poll `GET /en/upload/status/{s3_key}` every 3 s
  until `{"status": "completed", "download_url": ...}`

Downloading (for verification): `GET /en/download/cephfs-link/{s3_key}` (session, XHR) →
`{"download_url": ".../en/download/cephfs/{s3_key}"}`; a live round trip was byte-identical.

Unverified: whether chunks may be sent in parallel/out of order, and whether re-sending a chunk
index (retry after a lost response) is idempotent. The CLI sends sequentially like the browser.

## Auth Strategy
RWTH SSO uses Shibboleth + mandatory TOTP 2FA → cannot automate.
**Approach**: User exports a Netscape-format cookies.txt (Get cookies.txt LOCALLY extension) → `gigamove auth FILE` validates and saves it to `~/.config/gigamove/cookies.txt` → scp to HPC.

## File Structure
```
gigamove/
  __init__.py
  cli.py       # argparse (upload + auth subcommands)
  auth.py      # Netscape cookies.txt parsing + session check
  api.py       # upload page (CSRF, quota), chunk upload, status polling
  config.py    # ~/.config/gigamove/cookies.txt read/write
pyproject.toml  # distribution gigamove-cli; commands gigamove + gigamove-cli alias -> gigamove.cli:main
```

## CLI Interface
```
gigamove upload FILE [--comment TEXT] [--ask-password | --password TEXT] [--email ADDR...]
                     [--expires DAYS] [--no-notify] [--cookie-file PATH] [-v]

gigamove auth COOKIES.txt | gigamove auth --check
```
- stdout: only the download URL
- stderr: progress + status

## Dependencies
- `requests` (required)
- `tqdm` (optional, progress bar)
- Python 3.8+ (HPC compat)

## Key Constraints
- Max file size: 100 GB; user quota 1000 GB (both read from the page)
- Max expiry: 14 days
- expiryDate sent as YYYY-MM-DD (e.g., today + 14 days)
- notify=true = "E-Mail Notification" checkbox: uploader is emailed before the file expires (recipients get the link by email via the `recipients` field)
- Password can also come from GIGAMOVE_PASSWORD env var
- Private reference material (HAR capture, logs) lives in `_private/` (git-ignored, never publish)
