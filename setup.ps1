$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (!(Test-Path .venv/Scripts/python.exe)) {
        py -3.12 -m venv .venv
        if ($LASTEXITCODE) { throw 'Python 3.12 environment setup failed.' }
    }
    $python = '.venv/Scripts/python.exe'
    & $python -m pip install -e '.[privacy,gliner,presidio]'
    if ($LASTEXITCODE) { throw 'Python dependency installation failed.' }
    foreach ($languageModel in 'en_core_web_sm', 'pl_core_news_sm') {
        & $python -c "from importlib.util import find_spec; raise SystemExit(find_spec('$languageModel') is None)"
        if ($LASTEXITCODE) {
            & $python -m spacy download $languageModel
            if ($LASTEXITCODE) { throw "spaCy model installation failed for $languageModel." }
        }
    }

    $downloadModels = @'
from hashlib import file_digest
from pathlib import Path

from huggingface_hub import snapshot_download

models = {
    "privacy": (
        "openai/privacy-filter", "7ffa9a043d54d1be65afb281eddf0ffbe629385b",
        "06f66b87650b988b04e218285f9fe3df6a4943416b6ffa8171f07bc56cf12a9d",
        ["viterbi_calibration.json"],
    ),
    "gliner": (
        "fastino/gliner2-privacy-filter-PII-multi", "1cb4166094dc58fa8d836429f060d6c95f62b495",
        "0280f6f39f6012da50b6640bad438d9b7e763a1b0102094115d1b710c4dd79b6",
        ["encoder_config/config.json"],
    ),
}
for name, (repo, revision, digest, extra_files) in models.items():
    folder = Path(".models") / name
    files = ["config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json", *extra_files]
    if not all((folder / file).is_file() and (folder / file).stat().st_size for file in files):
        snapshot_download(repo, revision=revision, local_dir=folder, allow_patterns=files)
    weights = folder / "model.safetensors"
    with weights.open("rb") as stream:
        actual = file_digest(stream, "sha256").hexdigest()
    if actual != digest:
        raise SystemExit(f"Checksum mismatch. Remove {weights} and rerun setup.")
    print(f"{name} model verified.")
'@
    $downloadModels | & $python -
    if ($LASTEXITCODE) { throw 'Model download or verification failed.' }

    New-Item -ItemType Directory -Path .models/tessdata -Force | Out-Null
    foreach ($language in 'eng', 'pol') {
        $destination = ".models/tessdata/$language.traineddata"
        if ((Test-Path -LiteralPath $destination) -and (Get-Item -LiteralPath $destination).Length -gt 0) {
            continue
        }
        $url = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$language.traineddata"
        curl.exe --fail --silent --show-error --location --connect-timeout 20 --max-time 120 --retry 2 $url --output "$destination.part"
        if ($LASTEXITCODE) { throw "OCR download failed for $language." }
        Move-Item -LiteralPath "$destination.part" -Destination $destination -Force
    }
}
finally {
    Pop-Location
}
