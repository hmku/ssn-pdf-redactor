import tempfile
import unittest
from pathlib import Path

import pymupdf

import redact_ssn_pdfs as redactor


TARGET = "123456789"


def make_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Full SSN: 123-45-6789")
    page.insert_text((72, 100), "Masked SSN: XXX-XX-6789")
    page.insert_text((72, 128), "Reference number: 6789")
    page.insert_text((72, 156), "Tax year: 6789")
    document.save(path)
    document.close()


def make_boxed_ssn_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((310, 72), "Your social security number")
    x_positions = [322, 334, 346, 364, 376, 394, 406, 418, 430]
    for x, digit in zip(x_positions, TARGET):
        # Separate insertions mimic tax-form widgets/text objects. PyMuPDF
        # reports these as separate blocks even though they share a visual row.
        page.insert_text((x, 94), digit)
        page.draw_rect(pymupdf.Rect(x - 2, 82, x + 9, 98))
    document.save(path)
    document.close()


def extracted_text(path: Path) -> str:
    document = pymupdf.open(path)
    try:
        return "".join(page.get_text() for page in document)
    finally:
        document.close()


class RedactorTests(unittest.TestCase):
    def test_redacts_ssn_split_across_form_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "boxed.pdf"
            destination = Path(directory) / "boxed-redacted.pdf"
            make_boxed_ssn_pdf(source)

            source_document = pymupdf.open(source)
            try:
                # Confirm the fixture has three internal PDF lines even though
                # all nine digits appear on one visual row.
                groups = {}
                for word in source_document[0].get_text("words"):
                    if str(word[4]).isdigit():
                        groups.setdefault((word[5], word[6]), []).append(str(word[4]))
                self.assertEqual(
                    ["".join(value) for value in groups.values()],
                    ["123", "45", "6789"],
                )
            finally:
                source_document.close()

            matches = redactor.redact_pdf(source, destination, [TARGET])
            text = extracted_text(destination)

            self.assertEqual(len(matches), 1)
            self.assertNotIn(TARGET, "".join(character for character in text if character.isdigit()))
            self.assertEqual(redactor.verify_text_removed(destination, [TARGET]), [])

    def test_redacts_full_ssn_but_preserves_unrelated_last_four(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.pdf"
            destination = Path(directory) / "output.pdf"
            make_pdf(source)

            matches = redactor.redact_pdf(source, destination, [TARGET])
            text = extracted_text(destination)

            self.assertEqual(len(matches), 1)
            self.assertNotIn("123-45-6789", text)
            self.assertIn("Reference number: 6789", text)
            self.assertIn("Tax year: 6789", text)
            self.assertEqual(redactor.verify_text_removed(destination, [TARGET]), [])

    def test_contextual_last_four_redaction_is_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.pdf"
            destination = Path(directory) / "output.pdf"
            make_pdf(source)

            matches = redactor.redact_pdf(
                source, destination, [TARGET], redact_last_four=True
            )
            text = extracted_text(destination)

            self.assertEqual(len(matches), 2)
            self.assertNotIn("123-45-6789", text)
            self.assertNotIn("XXX-XX-6789", text)
            self.assertIn("Reference number: 6789", text)
            self.assertIn("Tax year: 6789", text)
            self.assertEqual(
                redactor.verify_text_removed(
                    destination, [TARGET], redact_last_four=True
                ),
                [],
            )

    def test_report_does_not_contain_ssn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "redaction_report.csv"
            redactor.write_report(
                report,
                [
                    {
                        "status": "redacted",
                        "source": "input.pdf",
                        "output": "input.pdf",
                        "pages": "1",
                        "matches": "1",
                        "notes": "Exact match removed",
                    }
                ],
            )

            contents = report.read_text()
            self.assertNotIn(TARGET, contents)
            self.assertIn("input.pdf", contents)


if __name__ == "__main__":
    unittest.main()
