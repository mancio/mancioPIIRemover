"""Map detections to rendered pixels and build a fresh image-only PDF."""

from dataclasses import dataclass
from io import BytesIO
from math import ceil, floor, isfinite

from PIL import Image, ImageDraw
import pymupdf

from .engines import chunks, document_identifiers


class ReviewError(Exception):
    pass


@dataclass
class TextLayer:
    text: str
    # Each character maps to a line number and a rectangle, or a layout space.
    positions: list


def text_layer(page, textpage=None):
    letters, positions = [], []
    line_number = 0
    raw = page.get_text("rawdict", textpage=textpage, flags=pymupdf.TEXTFLAGS_RAWDICT
                        & ~pymupdf.TEXT_PRESERVE_IMAGES, sort=True)
    for block in raw["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    value = char["c"]
                    letters.append(value)
                    position = None if value.isspace() else (line_number, pymupdf.Rect(char["bbox"]))
                    positions.extend([position] * len(value))
            letters.append("\n")
            positions.append(None)
            line_number += 1
    return TextLayer("".join(letters), positions)


def rectangles(layer, start, end):
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(layer.text):
        raise ReviewError("A detector returned an invalid text span.")
    lines = {}
    for position in layer.positions[start:end]:
        if position is not None:
            number, box = position
            lines[number] = box | lines[number] if number in lines else box
    return lines.values()


def detect(layer, detectors, findings, page, engine_status, allow_partial):
    def add(hit, offset, engine):
        for box in rectangles(layer, offset + hit.start, offset + hit.end):
            # Text extraction coordinates are unrotated. Masks use displayed coordinates.
            box = box * page.rotation_matrix
            padded = pymupdf.Rect(box.x0 - 1, box.y0 - 1, box.x1 + 1, box.y1 + 1)
            clipped = padded & page.rect
            if not clipped.is_empty:
                key = tuple(clipped)
                findings.setdefault(key, set()).add(f"{engine}:{hit.label}")

    for hit in document_identifiers(layer.text):
        add(hit, 0, "document-patterns")
    for engine, detector in detectors.items():
        try:
            for offset, content in chunks(layer.text):
                if content.strip():
                    for hit in detector(content):
                        if not 0 <= hit.start < hit.end <= len(content):
                            raise ReviewError("A detector returned an invalid chunk offset.")
                        add(hit, offset, engine)
        except Exception as exc:
            if not allow_partial:
                raise ReviewError(f"{engine} failed on page {page.number + 1} ({type(exc).__name__}).") from None
            engine_status[engine] = f"failed on one or more pages ({type(exc).__name__})"


def add_manual_boxes(page, boxes, findings):
    for coordinates in boxes:
        if (not isinstance(coordinates, list) or len(coordinates) != 4
                or any(type(value) not in (float, int) or not isfinite(value) for value in coordinates)):
            raise ReviewError("Manual masks need four finite numeric coordinates.")
        box = pymupdf.Rect(coordinates)
        if box.is_empty or not page.rect.contains(box):
            raise ReviewError(f"A manual mask is outside page {page.number + 1} or has zero area.")
        findings.setdefault(tuple(box), set()).add("manual")


def render_redacted(page, findings, output, dpi):
    width, height = page.rect.width, page.rect.height
    scale = dpi / 72
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    draw = ImageDraw.Draw(image)
    for x0, y0, x1, y1 in findings:
        draw.rectangle((floor(x0 * scale) - pixmap.x, floor(y0 * scale) - pixmap.y,
                        ceil(x1 * scale) - pixmap.x, ceil(y1 * scale) - pixmap.y), fill="black")
    pixels = BytesIO()
    image.save(pixels, format="PNG")
    destination = output.new_page(width=width, height=height)
    destination.insert_image(destination.rect, stream=pixels.getvalue())


def redact(source, output, detectors, engine_status, args, manual):
    if not source.is_pdf or source.needs_pass or not len(source):
        raise ReviewError("Input must be a readable, unlocked PDF with at least one page.")
    if any(key not in {str(n) for n in range(1, len(source) + 1)} for key in manual):
        raise ReviewError("Manual mask page numbers must match the input PDF.")
    pages = []
    for page in source:
        if page.rect.get_area() * (max(args.dpi, 300) / 72) ** 2 > 45_000_000:
            raise ReviewError(f"Page {page.number + 1} is too large to render safely.")
        findings = {}
        layer = text_layer(page)
        ocr = (args.ocr_all or not layer.text.strip() or "\ufffd" in layer.text
               or bool(page.get_image_info()) or bool(page.first_annot) or bool(page.first_widget))
        notes = []
        detect(layer, detectors, findings, page, engine_status, args.allow_partial)
        if ocr:
            try:
                ocr_page = page.get_textpage_ocr(language=args.ocr_language, dpi=300, full=True,
                                                 tessdata=str(args.models / "tessdata"))
            except Exception:
                raise ReviewError(f"OCR failed on page {page.number + 1}. Run the setup script and check the OCR languages.") from None
            ocr_layer = text_layer(page, ocr_page)
            detect(ocr_layer, detectors, findings, page, engine_status, args.allow_partial)
            if not ocr_layer.text.strip():
                notes.append("OCR found no text. Inspect graphics, handwriting and signatures.")
        boxes = manual.get(str(page.number + 1), [])
        if not isinstance(boxes, list):
            raise ReviewError("Each manual mask page must contain a list of rectangles.")
        add_manual_boxes(page, boxes, findings)
        if not findings:
            notes.append("No masks applied. Review the entire page.")
        render_redacted(page, findings, output, args.dpi)
        pages.append({
            "page": page.number + 1, "size_points": list(page.rect)[2:], "used_ocr": ocr,
            "notes": notes,
            "masks": [{"box": list(box), "detectors": sorted(labels)}
                      for box, labels in sorted(findings.items())],
        })
    return pages
