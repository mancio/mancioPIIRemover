import argparse
import json
import logging
import os
from pathlib import Path
import sys
from time import perf_counter

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
    parser.add_argument("-v", "--verbose", action="store_true", help="Show local progress and timings")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto",
                        help="Compute device for Privacy Filter and GLiNER2 (default: auto)")
    parser.add_argument("--threads", type=int, help="PyTorch CPU thread count")
    args = parser.parse_args()
    if args.threads is not None and args.threads < 1:
        parser.error("--threads must be at least 1")
    return args


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
    started = perf_counter()

    def progress(message):
        if args.verbose:
            print(f"[{perf_counter() - started:7.1f}s] {message}", file=sys.stderr, flush=True)

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
    detectors, status, devices = {}, {}, {}
    model_path = args.models.resolve()
    torch_configured = False
    neural_device = "cpu"
    for name in dict.fromkeys(args.engines):
        try:
            if name in ("privacy", "gliner") and not torch_configured:
                import torch

                if args.threads is not None:
                    torch.set_num_threads(args.threads)
                cuda = args.device != "cpu" and torch.cuda.is_available()
                if args.device == "cuda" and not cuda:
                    raise ReviewError("CUDA is unavailable. Install CUDA-enabled PyTorch or select --device cpu.")
                neural_device = "cuda:0" if cuda else "cpu"
                progress(f"PyTorch CPU threads: {torch.get_num_threads()}")
                if cuda:
                    progress(f"CUDA GPU: {torch.cuda.get_device_name(0)}")
                elif args.device == "auto":
                    progress("CUDA unavailable. Using CPU for both neural models.")
                torch_configured = True
            device = "cpu" if name == "presidio" else neural_device
            progress(f"Loading {name} on {device}")
            detectors[name] = load_engine(name, model_path, args.language, device)
            devices[name] = device
            status[name] = "ran"
            progress(f"{name} ready")
        except ReviewError:
            raise
        except Exception as exc:
            status[name] = f"unavailable ({type(exc).__name__})"
            progress(f"{name} {status[name]}")
            if not args.allow_partial:
                raise ReviewError(f"{name} is unavailable ({type(exc).__name__}). Run setup.ps1.") from None
    if not detectors:
        raise ReviewError("No selected engine could be loaded.")
    with pymupdf.open(args.input) as source, pymupdf.open() as output:
        pages = redact(source, output, detectors, status, args, manual, progress)
        if all(state != "ran" for state in status.values()):
            raise ReviewError("No selected engine completed all pages.")
        report = {
            "status": "manual review required",
            "engines": status,
            "devices": devices,
            "pages": pages,
        }
        progress("Writing review PDF and report")
        publish(args.output, report_path, output.tobytes(garbage=4, deflate=True),
                (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    progress("Finished")
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
