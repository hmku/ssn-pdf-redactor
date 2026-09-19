#!/usr/bin/env python3
"""OCR and permanently redact one or more exact SSNs from PDFs.

The script runs entirely on the local computer. It preserves source PDFs, prompts
for SSNs without echoing them, and writes redacted copies plus a CSV review log.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    import pymupdf
except ImportError:  # PyMuPDF used the import name "fitz" in older releases.
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None


@dataclass(frozen=True)
class Match:
    page_number: int
    ssn_label: str
    rect: object


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "OCR PDFs and permanently redact exact Social Security numbers. "
            "Original files are never modified."
        )
    )
    parser.add_argument("input", type=Path, help="A PDF or folder containing PDFs")
    parser.add_argument("output", type=Path, help="A new folder for redacted PDFs")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing files in the output folder",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Skip OCR and process only existing searchable text",
    )
    parser.add_argument(
        "--redact-last-four",
        action="store_true",
        help=(
            "Also redact the SSN's last four digits when SSN-related text is "
            "nearby or the number is visibly masked"
        ),
    )
    return parser.parse_args()


def dependency_check(no_ocr: bool) -> None:
    missing = []
    if pymupdf is None:
        missing.append("PyMuPDF (install with: python3 -m pip install pymupdf)")
    if not no_ocr and shutil.which("ocrmypdf") is None:
        missing.append("OCRmyPDF (install with: brew install ocrmypdf)")
    if missing:
        print("Missing required software:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        raise SystemExit(2)


def collect_ssns() -> list[str]:
    print("Enter each SSN to redact. Input is hidden and is not written to the report.")
    print("Press Return on an empty prompt when finished.")
    ssns: list[str] = []
    while True:
        raw = getpass.getpass(f"SSN #{len(ssns) + 1}: ").strip()
        if not raw:
            break
        digits = re.sub(r"\D", "", raw)
        if len(digits) != 9:
            print("Please enter exactly 9 digits, with or without hyphens.")
            continue
        if digits in ssns:
            print("That SSN is already in the list.")
            continue
        ssns.append(digits)
    if not ssns:
        raise SystemExit("No SSNs entered; nothing was changed.")
    return ssns


def resolve_pdfs(source: Path) -> tuple[Path, list[Path]]:
    source = source.expanduser().resolve()
    if source.is_file():
        if source.suffix.lower() != ".pdf":
            raise SystemExit(f"Input file is not a PDF: {source}")
        return source.parent, [source]
    if not source.is_dir():
        raise SystemExit(f"Input does not exist: {source}")
    pdfs = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdfs:
        raise SystemExit(f"No PDFs found under: {source}")
    return source, pdfs


def ensure_safe_output(source_root: Path, output: Path) -> Path:
    output = output.expanduser().resolve()
    if output == source_root or source_root in output.parents:
        raise SystemExit("Output folder must be outside the input folder.")
    output.mkdir(parents=True, exist_ok=True)
    return output


def run_ocr(source: Path, destination: Path) -> None:
    command = [
        "ocrmypdf",
        "--skip-text",
        "--rotate-pages",
        "--deskew",
        "--optimize",
        "1",
        "--output-type",
        "pdf",
        str(source),
        str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        message = detail[-1] if detail else f"exit status {completed.returncode}"
        raise RuntimeError(f"OCR failed: {message}")


def line_word_groups(page) -> list[list[tuple]]:
    """Return visual text runs instead of trusting PDF block/line metadata.

    Tax-form fields commonly store each SSN segment—or even each digit—as a
    separate PDF text object. PyMuPDF can therefore report visually adjacent
    boxes as unrelated blocks and lines. Reconstructing rows from coordinates
    lets those segments match while a horizontal-gap limit prevents distant
    fields on the same row from being concatenated.
    """
    words = list(page.get_text("words", sort=True))
    if not words:
        return []

    visual_rows: list[list[tuple]] = []
    for word in sorted(words, key=lambda item: ((item[1] + item[3]) / 2, item[0])):
        word_center = (word[1] + word[3]) / 2
        word_height = max(1.0, word[3] - word[1])
        for row in visual_rows:
            row_top = min(item[1] for item in row)
            row_bottom = max(item[3] for item in row)
            row_center = (row_top + row_bottom) / 2
            row_height = max(1.0, row_bottom - row_top)
            tolerance = max(3.0, min(word_height, row_height) * 0.55)
            if abs(word_center - row_center) <= tolerance:
                row.append(word)
                break
        else:
            visual_rows.append([word])

    groups: list[list[tuple]] = []
    for row in visual_rows:
        row.sort(key=lambda item: item[0])
        run = [row[0]]
        for word in row[1:]:
            previous = run[-1]
            gap = word[0] - previous[2]
            height = max(previous[3] - previous[1], word[3] - word[1], 1.0)
            # SSN boxes usually have small gaps between digits or sections.
            # A limit tied to text height avoids joining unrelated table cells.
            if gap <= max(18.0, height * 2.5):
                run.append(word)
            else:
                groups.append(run)
                run = [word]
        groups.append(run)
    return groups


SSN_CONTEXT = re.compile(
    r"\b(?:ssn|social\s+security(?:\s+number)?|last\s*(?:four|4)|ending\s+in)\b",
    re.IGNORECASE,
)


def words_rect(words: list[tuple], indexes: list[int]):
    rect = pymupdf.Rect(words[indexes[0]][:4])
    for word_index in indexes[1:]:
        rect |= pymupdf.Rect(words[word_index][:4])
    rect.x0 -= 1.5
    rect.y0 -= 1.5
    rect.x1 += 1.5
    rect.y1 += 1.5
    return rect


def all_offsets(haystack: str, needle: str) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return offsets
        offsets.append(found)
        start = found + len(needle)


def masked_last_four(text: str, last_four: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    pattern = re.compile(
        rf"(?:x{{3}}|\*{{3}}|•{{3}})[-–—.]?"
        rf"(?:x{{2}}|\*{{2}}|•{{2}})[-–—.]?{re.escape(last_four)}\b",
        re.IGNORECASE,
    )
    return bool(pattern.search(compact))


def find_matches(
    page,
    targets: list[str],
    page_number: int,
    redact_last_four: bool = False,
) -> list[Match]:
    matches: list[Match] = []
    seen: set[tuple[int, int, int, int, int]] = set()
    groups = line_word_groups(page)
    line_data = []
    for words in groups:
        rect = words_rect(words, list(range(len(words))))
        line_data.append((words, rect, " ".join(str(word[4]) for word in words)))
    line_data.sort(key=lambda item: (item[1].y0, item[1].x0))

    for line_index, (words, line_rect, line_text) in enumerate(line_data):
        digit_stream: list[str] = []
        digit_to_word: list[int] = []
        for word_index, word in enumerate(words):
            for character in str(word[4]):
                if character.isdigit():
                    digit_stream.append(character)
                    digit_to_word.append(word_index)
        digits = "".join(digit_stream)
        for target_index, target in enumerate(targets, start=1):
            full_offsets = all_offsets(digits, target)
            for start in full_offsets:
                contributing = sorted(set(digit_to_word[start : start + 9]))
                # Digits separated by explanatory words are unrelated values,
                # even if dropping all nondigits happens to produce a target.
                between = words[contributing[0] : contributing[-1] + 1]
                if any(re.search(r'[A-Za-z]', str(word[4])) for word in between):
                    continue
                rect = words_rect(words, contributing)
                key = (
                    target_index,
                    round(rect.x0),
                    round(rect.y0),
                    round(rect.x1),
                    round(rect.y1),
                )
                if key not in seen:
                    seen.add(key)
                    matches.append(Match(page_number, f"SSN #{target_index}", rect))

            if not redact_last_four:
                continue

            last_four = target[-4:]
            has_context = bool(SSN_CONTEXT.search(line_text)) or masked_last_four(
                line_text, last_four
            )
            # A tax form may put the label immediately above a digits-only field.
            # Do not borrow nearby context for lines containing other words: that
            # could erase years, amounts, ZIP codes, or reference numbers.
            digits_only_field = not re.search(r"[A-Za-z]", line_text)
            if not has_context and digits_only_field and line_index > 0:
                _, previous_rect, previous_text = line_data[line_index - 1]
                vertical_gap = line_rect.y0 - previous_rect.y1
                has_context = 0 <= vertical_gap <= 40 and bool(
                    SSN_CONTEXT.search(previous_text)
                )
            if not has_context:
                continue

            for start in all_offsets(digits, last_four):
                # A full SSN match already covers its own last four digits.
                if any(full <= start and start + 4 <= full + 9 for full in full_offsets):
                    continue
                contributing = sorted(set(digit_to_word[start : start + 4]))
                rect = words_rect(words, contributing)
                key = (
                    target_index,
                    round(rect.x0),
                    round(rect.y0),
                    round(rect.x1),
                    round(rect.y1),
                )
                if key not in seen:
                    seen.add(key)
                    matches.append(
                        Match(page_number, f"SSN #{target_index} last four", rect)
                    )
    return matches


def redact_pdf(
    source: Path,
    destination: Path,
    targets: list[str],
    redact_last_four: bool = False,
) -> list[Match]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open(source)
    all_matches: list[Match] = []
    try:
        # Widget appearances are not ordinary page text, and applying page
        # redactions alone can leave their recoverable /V values intact.
        # Bake fields into this output copy before extracting and redacting.
        if document.is_form_pdf:
            document.bake(annots=False, widgets=True)
        for page_index, page in enumerate(document):
            page_matches = find_matches(
                page, targets, page_index + 1, redact_last_four=redact_last_four
            )
            for match in page_matches:
                page.add_redact_annot(match.rect, fill=(0, 0, 0))
            if page_matches:
                # Explicitly remove text, overlapping line art, and the affected
                # pixels inside scanned-page images.
                page.apply_redactions(images=2, graphics=2, text=0)
            all_matches.extend(page_matches)
        document.save(destination, garbage=4, clean=True, deflate=True)
    finally:
        document.close()
    return all_matches


def verify_text_removed(
    destination: Path,
    targets: list[str],
    redact_last_four: bool = False,
) -> list[int]:
    failures: list[int] = []
    document = pymupdf.open(destination)
    try:
        for page_index, page in enumerate(document):
            if find_matches(
                page,
                targets,
                page_index + 1,
                redact_last_four=redact_last_four,
            ):
                failures.append(page_index + 1)
    finally:
        document.close()
    return failures


def process_one(
    source: Path,
    destination: Path,
    targets: list[str],
    no_ocr: bool,
    overwrite: bool,
    redact_last_four: bool,
) -> tuple[str, list[Match], str]:
    if destination.exists() and not overwrite:
        return "skipped", [], "Output exists; use --overwrite to replace it"
    with tempfile.TemporaryDirectory(prefix="ssn-redact-") as temporary:
        working = source
        if not no_ocr:
            ocr_path = Path(temporary) / "ocr.pdf"
            try:
                run_ocr(source, ocr_path)
                working = ocr_path
            except RuntimeError as exc:
                return "error", [], str(exc)
        temporary_output = Path(temporary) / "redacted.pdf"
        try:
            matches = redact_pdf(
                working,
                temporary_output,
                targets,
                redact_last_four=redact_last_four,
            )
            failures = verify_text_removed(
                temporary_output,
                targets,
                redact_last_four=redact_last_four,
            )
            if failures:
                pages = ", ".join(map(str, failures))
                return "error", matches, f"Verification still found an SSN on page(s): {pages}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(temporary_output, destination)
        except Exception as exc:
            return "error", [], f"Redaction failed: {exc}"
    if not matches:
        return "review", [], "No exact SSN match found; inspect this PDF manually"
    scope = "Full and contextual last-four matches" if redact_last_four else "Exact matches"
    return "redacted", matches, f"{scope} removed; visually inspect the listed pages"


def write_report(report_path: Path, rows: list[dict[str, str]]) -> None:
    with report_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["status", "source", "output", "pages", "matches", "notes"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    dependency_check(args.no_ocr)
    source_root, pdfs = resolve_pdfs(args.input)
    output_root = ensure_safe_output(source_root, args.output)
    targets = collect_ssns()
    print(f"\nProcessing {len(pdfs)} PDF(s). Originals will not be changed.\n")
    rows: list[dict[str, str]] = []
    error_count = 0
    review_count = 0
    for index, source in enumerate(pdfs, start=1):
        relative = source.relative_to(source_root)
        destination = output_root / relative
        print(f"[{index}/{len(pdfs)}] {relative}")
        status, matches, notes = process_one(
            source,
            destination,
            targets,
            args.no_ocr,
            args.overwrite,
            args.redact_last_four,
        )
        pages = sorted({match.page_number for match in matches})
        print(f"  {status}: {notes}")
        rows.append(
            {
                "status": status,
                "source": str(relative),
                "output": str(relative) if destination.exists() else "",
                "pages": ";".join(map(str, pages)),
                "matches": str(len(matches)),
                "notes": notes,
            }
        )
        error_count += status == "error"
        review_count += status == "review"
    report = output_root / "redaction_report.csv"
    write_report(report, rows)
    print(f"\nReport: {report}")
    print("Open every page listed in the report and visually confirm the black redaction boxes.")
    if review_count:
        print(f"Manual review required for {review_count} PDF(s) with no exact match.")
    if error_count:
        print(f"Processing failed for {error_count} PDF(s). See the report.")
    return 1 if error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
