import argparse
import json
import logging
import os
from pathlib import Path
import sys

import pymupdf

from .engines import ENGINE_NAMES, load_engine
from .pdf import ReviewError, redact


def parse_args():
    parser = argparse.ArgumentParser(description="Build a local, flattened PDF for PII review.")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path, help="New review PDF path")
    parser.add_argument("--engines", nargs="+", choices=ENGINE_NAMES, default=ENGINE_NAMES)
    parser.add_argument("--allow-partial", action="store_true", help="Continue if a selected engine fails")
    parser.add_argument("--language", choices=("en", "pl"), default="en", help="Presidio language")
    parser.add_argument("--models", type=Path, default=Path(".models"))
    parser.add_argument("--ocr-all", action="store_true", help="OCR every page as well as reading native text")
    parser.add_argument("--ocr-language", choices=("eng", "pol", "eng+pol"), default="eng+pol")
    parser.add_argument("--dpi", type=int, choices=(150, 200, 300), default=200)
    parser.add_argument("--manual-masks", type=Path, help="JSON mapping 1-based page numbers to mask rectangles")
    return parser.parse_args()


def publish(path, report_path, pdf_bytes, report):
    created = []
    try:
        for target, contents in ((report_path, report), (path, pdf_bytes)):
            with target.open("xb") as stream:
                created.append(target)
                stream.write(contents)
    except BaseException:
        for target in created:
            target.unlink(missing_ok=True)
        raise


def run(args):
    report_path = args.output.with_suffix(".review.json")
    if args.output.suffix.lower() != ".pdf":
        raise ReviewError("Choose an output filename ending in .pdf.")
    if args.output.exists() or report_path.exists():
        raise ReviewError("The requested output or review report already exists. Choose a new name.")
    if not args.output.parent.is_dir():
        raise ReviewError("Create the output folder first.")
    if not args.input.is_file():
        raise ReviewError("Input PDF was not found.")
    manual = json.loads(args.manual_masks.read_text(encoding="utf-8-sig")) if args.manual_masks else {}
    if not isinstance(manual, dict):
        raise ReviewError("Manual masks must be a JSON object keyed by page number.")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    logging.disable(logging.CRITICAL)
    detectors, status = {}, {}
    model_path = args.models.resolve()
    for name in dict.fromkeys(args.engines):
        try:
            if name in ("privacy", "gliner"):
                import torch

                torch.set_num_threads(min(4, os.cpu_count() or 1))
            detectors[name] = load_engine(name, model_path, args.language)
            status[name] = "ran"
        except Exception as exc:
            status[name] = f"unavailable ({type(exc).__name__})"
            if not args.allow_partial:
                raise ReviewError(f"{name} is unavailable ({type(exc).__name__}). Run setup.ps1.") from None
    if not detectors:
        raise ReviewError("No selected engine could be loaded.")
    with pymupdf.open(args.input) as source, pymupdf.open() as output:
        pages = redact(source, output, detectors, status, args, manual)
        if all(state != "ran" for state in status.values()):
            raise ReviewError("No selected engine completed all pages.")
        report = {
            "status": "manual review required",
            "engines": status,
            "pages": pages,
        }
        publish(args.output, report_path, output.tobytes(garbage=4, deflate=True),
                (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(f"Review PDF: {args.output}")
    print(f"Review report: {report_path}")
    print("Inspect every page before sharing, including signatures, handwriting and images.")
    for engine, state in status.items():
        print(f"{engine}: {state}")


def main():
    try:
        run(parse_args())
    except ReviewError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Third-party exception messages can include source text or file names.
        print(f"Processing failed ({type(exc).__name__}). Check the PDF, output path and setup.", file=sys.stderr)
        return 1
    return 0
