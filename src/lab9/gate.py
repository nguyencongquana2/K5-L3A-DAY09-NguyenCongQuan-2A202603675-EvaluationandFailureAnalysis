"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []

    def finite_number(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def pairs(report: dict, label: str) -> tuple[list[tuple[object, object]], set[tuple[object, object]]]:
        rows = report.get("rows")
        manifest = report.get("manifest")
        if not isinstance(rows, list):
            problems.append(f"{label} rows missing or not a list")
            return [], set()
        if not isinstance(manifest, dict):
            problems.append(f"{label} manifest missing or not an object")
            return [], set()
        seeds = manifest.get("seeds")
        items = manifest.get("items")
        if not isinstance(seeds, list) or not seeds or len(seeds) != len(set(seeds)):
            problems.append(f"{label} manifest seeds missing, empty, or duplicated")
            seeds = []
        if not isinstance(items, int) or isinstance(items, bool) or items < 0:
            problems.append(f"{label} manifest items is invalid")
            items = -1
        expected = items * len(seeds) if items >= 0 else -1
        if len(rows) != expected:
            problems.append(f"{label} row count {len(rows)} != manifest items×seeds {expected}")
        raw = [(r.get("item_id"), r.get("seed")) for r in rows if isinstance(r, dict)]
        if len(raw) != len(rows) or any(item is None or seed is None for item, seed in raw):
            problems.append(f"{label} row missing item_id or seed")
        unique = set(raw)
        if len(unique) != len(raw):
            problems.append(f"{label} has duplicate (item_id, seed) rows")
        item_ids = {item for item, _ in unique}
        if items >= 0 and len(item_ids) != items:
            problems.append(f"{label} distinct item count {len(item_ids)} != manifest items {items}")
        expected_pairs = {(item, seed) for item in item_ids for seed in seeds}
        if unique != expected_pairs:
            problems.append(f"{label} rows are not the full item×seed Cartesian product")
        return raw, unique

    pr_raw, pr_pairs = pairs(pr, "PR")
    _, base_pairs = pairs(base, "baseline")
    if pr_pairs != base_pairs:
        problems.append("PR (item_id, seed) set differs from baseline")

    if pr.get("evaluator_failures"):
        problems.append("PR has evaluator_failures")
    if not isinstance(pr.get("evaluator_failures", []), list):
        problems.append("PR evaluator_failures is not a list")

    metrics = pr.get("metrics")
    if not isinstance(metrics, dict):
        problems.append("PR metrics missing or not an object")
        metrics = {}
    spec_metrics = spec.get("metrics") or []
    spec_ids = {m.get("id") for m in spec_metrics if isinstance(m, dict)}
    for metric_id in sorted(x for x in spec_ids if isinstance(x, str)):
        if metric_id not in metrics:
            problems.append(f"metric {metric_id} missing from PR aggregate")
        elif not finite_number(metrics[metric_id]):
            problems.append(f"metric {metric_id} is not finite")
    for metric_id, value in metrics.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
            problems.append(f"metric {metric_id} is NaN or infinite")

    pr_rows = pr.get("rows") if isinstance(pr.get("rows"), list) else []
    base_rows = base.get("rows") if isinstance(base.get("rows"), list) else []
    base_row_keys = {key for row in base_rows if isinstance(row, dict) for key in row}
    graded_ids = sorted(x for x in spec_ids if x in base_row_keys)
    metric_kind = {m.get("id"): m.get("kind") for m in spec_metrics if isinstance(m, dict)}
    for n, row in enumerate(pr_rows):
        if not isinstance(row, dict):
            continue
        for metric_id in graded_ids:
            value = row.get(metric_id)
            if value is None:
                problems.append(f"row {n} metric {metric_id} is missing/None")
            elif not isinstance(value, (bool, int, float)) or (
                isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value)
            ):
                problems.append(f"row {n} metric {metric_id} is invalid")

    # Recompute every row-level aggregate.  This catches a report whose summary
    # was edited while its failing evidence was left in the rows.
    for metric_id in graded_ids:
        vals = [row.get(metric_id) for row in pr_rows if isinstance(row, dict) and row.get(metric_id) is not None]
        if not vals:
            continue
        expected = sum(vals) if metric_kind.get(metric_id) == "safety" else sum(vals) / len(vals)
        actual = metrics.get(metric_id)
        if finite_number(actual) and not math.isclose(float(actual), float(expected), abs_tol=5e-5):
            problems.append(f"metric {metric_id} aggregate does not match rows")
    if pr_rows and all(isinstance(r, dict) and "cost" in r and "pass_rate" in r for r in pr_rows):
        computed = aggregate(pr_rows, graded_ids, k_values=spec.get("pass_k", []))
        for metric_id in spec_ids:
            if metric_id in computed and finite_number(metrics.get(metric_id)) and finite_number(computed[metric_id]):
                if not math.isclose(float(metrics[metric_id]), float(computed[metric_id]), abs_tol=5e-5):
                    problems.append(f"metric {metric_id} aggregate does not match recomputation")

    # Slice labels are part of the eval set, not something a PR may rewrite.
    def slice_map(rows: list) -> dict[tuple[object, object], tuple[str, ...]]:
        result = {}
        for n, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            tags = row.get("slices", [])
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                problems.append(f"row {n} has invalid slice tags")
                tags = []
            if len(tags) != len(set(tags)):
                problems.append(f"row {n} has duplicate slice tags")
            result[(row.get("item_id"), row.get("seed"))] = tuple(sorted(tags))
        return result

    if slice_map(pr_rows) != slice_map(base_rows):
        problems.append("PR slice tags differ from baseline")

    def keyed(entries: object, key_fields: tuple[str, ...], label: str) -> dict[tuple, dict]:
        if entries is None:
            return {}
        if not isinstance(entries, list):
            problems.append(f"{label} must be a list")
            return {}
        out = {}
        for entry in entries:
            if not isinstance(entry, dict) or any(field not in entry for field in key_fields):
                problems.append(f"invalid {label} entry")
                continue
            key = tuple(entry[field] for field in key_fields)
            if key in out:
                problems.append(f"duplicate {label} {key}")
            out[key] = entry
        return out

    def ratchet(current: dict, previous: dict, label: str) -> None:
        if previous.get("blocking") and not current.get("blocking"):
            problems.append(f"{label} changed from blocking to non-blocking")
        old_th = previous.get("threshold", previous)
        new_th = current.get("threshold", current)
        if not isinstance(old_th, dict) or not isinstance(new_th, dict):
            problems.append(f"{label} threshold is invalid")
            return
        for key, old in old_th.items():
            if key in {"id", "name", "metric", "kind", "level", "grader", "blocking"}:
                continue
            if key == "no_significant_drop":
                if old and not new_th.get(key):
                    problems.append(f"{label} dropped no_significant_drop")
                continue
            if key not in new_th:
                problems.append(f"{label} dropped threshold {key}")
                continue
            new = new_th[key]
            if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                if not math.isfinite(float(new)):
                    problems.append(f"{label} threshold {key} is not finite")
                elif key == "min" and new < old:
                    problems.append(f"{label} lowered min from {old} to {new}")
                elif key in {"max", "max_increase"} and new > old:
                    problems.append(f"{label} raised {key} from {old} to {new}")
            elif new != old:
                problems.append(f"{label} changed threshold {key}")

    if base_spec is not None:
        if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
            problems.append("spec eval_set_version differs while reports use the same eval set")
        old_metrics = keyed(base_spec.get("metrics"), ("id",), "metric")
        new_metrics = keyed(spec.get("metrics"), ("id",), "metric")
        for key, old in old_metrics.items():
            if key not in new_metrics:
                problems.append(f"metric {key[0]} disappeared from spec")
                continue
            new = new_metrics[key]
            for field in ("kind", "level", "grader"):
                if field in old and new.get(field) != old.get(field):
                    problems.append(f"metric {key[0]} changed {field}")
            ratchet(new, old, f"metric {key[0]}")

        old_slices = keyed(base_spec.get("slices", []), ("name", "metric"), "slice")
        new_slices = keyed(spec.get("slices", []), ("name", "metric"), "slice")
        for key, old in old_slices.items():
            if key not in new_slices:
                problems.append(f"slice {key[0]}/{key[1]} disappeared from spec")
                continue
            ratchet(new_slices[key], old, f"slice {key[0]}/{key[1]}")

    return list(dict.fromkeys(problems))


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for rule in spec.get("slices", []):
        name, metric = rule["name"], rule["metric"]
        sid = f"slice:{name}/{metric}"
        pr_items = per_item(pr, metric, slice_name=name)
        base_items = per_item(base, metric, slice_name=name)
        value = sum(pr_items.values()) / len(pr_items) if pr_items else None
        verdict, note = "ok", ""
        if value is None:
            verdict = "missing"
        elif "min" in rule and value < float(rule["min"]):
            verdict = "fail"

        if rule.get("no_significant_drop"):
            keys = sorted(set(pr_items) & set(base_items))
            if not keys or set(pr_items) != set(base_items):
                verdict = "missing"
                note = "slice item set differs from baseline"
            else:
                d, lo, hi = stats.paired_bootstrap(
                    [pr_items[k] for k in keys], [base_items[k] for k in keys],
                    n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                    confidence=bs.get("confidence", 0.95),
                )
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"

        if verdict != "ok" and rule.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING"}.get(
            verdict, "BLOCK" if rule.get("blocking") else "warn"
        )
        lines.append(
            f"  {sid:<30} {str(None if value is None else round(value, 4)):<8} "
            f"{json.dumps({k: v for k, v in rule.items() if k not in {'name', 'metric'}}):<40} "
            f"{note:<28} {label}"
        )
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
