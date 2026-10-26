# gigamove-cli

Upload files to [RWTH Aachen's Gigamove](https://gigamove.rwth-aachen.de) from the command line and get a share link. Built for HPC clusters and other machines without a browser.

```console
$ gigamove upload results.tar.gz
Checking session and quota...
Uploading results.tar.gz (4.2 GB in 223 chunks)...
results.tar.gz: 100%|██████████| 4.20G/4.20G [10:12<00:00, 6.86MB/s]
Upload complete; waiting for Gigamove to process the file...
https://gigamove.rwth-aachen.de/en/download/71879332bc3ee9fa85b948076bc8195b
```

## How it works

You can't script a Gigamove login, because RWTH single sign-on requires a TOTP second factor. gigamove-cli borrows the session from your browser instead:

1. You log in to Gigamove in your browser as usual and export its cookies to a `cookies.txt` file.
2. `gigamove auth` checks the cookies and stores only the Gigamove ones in `~/.config/gigamove/cookies.txt`.
3. You copy that file to the cluster. `gigamove upload` then does what the Gigamove web page does: it checks your quota, sends the file in chunks, and waits for Gigamove to finish processing it. The result is a normal Gigamove download link.

## Installation

The recommended way is [uv](https://docs.astral.sh/uv/).

```bash

# Install gigamove-cli as a tool (puts `gigamove` on your PATH, in ~/.local/bin)
uv tool install "gigamove-cli[progress] @ git+https://github.com/pakiessling/gigamove-cli"
```

If you'd rather not install anything, use `uvx` to run it once:

```bash
uvx --from "gigamove-cli[progress] @ git+https://github.com/pakiessling/gigamove-cli" gigamove upload data.zip
```


<details>
<summary>Without uv</summary>

```bash
pip install --user "gigamove-cli[progress] @ git+https://github.com/pakiessling/gigamove-cli"
```

For development, from a local checkout: `uv pip install -e ".[progress]"` or `pip install -e ".[progress]"`.
</details>

**Requirements:** Python 3.8+ and `requests`. `tqdm` is optional (the `progress` extra) and gives a progress bar on interactive terminals.

## Quickstart

### 1. Export your cookies (local machine)

1. Log in to [Gigamove](https://gigamove.rwth-aachen.de) in your browser.
2. Install the **Get cookies.txt LOCALLY** extension ([Chrome Web Store](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc), or [Firefox Add-ons](https://addons.mozilla.org/firefox/addon/get-cookies-txt-locally/)). With the Gigamove tab open, click the extension icon and press **Export**. The format must be *Netscape*.

### 2. Save and validate them

```bash
gigamove auth ~/Downloads/gigamove.rwth-aachen.de_cookies.txt
```

### 3. Copy them to the cluster

```bash
ssh hpc 'mkdir -p ~/.config/gigamove'
scp -p ~/.config/gigamove/cookies.txt hpc:~/.config/gigamove/cookies.txt
```

`-p` keeps the file's owner-only (`0600`) permissions. Without it, the copy can end up readable by other users on the cluster.

### 4. Upload (on the cluster)

```bash
gigamove upload /scratch/$USER/results.tar.gz
```

## Commands

### `gigamove upload FILE`

Uploads one file and prints its download link.

| Option | Default | Description |
|---|---|---|
| `--comment TEXT` | none | Description shown on the download page |
| `--email ADDR` | none | Email the download link to this address. Repeat for several recipients |
| `--expires DAYS` | `14` | Days until the link expires, 1–14 |
| `--ask-password` | off | Prompt for a download password |
| `--password TEXT` | none | Download password given on the command line (see below) |
| `--md5` | off | Compute the file's MD5 checksum before uploading and add it to the description, so recipients can verify the download |
| `--no-notify` | off | Don't send yourself a reminder email before the file expires |
| `--cookie-file PATH` | `~/.config/gigamove/cookies.txt` | Use a different cookie file |
| `-v`, `--verbose` | off | Show extra details and every failed attempt |

**Download passwords.** There are three ways to set one, checked in this order:

1. `--password TEXT`. Avoid this on shared machines: other users can see command lines with `ps`, and it ends up in your shell history.
2. `--ask-password`, which prompts without echoing the input.
3. The `GIGAMOVE_PASSWORD` environment variable. This is the best choice for batch jobs.

**Examples**

```bash
# Upload with a description, send the link to two people, expire after 7 days
gigamove upload results.tar.gz \
  --comment "Run 42 output" \
  --email alice@rwth-aachen.de --email bob@example.org \
  --expires 7

# Password-protected
gigamove upload secret.zip --ask-password
GIGAMOVE_PASSWORD="$(cat ~/.gigamove-pw)" gigamove upload secret.zip

# Add the MD5 checksum to the description ("Run 42 output - MD5: 9e107d9d...")
gigamove upload results.tar.gz --comment "Run 42 output" --md5

# Use another account's cookies
gigamove upload file.zip --cookie-file ~/cookies-other.txt
```

### `gigamove auth COOKIES.txt`

Reads a Netscape-format cookie file, checks the session against Gigamove, and saves it to `~/.config/gigamove/cookies.txt` with `0600` permissions.

Only cookies for `gigamove.rwth-aachen.de` and its parent domains are saved, and everything else in the file is discarded. It's fine to export all your browser cookies; none of them end up in the saved file. Afterwards, delete the exported file from your Downloads folder.

### `gigamove auth --check`

Checks whether the saved session still works. Exits with 0 if it does and 1 if it doesn't, so you can use it in scripts:

```bash
gigamove auth --check || echo "Session expired: re-export cookies and run gigamove auth"
```

### `gigamove --version`

Prints the installed version.

## Using it in batch jobs

`gigamove` is easy to script: stdout carries only the link, and the exit code says whether the upload worked.

| Exit code | Meaning |
|---|---|
| `0` | Upload finished; the link is on stdout |
| `1` | Error, such as an expired session, a network failure or a file that is too large. The reason is on stderr |
| `2` | Invalid command-line arguments |
| `130` | Interrupted with Ctrl-C |

Example Slurm job that archives its results and mails you the link:

```bash
#!/bin/bash
#SBATCH --job-name=analysis
#SBATCH --time=04:00:00

python analysis.py --out results/
tar czf results-$SLURM_JOB_ID.tar.gz results/

if LINK=$(gigamove upload results-$SLURM_JOB_ID.tar.gz \
            --comment "Slurm job $SLURM_JOB_ID" \
            --email "$USER@rwth-aachen.de"); then
    echo "Results: $LINK"
else
    echo "Upload failed, see the error above" >&2
fi
```

When stderr isn't a terminal (for example in a Slurm output file), progress is printed as one line per 5% instead of a redrawn bar, which keeps logs readable.

To catch an expired session before a long job starts, put `gigamove auth --check || exit 1` at the top of the job script.

## Upload details

**Chunks.** The file is sent to Gigamove in 18 MiB chunks, one after the other, exactly like the web page does. After the last chunk, Gigamove may need some time to process the file. gigamove-cli waits for that, printing a status line every minute, and then prints the link.

**Checks before uploading.** gigamove-cli reads your storage quota, the per-file limit and the latest allowed expiry date from the Gigamove page. If the file doesn't fit, it stops before sending anything.

**Retries.** Each chunk is retried up to 7 times after network errors or temporary server errors (HTTP 5xx), waiting 2, 4, 8, 16, 32, 60 and 60 seconds, so a network outage of about three minutes doesn't fail a long upload. A chunk's timeout is 6 minutes. If Gigamove rejects the upload outright, its error message is shown and nothing is retried.

**Failures.** If a chunk fails for good, or you press Ctrl-C, the upload stops. There is no resume: running the command again starts from the beginning.

**Session expiry.** If the session runs out before or during an upload, gigamove-cli stops and tells you to re-export your cookies, instead of retrying pointlessly.

**Limits** (enforced by Gigamove):

| | |
|---|---|
| Maximum file size | 100 GB. Split larger files first, e.g. `split -b 95G big.tar big.tar.part-` |
| Storage quota | 1000 GB per user, across all files you're currently sharing |
| Maximum expiry | 14 days |
| Empty files | Not accepted |

The expiry date is counted from today's date and is capped at the latest date Gigamove allows.

## Security

- The saved cookie file gives full access to your Gigamove account until the session ends. Treat it like a password and keep it `0600`.
- Session cookies are only sent to `gigamove.rwth-aachen.de`.
- Like the web page, gigamove-cli sends the download password to Gigamove (over HTTPS) as part of each chunk request, including in the URL.

## Troubleshooting

| Message | What to do |
|---|---|
| `No cookie file found at ~/.config/gigamove/cookies.txt` | Run `gigamove auth` locally and copy the file over (Quickstart steps 2–3) |
| `No cookies for gigamove.rwth-aachen.de found in …` | The export doesn't contain Gigamove cookies. Export again while logged in, in Netscape format |
| `Gigamove session expired or invalid` | Log in again in the browser, re-export the cookies, run `gigamove auth` and copy the file to the cluster again |
| `does not fit in your Gigamove quota` | Delete files you no longer need under *Provided* on the Gigamove website |
| `HTTP 4xx: …` | Gigamove rejected the upload; the message after the status code is Gigamove's own explanation |
| `Chunk N/M failed after 8 attempts` | The network or Gigamove was unavailable for several minutes. Run the command again |
| `still processing the upload after 6 h` | Gigamove may still finish; check *Provided* on the website before uploading again |

Run with `-v` to see every failed attempt and the IDs used during the upload.

## Disclaimer

This is an unofficial tool, not affiliated with RWTH Aachen University or its IT Center. It relies on Gigamove's internal web API, which can change without notice.

## License

[MIT](LICENSE)
