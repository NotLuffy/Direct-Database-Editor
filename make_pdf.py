"""
Generates CNC_Direct_Editor_HowTo.pdf using ReportLab.
Run: python make_pdf.py
"""

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# ── Color palette (matches the app's dark theme → print-friendly equivalents)
C_DARK   = colors.HexColor("#1a1d2e")   # navy — headings
C_BLUE   = colors.HexColor("#5577cc")   # mid blue — section bars
C_GREEN  = colors.HexColor("#2a7a44")   # green — PASS / positive
C_AMBER  = colors.HexColor("#cc8800")   # amber — caution
C_RED    = colors.HexColor("#aa2222")   # red — FAIL / delete
C_GRAY   = colors.HexColor("#555555")   # body text
C_LGRAY  = colors.HexColor("#eeeeee")   # table alt row
C_WHITE  = colors.white

OUT = "CNC_Direct_Editor_HowTo.pdf"

doc = SimpleDocTemplate(
    OUT,
    pagesize=letter,
    leftMargin=0.75 * inch,
    rightMargin=0.75 * inch,
    topMargin=0.75 * inch,
    bottomMargin=0.75 * inch,
    title="CNC Direct Editor — How To Use",
    author="HAAS Tools",
)

ss = getSampleStyleSheet()

# ── Custom styles ────────────────────────────────────────────────────────────
def style(name, **kw):
    return ParagraphStyle(name, **kw)

S_TITLE  = style("Title2",  fontSize=22, textColor=C_DARK,
                 fontName="Helvetica-Bold", spaceAfter=4, leading=26)
S_SUB    = style("Sub",     fontSize=10, textColor=C_GRAY,
                 fontName="Helvetica", spaceAfter=12)
S_H1     = style("H1",      fontSize=13, textColor=C_WHITE,
                 fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=6,
                 backColor=C_BLUE, leftIndent=-6, rightIndent=-6,
                 borderPad=(4, 6, 4, 6))
S_H2     = style("H2",      fontSize=11, textColor=C_DARK,
                 fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=4)
S_BODY   = style("Body2",   fontSize=9.5, textColor=C_GRAY,
                 fontName="Helvetica", leading=14, spaceAfter=4)
S_BULLET = style("Bullet2", fontSize=9.5, textColor=C_GRAY,
                 fontName="Helvetica", leading=14, spaceAfter=3,
                 leftIndent=14, bulletIndent=4)
S_NOTE   = style("Note",    fontSize=8.5, textColor=C_AMBER,
                 fontName="Helvetica-Oblique", leading=13,
                 borderPad=4, borderColor=C_AMBER, borderWidth=0.5,
                 spaceAfter=6, spaceBefore=4)
S_CODE   = style("Code2",   fontSize=8.5, textColor=C_DARK,
                 fontName="Courier", leading=13,
                 backColor=colors.HexColor("#f0f0f0"),
                 leftIndent=12, spaceBefore=2, spaceAfter=6)

def h1(text):
    return Paragraph(f"&nbsp; {text}", S_H1)

def h2(text):
    return Paragraph(text, S_H2)

def p(text):
    return Paragraph(text, S_BODY)

def b(text):
    return Paragraph(f"• &nbsp; {text}", S_BULLET)

def note(text):
    return Paragraph(f"⚠ {text}", S_NOTE)

def sp(n=6):
    return Spacer(1, n)

def hr():
    return HRFlowable(width="100%", thickness=0.5,
                      color=colors.HexColor("#cccccc"), spaceAfter=6)

def table(data, col_widths, header_row=True):
    style_cmds = [
        ("FONTNAME",    (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 0), (-1, -1), 9),
        ("TEXTCOLOR",   (0, 0), (-1, -1), C_GRAY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_LGRAY]),
        ("GRID",        (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    if header_row:
        style_cmds += [
            ("BACKGROUND",  (0, 0), (-1, 0), C_DARK),
            ("TEXTCOLOR",   (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    return Table(data, colWidths=col_widths,
                 style=TableStyle(style_cmds))

# ─────────────────────────────────────────────────────────────────────────────
# Content
# ─────────────────────────────────────────────────────────────────────────────

W = 7.0 * inch   # usable width

story = []

# ── Cover ────────────────────────────────────────────────────────────────────
story += [
    sp(30),
    Paragraph("CNC Direct Editor", S_TITLE),
    Paragraph("Quick-Start Guide", style("QS", fontSize=15,
              textColor=C_BLUE, fontName="Helvetica", spaceAfter=8)),
    p("This guide covers everything a machinist needs to get up and running "
      "with the CNC Direct Editor — scanning your program library, reading "
      "the table, editing files, searching by order, and understanding "
      "verification results."),
    sp(6),
    hr(),
]

# ── 1. Getting Started ───────────────────────────────────────────────────────
story += [
    h1("1 — Getting Started"),
    h2("Adding Your Program Folder"),
    p("When you first open the app the table will be empty. Click "
      "<b>Add Folder</b> in the toolbar and select the folder that contains "
      "your G-code files (e.g., your network share or local repository)."),
    p("You can add more than one folder. All folders are scanned together "
      "and their files appear in the same table."),
    sp(),
    h2("Scanning"),
    p("After adding a folder, click <b>Rescan</b>. The app walks every file "
      "in the folder, reads the program title and O-number from the header, "
      "hashes the file, detects duplicates, and updates the database."),
    p("A progress bar tracks the scan. When it finishes you will see a "
      "summary of how many files were found, changed, or newly imported."),
    note("Never close the app or cut power during a scan — this can leave "
         "the database in an incomplete state."),
    sp(),
    h2("Re-Verify All"),
    p("After a scan, click <b>Re-Verify All</b> to run the verification "
      "checks on every file and update their scores. This can take a few "
      "minutes for a large library."),
]

# ── 2. The File Table ─────────────────────────────────────────────────────────
story += [
    h1("2 — The File Table"),
    p("Each row is one G-code file. The columns are:"),
    sp(4),
    table([
        ["Column",       "What It Shows"],
        ["O-Number",     "The program number (e.g. O70189)"],
        ["File Name",    "The filename on disk"],
        ["Score",        "Verification score out of 7  (see Section 4)"],
        ["Lines",        "Number of non-blank lines in the file"],
        ["Status",       "Current status — see status colours below"],
        ["Type",         "Part type derived from the title (STD, HC, 2PC, STEP, STEEL…)"],
        ["Title",        "Program title extracted from the G-code header"],
        ["Folder",       "Which source folder the file lives in"],
        ["Dup",          "[DUP] shown if the file is part of a duplicate group"],
        ["Notes",        "First 80 characters of your notes"],
        ["Verify",       "Individual check tokens (CB:PASS, DR:FAIL, etc.)"],
    ], [1.1*inch, 5.6*inch]),
    sp(8),
    h2("Row Colours by Status"),
    table([
        ["Colour",  "Status",       "Meaning"],
        ["Dark",    "ACTIVE",       "Normal — file is in good standing"],
        ["Amber",   "FLAGGED",      "Needs attention — review before use"],
        ["Green",   "REVIEW",       "Marked for human review after scan"],
        ["Red",     "DELETE",       "Marked for deletion (not yet removed)"],
        ["Blue",    "SHOP SPECIAL", "Protected — auto-resolve will never touch this file"],
    ], [0.8*inch, 1.3*inch, 4.6*inch]),
    sp(8),
    h2("Sorting and Filtering"),
    b("Click any column header to sort ascending; click again for descending."),
    b("Use the <b>Filters</b> button to filter by round size, CB, OB, "
      "thickness, hub, score range, or part type."),
    b("Click a status in the left sidebar (e.g. FLAGGED) to show only "
      "files with that status."),
    b("Type in the search bar at the top to search across O-number, "
      "filename, and title simultaneously."),
]

# ── 3. File Actions ───────────────────────────────────────────────────────────
story += [
    h1("3 — File Actions (Right-Click Menu)"),
    p("Right-click any row (or select multiple rows then right-click) "
      "to see available actions:"),
    sp(4),
    table([
        ["Action",           "What It Does"],
        ["Edit",             "Open file in the built-in G-code editor"],
        ["Re-Verify",        "Re-run verification checks on the selected file(s)"],
        ["Set Status",       "Change status: Active / Flagged / Review / Delete / Shop Special"],
        ["View Duplicates",  "Show all duplicate groups this file belongs to"],
        ["View Diff",        "Compare two selected files side-by-side"],
        ["Add / Edit Notes", "Add a note that appears in the Notes column"],
        ["Override Check",   "Force a single verification check to PASS or FAIL"],
    ], [1.6*inch, 5.1*inch]),
    sp(8),
    note("Marking a file as Delete does NOT remove it from disk. "
         "Use the Empty Trash toolbar button to permanently delete all "
         "files with Delete status — you will be asked to confirm first."),
]

# ── 4. Verification Scores ────────────────────────────────────────────────────
story += [
    h1("4 — Verification Scores"),
    p("Each file is scored out of 7. The score counts how many of the "
      "following checks pass:"),
    sp(4),
    table([
        ["Token", "Check",              "What Is Verified"],
        ["CB",    "Center Bore",        "Bore diameter matches the title spec (±0.001\")"],
        ["OB",    "Outer Bore / Hub",   "Hub bore diameter matches the title spec"],
        ["DR",    "Drill Depth",        "G81/G83 depth matches disc + hub thickness"],
        ["OD",    "OD Turn",            "Outside diameter matches the round size table"],
        ["TZ",    "Turning Z-Depth",    "T303 Z-depth does not exceed the safe limit"],
        ["PC",    "P-Code",             "G154 P-codes match the correct lathe registration"],
        ["HM",    "Home Position",      "G53 home Z is correct for the part thickness"],
    ], [0.55*inch, 1.4*inch, 4.75*inch]),
    sp(8),
    p("Score colours in the table:"),
    table([
        ["Score", "Colour",  "Interpretation"],
        ["7",     "Green",   "All checks pass — program is verified correct"],
        ["5–6",   "Yellow",  "Minor issue — review the failing check"],
        ["3–4",   "Orange",  "Multiple issues — do not run without review"],
        ["0–2",   "Red",     "Significant problems — program needs correction"],
    ], [0.7*inch, 1.0*inch, 5.0*inch]),
    sp(6),
    p("Each token in the Verify column shows its individual result:"),
    b("<b>PASS</b> — check passed within tolerance"),
    b("<b>FAIL</b> — check failed — value outside tolerance"),
    b("<b>N/F</b>  — check not applicable for this part type"),
    b("<b>PASS*</b> — manually overridden to PASS by operator"),
]

# ── 5. Editing Files ──────────────────────────────────────────────────────────
story += [
    h1("5 — Editing G-Code Files"),
    p("Right-click a file and choose <b>Edit</b> to open it in the "
      "built-in editor. The editor shows:"),
    b("Syntax highlighting for G/M-codes, coordinates, comments, and "
      "O-numbers."),
    b("Line numbers on the left (not selectable — Ctrl+A only copies "
      "the program text)."),
    b("Red/orange line highlights showing exactly which lines have "
      "verification issues."),
    b("A <b>Verify Results panel</b> on the right showing all 7 checks "
      "with found vs. expected values and a <b>Go to line</b> button "
      "for each."),
    sp(6),
    h2("Saving"),
    p("Click <b>Save + Verify</b> to save the file and immediately "
      "re-run all checks so you can see the updated results side-by-side. "
      "Or click <b>Save</b> to save without re-verifying."),
    p("A backup of the original file is created automatically before "
      "every save. On first edit you will be asked to choose a backup "
      "folder — this only happens once."),
    note("If you close the editor with unsaved changes the app will ask "
         "whether to discard them. Changes are NOT auto-saved."),
    sp(6),
    h2("Save as Revision"),
    p("Use <b>Save as Rev…</b> to snapshot the current file with a label "
      "(e.g. \"Before drill change\") without overwriting the working copy. "
      "Revisions are stored in the backup folder and listed in the "
      "Bug Reports tab."),
]

# ── 6. Order Search ───────────────────────────────────────────────────────────
story += [
    h1("6 — Order Sheet Search"),
    p("Click <b>Order Search</b> in the toolbar to open the search panel. "
      "Copy cells I through M from your order sheet and paste them directly "
      "into the paste box. The app will find matching programs in your library."),
    sp(4),
    h2("What Each Column Means"),
    table([
        ["Column", "Contains",      "Example"],
        ["I",      "Round size",    "7   or   9.5"],
        ["J",      "Bolt pattern",  "5550-5450-A   (H suffix = hub,  SR = steel ring,  2PC = two-piece)"],
        ["K",      "CB in mm",      "87.1   or   110/74 (.40 DEEP STEP)  for step parts"],
        ["L",      "OB / hub bore", "142   (leave blank if no hub)"],
        ["M",      "Thickness",     '1.00"  or  1.75"+.50"HUB  or  1.25" (20mm+20mm)'],
    ], [0.45*inch, 1.15*inch, 5.1*inch]),
    sp(8),
    h2("Reading the Results"),
    b("Results are scored 0–100%.  <b>Green ≥ 80%</b> is an exact match."),
    b("Each result shows which fields matched (Round ✓  CB ✓  Disc ✓)."),
    b("For <b>2-piece</b> orders the app finds matching pairs — "
      "a ring file and a hub/bell file — and shows them grouped together."),
    b("Double-click any result to jump straight to that file in the "
      "main table."),
    note("The order search scans your entire library regardless of active "
         "filters. You do not need to clear filters first."),
]

# ── 7. Tools ──────────────────────────────────────────────────────────────────
story += [
    h1("7 — Toolbar Tools"),
    table([
        ["Button",           "What It Does"],
        ["Add Folder",       "Add a folder to the workspace — files in it appear in the table"],
        ["Rescan",           "Re-scan all folders for new, changed, or removed files"],
        ["Import New",       "Import new files from a staging folder into the library"],
        ["Re-Verify All",    "Re-run verification on every file in the database"],
        ["Export XLSX",      "Export the full file list to an Excel spreadsheet"],
        ["Export Files",     "Copy a filtered set of files to another folder"],
        ["Daily Report",     "Generate an Excel report of files created on a chosen date"],
        ["2PC Match",        "Find and pair up 2-piece program files"],
        ["New File",         "Create a new G-code file from a template"],
        ["Filters",          "Show/hide the filter bar (round, CB, OB, thickness, score, type)"],
        ["Order Search",     "Search by pasting a row from your order sheet"],
        ["Settings",         "Configure backup folder, verify limits, and other options"],
        ["Report Bug",       "Submit a bug report — saved to the database for review"],
        ["Bug Reports",      "View, update, and export all submitted bug reports"],
        ["Empty Trash",      "Permanently delete all files marked with Delete status"],
    ], [1.5*inch, 5.2*inch]),
]

# ── 8. Tips ───────────────────────────────────────────────────────────────────
story += [
    h1("8 — Tips and Common Questions"),
    sp(4),
    h2("Why does a file show Score 0 or no verify tokens?"),
    p("The verifier needs a parseable title to know what to check. "
      "If the G-code header has no title (or the title format is not "
      "recognised), verification is skipped. Check that the first line "
      "of the file follows the format:  O12345 (ROUND CB/OB THK)"),
    sp(4),
    h2("A file I know is correct is showing FAIL — what do I do?"),
    p("Right-click the file → <b>Override Check</b> → choose the token "
      "and set it to PASS. This stamps a note on the file and marks the "
      "token as PASS* (operator override). The override survives re-verifies."),
    sp(4),
    h2("How do I find all HC programs for a specific round size?"),
    p("Click <b>Filters</b>, set Round Size to the size you want, "
      "then set Part Type to <b>HC — any</b>. The table will show only "
      "matching files."),
    sp(4),
    h2("How do I compare two versions of the same program?"),
    p("Hold <b>Ctrl</b> and click two rows to select them both. The app "
      "will automatically open a side-by-side diff view at the bottom "
      "of the screen showing every changed line highlighted."),
    sp(4),
    h2("The scan marked files as REVIEW — what does that mean?"),
    p("REVIEW means the scanner found two files with the same O-number "
      "but different content, or the same program title on different "
      "O-numbers. You need to decide which file to keep. Use the "
      "Duplicates tab at the bottom to compare them and keep the correct "
      "version."),
    sp(4),
    h2("Can I undo a status change?"),
    p("Yes — right-click the file, choose <b>Set Status</b>, and pick "
      "a different status. Status changes are instant but fully reversible "
      "until you run Empty Trash, which is permanent."),
    sp(4),
    note("The app saves your window layout, column widths, and open "
         "workspace automatically when you close it. Everything will be "
         "exactly as you left it next time you open it."),
]

# ── Footer note ───────────────────────────────────────────────────────────────
story += [
    sp(20),
    hr(),
    Paragraph("CNC Direct Editor  •  HAAS Tools  •  For internal use",
              style("Footer", fontSize=8, textColor=colors.HexColor("#aaaaaa"),
                    fontName="Helvetica", alignment=TA_CENTER)),
]

# ── Build ─────────────────────────────────────────────────────────────────────
doc.build(story)
print(f"Created: {OUT}")
