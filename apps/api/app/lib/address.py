"""Deterministic USPS-style address normalization and hashing.

Pure functions, no network calls. Pipeline: uppercase -> ASCII-fold accents ->
isolate "#" -> strip punctuation -> tokenize -> canonicalize unit designators,
directionals, and street suffixes via module-level tables -> join with single
spaces. Alphanumeric tokens (ordinals like "1ST", numeric street names) pass
through untouched because table keys are purely alphabetic.
"""

import hashlib
import re
import unicodedata
from typing import Final

_DIRECTIONALS: Final[dict[str, str]] = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
}

_SUFFIXES: Final[dict[str, str]] = {
    "DRIVE": "DR",
    "DRV": "DR",
    "STREET": "ST",
    "STR": "ST",
    "AVENUE": "AVE",
    "AVENU": "AVE",
    "AV": "AVE",
    "BOULEVARD": "BLVD",
    "BOULV": "BLVD",
    "LANE": "LN",
    "COURT": "CT",
    "ROAD": "RD",
    "PARKWAY": "PKWY",
    "PKWAY": "PKWY",
    "HIGHWAY": "HWY",
    "CIRCLE": "CIR",
    "PLACE": "PL",
    "TRAIL": "TRL",
    "WAY": "WAY",
    "TERRACE": "TER",
    "SQUARE": "SQ",
    "LOOP": "LOOP",
    "COVE": "CV",
    "BEND": "BND",
}

# "#4" / "UNIT 4" / "APT 4" canonicalize to "APT 4"; "STE"/"SUITE" stays "STE".
_UNITS: Final[dict[str, str]] = {
    "APT": "APT",
    "APARTMENT": "APT",
    "UNIT": "APT",
    "STE": "STE",
    "SUITE": "STE",
}

_UNIT_CANONICALS: Final[frozenset[str]] = frozenset(_UNITS.values())

_NON_TOKEN_CHARS: Final[re.Pattern[str]] = re.compile(r"[^A-Z0-9#]+")


def _ascii_fold(text: str) -> str:
    """Fold accented characters to plain ASCII (NFKD decompose, drop the rest)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def normalize_address(raw: str) -> str:
    """Normalize a raw street address string deterministically."""
    text = _ascii_fold(raw.upper())
    # Make "#" its own token so both "#4B" and "# 4B" become a unit designator.
    text = text.replace("#", " # ")
    text = _NON_TOKEN_CHARS.sub(" ", text)

    tokens: list[str] = []
    for token in text.split():
        if token == "#":
            # "APT #4" must not produce "APT APT 4".
            if tokens and tokens[-1] in _UNIT_CANONICALS:
                continue
            tokens.append("APT")
        elif token in _UNITS:
            tokens.append(_UNITS[token])
        elif token in _DIRECTIONALS:
            tokens.append(_DIRECTIONALS[token])
        elif token in _SUFFIXES:
            tokens.append(_SUFFIXES[token])
        else:
            tokens.append(token)
    return " ".join(tokens)


def address_hash(normalized: str) -> str:
    """SHA-256 hex digest of a normalized address string."""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
