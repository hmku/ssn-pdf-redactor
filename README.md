# SSN PDF Redactor

Also supports driver's license numbers, bank account numbers, and bank routing
numbers. Run the same command below; hidden prompts now ask for each category.
Press Return to skip a category or finish entering multiple values. You can
redact bank or license identifiers without entering an SSN.

Provide the complete numbers you want removed. License numbers may contain
letters (matched without case sensitivity); account and routing numbers retain
leading zeros. Spaces and hyphens are ignored during matching. This does not
automatically detect unknown identifiers. The `--redact-last-four` option applies
only to SSNs, not masked bank or license numbers. Short identifiers can match
unrelated values, so inspect the results.

A local command-line tool that OCRs PDFs and permanently removes exact Social
Security numbers. It preserves the originals, prompts for SSNs without echoing
them, and writes redacted copies plus a CSV review report.

The tool is designed for tax documents and other sensitive PDFs on macOS. It
does not upload documents or SSNs anywhere.

> [!IMPORTANT]
> OCR can misread digits. Always inspect the pages listed in the report and
> manually review every PDF for which no match was found. Do not treat an
> automated `redacted` result as proof that a document is safe to share.

## Requirements

- Python 3.9 or newer
- [OCRmyPDF](https://ocrmypdf.readthedocs.io/) for scanned PDFs
- [PyMuPDF](https://pymupdf.readthedocs.io/) for redaction

Install the free dependencies on macOS:

```bash
brew install ocrmypdf
python3 -m pip install -r requirements.txt
```

If `brew` is not installed, install Homebrew from <https://brew.sh> first.

## Usage

Put the source PDFs in one folder. The output folder must be separate and
outside the source folder. PDFs in subfolders are processed recursively, and
the folder structure is mirrored in the output.

```bash
python3 ./redact_ssn_pdfs.py \
  "/path/to/Original Tax PDFs" \
  "/path/to/Redacted Tax PDFs"
```

Enter each SSN when prompted. Input is hidden and is never written to shell
history, filenames, or the CSV report. Press Return at an empty prompt when
finished.

The matcher ignores punctuation and spacing, so the same target can match
`123-45-6789`, `123 45 6789`, or `123456789` when OCR recognizes it correctly.
It also reconstructs visually adjacent digits that a tax form stores as
separate internal text lines, including the boxed SSN fields on Form 1040.
Editable PDF form fields are converted into page content in the output copy
before matching. Redacted copies therefore no longer have editable form fields.

### Redact contextual last-four forms

To also remove the last four digits when they appear near an SSN label or in a
masked form such as `XXX-XX-1234`, add `--redact-last-four`:

```bash
python3 ./redact_ssn_pdfs.py \
  "/path/to/Original Tax PDFs" \
  "/path/to/Redacted Tax PDFs" \
  --redact-last-four
```

This option deliberately does not remove every matching four-digit number.
Doing so could erase tax years, dollar amounts, ZIP codes, and form numbers.

### Other options

- `--overwrite` replaces files already present in the output folder.
- `--no-ocr` skips OCR when every PDF already has searchable text.

Run `python3 ./redact_ssn_pdfs.py --help` for the complete CLI reference.

## Review the results

Open `redaction_report.csv` in the output folder. It contains relative paths,
match counts, and the pages that require inspection. Review every listed page
and every PDF marked `review` or `error`.

Keep the originals until the redacted copies have been checked. Also consider
removing bank account and routing numbers, IRS identity-protection PINs, dates
of birth, signatures, and other identifiers before sharing tax documents.

## Development

The tests use only synthetic SSNs and PDFs:

```bash
python3 -m unittest discover -s tests -v
```

To test the actual fillable 2025 IRS Form 1040, supply a blank form locally:

```bash
python3 tests/check_1040.py /path/to/blank/f1040.pdf tmp/pdfs/1040
```

This fills taxpayer and spouse fields with synthetic identifiers, checks removal
from page text and field values, and renders before/after images for inspection.

## License

[MIT](LICENSE)
