"""Terminal output helpers."""

from __future__ import annotations

import os
import shutil
import sys
import textwrap


def style(text: str, color: str, stream=None) -> str:
    stream = sys.stdout if stream is None else stream
    if (
        "NO_COLOR" in os.environ
        or not stream.isatty()
        or os.environ.get("TERM") == "dumb"
    ):
        return text
    return f"\033[{color}m{text}\033[0m"


def heading(title: str, detail: str) -> None:
    print(style(f"\n╭─ {title} " + "─" * max(0, 56 - len(title)), "1;36"))
    print(f"│ {detail}")
    print(style("╰" + "─" * 60, "36"))


def step_row(label: str, key: str, detail: str, color: str, stream=None) -> None:
    print(f"  {style(label.ljust(12), color, stream)} {style(key, '1', stream)}", file=stream)
    width = max(30, min(shutil.get_terminal_size().columns - 17, 100))
    for line in textwrap.wrap(detail, width=width):
        print(style(f"                 {line}", "2", stream), file=stream)


def verdict(label: str, detail: str, color: str, stream=None) -> None:
    print(f"  {style(label, '1;' + color, stream)}  {detail}", file=stream)


def stage(label: str, detail: str, color: str = "32", mark: str = "✓") -> None:
    print(f"  {style(f'{mark} {label}'.ljust(11), '1;' + color)} {detail}", flush=True)
