# Audit & Plan: Translate all Chinese content to English

- **Status:** Implemented. Code-review gate reached; waiting for the human to review before Phase 5 testing.
- **Scope (in):** all project-owned text that is in Chinese: `README.md` and the
  Chinese file name `docs/SW_MLB软件使用手册.pdf`.
- **Scope (out):** vendor-owned material that we did not write and cannot rebuild:
  - the *contents* of the vendor PDF manuals;
  - `New folder/YANDING_SW_MLB_V2.7.rar` (the original vendor package: 103 of its 610
    entries have Chinese names, e.g. `光源_API_Manual.pdf`, `LMH导轨转盘/`);
  - the vendor DLLs in `src/lightbox_mcp/vendor/` (binaries).
- **Last updated:** 2026-10-06

## 1. Current behavior
- `README.md` (130 lines) is written in Chinese. 66 lines contain Chinese text. Code
  blocks, tool names and console samples are already in English.
- All Python sources, tests, `LHMconfig.ini`, `pyproject.toml`, `.mcp.json` and
  `.gitignore` are already English/ASCII. That includes docstrings, comments, log
  lines, and the tool descriptions and error messages that agents see.
- `docs/` holds two vendor manuals: `SW_MLB_User_Manual.pdf` (English, 13 pp) and
  `SW_MLB软件使用手册.pdf` (Chinese, 15 pp). Both were made with WPS.

## 2. Flow / trace (how the search was done)
- CJK search with `grep -P '[\x{4e00}-\x{9fff}\x{3000}-\x{303f}\x{ff00}-\x{ffef}]'`
  over `*.py *.md *.ini *.toml *.json .gitignore`. Only `README.md` matched (66 lines).
- `file` encoding check on every source file: all ASCII except `README.md` (UTF-8 Chinese)
  and `logs.py`/`protocol.py`, which are UTF-8 only because of `→` and `—` in docstrings.
- `LHMconfig.ini` was decoded as GBK as well, in case the vendor's Chinese encoding was
  hiding text there. It is plain ASCII.
- File-name scan (`find . | grep -P '[\x{4e00}-\x{9fff}]'`): only `docs/SW_MLB软件使用手册.pdf`.
- `uv.lock`: 0 CJK lines.
- The DLLs: decoding them as GBK gives strings like `烫烫烫`. That is the MSVC Debug
  fill byte `0xCC`, not real text, so there is nothing to translate.

## 3. Data / model
Not applicable (documentation only).

## 4. Risks & possible bugs
- **Mistranslating technical claims in the README.** Medium severity: the README is the
  operator's guide for the lightbox PC. Mitigation: translate sentence by sentence,
  keep every code block, tool name, port, address and number exactly as written, and
  re-check each claim against the code. (Confidence: High.)
- **Broken references after renaming the PDF.** Low severity. The README cites
  "chapter 2 of the vendor manual". Chapter 2 of the English manual covers the same
  IPv4 `192.168.1.*` setup (checked with `pdftotext`), so the link can point to it.
- **The English manual may not be a full translation** (13 pp vs 15 pp). Not checked
  page by page. Keep the Chinese original rather than deleting it.

## 5. Constraints & invariants touched
- Tool names, parameters and console output shown in the README must still match the
  code after the refactor (see `AUDIT_code-refactor.md`).
- The `New folder/…rar` path is cited as the source of truth, so it stays unchanged.

## 6. Complexity & Effort Assessment
- **README translation:** effort S | risk Low (docs only) | confidence High | touches `README.md`
- **PDF rename:** effort S | risk Low (one README reference) | confidence High | touches `docs/`
- **Overall rollup:** S effort, Low risk. Riskiest item: wording accuracy of the README.

## 7. Open questions → decisions (2026-10-06)
- Chinese manual file name. **Decision: rename** to `docs/SW_MLB_User_Manual_zh-CN.pdf`
  (content kept).
- Plan approved as written.

---

## 8. Proposed design
- Rewrite `README.md` in English with the same structure and every fact unchanged. Also
  add a short "Project layout" table so the README reflects the module split from the
  refactor.
- Rename the Chinese PDF as decided in §7, and point the README's "vendor manual"
  references at `docs/SW_MLB_User_Manual.pdf`.

## 9. Change list
- `README.md`: full English translation, plus a project-layout section.
- `docs/SW_MLB软件使用手册.pdf`: rename only (if approved).

## 10. Risks & mitigations
- Mistranslation → claim-by-claim check against the code; keep code blocks verbatim.

## 11. Verification plan
- Re-run the CJK grep and file-name scan on the project and expect 0 hits, apart from the
  out-of-scope vendor archive.
- Check that every tool, flag and file named in the README exists in the code.

---

## 11b. Premise check
- **Claim:** `README.md` is the only text file with Chinese in it, and the PDF is the only Chinese file name.
  - Re-checked at the start of Phase 4 with the same `grep` and `find` as in §2.
  - **Holds.**

## 12. Implementation outcome
- **`README.md`:** rewritten in English.
  - Same sections, tables, code blocks, console sample, tool list and numbers.
  - The vendor-manual reference now points to chapter 2 of `docs/SW_MLB_User_Manual.pdf`.
  - Added: a "Project layout" table that reflects the refactor; a note that `--plc-ip` is also used by
    `device_check` (bug B1 fix); lint commands. The development comments now say which checks need
    Windows x64.
- **PDF:** `docs/SW_MLB软件使用手册.pdf` renamed to `docs/SW_MLB_User_Manual_zh-CN.pdf`. The content is
  byte-identical to the original (`cmp`).
- **Scope changes / deviations:** none.

## 13. Code review
- **Human review:** pending (see the README diff).
