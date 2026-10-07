# Audit & Plan: Code refactoring of lightbox_mcp

- **Status:** Implemented. Code-review gate reached; waiting for the human to review before Phase 5 testing.
- **Scope (in):** `src/lightbox_mcp/*.py`, `build_exe.py`, `packaging/entry.py`, and lint/format
  config in `pyproject.toml` plus a new `.flake8`. New test files go under `tests/`.
- **Scope (out):**
  - Any change to what agents see: tool names, parameters, descriptions, replies and error
    text, server instructions.
  - Any change to the bytes on the wire.
  - The vendor DLLs and `LHMconfig.ini`.
  - Editing existing tests.
  - Rebuilding `dist/lightbox_mcp.exe`. That needs Windows.
- **Last updated:** 2026-10-06

## 1. Current behavior
An MCP server for the YANDING MLB-LHM V2.0 lightbox: 22 tools for three lights, the rail and
the turntable. There are two light backends, chosen by `DeviceVersion`:
- LHM serial protocol (`lights.py` + `protocol.py`);
- CCXL DLL (`ccxl.py`).

The rail/turntable is driven through `LHMGuideV2.dll` (`rail.py`). The code is clean and well
commented. The problems are structural:
- `server.py` is 610 lines. The coding standard's hard ceiling is 500.
- It mixes six responsibilities: MCP middleware, global device state, light tools, rail tools,
  motion wait loops, and CLI/config resolution.
- The two light backends share an interface that is never written down. `server.py` and
  `selfcheck.py` branch with `isinstance`.
- Small helpers are duplicated across modules.

**Baseline, run on this PC (Linux, Python 3.10, deps pinned from `uv.lock`):**
- `pytest`: 14 passed, 3 skipped. The skips are the DLL byte-comparison tests, which need
  Windows x64.
- `tests/smoke_client.py`: every light step passes over `loop://`. It stops at
  `rail_get_ranges` because `LHMGuideV2.dll` needs Windows. That stop is expected.
- Behavior snapshot (scratch script): 22 tool schemas plus 61 tool calls in 3 scenarios
  (V2 + loop ports, V2 + missing COM ports + start-up self-check, embedded CCXL config +
  self-check). It records the replies, the error text and the console log. This is the
  equivalence baseline for the refactor.
- Lint: flake8 at 120 columns finds 9 issues, ruff finds 16 (13 in `src/`), and Black would
  reformat 13 of 20 files.

## 2. Flow / trace
Entry points:
- `pyproject.toml:17` (`lightbox_mcp.server:main`)
- `packaging/entry.py:3`
- `python -m lightbox_mcp.server` (used by `tests/smoke_client.py:31`)
- `.mcp.json` (`uv run lightbox-mcp`)

Path through `server.py`:
- `server.py:560` `main()`: argument parsing (`:562-581`), then `logs.setup`, `_banner` (`:543`),
  `_use_config` (`:528`, rebinds the globals `cfg` and `lights`), `selfcheck.run` (`:587`) and
  `mcp.run`.
- `server.py:113` builds the MCP instance with the `TrafficLogger` middleware (`:54-108`).
- `server.py:114-123` holds the module-level state `cfg`, `lights` and `rail`, plus
  `make_light_bank` (`:117`).
- Light tools are at `server.py:151-296`. Rail tools are at `:311-506`.

Backends:
- LHM serial backend: `lights.py:157` `LightBank`, `lights.py:109` `Light`, and `lights.py:61`
  `SharedPort` (one open port shared by every light on it).
- CCXL backend: `ccxl.py:153` `CcxlLightBank`, `ccxl.py:125` `CcxlLight`, and `ccxl.py:68`
  `CcxlController` (ctypes on the C++ class).

Rail:
- `rail.py:82` `RailController`. It resolves mangled C++ exports via `native.py:65`.
- `rail.py:142` `_check` reconnects once on codes 2/3.

Self-check and logging:
- `selfcheck.py:73` `run()` connects the rail and lights and prints the green/red summary.
- `selfcheck.py:31` `realtime()` reads the real-time values.
- `logs.py:153` `traced()` logs every DLL call (TX/RX).

## 3. Data / model
- `config.LhmConfig` (`config.py:45`) holds the lights, `has_cct`, `device_version`, ranges,
  `source` and `warnings`.
- Light bank interface, used by both backends (implicit duck typing today): `backend`,
  `lights`, `get()`, `status()`, `close_all()`, `supports()`, `set_param()` and `off()`.
- Per-light state is `last_command`: a dict with `"at"`, a naive local ISO time to the second.

## 4. Risks & possible bugs
- **Bug B1: `device_check` ignores `--plc-ip`.** `server.py:365` calls `selfcheck.run(rail, lights, cfg)`
  without the IP, so it always uses `192.168.1.1`. The start-up check at `server.py:587` uses
  `args.plc_ip`. With a non-default IP, `device_check` either tries the wrong PLC or labels
  the connection with the wrong address (`selfcheck.py:87`).
  - Severity: Low (the default IP is what SW_MLB uses).
  - Execution-verified: N (found by static trace). Confidence: High.
- **Bug B2: wrong checksum in a docstring.** `logs.py:7` shows `... 19 9B`, but the correct frame
  ends in `8D`; the smoke test asserts `8D`. Docs only. Verified: Y.
- **Smell: argument shadowed.** In `light_connect`, the loop variable `port` redefines the
  function argument `port` (`server.py:179`, ruff PLR1704). Harmless today. Verified: Y (lint).
- **Smell: wrong type hint.** `selfcheck.run(lights: LightBank)` (`selfcheck.py:73`) also
  receives a `CcxlLightBank`. Verified: Y.
- **Smell: private state reached across objects.** `CcxlLight.close` reads `l._open` on sibling
  lights (`ccxl.py:149`). `CcxlLightBank.close_all` writes `light._open` (`ccxl.py:183`).
- **Duplication.** Each helper below exists more than once; all were found by grep over `src/`:
  - COM-name normalization: `server.py:175`, `lights.py:80`
  - COM regex: `config.py:16`, `ccxl.py:100`
  - frame→hex formatting: `lights.py:144,154,207,224`
  - timestamps: `lights.py:206,223`, `ccxl.py:211,221`
  - "light not connected" error: `server.py:138`, `lights.py:137`, `ccxl.py:191`
  - best-effort reads: `server.py:304` and `selfcheck.py:24`
  - light names: the literal `("top", "up", "down")` in `server.py:170,289` duplicates `config.LIGHTS`
  - mode numbers: literals `1/2/3` in place of the `protocol.MODE_*` constants
    (`lights.py:181`, `ccxl.py:187,199`).
- **Note (out of scope): blocking calls on the event loop.** `rail_move_to` and `rail_home` call
  the DLL synchronously from async code (`server.py:400-425,443-464`). If the PLC is slow to
  respond, other MCP requests stall. Not changed here, because it would alter timing.
- **Note (out of scope): unclear flag naming.** `rail.homing()` returns the PLC's `GetHomeDone`
  flag but is documented as "in progress" (`rail.py:206`). Its meaning is not confirmed on
  hardware. Not changed.
- **Highest-risk area: DLL binding code.** `rail.py:103-119` and `ccxl.py:79-95` only run on the
  Windows lightbox PC. Nothing here exercises them, because the existing tests override
  `_raw` / `ctl`. Any edit there cannot be checked against the real DLL on this machine.

## 5. Constraints & invariants touched
- **The agent-facing contract must not change.** That means all 22 tool names, parameter
  schemas, descriptions, `INSTRUCTIONS`, reply payloads and error strings. Verified by
  diffing the behavior snapshot.
- **Bytes on the wire must not change.** `protocol.py` frames are checked byte for byte
  against the vendor DLLs (`tests/test_protocol_vs_dll.py`, Windows only), so `protocol.py`
  frame builders stay untouched.
- **Existing tests must pass unchanged.** They pin these internals:
  - `server.rail` is monkeypatched, and `server.rail_home` and `server.HOME_START_GRACE_S` are
    used directly (`tests/test_rail_home.py:34,51`). So `rail_home` must stay reachable from
    `server` and must read `rail` from the `server` module at call time.
  - `Light._port` and `SharedPort.serial` (`tests/test_lights.py:9-20`).
  - `CcxlLightBank.ctl`, replaced by a fake with `connect/disconnect/light_on_off/set_level`
    (`tests/test_ccxl.py:9-34`).
  - `RailController._raw(name, *args, require_connection=True)`, `_check`→`_raw` and
    `connect`→`_raw("InitAndConnect", …)` (`tests/test_rail_reconnect.py`).
  - `DataPramNew` field names (vendor struct names, `tests/test_ccxl.py:40`).
- **Entry points must keep working:** `lightbox_mcp.server:main`, `python -m lightbox_mcp.server`
  and `packaging/entry.py`. PyInstaller finds modules through static imports, so every new
  module must be imported normally (no dynamic imports).
- **Coding standard** (PEP 8 skill): modules under 500 lines, Black/Ruff/Flake8 clean.
  Project style wins: the existing code is written to about 120 columns, so the line length
  is set to 120 in config rather than reflowing everything to 79.

## 6. Complexity & Effort Assessment
- **Split `server.py`** into `middleware.py`, `cli.py` and `motion.py`, keeping tools + state + `main`
  in `server.py`:
  - Effort M | risk Medium (entry points, test monkeypatching, PyInstaller) | confidence High
    (snapshot and tests cover it)
  - Touches `server.py:54-108,379-466,512-557`
- **Formalize the light-backend interface:** a `LightBackend` Protocol, and move the
  `light_connect` per-backend logic into each backend.
  - Effort M | risk Medium (`light_connect` reply and errors must stay identical) | confidence High
    (5 snapshot calls cover both backends)
  - Touches `server.py:157-186`, `lights.py`, `ccxl.py`, `selfcheck.py:73`
- **Remove the duplicated helpers listed in §4:**
  - Effort S | risk Low | confidence High
  - Touches `config.py`, `lights.py`, `ccxl.py`, `protocol.py` (new helper only), `errors.py`,
    `selfcheck.py`, `logs.py`
- **Encapsulation and idiom fixes:** `_open` access, `functools.partial` instead of the
  `this`-binding lambdas, `native.exported_names` readability.
  - Effort S | risk **Medium** (the DLL binding only runs on Windows) | confidence Low until a
    fake-DLL test exists
  - Touches `ccxl.py:79-95,143-150,181-184`, `rail.py:110-117`, `native.py:56-57`
- **Fix bug B1 (`device_check` IP) and bug B2 (docstring):**
  - Effort S | risk Low | confidence High
  - Touches `server.py:361-365,577-587`, `logs.py:7`
- **Lint/format:** Black, Ruff and Flake8 at 120 on `src/`, `build_exe.py` and `packaging/`, with
  config in `pyproject.toml` and `.flake8`.
  - Effort S | risk Low (mechanical, re-verified by tests and the snapshot) | confidence High
- **Overall rollup:** M effort, Medium risk. Riskiest item: the DLL binding edits in `rail.py`
  and `ccxl.py`, which can only truly be verified on the lightbox PC.

## 7. Open questions → decisions (2026-10-06)
1. Bug B1 (`device_check` ignores `--plc-ip`). **Decision: fix it** in this refactor.
2. Existing tests. **Decision: leave them byte-for-byte untouched.** New tests are added
   alongside. Lint and format scope is `src/`, `build_exe.py` and `packaging/`.
3. Plan approved as written, including the Black/Ruff/Flake8 pass at 120 columns.

---

## 8. Proposed design / new flow
Target layout (no tool or wire behavior changes):

| Module | Responsibility |
| --- | --- |
| `server.py` | MCP instance, `INSTRUCTIONS`, device state (`cfg`, `lights`, `rail`), the 22 thin tool functions, `main()` |
| `middleware.py` (new) | `TrafficLogger`: logs every agent request and reply |
| `cli.py` (new) | Argument parsing, base dir, default-config resolution, start-up banner |
| `motion.py` (new) | Async wait loops for `rail_move_to` and `rail_home`, plus `HOME_START_GRACE_S`. They take the rail as a parameter, so `server.rail` monkeypatching still works |
| `backend.py` (new) | `LightBackend` Protocol (the interface both backends implement), `timestamp()`, `not_connected()` |
| `lights.py` / `ccxl.py` | Each gets `connect_lights(port, top_port, up_port, down_port)`, which takes over the old `isinstance` branch in `light_connect` |
| `config.py` | Adds `EMBEDDED_CONFIG`, `normalize_port()` and `com_port_number()`. Per-light INI keys become a table |
| `protocol.py` | Adds `to_hex(frame)`. Frame builders unchanged |
| `selfcheck.py` | Typed with `LightBackend`. One `read()` helper, which the rail-status tool reuses |

## 9. Change list (scope boundary for Phase 4)
- `src/lightbox_mcp/server.py`: move code out (above). `light_connect` delegates to the backend.
  Use `LIGHTS` and the mode constants. Fix B1 if approved.
- `src/lightbox_mcp/middleware.py`, `cli.py`, `motion.py` and `backend.py`: new.
- `src/lightbox_mcp/lights.py`, `ccxl.py`, `config.py`, `protocol.py`, `selfcheck.py`, `errors.py`,
  `logs.py`, `rail.py` and `native.py`: the cleanups listed in §6.
- `build_exe.py`, `packaging/entry.py`: formatting only.
- `pyproject.toml`: `[tool.black]` and `[tool.ruff]` with line-length 120. `.flake8` (new): the
  same limit, plus the standard Black-compatible ignores (E203, W503).
- `tests/test_dll_binding.py`, `tests/test_rail_move.py`, `tests/test_selfcheck.py`: new
  characterization tests (see §11).
- Not touched: the existing tests, `LHMconfig.ini`, the vendor DLLs, `uv.lock` and `dist/`.

## 10. Risks & mitigations
- **There is no git repository, so the edits cannot be reverted with version control.** Make a
  full tarball backup of the project before the first edit.
- **The agent-facing contract could drift.** Diff the behavior snapshot before and after; any
  difference blocks completion.
- **DLL-binding edits cannot be run on this machine.** Add a fake-DLL test that checks argtypes,
  restypes and that `this` is passed first. Write the new characterization tests *before* the
  refactor and confirm they pass on the original code.
- **Hardware paths can't be exercised here:** rail moves, real COM ports and the Windows-only DLL
  tests. Hand over an on-site checklist: rebuild the exe, run `pytest` on Windows (which
  includes the DLL byte tests), then run `device_check` / `get_realtime` / a short
  `rail_move_to` on the lightbox.

## 11. Verification plan
- **New tests** (written first, then run against the original and the refactored code):
  - `tests/test_dll_binding.py`: `RailController` and `CcxlController` bind and call the mangled
    exports correctly, using a fake library.
  - `tests/test_rail_move.py`: `rail_move_to` finishes; on a fault it stops and raises; on a
    timeout it stops and raises; out-of-range input and e-stop are rejected.
  - `tests/test_selfcheck.py`: `selfcheck.run` with a connected fake rail and loop:// lights
    returns the same summary.
- **Existing tests:** all must pass unchanged (14 pass, 3 skipped on Linux).
- **Behavior snapshot:** identical before and after. The only allowed difference is B1, and only
  if approved; the snapshot does not use `--plc-ip`, so it should be identical either way.
- **Lint:** `black --check`, `ruff check` and `flake8` are clean on `src/`, `build_exe.py` and
  `packaging/`.
- **Definition of done:** all of the above are green, the doc is updated with the results, and the
  remaining hardware checks are listed for the lightbox PC.

---

## 11b. Premise check (start of Phase 4)
- **Claim:** `server` is imported only by `packaging/entry.py`, `pyproject.toml`, `tests/test_rail_home.py` and
  `tests/smoke_client.py`.
  - Re-checked with a `grep` of all `*.py`, `*.toml` and `*.json` files for `server` imports and uses.
  - **Holds.**
- **Claim:** `device_check` calls `selfcheck.run` without the IP (bug B1).
  - Re-checked with a `grep` for `selfcheck.` callers: `server.py:365` (no IP) and `:587` (with IP).
  - **Holds.**
- **Claim:** the existing tests rely on no private attribute other than `Light._port`.
  - Re-checked with a `grep` for `._x` in `tests/`: `_port`, ctypes `_obj` and `_fields_` only.
  - **Holds.**
- **Extra check:** the SDK treats `ToolError` subclasses (`DeviceError`) exactly like `ToolError`:
  `mcp/server/mcpserver/tools/base.py:208-210`, `"Error executing tool <name>: <message>"`.
  - So moving errors from `ToolError` to `DeviceError` keeps the agent-visible text.
  - **Holds.**
- **Safety net first:**
  - A full backup was taken before the first edit: `backup_original_2026-10-06.tar.gz`, 60 MB, in the
    session scratchpad.
  - 26 characterization tests were written and run against the *original* code: all pass (one
    expectation was mine and was corrected to match the original's banner format).

## 12. Implementation outcome
- **Files changed:**
  - `server.py` 610 → 467 lines.
  - `lights.py`, `ccxl.py`, `config.py`, `selfcheck.py`, `rail.py`, `native.py`, `logs.py`, `errors.py`,
    `protocol.py`, `build_exe.py` (formatting only) and `pyproject.toml`.
- **New source files:** `middleware.py`, `cli.py`, `motion.py`, `backend.py` and `.flake8`.
- **New tests:** `tests/conftest.py` (shared `FakeRail`), `tests/test_dll_binding.py`, `tests/test_rail_tools.py`,
  `tests/test_selfcheck.py` and `tests/test_server_startup.py`.
- **Existing tests:** not touched. All 7 files are byte-identical to the backup.
- **Scope changes:** none outside §9. Test names deviate from the plan:
  - `test_rail_move.py` became `test_rail_tools.py`, because it covers all rail tools, not only moves.
  - `conftest.py` and `test_server_startup.py` were added (shared fake, and the B1 regression test).
  - All of these are new files under `tests/`, as the scope allowed. Flagged for review.
- **Deviations from the plan:** none in behavior. Notes for the reviewer:
  - `light_connect` now opens all lights in one worker-thread call instead of one call per light. The
    order and the results are the same.
  - Agent-visible errors from light-not-connected, `connect_lights`, and motion faults/timeouts are now
    raised as `DeviceError`, a `ToolError` subclass, with the same text.
  - `rail_home`'s `seconds` is now taken just before the final position read rather than just after. The
    difference is under a millisecond.
  - `build_exe.py` uses `# fmt: off` so each PyInstaller option stays on one line with its value.
- **Bug fixes:**
  - B1: `device_check` now uses the `--plc-ip` given at start-up (`server.plc_ip`).
  - B2: the docstring checksum is corrected.
- **Follow-ups / known limitations:**
  - `dist/lightbox_mcp.exe` is now stale and must be rebuilt on Windows.
  - The DLL byte-comparison tests and the real rail/light paths can only run on the Windows lightbox PC.
  - The notes in §4 (blocking DLL calls in async tools, and what `homing()` means) are unchanged.

## 13. Code review
- **Diff summary:** 2,429-line unified diff, excluding binaries, docs and the README. Contents:
  - The module split listed in §8.
  - The two backends gained `connect_lights()`, which replaces `isinstance` in `light_connect`.
  - The light-backend interface is now written down (`backend.LightBackend`).
  - Duplicated helpers were consolidated: `normalize_port`, `com_port_number`, `to_hex`, `timestamp`,
    `light_not_connected`, `selfcheck.read`.
  - Encapsulation fix: `CcxlLight.is_open` with `disconnect_if_unused()`.
  - `functools.partial` replaces the lambdas that bound `this`.
  - The PE export parser in `native.py` is easier to read.
  - Black formatting at 120 columns.
- **Matches the Phase 3 change list?** Yes (see §12 for the test-name deviations). Unplanned edits: none.
  Scope changes recorded: yes.
- **Invariants:** tool names, schemas, descriptions and `INSTRUCTIONS` were copied verbatim. `protocol.py`
  frame builders are unchanged. The entry points are unchanged: `lightbox_mcp.server:main`,
  `python -m lightbox_mcp.server` and `packaging/entry.py`. All new modules are imported statically, so
  PyInstaller picks them up.
- **Lint:** `black --check`, `ruff check` and `flake8` are clean on `src/`, `build_exe.py`, `packaging/` and
  the new tests.
- **Debug leftovers or commented-out code:** none (checked with `grep` for print, breakpoint, pdb and TODO).
- **Human review:** pending.
