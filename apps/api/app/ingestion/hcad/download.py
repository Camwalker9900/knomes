"""Snapshot acquisition for HCAD exports: streamed downloads, checksummed local files.

Every ingestion run starts from a :class:`~app.ingestion.base.RawSnapshot` — an
immutable, sha256-checksummed capture of the source file. Downloads stream to
disk in chunks (never fully in RAM); local files are checksummed the same way
so ``--file`` imports are just as reproducible as remote fetches.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

import httpx

from app.ingestion.base import RawSnapshot

logger = logging.getLogger(__name__)

HCAD_SOURCE_NAME: Final[str] = "hcad"

# .../apps/api/app/ingestion/hcad/download.py -> repo root is parents[5].
DEFAULT_RAW_DIR: Final[Path] = (
    Path(__file__).resolve().parents[5] / "data" / "raw" / "hcad"
)

_CHUNK_BYTES: Final[int] = 1 << 16
_DOWNLOAD_TIMEOUT_SECONDS: Final[float] = 120.0


def sha256_of_file(path: Path) -> str:
    """SHA-256 hex digest of a file, computed in streaming chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_from_file(path: Path, *, source_url: str | None = None) -> RawSnapshot:
    """Build a checksummed snapshot from an already-local export file."""
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"snapshot file does not exist: {resolved}")
    return RawSnapshot(
        source_name=HCAD_SOURCE_NAME,
        storage_path=resolved,
        checksum=sha256_of_file(resolved),
        retrieved_at=datetime.now(UTC),
        source_url=source_url,
    )


def fetch_snapshot(url: str | None, dest_dir: Path) -> RawSnapshot:
    """Stream-download an HCAD export to ``dest_dir`` and return its snapshot.

    The response body is written to disk chunk by chunk while the sha256
    checksum accumulates — the file is never held in memory. With no ``url``
    configured this raises ``ValueError`` telling the operator to pass
    ``--file`` (HCAD's download URL ships unset until licensing is confirmed;
    see ``settings.hcad_download_url``).
    """
    if not url:
        raise ValueError(
            "no HCAD download URL configured (settings.hcad_download_url is empty);"
            " pass --file PATH to import a local real_acct export instead"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(urlparse(url).path).name or "hcad_download.bin"
    dest = dest_dir / filename
    digest = hashlib.sha256()
    retrieved_at = datetime.now(UTC)
    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS
    ) as response:
        response.raise_for_status()
        with dest.open("wb") as out:
            for chunk in response.iter_bytes(_CHUNK_BYTES):
                out.write(chunk)
                digest.update(chunk)
    checksum = digest.hexdigest()
    logger.info(
        "hcad snapshot downloaded",
        extra={
            "source_name": HCAD_SOURCE_NAME,
            "source_url": url,
            "storage_path": str(dest),
            "checksum": checksum,
        },
    )
    return RawSnapshot(
        source_name=HCAD_SOURCE_NAME,
        storage_path=dest,
        checksum=checksum,
        retrieved_at=retrieved_at,
        source_url=url,
    )
