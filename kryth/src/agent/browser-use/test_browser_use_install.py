"""
Test file to verify browser-agent installation and core functionality.
Run with: python test_browser_agent_install.py
Or with pytest: pytest test_browser_agent_install.py -v
"""

import sys
import pytest

# Test 1: Check that the main package can be imported
def test_import_browser_agent():
    """Test that browser_agent package can be imported."""
    try:
        import browser_agent
        assert browser_agent is not None
        print("✓ browser_agent imported successfully")
    except ImportError as e:
        pytest.fail(f"Failed to import browser_agent: {e}")

# Test 2: Check that core classes are available via lazy imports
def test_core_classes_available():
    """Test that core classes are accessible."""
    import browser_agent
    
    # These should trigger lazy imports
    try:
        Agent = browser_agent.Agent
        assert Agent is not None
        print("✓ Agent class available")
    except ImportError as e:
        pytest.fail(f"Failed to import Agent: {e}")
    
    try:
        BrowserSession = browser_agent.BrowserSession
        assert BrowserSession is not None
        print("✓ BrowserSession class available")
    except ImportError as e:
        pytest.fail(f"Failed to import BrowserSession: {e}")
    
    try:
        Controller = browser_agent.Controller
        assert Controller is not None
        print("✓ Controller class available")
    except ImportError as e:
        pytest.fail(f"Failed to import Controller: {e}")

# Test 3: Check that LLM models are available
def test_llm_models_available():
    """Test that LLM model classes are accessible."""
    import browser_agent
    
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
            print(f"✓ {model_name} available")
        except ImportError as e:
            pytest.fail(f"Failed to import {model_name}: {e}")

# Test 4: Check that the package version is defined
def test_package_version():
    """Test that the package has a version."""
    import browser_agent
    from importlib.metadata import version, PackageNotFoundError
    
    try:
        ver = version('browser-agent')
        assert ver is not None
        assert isinstance(ver, str)
        print(f"✓ browser-agent version: {ver}")
    except PackageNotFoundError:
        pytest.fail("browser-agent package not found in installed packages")

# Test 5: Check that essential dependencies are installed
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
            print(f"✓ {module} is installed")
        except ImportError as e:
            pytest.fail(f"Missing required dependency: {module} - {e}")

# Test 6: Check that the BrowserProfile can be instantiated
def test_browser_profile_creation():
    """Test that BrowserProfile can be created."""
    from browser_use import BrowserProfile
    
    try:
        profile = BrowserProfile()
        assert profile is not None
        print("✓ BrowserProfile instantiation successful")
    except Exception as e:
        pytest.fail(f"Failed to create BrowserProfile: {e}")

# Test 7: Check that Controller can be instantiated
def test_controller_creation():
    """Test that Controller can be created."""
    from browser_use import Controller
    
    try:
        controller = Controller()
        assert controller is not None
        print("✓ Controller instantiation successful")
    except Exception as e:
        pytest.fail(f"Failed to create Controller: {e}")

if __name__ == "__main__":
    # Run all tests manually
    print("Running browser-use installation tests...\n")
    
    tests = [
        test_import_browser_use,
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
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} ERROR: {e}")
            failed += 1
        print()
    
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*50}")
    
    if failed > 0:
        sys.exit(1)
    else:
        print("\n✓ All tests passed! browser-use is correctly installed.")
        sys.exit(0)