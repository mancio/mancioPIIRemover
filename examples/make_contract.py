"""Generate a fictional test agreement. No external data is used."""

import argparse
from pathlib import Path

import pymupdf


def make_contract(path):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 65), "EXAMPLE ACCOUNTING SERVICES AGREEMENT", fontsize=17)
        for y, line in enumerate((
            "Fictional example for testing local redaction.",
            "",
            "Client: Clara Exampleton",
            "Address: 123 Fictional Lane, Sample City",
            "Email: clara@example.com",
            "Phone: +1 202-555-0142",
            "NIP: 123-456-78-90",
            "PESEL: 90010112345",
            "IBAN: PL00 0000 0000 0000 0000 0000 0000",
            "",
            "Scope of services",
            "The provider maintains bookkeeping records and prepares monthly statements.",
            "Either party may terminate the agreement with written notice.",
            "",
            "Signature block is intentionally blank.",
        ), start=105):
            page.insert_text((60, 105 + (y - 105) * 21), line, fontsize=11)
        doc.set_metadata({"author": "Synthetic author field", "title": "Synthetic contract"})
        doc.save(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    make_contract(parser.parse_args().output)
