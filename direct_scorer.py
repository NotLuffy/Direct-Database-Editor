"""
CNC Direct Editor — Verification scorer.

Calls verifier.verify_file() and counts how many of the 7 key checks PASS.
NF (None) counts as 0 — not a pass, not a penalty.
Returns (score: int 0-7, verify_status_string: str).
"""

import re
import verifier as _vfy

# The 7 scored result keys from verifier.verify_file()
_SCORE_KEYS = ["cb_ok", "ob_ok", "dr_ok", "od_ok", "tz_ok", "pcode_ok", "home_ok"]

# Token labels for each key (used to build verify_status string)
_TOKEN_LABELS = {
    "cb_ok":     ("CB",  "CB:PASS",  "CB:FAIL",  "CB:NF"),
    "ob_ok":     ("OB",  "OB:PASS",  "OB:FAIL",  "OB:NF"),
    "dr_ok":     ("DR",  "DR:PASS",  "DR:FAIL",  "DR:NF"),
    "od_ok":     ("OD",  "OD:PASS",  "OD:FAIL",  "OD:NF"),
    "tz_ok":     ("TZ",  "TZ:PASS",  "TZ:FAIL",  "TZ:NF"),
    "pcode_ok":  ("PC",  "PC:PASS",  "PC:FAIL",  "PC:NF"),
    "home_ok":   ("HM",  "HM:PASS",  "HM:FAIL",  "HM:NF"),
}

# --- Override helpers ---

_OVERRIDE_RE = re.compile(r'\[OVERRIDE:([^\]]+)\]')
# Map 2-letter token prefix to result key
_TOKEN_TO_KEY = {v[0]: k for k, v in _TOKEN_LABELS.items()}


def parse_overrides(notes: str) -> dict:
    """Parse override tokens from notes string.
    Returns dict like {"cb_ok": True, "od_ok": False} or empty dict."""
    if not notes:
        return {}
    m = _OVERRIDE_RE.search(notes)
    if not m:
        return {}
    overrides = {}
    for pair in m.group(1).split(","):
        parts = pair.strip().split("=")
        if len(parts) == 2:
            token, value = parts[0].strip().upper(), parts[1].strip().upper()
            key = _TOKEN_TO_KEY.get(token)
            if key:
                overrides[key] = (value == "PASS")
    return overrides


def set_override_in_notes(notes: str, check_token: str, value: str) -> str:
    """Add or update an override in the notes string.
    check_token: 2-letter code like 'CB', 'OD', etc.
    value: 'PASS' or 'FAIL'
    Returns updated notes string."""
    notes = notes or ""
    m = _OVERRIDE_RE.search(notes)
    if m:
        # Parse existing overrides, update/add the new one
        existing = {}
        for pair in m.group(1).split(","):
            parts = pair.strip().split("=")
            if len(parts) == 2:
                existing[parts[0].strip().upper()] = parts[1].strip().upper()
        existing[check_token.upper()] = value.upper()
        token_str = ",".join(f"{k}={v}" for k, v in sorted(existing.items()))
        return notes[:m.start()] + f"[OVERRIDE:{token_str}]" + notes[m.end():]
    else:
        token_str = f"[OVERRIDE:{check_token.upper()}={value.upper()}]"
        return (notes + " " + token_str).strip()


def clear_overrides_in_notes(notes: str) -> str:
    """Remove the [OVERRIDE:...] token from notes."""
    if not notes:
        return ""
    return _OVERRIDE_RE.sub("", notes).strip()


def apply_overrides_to_status(verify_status: str, overrides: dict) -> tuple[int, str]:
    """Apply overrides to a verify_status string.
    Returns (new_score, new_status_string) with * marking overridden tokens."""
    if not verify_status or not overrides:
        return score_from_verify_status(verify_status), verify_status
    tokens = verify_status.split()
    new_tokens = []
    score = 0
    for tok in tokens:
        if ":" not in tok:
            new_tokens.append(tok)
            continue
        # Strip existing * marker
        clean = tok.rstrip("*")
        prefix = clean.split(":")[0]
        key = _TOKEN_TO_KEY.get(prefix)
        if key and key in overrides:
            val = overrides[key]
            new_tok = f"{prefix}:{'PASS' if val else 'FAIL'}*"
            new_tokens.append(new_tok)
            if val:
                score += 1
        else:
            new_tokens.append(tok)
            if tok.rstrip("*").endswith(":PASS"):
                score += 1
    return score, " ".join(new_tokens)


def score_file(file_path: str, program_title: str,
               o_number: str = "") -> tuple[int, str]:
    """
    Run verification on file_path and return (score, verify_status_string).

    score: 0-7 (count of True values across the 7 scored keys)
    verify_status_string: e.g. "CB:PASS OB:NF DR:PASS OD:FAIL PC:PASS HM:PASS"
    Returns (0, "") on any error (file unreadable, title unparseable, etc.)
    """
    try:
        result = _vfy.verify_file(file_path, program_title, o_number=o_number or None)
    except Exception:
        return 0, ""

    if not result:
        return 0, ""

    score = 0
    tokens = []
    for key in _SCORE_KEYS:
        _, pass_tok, fail_tok, nf_tok = _TOKEN_LABELS[key]
        val = result.get(key)
        if val is True:
            score += 1
            tokens.append(pass_tok)
        elif val is False:
            tokens.append(fail_tok)
        else:
            tokens.append(nf_tok)

    # 2PC dimension tokens — appended when detected (not scored, just for pairing)
    rc     = result.get("recess_x_in")
    hb     = result.get("hub_od_in")
    hb_var = result.get("hub_is_variable", False)
    # Hub height for Piece B: prefer G-code-detected implicit hub;
    # fall back to HC height from title (covers HC --2PC files where
    # implicit_hub_in is cleared because hc_height_in is already set,
    # and also files where G-code hub detection found nothing).
    ih = result.get("implicit_hub_in")
    if ih is None:
        import re as _re
        if _re.search(r'-*2\s*PC\b', program_title, _re.IGNORECASE):
            ih = (result.get("specs") or {}).get("hc_height_in")
    if rc is not None:
        tokens.append(f"RC:{rc:.3f}\"")
    if hb is not None:
        tokens.append(f"HB:{hb:.3f}\"{('?' if hb_var else '')}")
    if ih is not None:
        tokens.append(f"IH:{ih:.3f}\"")

    return score, " ".join(tokens)


def get_error_lines(file_path: str, program_title: str,
                    o_number: str = "") -> dict:
    """
    Return {line_no_1based: tooltip_str} for every line that has a verification
    issue.  Used by EditorPanel to highlight problematic lines.

    Context-window lines (CB/OB/DR/OD/HM) are highlighted only when their
    check is actually FAIL (not NF).  Direct violation lines (feed rate,
    Z-depth, tool-home) are always highlighted.
    """
    try:
        result = _vfy.verify_file(file_path, program_title,
                                  o_number=o_number or None)
    except Exception:
        return {}
    if not result:
        return {}

    issues: dict = {}   # {1-based line_no: tooltip}

    def _add_ctx(ctx_list, label, ok):
        """Add context lines only when the check is explicitly failing."""
        if ok is False and ctx_list:
            for line_no, _ in ctx_list:
                issues.setdefault(line_no, label)

    _add_ctx(result.get("cb_context",      []), "CB: center-bore value",     result.get("cb_ok"))
    _add_ctx(result.get("ob_context",      []), "OB: outer-bore value",      result.get("ob_ok"))
    _add_ctx(result.get("dr_context",      []), "DR: drill depth",           result.get("dr_ok"))
    _add_ctx(result.get("od_op1_context",  []), "OD: OD turn (OP1)",         result.get("od_ok"))
    _add_ctx(result.get("od_op2_context",  []), "OD: OD turn (OP2)",         result.get("od_ok"))
    _add_ctx(result.get("tz_context",      []), "TZ: turning Z-depth",       result.get("tz_ok"))
    _add_ctx(result.get("th_context",      []), "HM: home position",         result.get("home_ok"))

    # Direct violation lines — always flag regardless of ok status
    for line_idx, fval in result.get("fr_violations", []):
        issues[line_idx + 1] = f"FR: feed rate {fval} may be too high"

    # CB finish feed rate violation
    if result.get("cb_f_ok") is False:
        _add_ctx(result.get("cb_f_context", []),
                 f"CB Feed: F{result.get('cb_f_found', '?')} should be F{result.get('cb_f_expected', 0.015)}",
                 False)

    for line_idx, zval in result.get("z_deep_violations", []):
        issues[line_idx + 1] = f"Z: depth {zval:.4f} exceeds limit"

    for line_idx, desc in result.get("th_violations", []):
        issues.setdefault(line_idx + 1, f"HM: {desc}")

    # Rough bore deep-pass violations: bore pass at X>6.8 not getting shallower
    for viol in result.get("rb_deep_violations", []):
        x_prev, z_prev, x_curr, z_curr, ln_1based = viol
        issues.setdefault(
            ln_1based,
            f"RB: X{x_curr:.3f} bore pass Z{z_curr:.4f} not shallower than"
            f" previous Z{z_prev:.4f} (passes beyond X6.8 must decrease in depth)",
        )

    return issues


def score_from_verify_status(verify_status: str) -> int:
    """Re-compute score from a stored verify_status string without re-reading the file."""
    if not verify_status:
        return 0
    return sum(1 for tok in verify_status.split() if tok.rstrip("*").endswith(":PASS"))
