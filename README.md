# mancioPIIRemover

Local Windows Python CLI for preparing a redacted copy of a PDF contract for visual review. By default it combines detections from all three engines and applies every candidate mask.

- [OpenAI Privacy Filter](https://huggingface.co/openai/privacy-filter) runs in Transformers on CPU.
- [Fastino GLiNER2-PII](https://huggingface.co/fastino/gliner2-privacy-filter-PII-multi) adds multilingual detection.
- [Microsoft Presidio](https://github.com/microsoft/presidio) runs with English or Polish spaCy. Built-in project rules also detect Polish IBANs and labeled PESEL, NIP and bank-account numbers.

## Install on Windows

In PowerShell, from the repository directory, with Python 3.12 installed:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

Setup downloads roughly 4 GB of model weights plus dependencies and OCR data. Processing uses local weights and does not send PDF data to a service. If you only need selected engines, install the corresponding extras from `pyproject.toml` and run with `--engines`. The default stops if a selected engine is unavailable or fails. Use `--allow-partial` to explicitly accept reduced coverage. Check the engine status in the report.

## Run the synthetic example

```powershell
New-Item -ItemType Directory -Force review | Out-Null
.\.venv\Scripts\python.exe examples\make_contract.py review\example.pdf
.\.venv\Scripts\python.exe -m mancio_pii review\example.pdf -o review\example.redacted.pdf
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Process a contract

```powershell
.\.venv\Scripts\python.exe -m mancio_pii "C:\path\to\contract.pdf" -o "C:\path\to\contract.redacted-review.pdf" --ocr-all
```

The original stays intact. The review copy is a new image-only PDF. It keeps the visible page layout and drops the original text layer, interactive forms, attachments and digital signatures. Page images include visible appearances, so inspect all pages for names, identifiers, images, handwriting and visible signatures. The JSON report lists page numbers, mask coordinates, detector labels, OCR usage and engine status. It omits matched text. Treat both outputs as private until you finish review.

The CLI automatically attempts local OCR for images, annotations, widgets and pages with absent or visibly garbled text. Use `--ocr-all` for contracts that may contain vector text, image-like graphics, or mixed formats. Use `--language pl` for Polish Presidio analysis. OCR defaults to English and Polish, and `--ocr-language` can select one language.

If you find something the detectors missed, add a JSON file such as `review\extra-masks.json` and rerun with `--manual-masks review\extra-masks.json` and a new output name. The example below masks part of displayed page 2. Coordinates are in points from the top-left of the displayed page, including any rotation. The order is left, top, right, bottom.

```json
{"2": [[70, 520, 290, 570]]}
```

The CLI refuses to overwrite either output. Keep original contracts and review files outside source control.
