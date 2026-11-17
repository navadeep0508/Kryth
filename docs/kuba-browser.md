# Use KUBA browser control in KRYTH

KUBA is KRYTH's built-in browser operator. It lets KRYTH inspect and control a
visible Chrome or Edge session. It is included with KRYTH; users do not need to
install a separate browser agent, configure MCP, or provide another model key.
KRYTH's existing approval rules still control actions that change a page.

## First-time setup

1. Install or update KRYTH and configure its model provider with `kryth setup`.
2. Install Chrome or Microsoft Edge and sign in to any sites you need. Close
   other browser windows if the browser reports that its profile is already in
   use.
3. In a terminal, run:

   ```text
   kryth browser doctor
   kryth browser setup
   ```

4. If KRYTH opens Chrome's `chrome://inspect/#remote-debugging` page, enable
   **Allow remote debugging for this browser instance** and click **Allow**.
   Chrome may show a separate permission prompt when KUBA connects; approve it
   to let KRYTH control that browser session. This is Chrome's permission and is
   separate from KRYTH's tool approval prompt.
5. Run `kryth browser setup` again if it asked for browser permission. A ready
   message with a tab count confirms that KUBA connected.
6. Start KRYTH with `kryth`, describe the browser task, and respond to any
   approval prompts. For example: `Open YouTube and search for Python tutorials`.

The browser window remains visible. Use `kryth browser doctor` later to check
whether the integration is enabled, the local KUBA helper is running, and a
browser connection is available. The doctor command does not launch a browser
or change browser settings.

## What to expect

KUBA can navigate, inspect page structure and accessibility information, click,
type, manage tabs, and take screenshots. The `observe` action returns a bounded
summary of the current URL/title, ready state, visible page text, interactive
controls, form labels, alerts, dialogs, loading indicators, and a compact
accessibility view. Compact mode is the default; request `mode = "detailed"`
when more context is needed. It avoids returning typed field values.

For clicks, KRYTH can target an exact semantic role/name, label, placeholder,
text, or test ID. It checks that the match is unique, visible, and enabled; an
ambiguous or stale target fails without clicking. It can also use a CSS selector
derived from the latest observation. Re-observe after a page transition before
using another target.

Navigation reports the resulting URL, title, and whether the document reached
the browser's complete state. Single page apps can render after that point, so
KRYTH must use `observe` to confirm the requested content. For YouTube search,
KRYTH uses its results URL directly, observes the matching results, and opens a
video only when one is requested and can be identified from the observed page.

Websites change their markup, load content asynchronously, and may present
sign-in, CAPTCHA, consent, or security prompts. Some sites may still require
user input or block automation; no browser integration can guarantee every
site or workflow will work unattended.

KRYTH requests approval for browser actions that change page state. Treat
publishing, purchases, deletion, sending messages, and other consequential
actions carefully and review the exact action before approving it.

## Configuration

Browser settings are part of the regular KRYTH TOML configuration:

```toml
[browser_plugin]
enabled = true
headless = false
timeout_seconds = 90
allowed_domains = [] # Empty allows any domain.
```

Set `allowed_domains = ["example.com"]` to limit browser navigation to that
domain and its subdomains. Browser control can also be disabled by setting
`enabled = false`, or filtered out by the top-level `allowed_tools` setting.

## Troubleshooting

| Message or symptom | What to do |
| --- | --- |
| `kryth` is not recognized | Reopen the terminal after installation. On Windows, run `python -m kryth setup --path` using the Python used to install KRYTH. |
| Chrome or Edge not found | Install a supported desktop browser and run `kryth browser doctor` again. |
| Remote debugging permission is needed | Run `kryth browser setup`, enable the Chrome setting it opens, approve the browser prompt, then rerun setup. |
| Browser profile is already in use | Close other Chrome/Edge processes normally and retry. Do not delete the browser profile or KUBA runtime files. |
| Helper daemon did not start | Check `%USERPROFILE%\.config\kuba-browser\tmp\bu-default.log` on Windows, or `~/.config/kuba-browser/tmp/bu-default.log` on Linux/macOS. Then run the doctor command again. |
| A site action says an element was not found | The site's page may differ or still be loading. KRYTH should inspect the current page and choose a control from observed page structure; report the failure if it cannot recover. |
| Tool is unavailable in a task | Check `[browser_plugin] enabled` and `allowed_tools` in KRYTH configuration. |

KRYTH uses its checked-in KUBA browser operator directly; it does not require
MCP configuration. On supported Python versions, KRYTH selects its bundled
KUBA engine. A built-in CDP fallback is used when the bundled operator cannot
be loaded.
