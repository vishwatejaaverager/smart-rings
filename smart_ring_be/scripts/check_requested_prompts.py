"""Opt-in live OpenRouter checks for the user's eleven monitoring prompts.

Run with .venv/bin/python scripts/check_requested_prompts.py --live.
This spends provider credits; it does not arm rules or send notifications.
Compilation success is separate from camera/model detection accuracy.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluator.llm_parser import parse_prompt_to_rule, UnsupportedIntentError
from src.evaluator.engine import evaluate


CASES = [
    ("Alert me if a person in a blue shirt approaches the door", "person", ["blue", "shirt"]),
    ("Alert me if someone wearing black pants is seen", "person", ["black"]),
    ("Alert if a person is seen walking towards the door", "person", ["walk"]),
    ("Alert me if a person is picking up or carrying a box", "person", ["box"]),
    ("Alert me whenever a package disappears", "package_count", "decrease"),
    ("Alert me if package count drops below 2", "package_count", "below_two"),
    ("Alert me when two or more packages arrive", "package_count", "two_arrivals"),
    ("Alert if package count decreases", "package_count", "decrease"),
    ("Alert if a white car arrives in the driveway", "car", ["white"]),
    ("Alert if a delivery truck or van is detected", "car", ["truck", "van"]),
    ("Alert if a car with license plate 7ABC123 arrives", "car", ["7abc123"]),
]

TOOL_CASES = [
    ("Did anyone come before 2:00 PM?", "query_history"),
    ("Was a package delivered before noon?", "query_history"),
    ("Alert me if my package does not arrive by 4:00 PM", "set_deadline_alert"),
]


def check_rule(rule, source, expected):
    assert rule.source == source, f"Expected {source}, got {rule.source}"
    if isinstance(expected, list):
        query = rule.vlm_query.lower()
        assert all(word in query for word in expected), query
        assert query.endswith("answer yes or no."), query
        return
    if expected == "decrease":
        assert rule.derive.type == "delta" and rule.derive.keep == "negative"
        assert rule.aggregate.fn == "latest" and rule.compare.op == "<" and rule.compare.value == 0
        counts = [2, 1]
    elif expected == "below_two":
        assert rule.derive.type == "identity" and rule.aggregate.fn == "latest"
        assert rule.compare.op == "<" and rule.compare.value == 2
        counts = [2, 1]
    else:
        assert rule.derive.type == "delta" and rule.derive.keep == "positive"
        assert rule.aggregate.fn == "sum" and rule.compare.op == ">=" and rule.compare.value == 2
        counts = [0, 1, 2]
    history, state = [], None
    for index, count in enumerate(counts):
        history.append({"timestamp": index * 10, "package_count": count})
        decision, state = evaluate(rule, history, state)
        assert decision.fired == (index == len(counts) - 1), f"Unexpected decision on {counts[:index+1]}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--tools-only", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live to explicitly enable provider requests")
    results = []
    for prompt, source, expected in ([] if args.tools_only else CASES):
        row = {"prompt": prompt, "expected_source": source}
        try:
            rule = parse_prompt_to_rule(prompt)
            check_rule(rule, source, expected)
            row.update(status="PASS", rule=rule.model_dump())
        except Exception as exc:
            row.update(status="FAIL", error=f"{type(exc).__name__}: {exc}")
        results.append(row)
        print(row["status"], prompt, row.get("error", ""), flush=True)
    for prompt, tool in TOOL_CASES:
        row = {"prompt": prompt, "required_tool": tool}
        try:
            rule = parse_prompt_to_rule(prompt)
            row.update(status="FAIL", error="Incorrectly compiled a history/deadline request as an immediate monitoring rule", rule=rule.model_dump())
        except UnsupportedIntentError as exc:
            row.update(status="PASS" if tool in str(exc) else "FAIL", note=str(exc))
        except Exception as exc:
            row.update(status="FAIL", error=str(exc))
        results.append(row)
        print(row["status"], prompt, flush=True)
    target = ROOT / "docs" / "requested_prompt_results.json"
    if args.tools_only and target.exists():
        results = json.loads(target.read_text())[:len(CASES)] + results
    target.write_text(json.dumps(results, indent=2) + "\n")
    failures = sum(row["status"] != "PASS" for row in results)
    print(f"{len(results)-failures}/{len(results)} passed; report: {target}")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
