# Smart Rings project analysis

Reviewed 21 October 2026. Scope: Python backend, desktop video monitor, Streamlit dashboard, rule parser/evaluator, vision pipeline, Ring streaming integration, webhook/Fire TV bridge, MCP adapters, TV application, deployment configuration, and available tests. Source review and local tests; no live camera, external model, webhook, or TV-device validation was performed. Application code was not changed.

## Architecture and implemented behavior

- `smart_ring_be/src/ui/dashboard.py`: Streamlit text interface and preset cards. Creates rules through FastAPI; displays status and can send a test webhook.
- `smart_ring_be/src/backend/main.py`: FastAPI exposes devices, streams, automations, webhook ingestion, and Fire TV WebSockets. Optional headless monitoring starts via `START_HEADLESS_LOOP`.
- `src/evaluator/llm_parser.py`: sends monitoring intent to OpenRouter using `openai/gpt-4o-mini`, extracts JSON, and validates Pydantic rules.
- `src/storage/rules_store.py`: thread-safe, process-local RAM rule storage. Restart loses rules by design. Reusing an ID replaces an existing rule.
- `src/vision/loop_engine.py`: repeatedly reads `vids/theft.mp4`, detects packages/people/cars, verifies package counts with a Roboflow workflow, asks local Ollama/Qwen visual questions, evaluates rules, and sends outgoing webhooks.
- `src/app.py`: a separate desktop OpenCV monitor implements much of the same behavior and can launch backend, dashboard, and TV app.
- `src/evaluator/engine.py`: pure package and visual evaluators support cooldowns and three alert modes.
- `src/integrations/ring/stream.py`: Ring WHEP/WebRTC receiving; separate routes provide frames and MJPEG preview.
- `src/notifications/webhook.py` and `src/backend/routes/fire_tv.py`: outgoing HTTP alerts are normalized and broadcast to connected TV apps.
- `firetv-app/src/App.tsx`: Vega React Native TV UI, WebSocket reconnect, event history, alert banner, and optional snapshots.
- `mcp/server.py`: MCP tools proxy rule/history/deadline requests to FastAPI. `mcp/alexa.py` is an HTTP adapter; the inspected implementation is not a complete Alexa skill or speech-recognition service.

The README is stale: its claim that features are not implemented conflicts with substantial working code. Conversely, agent, tools, timers, and several route files are empty placeholders.

## Exact reported failure

Request: `Alert me if a person in a blue shirt approaches the door`.

Call path:

1. Dashboard `create_automation` POSTs a prompt to `/api/v1/automations/rules`.
2. Route `create_or_register_rule` calls `parse_prompt_to_rule`.
3. `parse_prompt_to_rules` sends the intent to OpenRouter and extracts a JSON list.
4. An empty JSON list passes extraction and list validation.
5. `parse_prompt_to_rule` raises `ValueError("No valid rules returned by model.")` when that list is empty.
6. The route catches the exception and returns HTTP 400; the dashboard prints the reported error.

I reproduced the exact exception by mocking an HTTP 200 OpenRouter response with message content `[]`. On the inspected code path, the observed error indicates an empty parsed list. It does not indicate a YOLO or Ollama failure, because those stages have not been reached. The historical provider response is not recorded here, so why the model produced an empty list remains unverified.

The system prompt already includes the blue-shirt example. Adding that example again is insufficient. The parser has no enforced response schema, explicit nonempty-list contract, bounded retry for empty output, or useful internal diagnostics for the empty response. Its examples also broaden “blue shirt” to “blue shirt or blue hoodie.”

The route uses the single-rule helper, so any additional rules returned for a compound intent are discarded.

## Findings by priority

### P1: Fix before relying on monitoring

1. **“Approaches the door” is unsupported.** `llm_parser.py` translates the example to a clothing question. `loop_engine.py` examines a single cropped person image. There is no configured door region, stable person tracker, movement history, or approach/entry condition. A blue-shirt match anywhere in view can satisfy the current rule. Model extraction improvements alone cannot implement the requested temporal and spatial behavior.

2. **Ring streaming is disconnected from automation monitoring.** `loop_engine.py` defaults to a local theft video and opens it with `cv2.VideoCapture`. Ring frames are held separately by `backend/routes/streams.py`. Starting a Ring stream does not feed it into the automation engine. Introduce an explicit frame-source interface and expose the actual monitored source in status/UI.

3. **Rule compilation blocks FastAPI.** The async creation route calls synchronous `httpx.post`, potentially blocking the event loop for 25 seconds. This can delay other requests and WebSocket broadcasts. Dashboard timeout is 12 seconds and MCP/Alexa adapter timeouts are 10 seconds, creating ambiguous outcomes where the client times out but a rule is subsequently saved. Use async provider I/O or move compilation off the event loop, and align deadlines.

4. **Alert modes are overridden by video-cycle gating.** `loop_engine.py` and desktop `src/app.py` suppress every rule after its first alert per video cycle using `fired_this_cycle`, even when the rule says `every_time`. Resetting all rule states at rewind also permits `once_ever` to fire again. Keep lifecycle state separate from playback state and honor evaluator decisions.

5. **Visual worker scheduling and completion are unsafe.** `VLMReasoner` checks `busy` before launching a thread but sets it inside that thread, so admission is not atomic. Boolean-query results are never inserted into the cache checked by callers, causing repeated inference for the same query. There is no fairness queue across rules/objects. In-flight callbacks do not recheck whether a rule was disabled/deleted or the playback cycle changed. Add bounded scheduling, atomic admission, result lifecycle, and rule/cycle generation checks.

6. **Boolean interpretation can produce false positives.** `vlm_reasoner.py` uses substring matching (`"yes" in raw.lower()`), which accepts contradictory prose containing “yes.” `expected_answer` is stored but unused. Parse a strict boolean response and treat ambiguous output as unknown.

7. **Invalid rules can be activated.** `Rule.source` is any string; package configuration and visual query are all optional. I verified an unknown-source rule without evaluation fields passes validation. Package rules with a derive configuration but missing aggregate/compare can fail inside evaluation. Use a discriminated schema for package versus visual rules and reject unsupported sources/incomplete configurations before saving.

8. **Webhook trust is not enforced.** The sender can attach `ALERT_WEBHOOK_SECRET`, but ingestion never validates it. Rule mutation and WebSocket routes also have no authentication in the inspected application. A network client with access can create/delete rules, inject TV alerts, or subscribe to alerts. Address before exposing the service beyond a trusted development setup.

9. **Source contains a hardcoded credential fallback.** `src/vision/package_detection.py` supplies a literal default Roboflow key. Remove it, require environment configuration, and rotate it if it is an active credential. No credential value is reproduced in this report.

### P2: Reliability, deployment, and maintainability

- **Activation does not guarantee execution.** Rule creation always reports `activated` after storing the rule, even when the loop is stopped or the model cannot load. Report stored/monitoring/delivery states separately.
- **Process-local fallback is misleading.** On a backend connection error, the dashboard can save a rule in its own RAM store. That store is separate from the backend process and does not activate backend monitoring. Read fallbacks similarly obscure server outages.
- **Deletion result is incorrect in the dashboard.** `remove_rule_via_server` ignores the HTTP response and returns the local-store removal result, which can be false after successful backend deletion.
- **Deadlines lack date/timezone and event-window semantics.** `TimelineStore.check_deadlines` searches all stored events without restricting them to after deadline creation or before the deadline. Old events can satisfy a new deadline, invalid times are silently accepted, and checks depend on frame progress. Run scheduling independently using timezone-aware timestamps and explicit windows.
- **History can grow without bound.** Timeline events and TV event state have no retention limit. Detection logging repeats while attribute inference is pending or failing. Position buckets are not stable object identities.
- **Alert delivery has no durable retry.** Synchronous webhook calls block package processing; a failed send can still consume the per-cycle alert gate. “Delivered” webhook responses may have zero TV recipients. TV clients miss messages while disconnected because no replay exists.
- **Snapshots are omitted for rule alerts.** Headless and desktop rule-alert paths pass `frame=None`, despite TV snapshot support.
- **Engine stop/start can overlap work.** Stop waits only four seconds before marking stopped, while model/network work can take longer. A subsequent start clears the shared stop event even if the old worker is alive. Ensure worker termination or use per-run cancellation state.
- **Duplicated monitoring implementations diverge.** Desktop and headless paths differ in cache reset, state handling, and final-frame telemetry. Extract one engine with separate UI adapters.
- **Dependencies are incomplete/unpinned.** `aiortc` is imported by the stream module mounted at backend startup but is absent from requirements. The standalone tracker imports `langgraph`, also undeclared. Requirements use lower bounds rather than a reproducible lock. Local tests passing in an existing environment do not establish a clean Docker installation.
- **TV connectivity is machine-specific.** Default WebSocket URL is a hardcoded LAN IP using plaintext WS. Configure the endpoint for deployment. Device launch paths in the desktop launcher are also machine-specific.
- **Provider/configuration errors are misclassified.** Automation creation wraps all exceptions as HTTP 400, including upstream outages, missing configuration, and internal bugs. Preserve HTTP exceptions and distinguish validation failures from provider/internal errors. Avoid returning provider response bodies indiscriminately.
- **RAM storage requires one-process assumptions.** Multiple backend workers would hold separate rules, timelines, and WebSocket registries. Either enforce one worker for this prototype or externalize coordination.
- **Startup/shutdown clears existing rules.** The desktop entrypoint explicitly wipes local and backend rules before and after monitoring. This is broader than clearing temporary playback state and should be clearly deliberate.

## Verification

- Backend: `.venv/bin/python -m pytest tests/test_rule_evaluator.py tests/test_webhook.py tests/test_fire_tv.py tests/test_ring_integration.py -q`: **18 passed**, with 31 dependency warnings.
- TV: `npm test -- --runInBand --watchman=false`: **12 passed**, two suites, one snapshot passed. The first attempt using Watchman failed on local state-directory permissions; disabling the optional watcher allowed the tests to run.
- TV: `./node_modules/.bin/tsc --noEmit`: **failed** with three `Cannot find name 'global'` errors in `test/App.spec.tsx`, lines 39, 44, and 49.
- Mocked parser response reproduced the exact reported exception without calling a paid provider.
- Rule-model probe confirmed that incomplete unknown-source rules are accepted.
- The entire pytest directory was deliberately not collected: `test_package_tracking.py` loads a model and starts video inference at import time. It is an interactive demo script, not an isolated unit test. Move it out of default test discovery or guard execution.
- No parser regression tests, full visual-worker integration tests, approach/door-zone tests, deadline-window tests, or real camera-to-TV tests were found in the inspected suite.

## Suggested implementation order

1. Harden rule compilation: nonempty output contract, schema enforcement, bounded retry, internal diagnostics, async I/O, correct status codes, aligned client timeouts, parser/route regression tests.
2. Connect an explicit live camera frame source and implement tracked entry/approach into a configured door zone. Surface unsupported intent clauses until implemented.
3. Fix visual worker admission/fairness, strict boolean parsing, cancellation, and alert lifecycle state.
4. Add webhook authentication and remove the credential fallback; make environment and dependency configuration reproducible.
5. Unify engines, fix deadlines and delivery status, add retention/retry/replay as required, update documentation, and resolve TV type checking.

This is a substantial demo with working component tests. The reported parser failure is one defect; end-to-end support for the requested live door-approach alert requires the camera-source and temporal/spatial work as well.
