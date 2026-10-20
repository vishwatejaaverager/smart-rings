import threading
import time
from typing import Any, Dict, List, Optional

from src.evaluator.models import Rule


class RulesStore:
    """
    Thread-safe 100% IN-MEMORY store for active monitoring rules.
    Rules live exclusively in RAM variables and are automatically
    cleared when the server or application stops. No files are saved to disk.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._rules: Dict[str, Dict[str, Any]] = {}

    def add_rule(self, rule: Rule, prompt: str = "") -> Rule:
        """Add or update an active rule in memory."""
        with self._lock:
            entry = {
                "rule": rule.model_dump(),
                "prompt": prompt,
                "created_at": time.time(),
                "enabled": True,
            }
            self._rules[rule.id] = entry
            print(f"📋 [In-Memory RulesStore] Armed rule in RAM: '{rule.id}' (Prompt: '{prompt}')")
            return rule

    def get_active_rules(self) -> List[Rule]:
        """Returns all currently enabled rules in memory for the evaluator engine."""
        with self._lock:
            rules = []
            for item in self._rules.values():
                if item.get("enabled", True):
                    try:
                        rules.append(Rule.model_validate(item["rule"]))
                    except Exception as exc:
                        print(f"⚠️ [In-Memory RulesStore] Malformed rule '{item.get('rule', {}).get('id')}': {exc}")
            return rules

    def get_all_entries(self) -> List[Dict[str, Any]]:
        """Returns metadata of all registered rules from memory."""
        with self._lock:
            return [dict(v) for v in self._rules.values()]

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID from memory."""
        with self._lock:
            if rule_id in self._rules:
                del self._rules[rule_id]
                print(f"🗑️ [In-Memory RulesStore] Removed rule from RAM: '{rule_id}'")
                return True
            return False

    def clear_rules(self) -> None:
        """Clear all active rules from memory."""
        with self._lock:
            count = len(self._rules)
            self._rules.clear()
            if count > 0:
                print(f"🧹 [In-Memory RulesStore] Cleared {count} rule(s) from RAM.")

    def toggle_rule(self, rule_id: str, enabled: bool) -> bool:
        """Enable or disable a rule in memory."""
        with self._lock:
            if rule_id in self._rules:
                self._rules[rule_id]["enabled"] = enabled
                return True
            return False


# Global in-memory singleton instance
rules_store = RulesStore()
