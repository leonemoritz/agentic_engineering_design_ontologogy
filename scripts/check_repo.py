# SPDX-License-Identifier: Apache-2.0
"""Check SPDX headers and scan project files for common sensitive-data patterns."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".html", ".css", ".yml", ".yaml", ".toml"}
SECRET_PATTERNS = [
    re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-" + r"[A-Za-z0-9]{24,}\b"),
    re.compile(
        r"\b(?:customer|client|account)[-_ ]?(?:id|number)\s*[:=]\s*[A-Za-z0-9][A-Za-z0-9_-]{3,}\b",
        re.IGNORECASE,
    ),
]


def main() -> int:
    failures: list[str] = []
    files = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(part in {".git", ".venv", "__pycache__", ".pytest_cache"} for part in path.parts)
    ]
    for path in files:
        relative = path.relative_to(ROOT)
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if path.suffix in SOURCE_SUFFIXES and "SPDX-License-Identifier: Apache-2.0" not in content[:500]:
            failures.append(f"{relative}: missing Apache-2.0 SPDX identifier")
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                failures.append(f"{relative}: sensitive-data pattern matched ({pattern.pattern})")
    if failures:
        print("Repository hygiene check failed:", file=sys.stderr)
        print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
        return 1
    print(f"Repository hygiene check passed ({len(files)} files scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())