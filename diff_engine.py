"""
Diff engine: computes side-by-side diff between two CNC program files.
Returns structured data that the diff panel renders.
"""

import difflib
from dataclasses import dataclass
from typing import Optional


@dataclass
class DiffLine:
    line_no_left: Optional[int]   # None for inserted lines on left
    line_no_right: Optional[int]  # None for deleted lines on right
    text_left: str
    text_right: str
    kind: str  # 'equal' | 'replace' | 'insert' | 'delete' | 'empty'


def read_file_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception:
        return []


def compute_diff(path_a: str, path_b: str) -> list[DiffLine]:
    """
    Compute a side-by-side diff between two files.
    Returns a list of DiffLine objects for display.
    """
    lines_a = read_file_lines(path_a)
    lines_b = read_file_lines(path_b)

    # Strip trailing newlines for display but keep content
    def clean(line):
        return line.rstrip("\n\r")

    matcher = difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=False)
    result: list[DiffLine] = []

    ln_a = 1
    ln_b = 1

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for ka, kb in zip(range(i1, i2), range(j1, j2)):
                result.append(DiffLine(ln_a, ln_b, clean(lines_a[ka]), clean(lines_b[kb]), "equal"))
                ln_a += 1
                ln_b += 1

        elif tag == "replace":
            block_a = lines_a[i1:i2]
            block_b = lines_b[j1:j2]
            # Pad shorter side
            max_len = max(len(block_a), len(block_b))
            for k in range(max_len):
                ta = clean(block_a[k]) if k < len(block_a) else ""
                tb = clean(block_b[k]) if k < len(block_b) else ""
                la = ln_a if k < len(block_a) else None
                lb = ln_b if k < len(block_b) else None
                kind = "replace" if ta and tb else ("delete" if ta else "insert")
                result.append(DiffLine(la, lb, ta, tb, kind))
                if k < len(block_a):
                    ln_a += 1
                if k < len(block_b):
                    ln_b += 1

        elif tag == "delete":
            for ka in range(i1, i2):
                result.append(DiffLine(ln_a, None, clean(lines_a[ka]), "", "delete"))
                ln_a += 1

        elif tag == "insert":
            for kb in range(j1, j2):
                result.append(DiffLine(None, ln_b, "", clean(lines_b[kb]), "insert"))
                ln_b += 1

    return result


def similarity_percent(path_a: str, path_b: str) -> float:
    """Return 0-100 similarity score between two files."""
    lines_a = read_file_lines(path_a)
    lines_b = read_file_lines(path_b)
    if not lines_a and not lines_b:
        return 100.0
    if not lines_a or not lines_b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=False).ratio()
    return round(ratio * 100, 1)
