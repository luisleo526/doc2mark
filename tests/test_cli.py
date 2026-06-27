"""CLI regression tests."""

import json
import os
import subprocess
import sys
from unittest.mock import patch, MagicMock

from doc2mark.ocr.base import OCRConfig, Task


def run_cli(*args, env=None):
    cmd_env = os.environ.copy()
    cmd_env.pop("OPENAI_API_KEY", None)
    if env:
        cmd_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "doc2mark", *map(str, args)],
        capture_output=True,
        text=True,
        env=cmd_env,
        check=False,
    )


def test_cli_default_processes_text_without_api_key(tmp_path):
    input_file = tmp_path / "sample.txt"
    input_file.write_text("hello from cli", encoding="utf-8")

    result = run_cli(input_file)

    assert result.returncode == 0
    assert "hello from cli" in result.stdout
    assert "OPENAI_API_KEY" not in result.stderr


def test_cli_ocr_none_processes_text(tmp_path):
    input_file = tmp_path / "sample.txt"
    input_file.write_text("no ocr needed", encoding="utf-8")

    result = run_cli(input_file, "--ocr", "none")

    assert result.returncode == 0
    assert "no ocr needed" in result.stdout


def test_cli_json_outputs_document_payload(tmp_path):
    input_file = tmp_path / "sample.txt"
    input_file.write_text("json payload", encoding="utf-8")

    result = run_cli(input_file, "--format", "json", "--quiet")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["content"].strip() == "json payload"
    assert payload["metadata"]["filename"] == "sample.txt"
    assert payload["metadata"]["format"] == "txt"


def test_cli_both_writes_markdown_and_json(tmp_path):
    input_file = tmp_path / "sample.txt"
    input_file.write_text("both outputs", encoding="utf-8")
    output_file = tmp_path / "out"

    result = run_cli(input_file, "--format", "both", "-o", output_file, "--quiet")

    assert result.returncode == 0
    assert (tmp_path / "out.md").read_text(encoding="utf-8").strip() == "both outputs"
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert payload["content"].strip() == "both outputs"


def test_cli_rejects_ocr_images_without_provider(tmp_path):
    input_file = tmp_path / "sample.txt"
    input_file.write_text("bad config", encoding="utf-8")

    result = run_cli(input_file, "--ocr", "none", "--ocr-images")

    assert result.returncode != 0
    assert "--ocr-images requires" in result.stderr


# --- OCR config flag tests ---


def _capture_ocr_config(tmp_path, *extra_args):
    """Run the CLI with a mock loader to capture the OCRConfig that gets built.

    Returns the OCRConfig instance passed to UnifiedDocumentLoader.
    """
    input_file = tmp_path / "sample.txt"
    input_file.write_text("config test", encoding="utf-8")

    fake_result = MagicMock()
    fake_result.content = "config test"
    fake_result.metadata = MagicMock()
    fake_result.metadata.filename = "sample.txt"

    captured = {}

    class CapturingLoader:
        def __init__(self, **kwargs):
            captured["ocr_config"] = kwargs.get("ocr_config")
            captured["kwargs"] = kwargs

        def load(self, **kwargs):
            return fake_result

    with patch("doc2mark.cli.UnifiedDocumentLoader", CapturingLoader):
        from doc2mark.cli import main
        with patch("sys.argv", ["doc2mark", str(input_file), *extra_args]):
            try:
                main()
            except SystemExit:
                pass

    return captured.get("ocr_config")


def test_ocr_task_receipt_reaches_config(tmp_path):
    """--ocr-task receipt should produce OCRConfig with task=Task.RECEIPT."""
    config = _capture_ocr_config(tmp_path, "--ocr", "openai", "--ocr-task", "receipt")

    assert config is not None
    assert isinstance(config, OCRConfig)
    assert config.task == Task.RECEIPT


def test_no_structured_flag(tmp_path):
    """--no-structured should set structured=False on the OCRConfig."""
    config = _capture_ocr_config(tmp_path, "--ocr", "openai", "--no-structured")

    assert config is not None
    assert config.structured is False


def test_ocr_detail_raw(tmp_path):
    """--ocr-detail raw should set detail='raw' on the OCRConfig."""
    config = _capture_ocr_config(tmp_path, "--ocr", "openai", "--ocr-detail", "raw")

    assert config is not None
    assert config.detail == "raw"


def test_default_ocr_config_values(tmp_path):
    """Default flags should produce OCRConfig with task=AUTO, structured=True, detail='full'."""
    config = _capture_ocr_config(tmp_path, "--ocr", "openai")

    assert config is not None
    assert config.task == Task.AUTO
    assert config.structured is True
    assert config.detail == "full"


def test_ocr_config_always_built_for_non_tesseract(tmp_path):
    """OCRConfig should be built even when --ocr is none (the default)."""
    config = _capture_ocr_config(tmp_path)

    assert config is not None
    assert isinstance(config, OCRConfig)
    assert config.language is None  # language only set for tesseract


def test_tesseract_keeps_language(tmp_path):
    """--ocr tesseract should still populate OCRConfig.language from --ocr-lang."""
    config = _capture_ocr_config(tmp_path, "--ocr", "tesseract", "--ocr-lang", "deu")

    assert config is not None
    assert config.language == "deu"
    assert config.task == Task.AUTO  # default


def test_combined_flags(tmp_path):
    """All three new flags together should be reflected in the OCRConfig."""
    config = _capture_ocr_config(
        tmp_path,
        "--ocr", "openai",
        "--ocr-task", "handwriting",
        "--no-structured",
        "--ocr-detail", "raw",
    )

    assert config is not None
    assert config.task == Task.HANDWRITING
    assert config.structured is False
    assert config.detail == "raw"
