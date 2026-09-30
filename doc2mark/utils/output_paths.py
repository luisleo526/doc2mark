"""Output names for a run that converts many files into one output folder."""

import logging
import unicodedata
from collections import Counter
from pathlib import Path, PurePath
from typing import Dict, Mapping, Sequence

logger = logging.getLogger(__name__)


def _key(path: PurePath) -> str:
    """A name as a case-insensitive file system that ignores Unicode normalisation (macOS) sees it."""
    return unicodedata.normalize("NFC", path.as_posix()).casefold()


def _without_extension(relative: PurePath) -> PurePath:
    """``relative`` without its extension; a dot in the name is part of it (``v1.2.txt`` -> ``v1.2``). A name that
    is only a suffix (``..txt``, whose stem ``.`` is not a file name) keeps its whole name."""
    return relative.with_name(relative.stem) if relative.stem not in ("", ".", "..") else relative


def plan_output_names(files: Sequence[Path], relative: Mapping[Path, PurePath], output_root: Path,
                      suffixes: Sequence[str]) -> Dict[Path, PurePath]:
    """The output name of every file: ``{file: path relative to the output folder, without extension}``.

    ``relative`` gives each file its path inside the output folder before the extension is replaced (its path
    under the input folder, or just its name for a list of files). Files of different folders never share a
    name. Files that still would (``report.txt`` and ``report.md`` both give ``report.md``), or whose output
    would be one of the files themselves (``note.md`` converted into its own folder), keep their whole file name
    instead: ``report.txt`` is written as ``report.txt.md``, and with a number (``report.txt-2``) when even that
    is taken. Every other file keeps its stem. The plan depends only on the set of files, never on the order
    they are converted in, and each clash is logged as a warning. ``suffixes`` are the extensions written
    (``(".md",)``, ``(".json",)`` or both).
    """
    def overwrites_a_source(base: PurePath) -> bool:
        return any(_key((output_root / base.with_name(base.name + suffix)).resolve()) in sources
                   for suffix in suffixes)

    order = {path: index for index, path in enumerate(files)}
    natural = {path: _without_extension(relative[path]) for path in order}
    sources = {_key(path.resolve()) for path in order}
    sharing = Counter(_key(base) for base in natural.values())
    clashing = {path for path, base in natural.items() if sharing[_key(base)] > 1 or overwrites_a_source(base)}

    def by_name(path: Path):
        return _key(relative[path]), relative[path].as_posix(), order[path]

    names = {path: natural[path] for path in order if path not in clashing}
    taken = {_key(base) for base in names.values()}
    for path in sorted(clashing, key=by_name):
        candidate, number = relative[path], 1
        while _key(candidate) in taken or overwrites_a_source(candidate):
            number += 1
            candidate = relative[path].with_name(f"{relative[path].name}-{number}")
        taken.add(_key(candidate))
        names[path] = candidate

    for base_key in sorted({_key(natural[path]) for path in clashing}):
        group = sorted((path for path in clashing if _key(natural[path]) == base_key), key=by_name)
        inputs = ", ".join(relative[path].as_posix() for path in group)
        shown = natural[group[0]].as_posix() + suffixes[0]
        outputs = ", ".join(names[path].as_posix() + suffixes[0] for path in group)
        if len(group) > 1:
            logger.warning(f"{inputs} would all be written as {shown}; writing {outputs} instead")
        else:
            logger.warning(f"{inputs} would overwrite an input file ({shown}); writing {outputs} instead")
    return names
