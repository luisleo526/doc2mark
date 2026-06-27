"""Unit tests for doc2mark/formats/legacy.py (LegacyProcessor).

These tests run WITHOUT a real LibreOffice installation by mocking
subprocess calls and the _find_libreoffice / _convert_with_libreoffice
internals.  They characterise the current behaviour so future refactors
cannot silently regress it.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from doc2mark.core.base import (
    ConversionError,
    DocumentFormat,
    DocumentMetadata,
    ProcessedDocument,
    ProcessingError,
)
from doc2mark.formats.legacy import LegacyProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_SOFFICE = "/usr/bin/fake-soffice"


def _make_processor(libreoffice_path=FAKE_SOFFICE):
    """Create a LegacyProcessor with a mocked LibreOffice path.

    We patch _find_libreoffice so that __init__ does not probe the real
    filesystem.
    """
    with patch.object(LegacyProcessor, "_find_libreoffice", return_value=libreoffice_path):
        proc = LegacyProcessor(ocr=None)
    return proc


def _dummy_processed_doc(fmt=DocumentFormat.DOCX, filename="converted.docx"):
    """Return a minimal ProcessedDocument that the mocked office processor
    would hand back."""
    meta = DocumentMetadata(
        filename=filename,
        format=fmt,
        size_bytes=100,
    )
    return ProcessedDocument(content="# Hello world\n", metadata=meta)


# ---------------------------------------------------------------------------
# can_process
# ---------------------------------------------------------------------------

class TestCanProcess:
    """LegacyProcessor.can_process recognises legacy extensions."""

    @pytest.mark.parametrize("ext", ["doc", "xls", "ppt", "rtf", "pps"])
    def test_legacy_extensions_accepted(self, ext, tmp_path):
        proc = _make_processor()
        fpath = tmp_path / f"file.{ext}"
        fpath.touch()
        assert proc.can_process(fpath) is True

    @pytest.mark.parametrize("ext", ["docx", "xlsx", "pptx", "pdf", "txt", "csv"])
    def test_modern_extensions_rejected(self, ext, tmp_path):
        proc = _make_processor()
        fpath = tmp_path / f"file.{ext}"
        fpath.touch()
        assert proc.can_process(fpath) is False

    def test_case_insensitive(self, tmp_path):
        proc = _make_processor()
        fpath = tmp_path / "FILE.DOC"
        fpath.touch()
        assert proc.can_process(fpath) is True

    def test_accepts_string_path(self, tmp_path):
        proc = _make_processor()
        fpath = tmp_path / "file.doc"
        fpath.touch()
        assert proc.can_process(str(fpath)) is True


# ---------------------------------------------------------------------------
# _find_libreoffice
# ---------------------------------------------------------------------------

class TestFindLibreOffice:
    """Verify the discovery logic without touching real binaries."""

    def test_returns_first_existing_path(self):
        """If a well-known path exists on disk the method returns it."""
        with patch("os.path.exists", side_effect=lambda p: p == "/usr/bin/soffice"):
            with patch.object(LegacyProcessor, "__init__", lambda self, **kw: None):
                proc = LegacyProcessor.__new__(LegacyProcessor)
                result = proc._find_libreoffice()
        assert result == "/usr/bin/soffice"

    def test_falls_back_to_which_libreoffice(self):
        """When no well-known path exists, 'which libreoffice' is tried."""
        def fake_run(cmd, **kw):
            m = MagicMock()
            if cmd == ["which", "libreoffice"]:
                m.returncode = 0
                m.stdout = "/opt/lo/bin/libreoffice\n"
            else:
                m.returncode = 1
                m.stdout = ""
            return m

        with patch("os.path.exists", return_value=False):
            with patch("subprocess.run", side_effect=fake_run):
                with patch.object(LegacyProcessor, "__init__", lambda self, **kw: None):
                    proc = LegacyProcessor.__new__(LegacyProcessor)
                    result = proc._find_libreoffice()
        assert result == "/opt/lo/bin/libreoffice"

    def test_falls_back_to_which_soffice(self):
        """When 'which libreoffice' fails, 'which soffice' is tried."""
        def fake_run(cmd, **kw):
            m = MagicMock()
            if cmd == ["which", "soffice"]:
                m.returncode = 0
                m.stdout = "/snap/bin/soffice\n"
            else:
                m.returncode = 1
                m.stdout = ""
            return m

        with patch("os.path.exists", return_value=False):
            with patch("subprocess.run", side_effect=fake_run):
                with patch.object(LegacyProcessor, "__init__", lambda self, **kw: None):
                    proc = LegacyProcessor.__new__(LegacyProcessor)
                    result = proc._find_libreoffice()
        assert result == "/snap/bin/soffice"

    def test_returns_none_when_nothing_found(self):
        """If nothing works, None is returned."""
        def fake_run(cmd, **kw):
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            return m

        with patch("os.path.exists", return_value=False):
            with patch("subprocess.run", side_effect=fake_run):
                with patch.object(LegacyProcessor, "__init__", lambda self, **kw: None):
                    proc = LegacyProcessor.__new__(LegacyProcessor)
                    result = proc._find_libreoffice()
        assert result is None

    def test_subprocess_exception_is_swallowed(self):
        """If subprocess.run itself raises, the method does not blow up."""
        with patch("os.path.exists", return_value=False):
            with patch("subprocess.run", side_effect=OSError("nope")):
                with patch.object(LegacyProcessor, "__init__", lambda self, **kw: None):
                    proc = LegacyProcessor.__new__(LegacyProcessor)
                    result = proc._find_libreoffice()
        assert result is None


# ---------------------------------------------------------------------------
# check_libreoffice_installed
# ---------------------------------------------------------------------------

class TestCheckLibreOfficeInstalled:

    def test_true_when_path_set(self):
        proc = _make_processor(libreoffice_path=FAKE_SOFFICE)
        assert proc.check_libreoffice_installed() is True

    def test_false_when_path_none(self):
        proc = _make_processor(libreoffice_path=None)
        assert proc.check_libreoffice_installed() is False


# ---------------------------------------------------------------------------
# _convert_with_libreoffice  --  command-line construction
# ---------------------------------------------------------------------------

class TestConvertCommandLine:
    """Assert the exact soffice command line that gets built."""

    @pytest.mark.parametrize("target_format", ["docx", "xlsx", "pptx"])
    def test_command_structure(self, tmp_path, target_format):
        proc = _make_processor()
        input_file = tmp_path / f"input.doc"
        input_file.touch()
        outdir = str(tmp_path / "out")

        expected_cmd = [
            FAKE_SOFFICE,
            "--headless",
            "--convert-to", target_format,
            "--outdir", outdir,
            str(input_file),
        ]

        # Make conversion produce the expected output file
        converted = Path(outdir) / f"input.{target_format}"

        def fake_run(cmd, **kw):
            # Verify the command before returning success
            assert cmd == expected_cmd, f"Unexpected command: {cmd}"
            Path(outdir).mkdir(parents=True, exist_ok=True)
            converted.touch()
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=fake_run):
            result = proc._convert_with_libreoffice(input_file, target_format, outdir)
        assert result == converted

    def test_timeout_is_60_seconds(self, tmp_path):
        """subprocess.run is called with timeout=60."""
        proc = _make_processor()
        input_file = tmp_path / "input.doc"
        input_file.touch()
        outdir = str(tmp_path / "out")

        captured_kwargs = {}

        def fake_run(cmd, **kw):
            captured_kwargs.update(kw)
            Path(outdir).mkdir(parents=True, exist_ok=True)
            (Path(outdir) / "input.docx").touch()
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=fake_run):
            proc._convert_with_libreoffice(input_file, "docx", outdir)

        assert captured_kwargs.get("timeout") == 60

    def test_capture_output_and_text_flags(self, tmp_path):
        """subprocess.run is called with capture_output=True and text=True."""
        proc = _make_processor()
        input_file = tmp_path / "input.doc"
        input_file.touch()
        outdir = str(tmp_path / "out")

        captured_kwargs = {}

        def fake_run(cmd, **kw):
            captured_kwargs.update(kw)
            Path(outdir).mkdir(parents=True, exist_ok=True)
            (Path(outdir) / "input.docx").touch()
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=fake_run):
            proc._convert_with_libreoffice(input_file, "docx", outdir)

        assert captured_kwargs.get("capture_output") is True
        assert captured_kwargs.get("text") is True


# ---------------------------------------------------------------------------
# _convert_with_libreoffice  --  failure paths
# ---------------------------------------------------------------------------

class TestConvertFailures:

    def test_nonzero_returncode_raises_conversion_error(self, tmp_path):
        proc = _make_processor()
        input_file = tmp_path / "input.doc"
        input_file.touch()
        outdir = str(tmp_path / "out")

        def fake_run(cmd, **kw):
            m = MagicMock()
            m.returncode = 1
            m.stderr = "segfault in filter"
            m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(ConversionError, match="LibreOffice conversion failed"):
                proc._convert_with_libreoffice(input_file, "docx", outdir)

    def test_stderr_included_in_error_message(self, tmp_path):
        proc = _make_processor()
        input_file = tmp_path / "input.doc"
        input_file.touch()
        outdir = str(tmp_path / "out")

        def fake_run(cmd, **kw):
            m = MagicMock()
            m.returncode = 1
            m.stderr = "specific error detail"
            m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(ConversionError, match="specific error detail"):
                proc._convert_with_libreoffice(input_file, "docx", outdir)

    def test_stdout_used_when_stderr_empty(self, tmp_path):
        proc = _make_processor()
        input_file = tmp_path / "input.doc"
        input_file.touch()
        outdir = str(tmp_path / "out")

        def fake_run(cmd, **kw):
            m = MagicMock()
            m.returncode = 1
            m.stderr = ""
            m.stdout = "stdout error info"
            return m

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(ConversionError, match="stdout error info"):
                proc._convert_with_libreoffice(input_file, "docx", outdir)

    def test_timeout_raises_conversion_error(self, tmp_path):
        proc = _make_processor()
        input_file = tmp_path / "input.doc"
        input_file.touch()
        outdir = str(tmp_path / "out")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="soffice", timeout=60)):
            with pytest.raises(ConversionError, match="timed out"):
                proc._convert_with_libreoffice(input_file, "docx", outdir)

    def test_converted_file_not_found_raises(self, tmp_path):
        """When soffice exits 0 but produces no output file, ConversionError."""
        proc = _make_processor()
        input_file = tmp_path / "input.doc"
        input_file.touch()
        outdir = str(tmp_path / "out")
        Path(outdir).mkdir(parents=True, exist_ok=True)

        def fake_run(cmd, **kw):
            # Return success but produce no file
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(ConversionError, match="Converted file not found"):
                proc._convert_with_libreoffice(input_file, "docx", outdir)

    def test_fallback_glob_when_expected_name_missing(self, tmp_path):
        """If the expected filename is absent but another file with the right
        extension exists, the converter picks it up (current behaviour)."""
        proc = _make_processor()
        input_file = tmp_path / "input.doc"
        input_file.touch()
        outdir = str(tmp_path / "out")
        Path(outdir).mkdir(parents=True, exist_ok=True)

        def fake_run(cmd, **kw):
            # Produce a file with a different stem
            (Path(outdir) / "different_name.docx").touch()
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=fake_run):
            result = proc._convert_with_libreoffice(input_file, "docx", outdir)
        assert result.suffix == ".docx"
        assert result.name == "different_name.docx"


# ---------------------------------------------------------------------------
# process()  --  missing LibreOffice
# ---------------------------------------------------------------------------

class TestProcessMissingLibreOffice:

    def test_raises_processing_error_with_install_hint(self, tmp_path):
        proc = _make_processor(libreoffice_path=None)
        fpath = tmp_path / "legacy.doc"
        fpath.write_bytes(b"\x00" * 10)

        with pytest.raises(ProcessingError, match="LibreOffice is required"):
            proc.process(fpath)

    def test_error_message_contains_url(self, tmp_path):
        proc = _make_processor(libreoffice_path=None)
        fpath = tmp_path / "legacy.doc"
        fpath.write_bytes(b"\x00" * 10)

        with pytest.raises(ProcessingError, match="https://www.libreoffice.org/"):
            proc.process(fpath)


# ---------------------------------------------------------------------------
# process()  --  unsupported extension
# ---------------------------------------------------------------------------

class TestProcessUnsupportedFormat:

    def test_unsupported_extension_raises(self, tmp_path):
        """An extension not in format_mapping inside process() raises
        ProcessingError.  We have to craft a processor whose can_process
        would normally reject it; the check happens inside process() anyway."""
        proc = _make_processor()
        fpath = tmp_path / "file.xyz"
        fpath.write_bytes(b"\x00" * 10)

        # The code checks `extension not in format_mapping` after the
        # LibreOffice check.  Since .xyz is not in the mapping, we expect
        # ProcessingError wrapping the inner error.
        with pytest.raises(ProcessingError):
            proc.process(fpath)


# ---------------------------------------------------------------------------
# process()  --  format mapping  (successful conversion)
# ---------------------------------------------------------------------------

class TestProcessFormatMapping:
    """Verify that each legacy extension maps to the right target format
    and DocumentFormat enum, and that metadata is patched correctly."""

    FORMAT_CASES = [
        ("doc", "docx", DocumentFormat.DOC),
        ("xls", "xlsx", DocumentFormat.XLS),
        ("ppt", "pptx", DocumentFormat.PPT),
        ("pps", "pptx", DocumentFormat.PPS),
        ("rtf", "docx", DocumentFormat.RTF),
    ]

    @pytest.mark.parametrize("ext,target,expected_fmt", FORMAT_CASES)
    def test_target_format_and_metadata(self, ext, target, expected_fmt, tmp_path):
        proc = _make_processor()
        fpath = tmp_path / f"file.{ext}"
        fpath.write_bytes(b"\x00" * 42)

        dummy_doc = _dummy_processed_doc(
            fmt=DocumentFormat.DOCX, filename=f"file.{target}"
        )

        with patch.object(
            proc, "_convert_with_libreoffice", return_value=Path(f"/tmp/file.{target}")
        ) as mock_convert:
            with patch.object(
                type(proc), "office_processor", new_callable=PropertyMock
            ) as mock_office_prop:
                mock_office = MagicMock()
                mock_office.process.return_value = dummy_doc
                mock_office_prop.return_value = mock_office

                result = proc.process(fpath)

                # Assert _convert_with_libreoffice was called with the
                # correct target format.
                call_args = mock_convert.call_args
                assert call_args[0][1] == target  # second positional arg is target_format

        # Metadata should reflect the ORIGINAL file, not the converted one.
        assert result.metadata.format == expected_fmt
        assert result.metadata.filename == fpath.name
        assert result.metadata.size_bytes == 42

        # Extra metadata records the conversion.
        assert result.metadata.extra["converted_from"] == ext
        assert result.metadata.extra["converted_to"] == target

    @pytest.mark.parametrize("ext,target,expected_fmt", FORMAT_CASES)
    def test_office_processor_receives_converted_path(self, ext, target, expected_fmt, tmp_path):
        """The OfficeProcessor.process call gets the path returned by
        _convert_with_libreoffice."""
        proc = _make_processor()
        fpath = tmp_path / f"file.{ext}"
        fpath.write_bytes(b"\x00" * 10)

        converted = Path(f"/fake/converted.{target}")
        dummy_doc = _dummy_processed_doc()

        with patch.object(proc, "_convert_with_libreoffice", return_value=converted):
            with patch.object(
                type(proc), "office_processor", new_callable=PropertyMock
            ) as mock_office_prop:
                mock_office = MagicMock()
                mock_office.process.return_value = dummy_doc
                mock_office_prop.return_value = mock_office

                proc.process(fpath)
                mock_office.process.assert_called_once_with(converted)


# ---------------------------------------------------------------------------
# process()  --  conversion failure propagation
# ---------------------------------------------------------------------------

class TestProcessConversionFailure:

    def test_conversion_error_wrapped_in_processing_error(self, tmp_path):
        """A ConversionError from _convert_with_libreoffice is re-wrapped
        into ProcessingError('Legacy format processing failed: ...')."""
        proc = _make_processor()
        fpath = tmp_path / "file.doc"
        fpath.write_bytes(b"\x00" * 10)

        with patch.object(
            proc,
            "_convert_with_libreoffice",
            side_effect=ConversionError("boom"),
        ):
            with pytest.raises(ProcessingError, match="Legacy format processing failed"):
                proc.process(fpath)

    def test_original_message_preserved_in_wrapper(self, tmp_path):
        proc = _make_processor()
        fpath = tmp_path / "file.doc"
        fpath.write_bytes(b"\x00" * 10)

        with patch.object(
            proc,
            "_convert_with_libreoffice",
            side_effect=ConversionError("disk full"),
        ):
            with pytest.raises(ProcessingError, match="disk full"):
                proc.process(fpath)

    def test_office_processor_error_also_wrapped(self, tmp_path):
        """If the office processor blows up, we still get ProcessingError."""
        proc = _make_processor()
        fpath = tmp_path / "file.doc"
        fpath.write_bytes(b"\x00" * 10)

        with patch.object(
            proc,
            "_convert_with_libreoffice",
            return_value=Path("/fake/file.docx"),
        ):
            with patch.object(
                type(proc), "office_processor", new_callable=PropertyMock
            ) as mock_office_prop:
                mock_office = MagicMock()
                mock_office.process.side_effect = RuntimeError("parse failed")
                mock_office_prop.return_value = mock_office

                with pytest.raises(ProcessingError, match="Legacy format processing failed"):
                    proc.process(fpath)


# ---------------------------------------------------------------------------
# process()  --  metadata.extra initialisation
# ---------------------------------------------------------------------------

class TestMetadataExtraInit:

    def test_extra_none_gets_initialised(self, tmp_path):
        """When the office processor returns metadata with extra=None the
        code initialises it to a dict before writing conversion keys."""
        proc = _make_processor()
        fpath = tmp_path / "file.doc"
        fpath.write_bytes(b"\x00" * 5)

        meta = DocumentMetadata(
            filename="file.docx",
            format=DocumentFormat.DOCX,
            size_bytes=100,
        )
        # Explicitly set extra to None to test the guard
        meta.extra = None
        dummy_doc = ProcessedDocument(content="text", metadata=meta)

        with patch.object(proc, "_convert_with_libreoffice", return_value=Path("/x/file.docx")):
            with patch.object(
                type(proc), "office_processor", new_callable=PropertyMock
            ) as mock_office_prop:
                mock_office = MagicMock()
                mock_office.process.return_value = dummy_doc
                mock_office_prop.return_value = mock_office

                result = proc.process(fpath)

        assert isinstance(result.metadata.extra, dict)
        assert "converted_from" in result.metadata.extra

    def test_existing_extra_preserved(self, tmp_path):
        """Pre-existing keys in extra are not clobbered."""
        proc = _make_processor()
        fpath = tmp_path / "file.doc"
        fpath.write_bytes(b"\x00" * 5)

        meta = DocumentMetadata(
            filename="file.docx",
            format=DocumentFormat.DOCX,
            size_bytes=100,
            extra={"existing_key": "keep_me"},
        )
        dummy_doc = ProcessedDocument(content="text", metadata=meta)

        with patch.object(proc, "_convert_with_libreoffice", return_value=Path("/x/file.docx")):
            with patch.object(
                type(proc), "office_processor", new_callable=PropertyMock
            ) as mock_office_prop:
                mock_office = MagicMock()
                mock_office.process.return_value = dummy_doc
                mock_office_prop.return_value = mock_office

                result = proc.process(fpath)

        assert result.metadata.extra["existing_key"] == "keep_me"
        assert result.metadata.extra["converted_from"] == "doc"


# ---------------------------------------------------------------------------
# Lazy-loading of office_processor property
# ---------------------------------------------------------------------------

class TestOfficeProcessorProperty:

    def test_lazy_loaded(self):
        """office_processor is None until first access."""
        proc = _make_processor()
        assert proc._office_processor is None

    def test_returns_office_processor_instance(self):
        """Accessing the property imports and creates an OfficeProcessor."""
        from doc2mark.formats.office import OfficeProcessor

        proc = _make_processor()
        op = proc.office_processor
        assert isinstance(op, OfficeProcessor)

    def test_caches_instance(self):
        """Subsequent accesses return the same instance (no re-import)."""
        proc = _make_processor()
        first = proc.office_processor
        second = proc.office_processor
        assert first is second


# ---------------------------------------------------------------------------
# Edge: metadata.extra falsy-but-not-None (empty dict from dataclass default)
# ---------------------------------------------------------------------------

class TestMetadataExtraEdge:

    def test_extra_empty_dict_not_replaced(self, tmp_path):
        """When extra is {} (the dataclass default), the 'if not' guard
        evaluates to True and the code replaces it with a new {}.  This
        test characterises that current behaviour."""
        proc = _make_processor()
        fpath = tmp_path / "file.doc"
        fpath.write_bytes(b"\x00" * 5)

        meta = DocumentMetadata(
            filename="file.docx",
            format=DocumentFormat.DOCX,
            size_bytes=100,
            extra={},
        )
        dummy_doc = ProcessedDocument(content="text", metadata=meta)

        with patch.object(proc, "_convert_with_libreoffice", return_value=Path("/x/file.docx")):
            with patch.object(
                type(proc), "office_processor", new_callable=PropertyMock
            ) as mock_office_prop:
                mock_office = MagicMock()
                mock_office.process.return_value = dummy_doc
                mock_office_prop.return_value = mock_office

                result = proc.process(fpath)

        # The guard `if not result.metadata.extra` treats {} as falsy,
        # so it sets extra = {}.  Either way conversion keys must be present.
        assert "converted_from" in result.metadata.extra
        assert "converted_to" in result.metadata.extra

    def test_extra_empty_dict_guard_preserves_identity(self, tmp_path):
        """The guard in process() is 'if extra is None', so a pre-existing
        (even empty) extra dict keeps its object identity and conversion keys
        are added to it in place, rather than being replaced by a fresh {}."""
        proc = _make_processor()
        fpath = tmp_path / "file.doc"
        fpath.write_bytes(b"\x00" * 5)

        original_extra = {}
        meta = DocumentMetadata(
            filename="file.docx",
            format=DocumentFormat.DOCX,
            size_bytes=100,
            extra=original_extra,
        )
        dummy_doc = ProcessedDocument(content="text", metadata=meta)

        with patch.object(proc, "_convert_with_libreoffice", return_value=Path("/x/file.docx")):
            with patch.object(
                type(proc), "office_processor", new_callable=PropertyMock
            ) as mock_office_prop:
                mock_office = MagicMock()
                mock_office.process.return_value = dummy_doc
                mock_office_prop.return_value = mock_office

                result = proc.process(fpath)

        # The guard 'if result.metadata.extra is None' is False for {},
        # so the original dict is kept and mutated in place.
        assert result.metadata.extra is original_extra
        assert result.metadata.extra["converted_from"] == "doc"
