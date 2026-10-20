from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from src.evaluator.llm_parser import parse_prompt_to_rule, RuleCompilationError, UnsupportedIntentError
from src.evaluator.models import Rule
from src.storage.rules_store import rules_store
from src.vision.loop_engine import loop_engine

router = APIRouter(prefix="/automations", tags=["Automations"])


class CreateRuleRequest(BaseModel):
    prompt: Optional[str] = None
    rule: Optional[Dict[str, Any]] = None


class ToggleRuleRequest(BaseModel):
    enabled: bool


@router.get("/rules")
async def get_all_rules():
    """Retrieve all configured automation rules and their status."""
    return {
        "rules": rules_store.get_all_entries(),
        "total": len(rules_store.get_all_entries()),
    }


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_or_register_rule(request: CreateRuleRequest):
    """
    Create a new automation rule from natural language prompt or direct Rule schema,
    and activate it in the background video monitoring loop.
    """
    try:
        if request.rule:
            rule_obj = Rule.model_validate(request.rule)
            prompt = request.prompt or ""
        elif request.prompt:
            rule_obj = await run_in_threadpool(parse_prompt_to_rule, request.prompt)
            prompt = request.prompt
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'prompt' or 'rule' payload is required",
            )

        saved_rule = rules_store.add_rule(rule_obj, prompt=prompt)
        return {
            "status": "activated",
            "rule": saved_rule.model_dump(),
            "prompt": prompt,
            "monitoring_running": loop_engine.is_running,
            "warnings": (["Visual rules inspect individual object crops; movement towards the door, arrival, and driveway location are not verified."]
                         if rule_obj.source in ("person", "car") else []),
        }
    except HTTPException:
        raise
    except RuleCompilationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except UnsupportedIntentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create automation rule: {exc}",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    """Delete an active automation rule by ID."""
    removed = rules_store.remove_rule(rule_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule '{rule_id}' not found",
        )
    return {"status": "deleted", "rule_id": rule_id}


@router.patch("/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: str, request: ToggleRuleRequest):
    """Enable or disable an automation rule without deleting it."""
    success = rules_store.toggle_rule(rule_id, request.enabled)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule '{rule_id}' not found",
        )
    return {"status": "updated", "rule_id": rule_id, "enabled": request.enabled}


@router.delete("/rules")
async def clear_all_rules():
    """Clear all active rules from the monitoring engine."""
    rules_store.clear_rules()
    return {"status": "cleared", "message": "All rules removed from monitoring engine"}


@router.get("/status")
async def get_loop_engine_status():
    """Retrieve runtime telemetry and status of continuous background video loop."""
    return loop_engine.get_status()


@router.post("/loop/start")
async def start_loop_engine():
    """Start the continuous background video loop."""
    started = loop_engine.start()
    return {"status": "started" if started else "already_running", "engine": loop_engine.get_status()}


@router.post("/loop/stop")
async def stop_loop_engine():
    """Stop the continuous background video loop."""
    loop_engine.stop()
    return {"status": "stopped", "engine": loop_engine.get_status()}


from src.storage.timeline_store import timeline_store

class DeadlineRequest(BaseModel):
    expected_event: str
    deadline_time: str
    message: Optional[str] = None


@router.get("/history")
async def query_history(
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    event_type: Optional[str] = None,
):
    """Query past events recorded by the video monitoring loop."""
    events = timeline_store.query_history(
        start_time=start_time,
        end_time=end_time,
        event_type=event_type,
    )
    return {"events": events, "count": len(events)}


@router.post("/deadline", status_code=status.HTTP_201_CREATED)
async def create_deadline(request: DeadlineRequest):
    """Set an alert for an expected event that must occur before deadline_time."""
    dl = timeline_store.add_deadline(
        expected_event=request.expected_event,
        deadline_time=request.deadline_time,
        message=request.message,
    )
    return {"status": "armed", "deadline": dl}
