# KRYTH Context and Browser Execution Fix

Date: 2026-11-16

## Root causes

- Context usage events exposed cumulative prompt tokens across every model round trip in an agent turn. The UI interpreted that cumulative value as the current request's context size, producing impossible percentages such as `997,809 / 256,000 (389.8%)`.
- Compression used the last provider-reported usage value, which could be stale during tool loops. Requests had no final preflight check, and the configured tool-output cap was not applied to conversation history.
- YouTube's current page was the home page with an empty search input and no results. Navigating to a results URL produced results. A semantic click on a result returned success but left the URL unchanged; the old click handler did not verify the resulting page state.
- Workspace identity was based on the active CWD and project files. The model had no explicit source marker to distinguish a KRYTH checkout from another active project.

## Changes

- Usage events now carry separate per-request and cumulative turn usage. Context displays use only per-request prompt tokens; out-of-range provider usage is shown as unavailable instead of a percentage above 100%.
- Added an effective context limit that honors an active provider's optional `context_window` setting, with the configured model limit as fallback.
- Added a conservative serialized-request preflight in `LLMClient`. Requests above the input budget are rejected before client creation, leaving a configured output-token reserve and setting `max_tokens` on accepted calls.
- Context compression now triggers from a current serialized request estimate rather than stale usage alone.
- Oversized tool output is persisted under KRYTH's application data directory, while bounded context history keeps a readable truncation and full-output path reference.
- Workspace context now reports the active project root and loaded KRYTH runtime root separately, marks KRYTH source only when its markers exist under the active workspace, reports browser infrastructure only when it exists under that workspace, and lists configured provider/MCP integrations from config. It does not search sibling directories or external tool paths.
- KUBA link clicks now compare the observed page URL before and after the click. If an observed link does not navigate and no new tab opens, the engine validates and navigates to that observed link URL, then returns verification evidence.

## Browser verification

- Initial read-only state: YouTube home page, empty text inputs, zero video renderers; no query was present.
- Search: opened `https://www.youtube.com/results?search_query=Python+tutorials`; the page later showed Python tutorial results, including a Telusko course/video.
- Selection: the click handler reported `navigation_fallback: true` after the click did not change the URL. The fallback opened the observed result URL. Final observed state was `https://www.youtube.com/watch?v=YZkyL-f-YXY&list=PLsyeobzWxl7omDoEYrrf3oXvXxa6MPgek`, titled `Python Tutorial 2026 - YouTube`, with the video title visible. E2E outcome: **PASS**.
- Amazon: opened a separate tab for `python programming books`. Compared visible listing data for Python 3: The Comprehensive Guide (INR 6,146.58, 5.0 rating), Python Programming for Beginners (INR 2,356.24, 4.4), and Python Crash Course, 3rd Edition (INR 2,827.67, 4.8). This is a point-in-time page observation, not a purchase or endorsement.
- Filled the observed Amazon search field, confirmed its value, pressed Enter, and verified the search URL. No purchase action was taken.
- Two-tab listing, tab switch, 800 x 401 screenshot capture, disconnect/reconnect, and post-reconnect YouTube URL verification all succeeded. The temporary screenshot was removed.
- The browser remained on the verified YouTube watch page after session recovery; the Amazon comparison tab remains open in the background.

## Test checkpoint

- Previously verified full-suite checkpoint preserved: **265 passed, 1 skipped**. The full suite was not restarted.
- Current focused source tests: **48 passed** across context budget, browser observation/plugin, KUBA browser engine, and KUBA engine reliability tests.
- Project-context regression tests: **9 passed, 132 deselected**.
- `compileall` completed successfully.
- Ruff critical syntax/error rules completed successfully. A broader Ruff check still reports 10 F401/F541 findings in three touched files, including pre-existing unused imports and a no-placeholder f-string; those are unrelated to this fix and were not changed.
- One initial `rtk pytest` invocation resolved installed site-packages instead of this checkout and failed the new source tests. Re-running through the checkout with `PYTHONPATH` set passed; results above refer to the source checkout.

## Limitations and remaining work

- Provider-specific context limits must be configured on the provider profile when known. Otherwise KRYTH uses `model.context_window` (currently 256,000).
- The admission check is a conservative UTF-8 serialized-byte bound because the selected provider tokenizer may be unavailable. It can reject a request earlier than that provider's exact tokenizer would.
- Amazon pricing and ratings can change and were not rechecked beyond the observed search page.
- The existing installed `kryth` process was left running; this checkout's code changes apply to new source executions, not an already-running installed process. The full suite was intentionally not rerun.
- No separate execution-log file was supplied; the reported context statistic and browser history in the user message were used with the repository implementation and live browser state.
