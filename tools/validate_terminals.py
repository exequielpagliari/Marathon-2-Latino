
#!/usr/bin/env python3
"""
Validate Marathon 2 terminal source files.

Terminal structure:

    #TERMINAL <id>
        #UNFINISHED
        #FINISHED
        #FAILURE
        #SUCCESS
        ...
    #ENDTERMINAL <id>

Sections can end in three ways:
- Explicitly with #END.
- Implicitly when another section starts.
- Implicitly when #ENDTERMINAL is reached.

#END is optional.

Formatting codes:
- $B / $b: Bold
- $I / $i: Italic
- $U / $u: Underline
- $C0 through $C7: Text color

Formatting pairs are validated across the entire section,
not independently on each physical line.

Comments begin with ';' when it is the first non-whitespace character.
Text is not interpreted as commands unless it starts with '#'.
"""

from __future__ import annotations

import argparse
import re
import sys

from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Syntax
# ---------------------------------------------------------------------------

TERMINAL_START_RE = re.compile(
    r"^\s*#TERMINAL\s+(\d+)\s*$",
    re.IGNORECASE,
)

TERMINAL_END_RE = re.compile(
    r"^\s*#ENDTERMINAL\s+(\d+)\s*$",
    re.IGNORECASE,
)

SECTION_START_RE = re.compile(
    r"^\s*#(UNFINISHED|FINISHED|FAILURE|SUCCESS)\s*$",
    re.IGNORECASE,
)

END_SECTION_RE = re.compile(
    r"^\s*#END\s*$",
    re.IGNORECASE,
)

COMMAND_RE = re.compile(
    r"^\s*#([A-Za-z][A-Za-z0-9_]*)\b(.*)$",
)

INTEGER_RE = re.compile(r"^[+-]?\d+$")


# Recognized inline formatting tokens.
# C0-C7 are supported color codes.
FORMAT_CODE_RE = re.compile(
    r"\$(?:[BbIiUu]|C[0-7])"
)

# Finds formatting-like tokens, including unknown ones.
FORMAT_CANDIDATE_RE = re.compile(
    r"\$[A-Za-z][0-9]?"
)


# ---------------------------------------------------------------------------
# Marathon 2 / Aleph One terminal commands
# ---------------------------------------------------------------------------

SECTION_COMMANDS = {
    "TERMINAL",
    "ENDTERMINAL",
    "UNFINISHED",
    "FINISHED",
    "FAILURE",
    "SUCCESS",
    "END",
}

KNOWN_COMMANDS = {
    "LOGON",
    "LOGOFF",
    "PICT",
    "INFORMATION",
    "CHECKPOINT",

    # Interactive commands.
    "INTERLEVEL",
    "INTRALEVEL",
    "TAG",
    "SOUND",
    "STATIC",

    # Aleph One / scenario extensions.
    "MOVIE",
    "MUSIC",
    "CAMERA",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Issue:
    path: Path
    line: int
    level: str
    message: str

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.line}: "
            f"{self.level}: {self.message}"
        )


@dataclass
class SectionState:
    name: str
    line: int

    # Formatting counters are maintained across the entire section.
    format_counts: dict[str, int] = field(
        default_factory=lambda: {
            "$B": 0,
            "$b": 0,
            "$I": 0,
            "$i": 0,
            "$U": 0,
            "$u": 0,
        }
    )


@dataclass
class TerminalState:
    number: int
    line: int
    section: SectionState | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_candidate_file(path: Path) -> bool:
    """Return True for text-like terminal source candidates."""

    ignored_names = {
        "README.md",
        "LICENSE",
        "LICENSE.txt",
        ".gitignore",
    }

    ignored_extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".zip",
        ".7z",
        ".map",
        ".scea",
        ".snda",
        ".sndz",
        ".imga",
        ".shpa",
        ".phy",
        ".phya",
    }

    return (
        path.is_file()
        and path.name not in ignored_names
        and path.suffix.lower() not in ignored_extensions
    )


def parse_int(value: str) -> int | None:
    if not INTEGER_RE.fullmatch(value.strip()):
        return None

    return int(value.strip())


# ---------------------------------------------------------------------------
# Inline formatting validation
# ---------------------------------------------------------------------------

def validate_format_codes(
    line: str,
    path: Path,
    line_number: int,
) -> list[Issue]:
    """
    Validate inline formatting tokens.

    This function only detects unknown tokens.
    Pair balancing is handled separately at section scope.
    """

    issues: list[Issue] = []

    for match in FORMAT_CANDIDATE_RE.finditer(line):
        token = match.group(0)

        if not FORMAT_CODE_RE.fullmatch(token):
            issues.append(
                Issue(
                    path,
                    line_number,
                    "WARNING",
                    f"unknown inline formatting code {token!r}",
                )
            )

    return issues


def update_format_counts(
    line: str,
    section: SectionState,
) -> None:
    """
    Accumulate formatting tokens across the entire section.

    A formatting scope may span multiple physical lines.
    """

    for token in section.format_counts:
        section.format_counts[token] += len(
            re.findall(re.escape(token), line)
        )


def validate_section_format_counts(
    section: SectionState,
    path: Path,
) -> list[Issue]:
    """
    Validate formatting pairs when a section finishes.

    The check is section-wide, not line-wide.
    """

    issues: list[Issue] = []

    pairs = (
        ("$B", "$b"),
        ("$I", "$i"),
        ("$U", "$u"),
    )

    for opening, closing in pairs:
        opens = section.format_counts[opening]
        closes = section.format_counts[closing]

        if opens != closes:
            issues.append(
                Issue(
                    path,
                    section.line,
                    "ERROR",
                    (
                        f"unbalanced formatting code in "
                        f"#{section.name}: "
                        f"{opening}={opens}, {closing}={closes}"
                    ),
                )
            )

    return issues


def close_section(
    terminal: TerminalState,
    path: Path,
) -> list[Issue]:
    """
    Validate and close the active section.

    Returns an empty list if no section is active.
    """

    if terminal.section is None:
        return []

    issues = validate_section_format_counts(
        terminal.section,
        path,
    )

    terminal.section = None

    return issues


# ---------------------------------------------------------------------------
# Command validation
# ---------------------------------------------------------------------------

def validate_command(
    command: str,
    arguments: str,
    path: Path,
    line_number: int,
) -> list[Issue]:
    """Validate command name and important argument contracts."""

    issues: list[Issue] = []

    name = command.upper()
    args = arguments.strip()

    if name in SECTION_COMMANDS:
        return issues

    if name not in KNOWN_COMMANDS:
        issues.append(
            Issue(
                path,
                line_number,
                "WARNING",
                f"unknown terminal command #{command}",
            )
        )

        return issues

    # Commands with a numeric resource/id argument.
    numeric_first_argument = {
        "LOGON",
        "LOGOFF",
        "PICT",
        "CHECKPOINT",
        "TAG",
        "SOUND",
        "STATIC",
    }

    if name in numeric_first_argument:
        first = args.split(maxsplit=1)[0] if args else ""

        if parse_int(first) is None:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    f"#{command} requires a numeric argument",
                )
            )

    # #INTERLEVEL TELEPORT <level>
    if name == "INTERLEVEL":
        parts = args.split()

        if len(parts) != 2:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    (
                        "#INTERLEVEL requires: "
                        "#INTERLEVEL TELEPORT <level>"
                    ),
                )
            )

        elif parts[0].upper() != "TELEPORT":
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    (
                        "#INTERLEVEL requires TELEPORT as "
                        "its first argument"
                    ),
                )
            )

        elif parse_int(parts[1]) is None:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    "#INTERLEVEL TELEPORT requires a numeric level",
                )
            )

    # #INTRALEVEL TELEPORT <polygon/tag>
    if name == "INTRALEVEL":
        parts = args.split()

        if len(parts) != 2:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    (
                        "#INTRALEVEL requires: "
                        "#INTRALEVEL TELEPORT <value>"
                    ),
                )
            )

        elif parts[0].upper() != "TELEPORT":
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    (
                        "#INTRALEVEL requires TELEPORT as "
                        "its first argument"
                    ),
                )
            )

        elif parse_int(parts[1]) is None:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    "#INTRALEVEL TELEPORT requires a numeric value",
                )
            )

    # #PICT may optionally have an alignment modifier.
    if name == "PICT":
        parts = args.split()

        if len(parts) > 2:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    (
                        "#PICT accepts a resource id and optionally "
                        "RIGHT or CENTER"
                    ),
                )
            )

        elif len(parts) == 2 and parts[1].upper() not in {
            "RIGHT",
            "CENTER",
        }:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    (
                        f"invalid #PICT alignment {parts[1]!r}; "
                        "expected RIGHT or CENTER"
                    ),
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Parser / state machine
# ---------------------------------------------------------------------------

def validate_terminal_file(path: Path) -> list[Issue]:
    issues: list[Issue] = []

    try:
        text = path.read_text(encoding="utf-8")

    except UnicodeDecodeError as exc:
        return [
            Issue(
                path,
                1,
                "ERROR",
                f"file is not valid UTF-8: {exc}",
            )
        ]

    lines = text.splitlines()

    terminal: TerminalState | None = None

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()

        if not stripped:
            continue

        # Comments: ';' is the first non-whitespace character.
        if stripped.startswith(";"):
            continue

        # ---------------------------------------------------------------
        # #TERMINAL <id>
        # ---------------------------------------------------------------

        terminal_match = TERMINAL_START_RE.match(line)

        if terminal_match:
            number = int(terminal_match.group(1))

            if terminal is not None:
                issues.extend(
                    close_section(terminal, path)
                )

                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        (
                            f"nested #TERMINAL {number}; "
                            f"terminal {terminal.number} is still open"
                        ),
                    )
                )

            else:
                terminal = TerminalState(
                    number=number,
                    line=line_number,
                )

            continue

        # ---------------------------------------------------------------
        # #ENDTERMINAL <id>
        # ---------------------------------------------------------------

        terminal_end_match = TERMINAL_END_RE.match(line)

        if terminal_end_match:
            number = int(terminal_end_match.group(1))

            if terminal is None:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        (
                            f"#ENDTERMINAL {number} "
                            "appears outside a terminal"
                        ),
                    )
                )

                continue

            # The active section ends implicitly here.
            issues.extend(
                close_section(terminal, path)
            )

            if number != terminal.number:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        (
                            f"#ENDTERMINAL {number} does not match "
                            f"#TERMINAL {terminal.number}"
                        ),
                    )
                )

            terminal = None

            continue

        # ---------------------------------------------------------------
        # Section start
        # ---------------------------------------------------------------

        section_match = SECTION_START_RE.match(line)

        if section_match:
            section_name = section_match.group(1).upper()

            if terminal is None:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        (
                            f"section #{section_name} "
                            "appears outside a terminal"
                        ),
                    )
                )

                continue

            # A new section implicitly closes the previous one.
            if terminal.section is not None:
                issues.extend(
                    close_section(terminal, path)
                )

            terminal.section = SectionState(
                name=section_name,
                line=line_number,
            )

            continue

        # ---------------------------------------------------------------
        # Explicit section end: #END
        # ---------------------------------------------------------------

        if END_SECTION_RE.match(line):
            if terminal is None:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        "#END appears outside a terminal",
                    )
                )

            elif terminal.section is None:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        "#END appears without an active section",
                    )
                )

            else:
                issues.extend(
                    close_section(terminal, path)
                )

            continue

        # ---------------------------------------------------------------
        # Any other #command
        # ---------------------------------------------------------------

        command_match = COMMAND_RE.match(line)

        if command_match:
            command = command_match.group(1)
            arguments = command_match.group(2)

            if terminal is None:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        (
                            f"command #{command} "
                            "appears outside a terminal"
                        ),
                    )
                )

            elif terminal.section is None:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        (
                            f"command #{command} appears outside "
                            "a terminal section"
                        ),
                    )
                )

            issues.extend(
                validate_command(
                    command,
                    arguments,
                    path,
                    line_number,
                )
            )

            continue

        # ---------------------------------------------------------------
        # Plain text
        # ---------------------------------------------------------------

        if terminal is None:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    "text appears outside a #TERMINAL block",
                )
            )

            continue

        if terminal.section is None:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    "text appears outside a terminal section",
                )
            )

            continue

        # Detect unknown inline formatting tokens.
        issues.extend(
            validate_format_codes(
                line,
                path,
                line_number,
            )
        )

        # Accumulate formatting tokens for the entire section.
        update_format_counts(
            line,
            terminal.section,
        )

    # -------------------------------------------------------------------
    # EOF recovery
    # -------------------------------------------------------------------

    if terminal is not None:
        # Validate the final section even if it has no explicit #END.
        issues.extend(
            close_section(terminal, path)
        )

        # The terminal itself must still be closed.
        issues.append(
            Issue(
                path,
                terminal.line,
                "ERROR",
                (
                    f"terminal {terminal.number} "
                    "was not closed with #ENDTERMINAL"
                ),
            )
        )

    return issues


# ---------------------------------------------------------------------------
# Repository discovery
# ---------------------------------------------------------------------------

def find_terminal_files(root: Path) -> list[Path]:
    """Discover terminal source files without assuming a fixed filename."""

    search_roots = [
        root / "MapaSeparado",
        root / "MapaUnido",
    ]

    files: list[Path] = []

    for search_root in search_roots:
        if not search_root.exists():
            continue

        for path in search_root.rglob("*"):
            if not is_candidate_file(path):
                continue

            if path.name.lower().endswith(".term.txt"):
                files.append(path)

    return sorted(files)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Marathon 2 terminal source files."
    )

    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="repository root (default: current directory)",
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()

    files = find_terminal_files(root)

    if not files:
        print("ERROR: no *.term.txt files found.")
        return 1

    errors = 0
    warnings = 0

    print(f"Found {len(files)} terminal files.")
    print()

    for path in files:
        issues = validate_terminal_file(path)

        relative = path.relative_to(root)

        file_errors = sum(
            issue.level == "ERROR"
            for issue in issues
        )

        file_warnings = sum(
            issue.level == "WARNING"
            for issue in issues
        )

        if file_errors:
            status = "FAIL"

        elif file_warnings:
            status = "WARN"

        else:
            status = "PASS"

        print(f"{status:<5} {relative}")

        for issue in issues:
            print(f"      {issue}")

        errors += file_errors
        warnings += file_warnings

    print()
    print("========================================")
    print("Marathon 2 terminal validation summary")
    print("========================================")
    print(f"Files:    {len(files)}")
    print(f"Errors:   {errors}")
    print(f"Warnings: {warnings}")

    if errors:
        print()
        print("VALIDATION FAILED")
        return 1

    print()
    print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
