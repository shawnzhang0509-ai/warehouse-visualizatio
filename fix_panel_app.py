"""Remove Git merge conflict markers from panel_app.py (Windows local fix)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "panel_app.py"
MARKER = re.compile(r"^<{7}|^={7}|^>{7}")


def strip_conflict_markers(text: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    mode = "keep"  # keep | skip_head | take_theirs
    changed = False
    for line in lines:
        stripped = line.lstrip("\ufeff").strip("\n\r")
        if stripped.startswith("<<<<<<<"):
            mode = "skip_head"
            changed = True
            continue
        if stripped.startswith("=======") and mode == "skip_head":
            mode = "take_theirs"
            continue
        if stripped.startswith(">>>>>>>") and mode in ("skip_head", "take_theirs"):
            mode = "keep"
            continue
        if mode == "skip_head":
            continue
        if mode == "take_theirs":
            out.append(line)
            continue
        out.append(line)
    return "".join(out), changed


def ensure_app_version(text: str) -> str:
    if "APP_VERSION" in text and "<<<<<<" not in text:
        return text
    # fallback: fix line 38 area if still broken
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if "APP_VERSION" in line or "<<<<<<" in line:
            lines[i] = 'APP_VERSION = "1.9.58"\n'
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("="):
                lines[i + 1] = ""
            break
    return "".join(lines)


def main() -> int:
    if not TARGET.is_file():
        print(f"Missing {TARGET}", file=sys.stderr)
        return 1
    raw = TARGET.read_text(encoding="utf-8-sig", errors="replace")
    if "<<<<<<<" not in raw and ">>>>>>>" not in raw:
        print("panel_app.py OK (no conflict markers).")
        return 0
    fixed, _ = strip_conflict_markers(raw)
    fixed = ensure_app_version(fixed)
    TARGET.write_text(fixed, encoding="utf-8")
    print("Fixed panel_app.py — removed Git conflict markers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
