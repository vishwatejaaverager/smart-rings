"""
Alexa Smart Home Assistant Integration.
Provides a clean interface for Alexa voice/text intents to interact with
the Smart Home AI Defense system via the external adapter layer.
"""
from typing import Any, Dict, List, Optional
import httpx


class AlexaAssistant:
    """
    Alexa voice interface handler.
    Translates user speech intents into backend automation rules and returns
    natural spoken responses for Alexa.
    """

    def __init__(self, backend_url: str = "http://127.0.0.1:8000/api/v1/automations/rules"):
        self.backend_url = backend_url

    def handle_voice_intent(self, prompt: str) -> Dict[str, Any]:
        """
        Processes an Alexa voice command (e.g. 'Alexa, alert me if package disappears').
        Arms the rule on the backend and returns the spoken response text for Alexa.
        """
        try:
            with httpx.Client(timeout=65.0) as client:
                resp = client.post(self.backend_url, json={"prompt": prompt})
                if resp.status_code in (200, 201):
                    data = resp.json()
                    rule = data.get("rule", {})
                    rule_name = rule.get("id", "Custom Rule").replace("_", " ").title()
                    return {
                        "success": True,
                        "speech_response": (
                            f"I've armed the '{rule_name}' defense rule. "
                            f"If triggered, an alert will be sent immediately to your Fire TV."
                        ),
                        "rule": rule,
                    }
                return {
                    "success": False,
                    "speech_response": f"Sorry, I couldn't configure that rule: {resp.text}",
                    "rule": None,
                }
        except Exception as exc:
            return {
                "success": False,
                "speech_response": f"I couldn't reach your smart home system: {exc}",
                "rule": None,
            }

    def list_armed_rules(self) -> List[Dict[str, Any]]:
        """Returns all rules currently armed in the system."""
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(self.backend_url)
                if resp.status_code == 200:
                    return resp.json().get("rules", [])
        except Exception:
            pass
        return []

    def clear_all_rules(self) -> bool:
        """Clears all rules from the system."""
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.delete(self.backend_url)
                return resp.status_code == 200
        except Exception:
            return False


alexa_assistant = AlexaAssistant()
