#!/usr/bin/env python3
"""Fetch a real HCAD Real_acct_owner sample and truncate it into the fixture dir.

RUN MANUALLY — this script talks to HCAD's public data downloads site
(download.hcad.org). It is never invoked by tests or CI; those run against the
committed synthetic fixture.

LICENSING NOTE: the download is Harris Central Appraisal District public data
(hcad.org "Public Data" downloads). Review HCAD's data terms before
redistributing, and think twice before committing real rows to the repo — the
checked-in fixture is synthetic on purpose.

Usage:
    python3 scripts/fetch_hcad_sample.py [URL] [--rows N] [--out PATH]

URL resolution order: CLI argument, HCAD_DOWNLOAD_URL environment variable,
then the API's settings.hcad_download_url (if the app package is importable).
Only the standard library is required; httpx is used when available.

The script downloads the zip, extracts real_acct.txt, keeps the header plus
the first N data rows (default 1000), and writes the result into
data/fixtures/hcad_sample/. WARNING: by default this OVERWRITES the committed
synthetic fixture real_acct.txt (restore it with git if that was unintended);
pass --out to write elsewhere.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "fixtures" / "hcad_sample" / "real_acct.txt"
MEMBER_SUFFIX = "real_acct.txt"
CHUNK_BYTES = 1 << 16

LICENSING_NOTE = """\
--------------------------------------------------------------------------
HCAD PUBLIC DATA DOWNLOAD — run manually, never from tests or CI.
Source: Harris Central Appraisal District public data downloads
(hcad.org -> Public Data / download.hcad.org). Review HCAD's terms before
redistributing; avoid committing real rows — the repo fixture is synthetic.
--------------------------------------------------------------------------\
"""


def resolve_url(cli_url: str | None) -> str:
    """CLI arg -> HCAD_DOWNLOAD_URL env -> settings.hcad_download_url -> ''."""
    if cli_url:
        return cli_url
    env_url = os.environ.get("HCAD_DOWNLOAD_URL", "").strip()
    if env_url:
        return env_url
    try:
        sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
        from app.core.config import settings

        return settings.hcad_download_url.strip()
    except Exception:
        return ""


def download(url: str, dest: Path) -> None:
    """Stream the download to disk; httpx when available, urllib otherwise."""
    try:
        import httpx
    except ImportError:
        httpx = None  # type: ignore[assignment]
    if httpx is not None:
        with httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as response:
            response.raise_for_status()
            with dest.open("wb") as out:
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    out.write(chunk)
        return
    with urllib.request.urlopen(url, timeout=300.0) as response, dest.open("wb") as out:
        shutil.copyfileobj(response, out, CHUNK_BYTES)


def truncate_lines(source_path: Path, out_path: Path, rows: int) -> int:
    """Write header + first N data rows of a real_acct file; returns rows kept."""
    kept = 0
    with source_path.open("rb") as src, out_path.open("wb") as out:
        header = src.readline()
        if not header:
            raise SystemExit("error: downloaded real_acct file is empty")
        out.write(header)
        for line in src:
            if kept >= rows:
                break
            out.write(line)
            kept += 1
    return kept


def extract_sample(archive_path: Path, out_path: Path, rows: int) -> int:
    """Extract real_acct.txt from the zip (or use the file as-is) and truncate."""
    with tempfile.TemporaryDirectory(prefix="hcad_sample_") as tmp:
        extracted = Path(tmp) / "real_acct_full.txt"
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as archive:
                member = next(
                    (
                        name
                        for name in archive.namelist()
                        if name.lower().endswith(MEMBER_SUFFIX)
                    ),
                    None,
                )
                if member is None:
                    raise SystemExit(
                        f"error: no {MEMBER_SUFFIX} member found in {archive_path.name}"
                    )
                with archive.open(member) as src, extracted.open("wb") as out:
                    shutil.copyfileobj(src, out, CHUNK_BYTES)
        else:
            # Some mirrors serve the txt directly.
            extracted = archive_path
        return truncate_lines(extracted, out_path, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", nargs="?", default=None, help="Real_acct_owner.zip URL")
    parser.add_argument(
        "--rows", type=int, default=1000, help="data rows to keep (default 1000)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"output path (default {DEFAULT_OUT})",
    )
    args = parser.parse_args()

    print(LICENSING_NOTE)
    url = resolve_url(args.url)
    if not url:
        print(
            "error: no download URL. Pass one as an argument, set"
            " HCAD_DOWNLOAD_URL, or configure settings.hcad_download_url.",
            file=sys.stderr,
        )
        return 2

    if args.out.resolve() == DEFAULT_OUT.resolve():
        print(
            f"NOTE: overwriting the committed synthetic fixture at {DEFAULT_OUT}."
            " Use 'git restore data/fixtures/hcad_sample/real_acct.txt' to undo."
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hcad_download_") as tmp:
        archive_path = Path(tmp) / "Real_acct_owner.zip"
        print(f"downloading {url} ...")
        download(url, archive_path)
        print(f"downloaded {archive_path.stat().st_size} bytes; extracting sample ...")
        kept = extract_sample(archive_path, args.out, args.rows)
    print(f"wrote {args.out} (header + {kept} data rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
