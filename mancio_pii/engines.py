from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
import re

ENGINE_NAMES = ("privacy", "gliner", "presidio")
GLINER_LABELS = """
person full_name first_name middle_name last_name date_of_birth email phone_number
address street_address city state_or_region postal_code country government_id
national_id_number passport_number drivers_license_number license_number tax_id
tax_number bank_account account_number routing_number iban payment_card card_number
card_expiry card_cvv username ip_address account_id sensitive_account_id password
secret api_key access_token recovery_code sensitive_date document_date
expiration_date transaction_date
""".split()

DOCUMENT_PATTERNS = {
    "LABELED_ADDRESS": r"(?im)^[ \t]*(?:address|street address|adres(?: zamieszkania| korespondencyjny)?)[ \t]*:[ \t]*([^\n]+)",
    "PL_PESEL": r"(?i)\bPESEL[ \t:]*((?:\d[ -]?){10}\d)(?!\d)",
    "PL_NIP": r"(?i)\b(?:NIP|Tax (?:ID|number))[ \t:]*((?:\d[ -]?){9}\d)(?!\d)",
    "PL_IBAN": r"(?i)\b(PL[ ]?(?:\d[ -]?){25}\d)(?!\d)",
    "PL_BANK_ACCOUNT": r"(?i)\b(?:NRB|(?:bank )?account (?:no\.?|number)|numer (?:rachunku|konta))[ \t:]*((?:\d[ -]?){25}\d)(?!\d)",
}


@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    label: str


def document_identifiers(text):
    for label, pattern in DOCUMENT_PATTERNS.items():
        for match in re.finditer(pattern, text):
            yield Hit(*match.span(1), label)


def chunks(text, limit=1000, overlap=200):
    """Yield overlapping text slices with offsets into the original page."""
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundary = text.rfind(" ", end - overlap, end)
            if boundary > start + overlap:
                end = boundary
        yield start, text[start:end]
        if end == len(text):
            break
        start = end - overlap


def load_engine(name, directory: Path, language):
    if name == "privacy":
        from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline

        model_path = directory / name
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        model = AutoModelForTokenClassification.from_pretrained(
            model_path, local_files_only=True, use_safetensors=True,
        )
        classify = pipeline(
            "token-classification", model=model, tokenizer=tokenizer, device=-1,
            aggregation_strategy="simple",
        )

        def detect(text):
            for item in classify(text):
                yield Hit(item["start"], item["end"], item["entity_group"])

        return detect

    if name == "gliner":
        from gliner2 import GLiNER2

        model_path = directory / name
        if not (model_path / "model.safetensors").is_file():
            raise FileNotFoundError("GLiNER2 weights missing")
        model = GLiNER2.from_pretrained(str(model_path))
        model.eval()

        def detect(text):
            result = model.extract_entities(text, GLINER_LABELS, threshold=0.45, include_spans=True)
            for label, items in result["entities"].items():
                for item in items:
                    if text[item["start"]:item["end"]] != item["text"]:
                        raise ValueError("GLiNER2 returned inconsistent offsets")
                    yield Hit(item["start"], item["end"], label)

        return detect

    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import EmailRecognizer

    class OfflineEmailRecognizer(EmailRecognizer):
        def validate_result(self, pattern_text):
            # The built-in expression requires a dotted domain. Skip the online suffix lookup.
            return None

    spacy_model = {"en": "en_core_web_sm", "pl": "pl_core_news_sm"}[language]
    if find_spec(spacy_model) is None:
        raise ModuleNotFoundError(spacy_model)
    provider = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": language, "model_name": spacy_model}],
    })
    analyzer = AnalyzerEngine(
        nlp_engine=provider.create_engine(), supported_languages=[language],
    )

    analyzer.registry.remove_recognizer("EmailRecognizer", language=language)
    analyzer.registry.add_recognizer(OfflineEmailRecognizer(supported_language=language))

    def detect(text):
        for item in analyzer.analyze(text=text, language=language, score_threshold=0.4):
            yield Hit(item.start, item.end, item.entity_type)

    return detect
