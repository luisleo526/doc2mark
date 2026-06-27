"""CLI regression tests."""

import json
import os
import subprocess
import sys


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
