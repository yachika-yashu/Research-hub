"""
Baseline manager — saves benchmark results as a reference baseline and detects
regressions when compared to future runs.

Usage
-----
  from benchmarking.baseline_manager import save_baseline, compare_with_baseline, print_regression_report

  # Save current results as the new baseline
  save_baseline(eval_results_dict)

  # Compare a new run against the saved baseline
  report = compare_with_baseline(current_results_dict)
  print_regression_report(report)

Runnable standalone:
  python -m benchmarking.baseline_manager --help
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE            = Path(__file__).parent
BASELINE_DIR     = _HERE / "baseline"
HISTORY_DIR      = _HERE / "baseline_history"
BASELINE_FILE    = BASELINE_DIR / "baseline.json"
MAX_HISTORY      = 5   # keep last 5 rotated baselines


# ── Helpers ───────────────────────────────────────────────────────────────────

def _git_commit() -> str:
    """Return the short HEAD commit hash, or 'unknown' if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _flatten(obj: dict, prefix: str = "") -> dict[str, float]:
    """
    Recursively flatten a nested dict of numeric values into dot-notation keys.
    Non-numeric leaves are ignored.
    """
    out: dict[str, float] = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        elif isinstance(v, (int, float)):
            out[key] = float(v)
    return out


# ── Public API ────────────────────────────────────────────────────────────────

def save_baseline(eval_results: dict) -> None:
    """
    Save eval_results as the new baseline.

    If a baseline already exists it is moved to baseline_history/ with a
    timestamp suffix before being overwritten.  Only the last MAX_HISTORY
    history files are kept; older ones are deleted.
    """
    try:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)

        # Rotate existing baseline into history
        if BASELINE_FILE.exists():
            try:
                old = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
                old_ts = (
                    old.get("saved_at", datetime.now(timezone.utc).isoformat())
                    .replace(":", "-")
                    .replace(".", "-")[:23]
                )
            except Exception:
                old_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

            dest = HISTORY_DIR / f"baseline_{old_ts}.json"
            try:
                BASELINE_FILE.rename(dest)
            except Exception:
                import shutil
                shutil.copy2(BASELINE_FILE, dest)
                BASELINE_FILE.unlink(missing_ok=True)

            # Prune old history
            history_files = sorted(HISTORY_DIR.glob("baseline_*.json"))
            for old_file in history_files[:-MAX_HISTORY]:
                try:
                    old_file.unlink()
                except Exception:
                    pass

        commit = _git_commit()
        baseline = {
            "saved_at":   datetime.now(timezone.utc).isoformat(),
            "git_commit": commit,
            "results":    eval_results,
        }
        BASELINE_FILE.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        print(f"Baseline saved  (commit: {commit}  time: {baseline['saved_at'][:19]}Z)")
        print(f"  -> {BASELINE_FILE}")
    except Exception as exc:
        print(f"ERROR: Could not save baseline — {exc}")
        raise


def load_baseline() -> Optional[dict]:
    """
    Load the saved baseline.  Returns None if no baseline file exists yet.
    """
    if not BASELINE_FILE.exists():
        return None
    try:
        return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARNING: Could not load baseline ({exc}) — treating as missing.")
        return None


def compare_with_baseline(current_results: dict) -> dict:
    """
    Compare current_results against the saved baseline.

    Returns a regression report dict:
    {
        "has_baseline": bool,
        "regression_detected": bool,
        "critical_regressions": int,
        "warning_regressions": int,
        "regressions": [
            {"metric": str, "category": str,
             "baseline": float, "current": float,
             "delta": float, "severity": "critical"|"warning"}
        ],
        "improvements": [...same structure without severity...],
        "unchanged":    [...same structure without severity...],
        "baseline_commit":   str | None,
        "baseline_saved_at": str | None,
    }

    Severity thresholds:
        delta < -0.10  →  critical
        delta < -0.05  →  warning
        delta > +0.01  →  improvement
        else           →  unchanged
    """
    baseline = load_baseline()
    if baseline is None:
        return {
            "has_baseline":       False,
            "regression_detected": False,
            "critical_regressions": 0,
            "warning_regressions":  0,
            "regressions":   [],
            "improvements":  [],
            "unchanged":     [],
            "baseline_commit":   None,
            "baseline_saved_at": None,
        }

    base_flat    = _flatten(baseline.get("results", {}))
    current_flat = _flatten(current_results)

    regressions:  list[dict] = []
    improvements: list[dict] = []
    unchanged:    list[dict] = []

    for key, base_val in base_flat.items():
        curr_val = current_flat.get(key)
        if curr_val is None:
            continue

        # Derive a human-readable category from the dot-notation key
        parts    = key.rsplit(".", 1)
        category = parts[0] if len(parts) == 2 else "global"
        metric   = parts[-1]

        delta = curr_val - base_val
        entry = {
            "metric":   metric,
            "category": category,
            "baseline": round(base_val,  4),
            "current":  round(curr_val,  4),
            "delta":    round(delta,     4),
        }

        if delta < -0.10:
            entry["severity"] = "critical"
            regressions.append(entry)
        elif delta < -0.05:
            entry["severity"] = "warning"
            regressions.append(entry)
        elif delta > 0.01:
            improvements.append(entry)
        else:
            unchanged.append(entry)

    critical_n = sum(1 for r in regressions if r.get("severity") == "critical")
    warning_n  = sum(1 for r in regressions if r.get("severity") == "warning")

    return {
        "has_baseline":         True,
        "regression_detected":  len(regressions) > 0,
        "critical_regressions": critical_n,
        "warning_regressions":  warning_n,
        "regressions":          regressions,
        "improvements":         improvements,
        "unchanged":            unchanged,
        "baseline_commit":      baseline.get("git_commit"),
        "baseline_saved_at":    baseline.get("saved_at"),
    }


def print_regression_report(report: dict) -> None:
    """Print a formatted regression report to stdout."""
    if not report.get("has_baseline"):
        print("  No baseline found.")
        print("  Run:  python benchmarking/run_benchmark.py --save-baseline")
        print("  to establish a baseline for future comparisons.")
        return

    commit   = report.get("baseline_commit", "?")[:8]
    saved    = (report.get("baseline_saved_at") or "?")[:10]
    print(f"\n  Regression Report vs baseline  (commit: {commit}  saved: {saved})\n")

    regressions  = report.get("regressions",  [])
    improvements = report.get("improvements", [])

    col = [22, 16, 10, 10, 10, 10]

    def _row(*cells):
        parts = []
        for i, c in enumerate(cells):
            w = col[i] if i < len(col) else 12
            parts.append(str(c).ljust(w)[:w])
        return "  " + "  ".join(parts)

    if regressions:
        print("  REGRESSIONS DETECTED:")
        print(_row("Metric", "Category", "Baseline", "Current", "Delta", "Severity"))
        print("  " + "-" * 82)
        for r in regressions:
            sev = r.get("severity", "?").upper()
            print(_row(
                r["metric"], r["category"],
                f"{r['baseline']:.4f}", f"{r['current']:.4f}",
                f"{r['delta']:+.4f}", sev,
            ))
    else:
        print("  No regressions detected.")

    if improvements:
        print("\n  IMPROVEMENTS:")
        print(_row("Metric", "Category", "Baseline", "Current", "Delta", ""))
        print("  " + "-" * 82)
        for i in improvements:
            print(_row(
                i["metric"], i["category"],
                f"{i['baseline']:.4f}", f"{i['current']:.4f}",
                f"{i['delta']:+.4f}", "",
            ))

    critical = report.get("critical_regressions", 0)
    warning  = report.get("warning_regressions",  0)
    n_imp    = len(improvements)
    print(f"\n  Overall: {len(regressions)} regression(s) "
          f"({critical} critical, {warning} warning)  /  {n_imp} improvement(s)\n")

    if critical > 0:
        print("  Action required: fix critical regressions before merging.")
    elif warning > 0:
        print("  Action recommended: review warning-level regressions.")
    else:
        print("  No action required.")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Baseline Manager")
    sub = parser.add_subparsers(dest="cmd")

    p_save = sub.add_parser("save", help="Save benchmarking/results/full_report.json as baseline")
    p_save.add_argument("--file", default="benchmarking/results/full_report.json",
                        help="JSON results file to save as baseline")

    p_show = sub.add_parser("show", help="Print the current baseline metadata")

    p_compare = sub.add_parser("compare", help="Compare a results file with the baseline")
    p_compare.add_argument("--file", default="benchmarking/results/full_report.json",
                           help="JSON results file to compare")

    args = parser.parse_args()

    if args.cmd == "save":
        try:
            data = json.loads(Path(args.file).read_text(encoding="utf-8"))
            save_baseline(data)
        except FileNotFoundError:
            print(f"ERROR: {args.file} not found.  Run run_benchmark.py first.")
            sys.exit(1)

    elif args.cmd == "show":
        b = load_baseline()
        if b is None:
            print("No baseline saved yet.")
        else:
            print(f"Baseline commit : {b.get('git_commit')}")
            print(f"Saved at        : {b.get('saved_at')}")
            flat = _flatten(b.get("results", {}))
            print(f"Tracked metrics : {len(flat)}")
            for k, v in list(flat.items())[:10]:
                print(f"  {k} = {v}")
            if len(flat) > 10:
                print(f"  ... and {len(flat)-10} more")

    elif args.cmd == "compare":
        try:
            current = json.loads(Path(args.file).read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"ERROR: {args.file} not found.")
            sys.exit(1)
        report = compare_with_baseline(current)
        print_regression_report(report)
        if report.get("critical_regressions", 0) > 0:
            sys.exit(1)

    else:
        parser.print_help()
