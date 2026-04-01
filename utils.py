"""
Utility functions: O-number parsing, rename logic, letter suffix generation,
and program title extraction/comparison.
"""

import re
import string
import difflib

# ---------------------------------------------------------------------------
# O-number ranges by round size (inch).  Format: {round_size_in: (lo, hi)}
# Both lo/hi are the integer part of the O-number (e.g. 57000 for O57000).
# ---------------------------------------------------------------------------
O_NUMBER_RANGES: dict = {
    5.75:  (57000, 59999),
    6.00:  (60000, 62499),
    6.25:  (62500, 64999),
    6.50:  (65000, 69999),
    7.00:  (70000, 74999),
    7.50:  (75000, 79999),
    8.00:  (80000, 84999),
    8.50:  (85000, 89999),
    9.50:  (95000, 99999),
    10.25: (10000, 10499),
    10.50: (10500, 10999),
    13.00: (13000, 13999),
}


def get_o_number_range(round_size_in: float):
    """Return (lo, hi) inclusive integer range for a round size, or None (free range)."""
    for rs, rng in O_NUMBER_RANGES.items():
        if abs(rs - round_size_in) < 0.01:
            return rng
    return None


def o_number_int(o_number: str):
    """Convert 'O57286' → 57286, or None if invalid."""
    try:
        return int(o_number[1:])
    except (ValueError, IndexError, TypeError):
        return None


def o_number_in_correct_range(o_number: str, round_size_in: float) -> bool:
    """Return True if o_number integer falls within the expected range."""
    rng = get_o_number_range(round_size_in)
    if rng is None:
        return True  # free range — always OK
    n = o_number_int(o_number)
    if n is None:
        return False
    return rng[0] <= n <= rng[1]


def find_in_range(lo: int, hi: int, used: set) -> int | None:
    """Return the lowest integer in [lo, hi] not in used, or None if full."""
    for n in range(lo, hi + 1):
        if n not in used:
            return n
    return None


def format_o_number(n: int) -> str:
    """Format an integer as a 5-digit O-number string: 57000 → 'O57000'."""
    return f"O{n:05d}"


# ---------------------------------------------------------------------------
# Exempt-from-rerange detection
# Special programs (fixtures, tooling, adapters) that don't follow the
# round-size O-number convention and should never be auto-reranged.
# ---------------------------------------------------------------------------
_EXEMPT_KEYWORDS = [
    "center holder",
    "spike nut",
    "part holder",
    "fitting ring",
    "lift pad",
    "remove hc",
    "move hc",
    "remove/move",
    "adapter",
    "fixture",
    "tooling",
]

# Matches size-conversion titles like "13 to 8.5", "8.5 to 6", "6.5 TO 5.75"
_CONVERSION_RE = re.compile(r'\d+(?:\.\d+)?\s+to\s+\d+(?:\.\d+)?', re.IGNORECASE)


def is_exempt_from_rerange(title: str) -> bool:
    """
    Return True if the program title indicates a non-disc special program
    (fixture, tooling, adapter, size-conversion) that should keep its
    O-number regardless of which range it falls in.
    """
    if not title:
        return False
    t = title.lower()
    for kw in _EXEMPT_KEYWORDS:
        if kw in t:
            return True
    return bool(_CONVERSION_RE.search(t))


# Matches O-number filenames: O12345, O12345.nc, O12345_1, O12345_1.txt, etc.
O_NUMBER_PATTERN = re.compile(
    r'^(O\d{4,6})(?:_(\d+))?(\.[^.]+)?$',
    re.IGNORECASE
)


def parse_o_number(filename: str) -> dict | None:
    """
    Parse a filename into its components.

    Returns dict with keys:
        o_number   - "O12345" (normalized uppercase)
        o_suffix   - "1" (backup suffix number) or None
        extension  - ".nc" or None
        base_name  - "O12345_1" (without extension)

    Returns None if filename does not match the O-number pattern.
    """
    match = O_NUMBER_PATTERN.match(filename)
    if not match:
        return None

    o_num = match.group(1).upper()
    o_suffix = match.group(2)  # may be None
    extension = match.group(3)  # may be None

    base_name = o_num
    if o_suffix:
        base_name = f"{o_num}_{o_suffix}"

    return {
        "o_number": o_num,
        "o_suffix": o_suffix,
        "extension": extension if extension else "",
        "base_name": base_name,
    }


def generate_letter_suffixes():
    """
    Generate letter suffixes in order: A, B, C, ... Z, AA, AB, ... ZZ
    Used to disambiguate same-named files with different content.
    """
    letters = string.ascii_uppercase
    # Single letters
    for c in letters:
        yield c
    # Two-letter combinations
    for c1 in letters:
        for c2 in letters:
            yield c1 + c2


# ---------------------------------------------------------------------------
# Program title extraction (from inside the G-code file)
# ---------------------------------------------------------------------------

# Matches the first O-number line: O12345 (TITLE TEXT)
_TITLE_LINE_RE = re.compile(r'^O\d{4,6}\s*\(([^)]*)\)', re.IGNORECASE)

# Extract all numeric values (including decimals) from a string
_NUMERALS_RE = re.compile(r'\d+\.?\d*')


def extract_program_title(file_path: str) -> str:
    """
    Read the first meaningful line of a G-code file and return the
    program title found in the comment parentheses, e.g.:
        O73308 (7 IN 106.1/95MM 3.0 HC)  ->  "7 IN 106.1/95MM 3.0 HC"

    Returns empty string if no title comment is found.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line == "%":
                    continue
                m = _TITLE_LINE_RE.match(line)
                if m:
                    return m.group(1).strip()
                # First non-empty, non-% line checked — stop regardless
                break
    except Exception:
        pass
    return ""


def extract_title_numerals(title: str) -> list:
    """
    Extract all numeric values from a title string in order, as floats.
    E.g. "7 IN 106.1/95MM 3.0 HC"  ->  [7.0, 106.1, 95.0, 3.0]
    """
    return [float(n) for n in _NUMERALS_RE.findall(title)]


def title_similarity(title_a: str, title_b: str) -> str:
    """
    Compare two program titles and return a similarity category:
        'identical'      - exact match (case-insensitive, stripped)
        'same_numerics'  - all numeric values match in the same order
        'similar'        - some numerics match or string similarity > 70%
        'different'      - clearly different part specs

    Used to distinguish "same part, minor edit" from "different part, O-number conflict".
    """
    if not title_a and not title_b:
        return "identical"
    if not title_a or not title_b:
        return "different"

    # Exact match (normalized)
    if title_a.strip().upper() == title_b.strip().upper():
        return "identical"

    nums_a = extract_title_numerals(title_a)
    nums_b = extract_title_numerals(title_b)

    # If both have numbers and they all match in order (within tolerance)
    if nums_a and nums_b and len(nums_a) == len(nums_b):
        if all(abs(a - b) < 0.01 for a, b in zip(nums_a, nums_b)):
            return "same_numerics"

    # String similarity ratio
    ratio = difflib.SequenceMatcher(
        None, title_a.upper(), title_b.upper()
    ).ratio()
    if ratio >= 0.70:
        return "similar"

    # Partial numeric overlap — if first number matches (same diameter)
    if nums_a and nums_b and abs(nums_a[0] - nums_b[0]) < 0.01:
        return "similar"

    return "different"


def build_working_name(o_number: str, o_suffix: str | None, letter_suffix: str | None, extension: str) -> str:
    """
    Construct the working filename from components.

    Examples:
        O12345, None, None, ""      -> "O12345"
        O12345, None, None, ".nc"   -> "O12345.nc"
        O12345, "1", None, ""       -> "O12345_1"
        O12345, None, "A", ""       -> "O12345_A"
        O12345, "1", "A", ""        -> "O12345_1_A"
    """
    name = o_number
    if o_suffix:
        name += f"_{o_suffix}"
    if letter_suffix:
        name += f"_{letter_suffix}"
    if extension:
        name += extension
    return name
