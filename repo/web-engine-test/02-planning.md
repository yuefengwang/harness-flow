# 02-Planning

> Hooks: `hooks/02-planning.md`

## Task DAG

- [ ] **Task 1**: `test-infra-setup` | Deps: None
  - **Do**: Create `repo/web-engine-test/conftest.py` with shared fixtures: FastAPI test client factory, `WebEngineManager` singleton reset fixture, task lifecycle fixtures (create + cleanup), SSE event collector helper. Create `repo/web-engine-test/requirements-test.txt` with `pytest`, `pytest-asyncio`, `httpx`.
  - **Verify**: Fixtures can be imported without circular imports; existing 47 tests in `tests/unit/web/` still pass.

- [ ] **Task 2**: `engine-session-edge-cases` | Deps: `test-infra-setup`
  - **Do**: Write `repo/web-engine-test/test_engine_session_edge.py` covering:
    1. `get_event()` returns `None` immediately after `destroy()` (not stuck in loop)
    2. `get_all_events()` returns empty list after destroy
    3. Concurrent `_push_event` from multiple threads doesn't lose events
    4. `_bridge_answers` exception path (force failure in async_res_queue.get)
    5. `submit_answer` / `submit_command` on destroyed session does not raise
    6. `status` property returns `"idle"` when engine.agent is None
  - **Verify**: 6 new tests pass; no regression on existing tests.

- [ ] **Task 3**: `deploy-route-edge-cases` | Deps: `test-infra-setup`
  - **Do**: Write `repo/web-engine-test/test_deploy_route.py` covering deploy edge cases NOT in existing test_tasks_api.py:
    1. `POST /tasks/{name}/deploy` on nonexistent task returns 404
    2. `POST /tasks/{name}/deploy` on a Finished task with missing/nonexistent `target_dir` returns 400
    3. Verify deploy_status transitions via direct `_service.complete_deploy` call
    4. `POST /tasks/{name}/deploy` on a trashed task returns 400
  - **Verify**: 4 new tests pass; deploy state machine verified.

- [ ] **Task 4**: `api-error-scenarios` | Deps: `test-infra-setup`
  - **Do**: Write `repo/web-engine-test/test_api_errors.py` covering:
    1. `POST /tasks/create` with empty name returns 400
    2. `POST /tasks/{name}/advance` on nonexistent task returns 404
    3. `POST /tasks/{name}/remove` on nonexistent task returns 400
    4. `POST /tasks/{name}/engine/start` with path traversal attempt (`../`) in name
    5. `GET /tasks/{name}/console` with path traversal attempt (`../`) in name
    6. `GET /tasks/{name}/sse` returns 404 when session destroyed mid-stream (use httpx for streaming read + destroy)
    7. `POST /tasks/{name}/engine/answer` with missing `text` field returns 422
    8. `POST /tasks/{name}/engine/command` with missing `cmd` field returns 422
  - **Verify**: 8 new tests pass; error responses have correct status codes.

- [ ] **Task 5**: `template-rendering-tests` | Deps: `test-infra-setup`
  - **Do**: Write `repo/web-engine-test/test_templates.py` covering:
    1. Dashboard index renders task list with active and trashed tasks
    2. Dashboard index renders empty state when no tasks
    3. Detail page renders all stage files with correct done/pending states
    4. Detail page renders log content when log file exists
    5. Console page renders with all UI elements (start button, log container, input area)
    6. Console page renders existing log lines
    7. Task table partial renders "暂无活跃任务" when empty
    8. Task table partial renders both active and trashed task sections
    9. Detail page renders deploy log section when deploy_log exists
  - **Verify**: 9 new tests pass; content assertions on rendered HTML.

- [ ] **Task 6**: `sse-streaming-edge-cases` | Deps: `test-infra-setup`
  - **Do**: Write `repo/web-engine-test/test_sse_streaming.py` covering:
    1. SSE stream yields events in correct order (log, question, settlement) — use `httpx` async client to stream
    2. SSE stream sends keepalive comments during idle periods — check raw stream for `: keepalive` lines
    3. SSE stream terminates when session is destroyed — destroy session mid-read, verify stream closes
    4. Multiple SSE clients can connect to the same session simultaneously — open 2+ concurrent `httpx` streams
    5. SSE stream handles rapid event bursts without dropping — push 100 events rapidly, verify count on client
  - **Verify**: 5 new tests pass; streaming behavior validated.

- [ ] **Task 7**: `frontend-js-test` | Deps: none (standalone, requires Playwright)
  - **Do**: Write `repo/web-engine-test/test_console_js.py` using Playwright to:
    1. Launch browser → navigate to console page → verify UI elements render
    2. Click "启动引擎" → verify button text changes to "已启动"
    3. Type a message → submit → verify it appears in log container
    4. Verify SSE reconnection behavior after disconnect (simulate network disconnect)
  - **Note**: Requires `playwright install` before running. Can be deferred if Playwright not available.
  - **Verify**: 4 Playwright tests pass; frontend behavior matches spec.

- [ ] **Task 8**: `engine-manager-concurrency` | Deps: `test-infra-setup`
  - **Do**: Write `repo/web-engine-test/test_engine_manager_concurrency.py` covering:
    1. 10 threads simultaneously creating/destroying sessions for different tasks
    2. 5 threads concurrently pushing events to same session via `_push_event`
    3. Thread-safe singleton access from multiple threads (verify `_instance` consistency)
    4. Memory: verify sessions are removed from `_sessions` dict after `destroy_session`
    5. Rapid start/stop cycles (create_session → destroy_session × 20) no leak in `_sessions` dict
    6. Concurrent `get_event()` and `_push_event` from different threads — no data races
  - **Verify**: 6 new tests pass; no deadlocks or data races.

## Test Strategy
- **Method**: unit (pytest + FastAPI TestClient) + integration (SSE streaming with httpx) + e2e (Playwright for JS, optional)
- **Key path**: Engine session lifecycle → SSE streaming → Console UI (full user flow)
- **Existing base**: 47 tests already in `tests/unit/web/` — new tests are additive, no existing code modified
- **Repro script** (bugfix): N/A (feature, not bugfix)

## Tech Detail
- **Key types/interfaces**: `WebEngineSession`, `WebEngineManager`, `FastAPI.testclient.TestClient`, `Jinja2Templates`, `StreamingResponse`, `TaskService`
- **Files to create**:
  - `repo/web-engine-test/conftest.py` — shared test fixtures
  - `repo/web-engine-test/requirements-test.txt` — test dependencies
  - `repo/web-engine-test/test_engine_session_edge.py` — session edge cases
  - `repo/web-engine-test/test_deploy_route.py` — deploy route edge cases
  - `repo/web-engine-test/test_api_errors.py` — API error scenarios
  - `repo/web-engine-test/test_templates.py` — template smoke tests (9 cases)
  - `repo/web-engine-test/test_sse_streaming.py` — SSE streaming tests (5 cases)
  - `repo/web-engine-test/test_console_js.py` — Playwright frontend tests (optional)
  - `repo/web-engine-test/test_engine_manager_concurrency.py` — concurrency tests (6 cases)
- **Existing test base (not modified)**: `tests/unit/web/` (47 tests, 3 files)

## Resource Check
- pytest 8.4.2 ✅ | httpx 0.28.1 ✅ | pytest-asyncio ✅ | Playwright ❌ (Task 7 optional, needs manual install)
- Web engine source code: engine_manager.py (174 lines), app.py, console.py, tasks.py ✅
- Templates: index.html, detail.html, console.html, task_table.html ✅
- Static JS: console.js ✅ (needed for Playwright test assertions)

## Gate
- [ ] All 38+ new tests pass (Tasks 2-8 minus optional Task 7)
- [ ] No regression on existing 47 tests (`pytest tests/unit/web/`)
- [ ] No regression risk: tests are additive, no existing code modified
