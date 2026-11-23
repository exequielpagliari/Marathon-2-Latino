#!/usr/bin/env python3
"""
Validate Marathon 2 split terminal source files.

Structure supported by this validator:

    document       := terminal*
    terminal       := '#TERMINAL' ID section* '#ENDTERMINAL' ID
    section        := section_header content*
    section_header := '#UNFINISHED' | '#FINISHED' | '#FAILURE' | '#SUCCESS'

A section ends implicitly when another section header appears or when
#ENDTERMINAL is reached. #END is accepted as an optional explicit section
separator, but is not required by the split terminal format.

Comments begin with ';' when it is the first non-whitespace character.
Unknown terminal commands and unknown inline formatting codes are warnings.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
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

# Known inline formatting tokens. C-colour codes are treated as a complete
# token (e.g. $C2), never as a standalone "$C" prefix.
FORMAT_CODE_RE = re.compile(
    r"\$(?:[BbIiUu]|C[0-7])"
)
FORMAT_LIKE_TOKEN_RE = re.compile(
    r"\$(?:C\d|[A-Za-z])"
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
    "INTERLEVEL",
    "INTRALEVEL",
    "TAG",
    "SOUND",
    "STATIC",
    # Recognized extensions / commands found in scenario data.
    "MOVIE",
    "MUSIC",
    "CAMERA",
}


@dataclass(frozen=True)
class Issue:
    path: Path
    line: int
    level: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.level}: {self.message}"


@dataclass
class SectionState:
    name: str
    line: int


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
        ".png", ".jpg", ".jpeg", ".gif", ".bmp",
        ".tif", ".tiff", ".zip", ".7z", ".map",
        ".scea", ".snda", ".sndz", ".imga",
        ".shpa", ".phy", ".phya",
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


def validate_format_codes(
    line: str,
    path: Path,
    line_number: int,
) -> list[Issue]:
    """Validate recognized inline formatting tokens without false $C alerts."""

    issues: list[Issue] = []

    for match in FORMAT_LIKE_TOKEN_RE.finditer(line):
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

    # These formatting controls are expected to be paired on each line.
    # Colour controls are not paired.
    pairs = {
        "$B": "$b",
        "$I": "$i",
        "$U": "$u",
    }

    for opening, closing in pairs.items():
        opens = line.count(opening)
        closes = line.count(closing)

        if opens != closes:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    (
                        "unbalanced formatting code: "
                        f"{opening}={opens}, {closing}={closes}"
                    ),
                )
            )

    return issues


def validate_command(
    command: str,
    arguments: str,
    path: Path,
    line_number: int,
) -> list[Issue]:
    """Validate command names and selected argument contracts."""

    issues: list[Issue] = []
    name = command.upper()
    args = arguments.strip()

    if name in SECTION_COMMANDS:
        return issues

    if name not in KNOWN_COMMANDS:
        return [
            Issue(
                path,
                line_number,
                "WARNING",
                f"unknown terminal command #{command}",
            )
        ]

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

    if name == "INTERLEVEL":
        parts = args.split()

        if len(parts) != 2:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    "#INTERLEVEL requires: #INTERLEVEL TELEPORT <level>",
                )
            )
        elif parts[0].upper() != "TELEPORT":
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    "#INTERLEVEL requires TELEPORT as its first argument",
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

    if name == "INTRALEVEL":
        parts = args.split()

        if len(parts) != 2:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    "#INTRALEVEL requires: #INTRALEVEL TELEPORT <value>",
                )
            )
        elif parts[0].upper() != "TELEPORT":
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    "#INTRALEVEL requires TELEPORT as its first argument",
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

    if name == "PICT":
        parts = args.split()

        if len(parts) > 2:
            issues.append(
                Issue(
                    path,
                    line_number,
                    "ERROR",
                    "#PICT accepts a resource id and optionally RIGHT or CENTER",
                )
            )
        elif len(parts) == 2 and parts[1].upper() not in {"RIGHT", "CENTER"}:
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

    terminal: TerminalState | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()

        if not stripped:
            continue

        # A semicolon at the beginning of a line marks a comment.
        if stripped.startswith(";"):
            continue

        terminal_match = TERMINAL_START_RE.match(line)
        if terminal_match:
            number = int(terminal_match.group(1))

            if terminal is not None:
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
                terminal = TerminalState(number=number, line=line_number)

            continue

        terminal_end_match = TERMINAL_END_RE.match(line)
        if terminal_end_match:
            number = int(terminal_end_match.group(1))

            if terminal is None:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        f"#ENDTERMINAL {number} appears outside a terminal",
                    )
                )
                continue

            # In split Marathon 2 terminal files, the active section ends
            # implicitly at #ENDTERMINAL. No #END is required here.
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

        section_match = SECTION_START_RE.match(line)
        if section_match:
            section_name = section_match.group(1).upper()

            if terminal is None:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        f"section #{section_name} appears outside a terminal",
                    )
                )
                continue

            # A new section header implicitly ends the previous section.
            terminal.section = SectionState(
                name=section_name,
                line=line_number,
            )
            continue

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
                # Optional compatibility with files that do use #END.
                terminal.section = None
            continue

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
                        f"command #{command} appears outside a terminal",
                    )
                )
            elif terminal.section is None:
                issues.append(
                    Issue(
                        path,
                        line_number,
                        "ERROR",
                        (
                            f"command #{command} appears outside a "
                            "terminal section"
                        ),
                    )
                )

            issues.extend(
                validate_command(command, arguments, path, line_number)
            )
            continue

        # Plain text must belong to an open terminal and active section.
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

        issues.extend(validate_format_codes(line, path, line_number))

    # A terminal itself must always be closed. An active section does not
    # need a separate terminator in this format.
    if terminal is not None:
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
    """Find *.term.txt files in the repository's split/combined folders."""

    search_roots = [
        root / "MapaSeparado",
        root / "MapaUnido",
    ]

    files: list[Path] = []

    for search_root in search_roots:
        if not search_root.exists():
            continue

        for path in search_root.rglob("*"):
            if (
                is_candidate_file(path)
                and path.name.lower().endswith(".term.txt")
            ):
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

        if not issues:
            print(f"PASS  {relative}")
            continue

        print(f"FAIL  {relative}")
        for issue in issues:
            print(f"      {issue}")

        errors += sum(issue.level == "ERROR" for issue in issues)
        warnings += sum(issue.level == "WARNING" for issue in issues)

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
