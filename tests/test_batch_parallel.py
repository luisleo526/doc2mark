"""Tests for cross-document batch parallelism and progress callbacks.

These exercise the optional ``max_workers`` / ``progress_callback`` parameters
added to ``UnifiedDocumentLoader.batch_process`` and ``batch_process_files``.
The default (sequential) behavior must be unchanged, and the concurrent path
must produce identical results plus a complete error entry for failing files.
"""

import threading

import pytest

from doc2mark.core.loader import UnifiedDocumentLoader


@pytest.fixture
def loader():
    """Loader with OCR disabled so tests never touch any network/API."""
    return UnifiedDocumentLoader(ocr_provider=None)


@pytest.fixture
def sample_files(sample_documents_dir):
    """A small set of varied, OCR-free sample documents to batch over."""
    names = [
        'sample_text.txt',
        'sample_data.csv',
        'sample_data.json',
        'sample_document.md',
    ]
    paths = [sample_documents_dir / name for name in names]
    existing = [p for p in paths if p.exists()]
    if len(existing) < 2:
        pytest.skip("Not enough OCR-free sample documents available for batch tests")
    return existing


def _strip_durations(results):
    """Drop timing fields that legitimately differ between runs."""
    stripped = {}
    for key, entry in results.items():
        entry = dict(entry)
        entry.pop('duration', None)
        stripped[key] = entry
    return stripped


class TestBatchProcessFilesParallel:
    """batch_process_files with max_workers and progress_callback."""

    def test_parallel_matches_sequential(self, loader, sample_files):
        sequential = loader.batch_process_files(
            sample_files, save_files=False, show_progress=False
        )
        parallel = loader.batch_process_files(
            sample_files, save_files=False, show_progress=False, max_workers=4
        )

        # Same set of keys, same per-file payload (ignoring timing).
        assert set(sequential.keys()) == set(parallel.keys())
        assert _strip_durations(sequential) == _strip_durations(parallel)

    def test_results_deterministic_order(self, loader, sample_files):
        """Parallel results preserve input order regardless of completion order."""
        parallel = loader.batch_process_files(
            sample_files, save_files=False, show_progress=False, max_workers=4
        )
        expected_order = [str(p) for p in sample_files]
        assert list(parallel.keys()) == expected_order

    def test_progress_callback_invoked_total_times(self, loader, sample_files):
        calls = []
        lock = threading.Lock()

        def cb(done, total, path):
            with lock:
                calls.append((done, total, path))

        results = loader.batch_process_files(
            sample_files,
            save_files=False,
            show_progress=False,
            max_workers=4,
            progress_callback=cb,
        )

        total = len(sample_files)
        assert len(calls) == total
        # total is reported consistently and the final 'done' equals the count.
        assert all(t == total for _d, t, _p in calls)
        assert {d for d, _t, _p in calls} == set(range(1, total + 1))
        # Each reported path is a real result key.
        assert {p for _d, _t, p in calls} == set(results.keys())

    def test_progress_callback_sequential(self, loader, sample_files):
        """Callback also fires in the default sequential path, in order."""
        calls = []

        def cb(done, total, path):
            calls.append((done, total, path))

        loader.batch_process_files(
            sample_files,
            save_files=False,
            show_progress=False,
            progress_callback=cb,
        )

        total = len(sample_files)
        assert [d for d, _t, _p in calls] == list(range(1, total + 1))
        assert [p for _d, _t, p in calls] == [str(p) for p in sample_files]

    def test_bad_file_records_error_entry(self, loader, sample_files, tmp_path):
        """A mix including a bad file still yields a complete dict with errors."""
        bad = tmp_path / "does_not_exist.txt"  # missing -> load() raises
        mixed = sample_files + [bad]

        results = loader.batch_process_files(
            mixed, save_files=False, show_progress=False, max_workers=4
        )

        # Every input file has an entry, including the failing one.
        assert set(results.keys()) == {str(p) for p in mixed}
        bad_entry = results[str(bad)]
        assert bad_entry['status'] == 'failed'
        assert 'error' in bad_entry and bad_entry['error']
        # Good files still succeed.
        for good in sample_files:
            assert results[str(good)]['status'] == 'success'

    def test_bad_file_parallel_matches_sequential(self, loader, sample_files, tmp_path):
        bad = tmp_path / "missing.csv"
        mixed = sample_files + [bad]

        sequential = loader.batch_process_files(
            mixed, save_files=False, show_progress=False
        )
        parallel = loader.batch_process_files(
            mixed, save_files=False, show_progress=False, max_workers=4
        )

        assert _strip_durations(sequential) == _strip_durations(parallel)

    def test_max_workers_one_is_sequential(self, loader, sample_files):
        """max_workers<=1 keeps the sequential path and identical results."""
        sequential = loader.batch_process_files(
            sample_files, save_files=False, show_progress=False
        )
        single = loader.batch_process_files(
            sample_files, save_files=False, show_progress=False, max_workers=1
        )
        assert _strip_durations(sequential) == _strip_durations(single)


class TestBatchProcessDirectoryParallel:
    """batch_process (directory variant) with max_workers and progress_callback."""

    def _seed_dir(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello world\nsecond line\n")
        (tmp_path / "b.csv").write_text("Name,Age\nAlice,30\nBob,25\n")
        (tmp_path / "c.json").write_text('{"k": "v", "n": 1}')
        (tmp_path / "d.md").write_text("# Title\n\nSome **markdown** text.\n")
        return tmp_path

    def test_directory_parallel_matches_sequential(self, loader, tmp_path):
        input_dir = self._seed_dir(tmp_path)

        sequential = loader.batch_process(
            input_dir, save_files=False, show_progress=False, recursive=False
        )
        parallel = loader.batch_process(
            input_dir, save_files=False, show_progress=False, recursive=False,
            max_workers=4,
        )

        assert set(sequential.keys()) == set(parallel.keys())
        assert _strip_durations(sequential) == _strip_durations(parallel)
        assert all(entry['status'] == 'success' for entry in parallel.values())

    def test_directory_progress_callback(self, loader, tmp_path):
        input_dir = self._seed_dir(tmp_path)
        lock = threading.Lock()
        calls = []

        def cb(done, total, path):
            with lock:
                calls.append((done, total, path))

        results = loader.batch_process(
            input_dir, save_files=False, show_progress=False, recursive=False,
            max_workers=4, progress_callback=cb,
        )

        total = len(results)
        assert total >= 4
        assert len(calls) == total
        assert all(t == total for _d, t, _p in calls)
        assert {d for d, _t, _p in calls} == set(range(1, total + 1))

    def test_directory_results_deterministic_order(self, loader, tmp_path):
        input_dir = self._seed_dir(tmp_path)
        sequential = loader.batch_process(
            input_dir, save_files=False, show_progress=False, recursive=False
        )
        parallel = loader.batch_process(
            input_dir, save_files=False, show_progress=False, recursive=False,
            max_workers=4,
        )
        # Same deterministic ordering between the two paths.
        assert list(sequential.keys()) == list(parallel.keys())
