import tempfile
import unittest
from unittest.mock import patch
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
    def test_hyphenated_last_four(self):
        # Exercise extracted word coordinates without saving any identifiers.
        class Page:
            def __init__(self, text):
                self.text = text

            def get_text(self, *args, **kwargs):
                words, x = [], 72
                for index, token in enumerate(self.text.split()):
                    words.append((x, 72, x + len(token) * 6, 84,
                                  token, 0, 0, index))
                    x += len(token) * 6 + 4
                return words

        for text, expected in [('-6789', 1), ('- 6789', 1),
                               ('-6789 Reference: 6789', 1),
                               ('Loss: -6789', 1), ('ZIP: 10001-6789', 1),
                               ('Reference: ABC-6789', 1),
                               ('Reference: 6789', 0), ('-67890', 0),
                               ('-6789A', 0), ('123-45-6789', 1)]:
            with self.subTest(text=text):
                self.assertEqual(len(redactor.find_matches(Page(text), [TARGET], 1, True)), expected)
        self.assertEqual(redactor.find_matches(Page('-6789'), [TARGET], 1, False), [])

    def test_bank_only_prompts_preserve_leading_zeros(self):
        with patch('getpass.getpass', side_effect=['', '', '001234567890', '', '021000021', '']), patch('builtins.print'):
            targets = redactor.collect_identifiers()
        self.assertEqual(targets, [redactor.Identifier('Bank account', '001234567890'),
                                   redactor.Identifier('Bank routing', '021000021')])

    def test_boxed_routing_on_scan_with_text_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'scan.pdf'
            destination = Path(directory) / 'redacted.pdf'
            value = '021000021'
            with pymupdf.open() as original:
                page = original.new_page()
                for index, digit in enumerate(value):
                    page.insert_text((72 + index * 14, 90), digit)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                with pymupdf.open() as scan:
                    page = scan.new_page()
                    page.insert_image(page.rect, pixmap=pixmap)
                    for index, digit in enumerate(value):
                        page.insert_text((72 + index * 14, 90), digit, render_mode=3)
                    scan.save(source)
            targets = [redactor.Identifier('Bank routing', value)]
            matches = redactor.redact_pdf(source, destination, targets)
            self.assertEqual(len(matches), 1)
            self.assertFalse(redactor.verify_text_removed(destination, targets))
            with pymupdf.open(destination) as document:
                pixels = document[0].get_pixmap(clip=pymupdf.Rect(74, 80, 185, 88))
                self.assertEqual(max(pixels.samples), 0)

    def test_additional_identifiers_in_text_and_form_fields(self):
        targets = [redactor.Identifier("Driver's license", 'A01234567'),
                   redactor.Identifier('Bank account', '001234567890'),
                   redactor.Identifier('Bank routing', '021000021')]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'identifiers.pdf'
            destination = Path(directory) / 'redacted.pdf'
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((72, 72), 'License: a012-34567')
                page.insert_text((72, 110), 'Account: 0012 3456 7890')
                page.insert_text((72, 148), 'Keep longer value: 90210000219')
                page.insert_text((72, 180), 'Keep words: 021 fee 000021')
                widget = pymupdf.Widget()
                widget.field_name = 'routing'
                widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
                widget.rect = pymupdf.Rect(72, 210, 210, 235)
                widget.field_value = targets[2].value
                page.add_widget(widget)
                document.save(source)
            matches = redactor.redact_pdf(source, destination, targets, True)
            self.assertEqual(len(matches), 3)
            with pymupdf.open(destination) as document:
                text = document[0].get_text()
                self.assertIn('90210000219', text)
                self.assertIn('021 fee 000021', text)
                self.assertNotIn('a012-34567', text)
                self.assertNotIn('0012 3456 7890', text)
                self.assertFalse(document.is_form_pdf)
            self.assertFalse(redactor.verify_text_removed(destination, targets))
            self.assertNotIn('001234567890', repr(targets))

    def test_editable_ssn_field_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'form.pdf'
            destination = Path(directory) / 'redacted.pdf'
            with pymupdf.open() as document:
                page = document.new_page()
                widget = pymupdf.Widget()
                widget.field_name = 'taxpayer_ssn'
                widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
                widget.rect = pymupdf.Rect(72, 72, 200, 95)
                widget.field_value = TARGET
                page.add_widget(widget)
                document.save(source)
            matches = redactor.redact_pdf(source, destination, [TARGET])
            self.assertEqual(len(matches), 1)
            with pymupdf.open(destination) as document:
                self.assertFalse(document.is_form_pdf)
                self.assertNotIn(TARGET, document[0].get_text())

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
