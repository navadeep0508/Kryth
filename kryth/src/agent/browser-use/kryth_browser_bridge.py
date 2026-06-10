"""
Kryth Browser Bridge - Integration layer between Kryth and browser_agent.

This module provides a clean interface for Kryth to execute browser automation
tasks using the browser_agent library with support for multiple LLM providers.
"""

import asyncio
import os
from typing import Dict, Any, Optional

from browser_agent import Agent, BrowserSession
from browser_agent.agent.views import AgentSettings

# Import LLM classes conditionally to avoid heavy imports if not used
def _import_llm(provider: str):
    """Lazy import of LLM classes."""
    if provider == "nvidia":
        from browser_agent.llm.nvidia.chat import ChatNVIDIA
        return ChatNVIDIA
    elif provider == "openai":
        from browser_agent.llm.openai.chat import ChatOpenAI
        return ChatOpenAI
    elif provider == "anthropic":
        from browser_agent.llm.anthropic.chat import ChatAnthropic
        return ChatAnthropic
    elif provider == "google":
        from browser_agent.llm.google.chat import ChatGoogle
        return ChatGoogle
    elif provider == "ollama":
        from browser_agent.llm.ollama.chat import ChatOllama
        return ChatOllama
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


class KrythBrowserBridge:
    """
    Bridge between Kryth and browser_agent.
    
    This class wraps browser_agent functionality in a simple interface
    that Kryth can call as a tool.
    """
    
    def __init__(
        self,
        llm_provider: str = "nvidia",
        model_name: str = "stepfun-ai/step-3.7-flash",
        api_key: Optional[str] = None,
        headless: bool = True,
        max_steps: int = 20,
        use_vision: bool = True,
    ):
        """
        Initialize the browser bridge.
        
        Args:
            llm_provider: LLM provider name (nvidia, openai, anthropic, google, ollama)
            model_name: Model identifier (provider-specific)
            api_key: API key (if None, reads from environment)
            headless: Run browser without UI
            max_steps: Maximum steps for agent
            use_vision: Enable vision capabilities
        """
        self.llm_provider = llm_provider
        self.model_name = model_name
        self.api_key = api_key or self._get_api_key()
        self.headless = headless
        self.max_steps = max_steps
        self.use_vision = use_vision
        
    def _get_api_key(self) -> str:
        """Get API key from environment based on provider."""
        env_vars = {
            "nvidia": "NVIDIA_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "ollama": None,  # Ollama runs locally, no API key needed
        }
        
        env_var = env_vars.get(self.llm_provider)
        if not env_var:
            return None  # For local models like Ollama
            
        key = os.getenv(env_var)
        if not key and self.llm_provider != "ollama":
            raise ValueError(
                f"Missing API key for {self.llm_provider}. "
                f"Set {env_var} environment variable."
            )
        return key
    
    def _create_llm(self):
        """Create LLM instance, mirroring run_nvidia_agent.py exactly.

        Uses ChatNVIDIA for NVIDIA keys — the same class the working script
        uses — which sends correct NIM params (max_tokens, not max_completion_tokens)
        and supports response_format json_schema for llama/vision models.
        Auto-routes if the key starts with 'nvapi-'.
        """
        provider = self.llm_provider
        api_key = self.api_key or ""

        if provider == "nvidia" or api_key.startswith("nvapi-"):
            from browser_agent.llm.nvidia.chat import ChatNVIDIA
            nvidia_key = api_key or os.getenv("NVIDIA_API_KEY", "")
            return ChatNVIDIA(
                model=self.model_name,
                api_key=nvidia_key,
                temperature=0.7,
                max_tokens=4096,
            )

        if provider == "openai":
            from browser_agent.llm.openai.chat import ChatOpenAI
            return ChatOpenAI(model=self.model_name, api_key=api_key or None, temperature=0.2)

        if provider == "anthropic":
            from browser_agent.llm.anthropic.chat import ChatAnthropic
            return ChatAnthropic(model=self.model_name, api_key=api_key or None, max_tokens=8192)

        if provider == "google":
            from browser_agent.llm.google.chat import ChatGoogle
            return ChatGoogle(model=self.model_name, api_key=api_key or None, max_output_tokens=8096)

        if provider == "ollama":
            from browser_agent.llm.ollama.chat import ChatOllama
            return ChatOllama(model=self.model_name)

        from browser_agent.llm.openai.chat import ChatOpenAI
        return ChatOpenAI(model=self.model_name, api_key=api_key or None)
    
    async def execute_task(
        self,
        task: str,
        headless: Optional[bool] = None,
        max_steps: Optional[int] = None,
        use_vision: Optional[bool] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute a browser automation task.
        
        Args:
            task: Natural language task description
            headless: Override default headless setting
            max_steps: Override default max steps
            use_vision: Override default vision setting
            **kwargs: Additional AgentSettings options
            
        Returns:
            Dictionary with results, history, and metadata
        """
        # Use instance defaults if not overridden
        headless = headless if headless is not None else self.headless
        max_steps = max_steps if max_steps is not None else self.max_steps
        use_vision = use_vision if use_vision is not None else self.use_vision
        
        # Create components
        try:
            llm = self._create_llm()
        except ImportError as e:
            return {
                "success": False,
                "error": f"Failed to import LLM class: {e}",
                "final_result": None,
                "steps": 0,
            }
        
        browser = BrowserSession(headless=headless)
        
        # Build settings
        settings_kwargs = {
            "max_steps": max_steps,
            "use_vision": use_vision,
        }
        settings_kwargs.update(kwargs)
        settings = AgentSettings(**settings_kwargs)
        
        # Create and run agent
        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            settings=settings,
        )
        
        try:
            history = await agent.run()
            
            return {
                "success": True,
                "final_result": history.final_result(),
                "steps": len(history),
                "history": history,
            }
        except Exception as e:
            import traceback
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "final_result": None,
                "steps": 0,
            }
        finally:
            await browser.close()
    
    def run_sync(self, task: str, **kwargs) -> Dict[str, Any]:
        """
        Synchronous wrapper for execute_task.
        
        Args:
            task: Natural language task description
            **kwargs: Same as execute_task
            
        Returns:
            Dictionary with results
        """
        return asyncio.run(self.execute_task(task, **kwargs))


# Convenience function for quick usage
def run_browser_task(
    task: str,
    llm_provider: str = "nvidia",
    model_name: str = "meta/llama-3.2-90b-vision-instruct",
    **kwargs
) -> Dict[str, Any]:
    """
    Quick function to run a browser task without creating a bridge instance.
    
    Args:
        task: The task to execute
        llm_provider: LLM provider
        model_name: Model name
        **kwargs: Additional options for KrythBrowserBridge
        
    Returns:
        Result dictionary
    """
    bridge = KrythBrowserBridge(
        llm_provider=llm_provider,
        model_name=model_name,
        **kwargs
    )
    return bridge.run_sync(task)


# Example usage
if __name__ == "__main__":
    # Simple example
    result = run_browser_task(
        "Go to example.com and tell me what you see",
        headless=True,
        max_steps=10,
    )
    
    print("Success:", result["success"])
    print("Result:", result.get("final_result", "No result"))
    if not result["success"]:
        print("Error:", result.get("error"))