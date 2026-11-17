from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tools.browser_observation import observation_script, semantic_locator_script, summarize_accessibility


def test_observation_script_is_bounded_task_aware_and_omits_field_values():
    compact = observation_script("Python video", "compact")
    detailed = observation_script("Python video", "detailed")

    assert "interactiveElements" in compact
    assert "formFields" in compact
    assert "loadingIndicators" in compact
    assert "visibleText:body.slice(0,2000)" in compact
    assert "visibleText:body.slice(0,8000)" in detailed
    assert "e.value" not in compact
    assert '"Python video"' in compact


def test_semantic_locator_requires_observed_exact_match_criteria():
    script = semantic_locator_script(role="link", name="Python video")

    criteria = json.loads(script.split("const q=", 1)[1].split(";", 1)[0])
    assert criteria == {"role": "link", "name": "python video"}
    assert "Semantic locator is ambiguous" in script
    with pytest.raises(ValueError, match="at least one"):
        semantic_locator_script()


def test_accessibility_summary_omits_sensitive_value_properties():
    result = summarize_accessibility(
        [{
            "role": {"value": "textbox"},
            "name": {"value": "Email"},
            "value": {"value": "private@example.com"},
            "properties": [{"name": "focusable", "value": {"value": True}}],
        }],
        "email",
    )

    assert result == [{"index": 0, "role": "textbox", "name": "Email", "focusable": True}]
    assert "private@example.com" not in json.dumps(result)


@pytest.mark.parametrize("builder", [observation_script, semantic_locator_script])
def test_generated_javascript_parses_when_node_is_available(builder):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; generated browser JavaScript syntax was not checked")
    script = builder("video", "compact") if builder is observation_script else builder(role="link", name="video")
    check = f"new Function({json.dumps(script)});"

    subprocess.run(
        [node, "-e", check],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
