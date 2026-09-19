"""Integration check using a caller-supplied blank IRS 2025 Form 1040.

Usage: python3 tests/check_1040.py /path/to/blank/f1040.pdf /path/to/test-output
Only synthetic identifiers are written; the blank input is preserved.
"""
import sys
from pathlib import Path

import pymupdf as pdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import redact_ssn_pdfs as redactor


def main():
    blank, output = map(Path, sys.argv[1:])
    output.mkdir(parents=True, exist_ok=True)
    source = output / 'synthetic-1040.pdf'
    destination = output / 'redacted-1040.pdf'
    targets = ['123456789', '987654321']
    document = pdf.open(blank)
    fields = {'f1_16[0]': targets[0], 'f1_19[0]': targets[1]}
    filled = 0
    for page in document:
        for widget in page.widgets() or []:
            if widget.field_type_string == 'Text' and widget.field_value:
                raise ValueError('Input must be a blank form')
    for widget in document[0].widgets():
        key = widget.field_name.split('.')[-1]
        if key in fields:
            widget.field_value = fields[key]
            widget.update()
            filled += 1
    assert filled == 2, 'Expected taxpayer and spouse SSN fields on 2025 Form 1040'
    document.save(source)
    document.close()
    with pdf.open(source) as check:
        for widget in check[0].widgets():
            key = widget.field_name.split('.')[-1]
            if key in fields:
                assert widget.field_value == fields[key]
        check[0].get_pixmap(matrix=pdf.Matrix(2, 2)).save(output / 'before.png')
    matches = redactor.redact_pdf(source, destination, targets)
    with pdf.open(destination) as check:
        check[0].get_pixmap(matrix=pdf.Matrix(2, 2)).save(output / 'after.png')
        remaining = [w.field_value for p in check for w in (p.widgets() or [])]
        assert not any(t in str(value) for t in targets for value in remaining), 'SSN still stored in form field'
    assert len(matches) == 2, f'Expected two SSN redactions, found {len(matches)}'
    assert not redactor.verify_text_removed(destination, targets)
    print('PASS: both actual 1040 SSN fields removed from page text and form values')


if __name__ == '__main__':
    main()
