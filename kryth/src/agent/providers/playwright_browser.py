"""Browser provider — delegates to browser_use_provider (browser_agent / browser-use).

This module is kept as a compatibility shim so existing imports of
`agent.providers.playwright_browser` continue to work after the migration
from the old Playwright-based implementation to browser_agent.
"""

from agent.providers.browser_use_provider import (  # noqa: F401
    ensure_available,
    open_url,
    get_url,
    get_title,
    get_html,
    state,
    back,
    click,
    type_text,
    fill,
    select,
    keys,
    scroll,
    eval_js,
    extract,
    screenshot,
    tab_list,
    tab_new,
    tab_select,
    download,
    browser_task,
    _get_page,
)
