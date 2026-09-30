"""Does the optional TypeSafe judge make doc2mark's decisions better? Per hook, on labelled data.

For each hook (legibility, boilerplate, non_content) this script reads the labelled sets in
``tests/data/judge`` (see its README), decides every item twice -- with the deterministic rule
alone, and with the rule plus the judge exactly as the pipeline combines them -- and prints
accuracy, precision and recall of the action the hook takes on three sets:

- TRAIN: the only set thresholds are calibrated on (``--calibrate``, legibility and boilerplate);
- TEST: held out, from the same generators but split by family (every variant of one template,
  base text or document in one split) with near-duplicates kept together;
- EXTERNAL (``tests/data/judge/external``): items written by the PR #22 reviewer, never used for
  calibration.

How the rule and the judge combine (as in the pipeline):

- legibility (action: the page is illegible and OCR'd): the deterministic detector flags the
  layer, else the judge is asked (``strategy.wants_judgment``) and its probability is below the
  threshold; pages the route OCRs anyway (searchable scans) are not scored;
- boilerplate (action: a repeated line the rule keeps on every page is thinned to its first
  copy): the judge's probability is at or above the threshold; the label is the output the line
  needs (``expected_copies`` "one" or "all"). Lines the judge is never asked about (a running
  header's first copy, which the rule already reduced to one) are not scored;
- non_content (action: the answer is no content): the whole-answer patterns fire, else the judge
  is asked (answers of at most ``refusal.MAX_JUDGE_CHARS``) and its probability is at or above
  the act threshold (0.95); from the suspect threshold (0.90) up to it the answer is kept and
  only flagged, which counts as keeping it.

Needs the ``doc2mark[typesafe]`` extra and ``TYPESAFE_API_KEY``; verdicts are cached (``--cache-dir``,
default ``$DOC2MARK_JUDGE_CACHE`` or ``~/.cache/doc2mark/judge``), so a re-run is free and
deterministic. ``--deck PATH`` adds the slice built at run time from a real deck read in place
(``judge_sets.deck_items``; never committed) and a whole-document cost/latency run on it.

    python eval/judge_eval.py --calibrate
    python eval/judge_eval.py --calibrate --deck /path/to/deck.pdf --json out.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from doc2mark.core.strategy import text_layer_stats, wants_judgment  # noqa: E402
from doc2mark.judge import questions as Q  # noqa: E402
from doc2mark.judge.typesafe import TypeSafeJudge, _pipeline_threshold  # noqa: E402
from doc2mark.ocr.refusal import MAX_JUDGE_CHARS, _normalize, matches_non_content_pattern  # noqa: E402

HOOKS = ("legibility", "boilerplate", "non_content")
DATA = ROOT / "tests" / "data" / "judge"
SETS = ("train", "test", "external")
#: What the hook's action is, i.e. the positive class of precision/recall.
ACTION = {"legibility": "illegible page", "boilerplate": "thinned to one copy", "non_content": "no content"}
#: The hooks whose threshold is calibrated on TRAIN; non_content's act and suspect thresholds are a
#: fixed policy (the review set them above the overlap of refusals and real answers).
CALIBRATED = ("legibility", "boilerplate")
GRID = [round(0.05 * n, 2) for n in range(1, 20)]


def load(hook: str, data: Path) -> List[Dict[str, Any]]:
    """The labelled items of one hook: the TRAIN/TEST set, then the EXTERNAL one (split "external")."""
    items = []
    for path, split in ((data / f"{hook}.jsonl", None), (data / "external" / f"{hook}.jsonl", "external")):
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as handle:
            items += [dict(json.loads(line), **({"split": split} if split else {})) for line in handle if line.strip()]
    return items


# --- per item: the rule's verdict, whether the judge is asked, the judge's arguments -----------------


def positive(hook: str, item: Dict[str, Any]) -> bool:
    """Whether the item's label is the hook's action class."""
    return item["label"] == (0 if hook == "legibility" else 1)


def scored(hook: str, item: Dict[str, Any]) -> bool:
    """Whether the hook's decision matters for the item: not a page the route OCRs for another reason
    (a searchable scan), not a line the judge is never asked about."""
    route = item.get("route")
    if isinstance(route, str) and route.startswith("image:") and route != "image:illegible_text_layer":
        return False
    return item.get("asked", True) is not False


def rule(hook: str, item: Dict[str, Any]) -> Tuple[bool, bool, tuple]:
    """(the rule acts, the judge is asked, the judge's arguments) for one item."""
    if hook == "legibility":
        layer = text_layer_stats([tuple(span) for span in item["spans"]])
        return layer.garbled, wants_judgment(layer), (item["text"],)
    if hook == "boilerplate":
        return False, True, (item["text"], item["context"])
    answer = _normalize(item["text"])
    fired = matches_non_content_pattern(answer)
    return fired, bool(answer) and not fired and len(answer) <= MAX_JUDGE_CHARS, (answer,)


def decide(hook: str, fired: bool, asked: bool, p: Optional[float], threshold: float) -> bool:
    """The pipeline's decision: the rule, or else the judge's probability against the threshold."""
    if fired:
        return True
    if not asked or p is None:
        return False
    return p < threshold if hook == "legibility" else p >= threshold


def scores(truth: Sequence[bool], predicted: Sequence[bool]) -> Dict[str, Any]:
    tp = sum(1 for t, p in zip(truth, predicted) if t and p)
    fp = sum(1 for t, p in zip(truth, predicted) if not t and p)
    fn = sum(1 for t, p in zip(truth, predicted) if t and not p)
    tn = len(truth) - tp - fp - fn
    return {
        "n": len(truth), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": (tp + tn) / len(truth) if truth else None,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def calibrate(hook: str, rows: List[Dict[str, Any]]) -> Tuple[float, float]:
    """The threshold with the best TRAIN accuracy: (threshold, accuracy). Among tied thresholds the
    pipeline's own one wins, else the one that acts least (verbatim first: the lowest for legibility,
    which acts below its threshold, the highest for the others). The rule was fixed before the
    TRAIN/TEST re-split of review round 1."""
    default = Q.RAW_THRESHOLDS[hook]
    grid = sorted(set(GRID + [default]))
    truth = [row["positive"] for row in rows]
    results = []
    for threshold in grid:
        predicted = [decide(hook, row["fired"], row["asked"], row["p"], threshold) for row in rows]
        results.append((scores(truth, predicted)["accuracy"] or 0.0, threshold))
    best = max(accuracy for accuracy, _ in results)
    tied = [threshold for accuracy, threshold in results if accuracy == best]
    if default in tied:
        return default, best
    return (min(tied) if hook == "legibility" else max(tied)), best


def evaluate(hook: str, items: List[Dict[str, Any]], judge: TypeSafeJudge) -> List[Dict[str, Any]]:
    """Ask the judge about every scored item it would be asked about; one row per scored item."""
    rows = []
    for item in items:
        if not scored(hook, item):
            continue
        fired, asked, args = rule(hook, item)
        verdict = judge.verdict(hook, *args) if asked else None
        rows.append({
            "id": item["id"], "kind": item.get("kind"), "split": item.get("split"), "label": item["label"],
            "positive": positive(hook, item), "fired": fired, "asked": asked,
            "p": verdict.probability if verdict else None,
            "latency_ms": verdict.latency_ms if verdict else None,
            "input_tokens": verdict.input_tokens if verdict else None,
            "copies": len((item.get("context") or {}).get("pages") or []) if hook == "boilerplate" else None,
        })
    return rows


def report(hook: str, rows: List[Dict[str, Any]], threshold: float, split: str) -> Dict[str, Any]:
    chosen = [row for row in rows if row["split"] == split]
    truth = [row["positive"] for row in chosen]
    decisions = [decide(hook, row["fired"], row["asked"], row["p"], threshold) for row in chosen]
    result = {"split": split, "threshold": threshold, "asked": sum(row["asked"] for row in chosen),
              "unanswered": sum(1 for row in chosen if row["asked"] and row["p"] is None),
              "rule": scores(truth, [row["fired"] for row in chosen]), "rule+jev": scores(truth, decisions)}
    if hook == "boilerplate":
        # Output view: copies of repeated lines the judge removed (every one but the first).
        removed = [(row, max(0, (row["copies"] or 0) - 1)) for row, acted in zip(chosen, decisions) if acted]
        result["copies_removed"] = sum(n for _, n in removed)
        result["copies_removed_wrongly"] = sum(n for row, n in removed if not row["positive"])
        result["copies_left_to_remove"] = sum(max(0, (row["copies"] or 0) - 1) for row, acted in zip(chosen, decisions)
                                              if row["positive"] and not acted)
    if hook == "non_content":
        suspect = Q.RAW_SUSPECT_THRESHOLDS["non_content"]
        flagged = [row for row, acted in zip(chosen, decisions) if not acted and row["p"] is not None
                   and suspect <= row["p"] < threshold]
        result["suspected"] = len(flagged)
        result["suspected_refusals"] = sum(1 for row in flagged if row["positive"])
    return result


def fmt(value: Optional[float]) -> str:
    return "  n/a" if value is None else f"{value:5.1%}" if value < 1 else "100% "


def print_table(results: Dict[str, Dict[str, Any]]) -> None:
    print()
    print(f"{'hook':12s} {'set':8s} {'method':9s} {'acc':>6s} {'prec':>6s} {'recall':>6s}   tp  fp  fn  tn   "
          f"(action: positive class)")
    for hook, result in results.items():
        for split_result in result["splits"]:
            if not split_result["rule"]["n"]:
                continue
            for method in ("rule", "rule+jev"):
                s = split_result[method]
                print(f"{hook:12s} {split_result['split']:8s} {method:9s} {fmt(s['accuracy']):>6s} "
                      f"{fmt(s['precision']):>6s} {fmt(s['recall']):>6s}  {s['tp']:3d} {s['fp']:3d} {s['fn']:3d} "
                      f"{s['tn']:3d}   ({ACTION[hook]}; n={s['n']}, judge asked {split_result['asked']}, "
                      f"threshold {split_result['threshold']:g})")
            if hook == "boilerplate":
                print(f"{'':12s} {split_result['split']:8s} output: {split_result['copies_removed']} repeated copies "
                      f"removed, {split_result['copies_removed_wrongly']} of them wrongly; "
                      f"{split_result['copies_left_to_remove']} chrome copies left in")
            if hook == "non_content":
                print(f"{'':12s} {split_result['split']:8s} kept but flagged non_content_suspected: "
                      f"{split_result['suspected']} ({split_result['suspected_refusals']} of them refusals)")


def latency(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    fresh = [row for row in rows if row["latency_ms"]]
    ms = sorted(row["latency_ms"] for row in fresh)
    tokens = [row["input_tokens"] for row in fresh if row["input_tokens"]]

    def pct(share):
        return ms[min(len(ms) - 1, max(0, int(round(share * (len(ms) - 1)))))] if ms else None

    mean_tokens = statistics.mean(tokens) if tokens else None
    return {"requests": len(fresh), "p50_ms": pct(0.5), "p95_ms": pct(0.95),
            "mean_input_tokens": round(mean_tokens) if mean_tokens else None,
            "cost_usd_per_request": mean_tokens * Q.PRICE_PER_INPUT_TOKEN if mean_tokens else None}


# --- a whole document: how many questions, how long, how much ---------------------------------------


def document_run(path: Path, judge: TypeSafeJudge) -> Dict[str, Any]:
    """Convert ``path`` with the judge and an OCR stand-in that returns nothing (so the legibility
    judge is consulted as with real OCR, without OCR cost): what the judge did and the wall time."""
    from doc2mark import UnifiedDocumentLoader
    from doc2mark.ocr.base import BaseOCR, OCRResult

    class NoOCR(BaseOCR):
        def batch_process_images(self, images, **kwargs):
            return [OCRResult(text="") for _ in images]

    started = time.perf_counter()
    result = UnifiedDocumentLoader(ocr_provider=NoOCR(), judge=judge).load(path, ocr_images=True)
    seconds = time.perf_counter() - started
    stats = dict((result.metadata.extra or {}).get("judge") or {})
    stats["seconds"] = round(seconds, 2)
    stats["pages"] = result.metadata.page_count
    return stats


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DATA, help="labelled sets (default tests/data/judge)")
    parser.add_argument("--hooks", default=",".join(HOOKS))
    parser.add_argument("--calibrate", action="store_true",
                        help="calibrate the legibility and boilerplate thresholds on the TRAIN split")
    parser.add_argument("--cache-dir", default=None, help="verdict cache (default: the judge's default)")
    parser.add_argument("--deck", type=Path, default=None, help="a real deck read in place for the extra slice")
    parser.add_argument("--documents", type=Path, nargs="*", default=[],
                        help="documents to convert with the judge for per-document cost and latency")
    parser.add_argument("--json", type=Path, default=None, help="write all rows and results here")
    args = parser.parse_args(argv)

    judge = TypeSafeJudge(cache_dir=args.cache_dir, hooks=HOOKS)
    if judge.unavailable:
        print(f"TypeSafe judge unavailable: {judge.unavailable}", file=sys.stderr)
        return 2

    results: Dict[str, Dict[str, Any]] = {}
    deck = None
    if args.deck is not None:
        from judge_sets import deck_items
        deck = deck_items(args.deck)
    for hook in [h.strip() for h in args.hooks.split(",") if h.strip()]:
        rows = evaluate(hook, load(hook, args.data), judge)
        train = [row for row in rows if row["split"] == "train"]
        if args.calibrate and hook in CALIBRATED:
            threshold, train_accuracy = calibrate(hook, train)
        else:
            threshold, train_accuracy = Q.RAW_THRESHOLDS[hook], None
        splits = [report(hook, rows, threshold, split) for split in SETS]
        if deck is not None and deck.get(hook):
            deck_rows = evaluate(hook, [dict(item, split="deck") for item in deck[hook]], judge)
            rows += deck_rows
            splits.append(report(hook, deck_rows, threshold, "deck"))
        results[hook] = {"threshold": threshold, "pipeline_threshold": _pipeline_threshold(hook),
                         "train_accuracy": train_accuracy, "splits": splits,
                         "latency": latency(rows), "rows": rows}

    print_table(results)
    print()
    for hook, result in results.items():
        lat = result["latency"]
        cost = lat["cost_usd_per_request"]
        how = "calibrated" if args.calibrate and hook in CALIBRATED else "shipped"
        print(f"{hook:12s} {how} threshold {result['threshold']:g} (pipeline {result['pipeline_threshold']:g}); "
              f"fresh requests {lat['requests']}, p50 {lat['p50_ms']} ms, p95 {lat['p95_ms']} ms, "
              f"{lat['mean_input_tokens']} input tokens, ${cost:.7f} per request" if cost else
              f"{hook:12s} {how} threshold {result['threshold']:g}; no fresh requests (all cached)")
        for row in result["rows"]:
            if row["split"] in ("test", "external") and \
                    decide(hook, row["fired"], row["asked"], row["p"], result["threshold"]) != row["positive"]:
                print(f"    {row['split'].upper()} miss {row['id']} ({row['kind']}): label {row['label']}, p={row['p']}")

    documents = {}
    for path in ([args.deck] if args.deck is not None else []) + list(args.documents):
        name = "deck" if path == args.deck else path.name
        documents[name] = document_run(path, judge)
        print(f"\n{name} converted with the judge (OCR stand-in): {documents[name]}")
    usage = judge.usage()
    print(f"\njudge totals: {json.dumps({k: v for k, v in usage.items() if k != 'cache'})}")

    if args.json:
        args.json.write_text(json.dumps({"results": results, "documents": documents, "usage": usage},
                                        ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
