from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from types import SimpleNamespace

from PIL import Image
import pymupdf

from mancio_pii.engines import Hit, chunks, document_identifiers
from mancio_pii.pdf import redact, text_layer


class RedactionTests(unittest.TestCase):
    def test_chunks_cover_input_with_exact_offsets(self):
        value = ("some words and a long " * 200).strip()
        end = 0
        for offset, content in chunks(value):
            self.assertLessEqual(offset, end)
            self.assertEqual(content, value[offset:offset + len(content)])
            end = offset + len(content)
        self.assertEqual(end, len(value))

    def test_document_identifiers_keep_prefixes(self):
        value = "PESEL: 90010112345 NIP: 123-456-78-90 IBAN: PL00 0000 0000 0000 0000 0000 0000"
        self.assertEqual([value[h.start:h.end] for h in document_identifiers(value)],
                         ["90010112345", "123-456-78-90", "PL00 0000 0000 0000 0000 0000 0000"])

    def test_rotated_pdf_burns_masks_and_drops_hidden_content(self):
        with pymupdf.open() as source, pymupdf.open() as output:
            page = source.new_page()
            page.insert_text((60, 80), "Contact: fake@example.com")
            secret = page.search_for("fake@example.com")[0]
            page.set_rotation(90)
            expected = (secret * page.rotation_matrix).tl + pymupdf.Point(2, 2)
            source.set_metadata({"author": "hidden author"})
            source.embfile_add("private.txt", b"hidden attachment")
            original = source.tobytes(no_new_id=True)

            def email(text):
                start = text.index("fake@example.com")
                yield Hit(start, start + len("fake@example.com"), "email")

            status = {"test": "ran"}
            args = SimpleNamespace(ocr_all=False, dpi=200, allow_partial=False)
            pages = redact(source, output, {"test": email}, status, args, {})
            self.assertEqual(len(pages[0]["masks"]), 1)
            with pymupdf.open(stream=output.tobytes(), filetype="pdf") as saved:
                self.assertEqual(saved.embfile_count(), 0)
                self.assertEqual(saved.metadata["author"], "")
                self.assertEqual(saved[0].get_text(), "")
                pix = saved[0].get_pixmap(dpi=200)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                self.assertEqual(image.getpixel((round(expected.x * 200 / 72),
                                                 round(expected.y * 200 / 72))), (0, 0, 0))
            self.assertEqual(source.tobytes(no_new_id=True), original)

    def test_image_only_pdf_is_redacted_with_local_ocr(self):
        models = Path(".models").resolve()
        if not (models / "tessdata/eng.traineddata").is_file():
            self.skipTest("Run setup.ps1 to install the local OCR fixture dependency.")
        with pymupdf.open() as text_pdf, pymupdf.open() as scan, pymupdf.open() as output:
            original = text_pdf.new_page(width=300, height=180)
            original.insert_text((40, 80), "ALICE EXAMPLE", fontsize=20)
            page = scan.new_page(width=300, height=180)
            page.insert_image(page.rect, pixmap=original.get_pixmap(dpi=300))
            self.assertEqual(page.get_text(), "")

            def name(text):
                start = text.index("ALICE EXAMPLE")
                yield Hit(start, start + len("ALICE EXAMPLE"), "name")

            args = SimpleNamespace(ocr_all=False, dpi=200, allow_partial=False,
                                   ocr_language="eng", models=models)
            pages = redact(scan, output, {"test": name}, {"test": "ran"}, args, {})
            self.assertTrue(pages[0]["used_ocr"])
            self.assertEqual(len(pages[0]["masks"]), 1)
            pix = output[0].get_pixmap(dpi=200)
            self.assertEqual(pix.pixel(round(70 * 200 / 72), round(73 * 200 / 72)), (0, 0, 0))

    def test_span_offsets_survive_line_break(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((60, 80), "Clara")
            page.insert_text((60, 95), "Exampleton")
            layer = text_layer(page)
            self.assertEqual(layer.text, "Clara\nExampleton\n")
            self.assertIsNone(layer.positions[5])

    def test_cli_fails_closed_when_model_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "original.pdf", root / "output.pdf"
            with pymupdf.open() as doc:
                doc.new_page()
                doc.save(source)
            result = subprocess.run(
                [sys.executable, "-m", "mancio_pii", str(source), "-o", str(destination),
                 "--engines", "privacy", "--models", str(root / "missing")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("privacy is unavailable", result.stderr)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".review.json").exists())

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.pdf"
            output.write_bytes(b"keep me")
            result = subprocess.run(
                [sys.executable, "-m", "mancio_pii", str(output), "-o", str(output)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output.read_bytes(), b"keep me")


if __name__ == "__main__":
    unittest.main()
