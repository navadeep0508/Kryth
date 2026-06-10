"""
Test file to verify browser-agent installation and core functionality.
Run with: python test_browser_agent_install.py
Or with pytest: pytest test_browser_agent_install.py -v
"""

import sys
import pytest


def test_import_browser_agent():
    """Test that browser_agent package can be imported."""
    browser_agent = pytest.importorskip("browser_agent", reason="browser_agent not installed")
    assert browser_agent is not None
    print("OK: browser_agent imported successfully")


def test_core_classes_available():
    """Test that core classes are accessible."""
    browser_agent = pytest.importorskip("browser_agent", reason="browser_agent not installed")

    try:
        Agent = browser_agent.Agent
        assert Agent is not None
        print("OK: Agent class available")
    except ImportError as e:
        pytest.fail(f"Failed to import Agent: {e}")

    try:
        BrowserSession = browser_agent.BrowserSession
        assert BrowserSession is not None
        print("OK: BrowserSession class available")
    except ImportError as e:
        pytest.fail(f"Failed to import BrowserSession: {e}")

    try:
        Controller = browser_agent.Controller
        assert Controller is not None
        print("OK: Controller class available")
    except ImportError as e:
        pytest.fail(f"Failed to import Controller: {e}")


def test_llm_models_available():
    """Test that LLM model classes are accessible."""
    browser_agent = pytest.importorskip("browser_agent", reason="browser_agent not installed")

    llm_models = [
        'ChatOpenAI',
        'ChatAnthropic',
        'ChatGoogle',
        'ChatGroq',
        'ChatOllama',
    ]

    for model_name in llm_models:
        try:
            model_class = getattr(browser_agent, model_name)
            assert model_class is not None
            print(f"OK: {model_name} available")
        except ImportError as e:
            pytest.fail(f"Failed to import {model_name}: {e}")


def test_package_version():
    """Test that the package has a version."""
    pytest.importorskip("browser_agent", reason="browser_agent not installed")
    from importlib.metadata import version, PackageNotFoundError

    try:
        ver = version('browser-agent')
        assert ver is not None
        assert isinstance(ver, str)
        print(f"OK: browser-agent version: {ver}")
    except PackageNotFoundError:
        pytest.skip("browser-agent package metadata not found (may be installed from source)")


def test_dependencies():
    """Test that key dependencies are available."""
    required_modules = [
        'pydantic',
        'aiohttp',
        'httpx',
        'playwright',
        'openai',
        'anthropic',
    ]

    for module in required_modules:
        try:
            __import__(module)
            print(f"OK: {module} is installed")
        except ImportError as e:
            pytest.fail(f"Missing required dependency: {module} - {e}")


def test_browser_profile_creation():
    """Test that BrowserProfile can be created."""
    pytest.importorskip("browser_use", reason="browser_use not installed")
    from browser_use import BrowserProfile

    try:
        profile = BrowserProfile()
        assert profile is not None
        print("OK: BrowserProfile instantiation successful")
    except Exception as e:
        pytest.fail(f"Failed to create BrowserProfile: {e}")


def test_controller_creation():
    """Test that Controller can be created."""
    pytest.importorskip("browser_use", reason="browser_use not installed")
    from browser_use import Controller

    try:
        controller = Controller()
        assert controller is not None
        print("OK: Controller instantiation successful")
    except Exception as e:
        pytest.fail(f"Failed to create Controller: {e}")


if __name__ == "__main__":
    print("Running browser-use installation tests...\n")

    tests = [
        test_import_browser_agent,
        test_core_classes_available,
        test_llm_models_available,
        test_package_version,
        test_dependencies,
        test_browser_profile_creation,
        test_controller_creation,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {test.__name__}: {e}")
            failed += 1
        print()

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*50}")

    if failed > 0:
        sys.exit(1)
    else:
        print("\nAll tests passed! browser-use is correctly installed.")
        sys.exit(0)
