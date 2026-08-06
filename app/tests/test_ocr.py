"""Deterministic extraction and classification.

These run without Tesseract or any PDF library: the rules operate on text, so
the text is supplied directly. That is also how the production pipeline is
structured -- reading is one concern, understanding is another.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms.models import DocumentType  # noqa: E402
from bms.ocr.classify import classify  # noqa: E402
from bms.ocr.fields import (  # noqa: E402
    extract_fields,
    find_dates,
    mrz_check_digit,
    normalise_emirates_id,
    parse_mrz,
)
from bms.ocr.text import TextPipeline, PlainTextFile  # noqa: E402

# A synthetic TD3 passport MRZ with correct check digits.
MRZ_TEXT = """
REPUBLIC OF INDIA
PASSPORT
P<INDKANNADHASAN<<RITHICK<<<<<<<<<<<<<<<<<<<
P1234567<1IND9003071M3001019<<<<<<<<<<<<<<02
"""


def test_mrz_check_digit_matches_known_values():
    # Worked examples from the ICAO 9303 specification.
    assert mrz_check_digit("D23145890734") == 9
    assert mrz_check_digit("340712") == 7


def test_parse_mrz_reads_identity_and_validates_check_digits():
    mrz = parse_mrz(MRZ_TEXT)
    assert mrz is not None
    assert mrz.surname == "KANNADHASAN"
    assert mrz.given_names == "RITHICK"
    assert mrz.passport_no == "P1234567"
    assert mrz.nationality == "IND"
    assert mrz.date_of_birth == "1990-03-07"
    assert mrz.expiry_date == "2030-01-01"
    assert mrz.sex == "Male"
    assert mrz.checks_passed == mrz.checks_total
    assert mrz.confidence > 0.95


def test_mrz_confidence_falls_when_check_digits_fail():
    corrupted = MRZ_TEXT.replace("P1234567<1IND", "P1234567<7IND")
    mrz = parse_mrz(corrupted)
    assert mrz is not None
    assert mrz.checks_passed < mrz.checks_total
    assert mrz.confidence < 0.95


def test_parse_mrz_returns_none_without_an_mrz():
    assert parse_mrz("Just an ordinary letter with no machine readable zone.") is None


def test_mrz_century_split_keeps_dob_past_and_expiry_future():
    """A two-digit year is ambiguous; birth is past, expiry is not."""
    mrz = parse_mrz(MRZ_TEXT)
    assert mrz.date_of_birth.startswith("19")
    assert mrz.expiry_date.startswith("20")


def test_emirates_id_normalisation():
    assert normalise_emirates_id("784-1998-0432182-8") == "784-1998-0432182-8"
    assert normalise_emirates_id("784199804321828") == "784-1998-0432182-8"
    assert normalise_emirates_id("784 1998 0432182 8") == "784-1998-0432182-8"


def test_emirates_id_rejects_wrong_shape():
    # The 21-digit value seen in a real client sheet is not an Emirates ID.
    assert normalise_emirates_id("800220260210090601960") is None
    assert normalise_emirates_id("123-1998-0432182-8") is None
    assert normalise_emirates_id("784-1998-043218") is None


def test_find_dates_handles_the_formats_clients_actually_send():
    text = "Issued 05/02/2026, expires 30-04-2030, entered 14 Mar 2026, recorded 2026-01-09"
    found = dict(find_dates(text))
    assert "2026-02-05" in found.values()
    assert "2030-04-30" in found.values()
    assert "2026-03-14" in found.values()
    assert "2026-01-09" in found.values()


def test_find_dates_rejects_impossible_dates():
    assert find_dates("32/13/2026") == []


def test_extract_fields_from_a_passport():
    fields = {f.field_key: f for f in extract_fields(MRZ_TEXT)}
    assert fields["passport_no"].normalised_value == "P1234567"
    assert fields["date_of_birth"].normalised_value == "1990-03-07"
    assert fields["passport_no"].method == "mrz"


def test_extract_fields_from_a_visa_page():
    text = """
    RESIDENCE VISA
    U.I.D No: 58620894
    Visa File Number: 201/2026/7073681
    Profession: TEACHER
    """
    fields = {f.field_key: f.normalised_value for f in extract_fields(text)}
    assert fields["unified_no"] == "58620894"
    assert fields["visa_file_number"] == "201/2026/7073681"


def test_extract_fields_requires_a_label_for_uid():
    """A bare 8-digit run must not be claimed as a UID."""
    fields = {f.field_key for f in extract_fields("Reference 58620894 attached")}
    assert "unified_no" not in fields


def test_extract_fields_returns_nothing_for_empty_text():
    assert extract_fields("") == []
    assert extract_fields("   \n  ") == []


def test_classify_passport_by_mrz():
    result = classify("scan001.pdf", MRZ_TEXT)
    assert result.document_type is DocumentType.PASSPORT
    assert not result.needs_manual_review


def test_classify_birth_certificate():
    result = classify("Ahmad Ali BC Eng.pdf", "CERTIFICATE OF BIRTH\nMother\nFather\nLive birth")
    assert result.document_type is DocumentType.BIRTH_CERTIFICATE


def test_classify_cancellation():
    text = "CANCEL RESIDENCE\nCancellation of residence permit"
    assert classify("CANCEL RESIDENCE _ X.pdf", text).document_type is DocumentType.CANCELLATION


def test_classify_unknown_when_evidence_is_thin():
    """Better to ask than to file it wrongly."""
    result = classify("scan.pdf", "Page 1 of 2")
    assert result.document_type is DocumentType.UNKNOWN
    assert result.needs_manual_review


def test_classify_uses_filename_only_as_a_weak_hint():
    """A filename alone must not produce a confident classification."""
    result = classify("passport.pdf", None)
    assert result.needs_manual_review


def test_image_without_text_is_a_probable_photograph_but_still_reviewed():
    result = classify("PIC.jpg", None)
    assert result.document_type in (DocumentType.PHOTOGRAPH, DocumentType.UNKNOWN)
    assert result.needs_manual_review


def test_text_pipeline_reads_plain_text_without_any_ocr_engine():
    pipeline = TextPipeline([PlainTextFile()])
    result = pipeline.extract("notes.txt", b"Staff ID: 82270")
    assert result is not None
    assert "82270" in result.text
    assert result.source == "plain_text"


def test_text_pipeline_returns_none_when_nothing_can_read_the_file():
    """The honest answer when no engine is installed -- never a guess."""
    pipeline = TextPipeline([PlainTextFile()])
    assert pipeline.extract("scan.jpg", b"\xff\xd8\xff\xe0binary") is None


# ------------------------------------------------- Tesseract bundled in-project


def test_a_bundled_tesseract_is_found_without_any_configuration(tmp_path, monkeypatch):
    """A locked-down host may not permit an installer, so a copy that lives in
    the project folder has to be picked up on its own."""
    from bms import config as config_module

    vendor = tmp_path / "vendor"
    (vendor / "tesseract").mkdir(parents=True)
    binary = vendor / "tesseract" / "tesseract"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    monkeypatch.setattr(config_module, "VENDOR_ROOT", vendor)
    monkeypatch.delenv("BMS_TESSERACT_CMD", raising=False)
    assert config_module.bundled_tesseract() == str(binary)
    assert config_module._resolve_tesseract() == str(binary)


def test_an_explicit_setting_wins_over_a_bundled_copy(tmp_path, monkeypatch):
    from bms import config as config_module

    vendor = tmp_path / "vendor"
    (vendor / "tesseract").mkdir(parents=True)
    binary = vendor / "tesseract" / "tesseract"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    monkeypatch.setattr(config_module, "VENDOR_ROOT", vendor)
    monkeypatch.setenv("BMS_TESSERACT_CMD", "/opt/custom/tesseract")
    assert config_module._resolve_tesseract() == "/opt/custom/tesseract"


def test_nothing_bundled_falls_back_to_the_system_tesseract(tmp_path, monkeypatch):
    from bms import config as config_module

    monkeypatch.setattr(config_module, "VENDOR_ROOT", tmp_path / "empty")
    monkeypatch.delenv("BMS_TESSERACT_CMD", raising=False)
    assert config_module.bundled_tesseract() is None
    assert config_module._resolve_tesseract() == "tesseract"


def test_a_bundled_copy_gets_its_own_language_files(tmp_path):
    """Without TESSDATA_PREFIX a portable Tesseract fails with an unhelpful
    'Error opening data file', so this is what makes bundling actually work."""
    from bms.config import Settings
    from bms.ocr.text import TesseractOcr

    root = tmp_path / "tesseract"
    (root / "tessdata").mkdir(parents=True)
    binary = root / "tesseract"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    config = Settings(tesseract_cmd=str(binary))
    assert config.tessdata_dir == root / "tessdata"

    environment = TesseractOcr(config)._environment()
    assert environment is not None
    assert environment["TESSDATA_PREFIX"] == str(root / "tessdata")


def test_a_system_tesseract_is_left_to_find_its_own_data():
    """Overriding TESSDATA_PREFIX for a system install would break it."""
    from bms.config import Settings
    from bms.ocr.text import TesseractOcr

    config = Settings(tesseract_cmd="tesseract")
    assert config.tessdata_dir is None
    assert TesseractOcr(config)._environment() is None


def test_an_absolute_path_is_detected_even_though_it_is_not_on_PATH(tmp_path):
    """shutil.which only searches PATH, so a bundled copy needs a direct check."""
    from bms.config import Settings
    from bms.ocr.text import TesseractOcr

    binary = tmp_path / "tesseract"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    assert TesseractOcr(Settings(tesseract_cmd=str(binary))).available()
    assert not TesseractOcr(Settings(tesseract_cmd=str(tmp_path / "absent"))).available()


def test_the_shipped_env_example_does_not_disable_bundling():
    """It set BMS_TESSERACT_CMD=tesseract, which is the highest-priority branch.

    Both installers copy .env.example to app/.env, start-linux.sh sources it and
    the systemd unit loads it -- so every install produced by the project's own
    installers silently ignored a bundled Tesseract, while the documentation
    said bundling needed no configuration at all.
    """
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / "deploy" / ".env.example"
    for line in example.read_text().splitlines():
        stripped = line.strip()
        assert not stripped.startswith("BMS_TESSERACT_CMD="), (
            "an active BMS_TESSERACT_CMD overrides and disables vendor/tesseract/"
        )


def test_a_bundled_copy_is_told_where_its_languages_are_on_the_command_line(tmp_path):
    """TESSDATA_PREFIX alone is not portable across Tesseract versions.

    Tesseract 5 reads it as the directory holding the language files; Tesseract 4
    -- still what Debian 11 and Ubuntu 22.04 ship -- appends "tessdata/" and then
    cannot find them. `--tessdata-dir` means the same thing in both.
    """
    from bms.config import Settings
    from bms.ocr.text import TesseractOcr

    root = tmp_path / "tesseract"
    (root / "tessdata").mkdir(parents=True)
    binary = root / "tesseract"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    bundled = TesseractOcr(Settings(tesseract_cmd=str(binary)))
    assert bundled._tessdata_arguments() == ["--tessdata-dir", str(root / "tessdata")]

    # A system install knows its own prefix and must be left alone.
    assert TesseractOcr(Settings(tesseract_cmd="tesseract"))._tessdata_arguments() == []
