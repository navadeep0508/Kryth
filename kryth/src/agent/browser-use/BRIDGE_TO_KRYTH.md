# Bridge Guide: Integrating browser_agent with Kryth

This document provides a comprehensive guide for connecting the `browser_agent` (browser-use) to the Kryth AI agent system.

## Table of Contents
1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Architecture](#architecture)
4. [Integration Methods](#integration-methods)
5. [Step-by-Step Setup](#step-by-step-setup)
6. [API Reference](#api-reference)
7. [Examples](#examples)
8. [Troubleshooting](#troubleshooting)

---

## Overview

### What is browser_agent?
`browser_agent` is a powerful Python library that enables AI agents to control web browsers. It provides:
- High-level `Agent` class that can execute tasks autonomously
- Support for multiple LLM providers (OpenAI, Anthropic, NVIDIA, etc.)
- Vision capabilities for understanding page content
- Tool system for browser actions (click, type, navigate, etc.)
- Conversation history and state management

### What is Kryth?
Kryth is an AI agent framework that orchestrates multiple tools and capabilities. This bridge allows Kryth to leverage browser automation as one of its tools.

---

## Prerequisites

1. **Python 3.11+** installed
2. **browser_agent package** installed (`pip install -e .`)
3. **Kryth system** up and running
4. **API keys** for your chosen LLM provider (NVIDIA, OpenAI, etc.)
5. **Playwright browsers** installed: `playwright install chromium`

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│    Kryth Agent  │────▶│  Browser Bridge  │────▶│  browser_agent  │
│   (Orchestrator)│     │   (Adapter)      │     │   (Executor)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
         │                        │                        │
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Task Request   │     │  Transform to    │     │  Browser       │
│  (Natural Lang) │     │  Agent.run()     │     │  Automation    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

### Key Components

1. **Kryth Agent**: Your main AI agent that decides when to use browser automation
2. **Browser Bridge**: Adapter code that translates Kryth's tool calls to browser_agent API
3. **browser_agent**: The underlying library that handles browser control

---

## Integration Methods

### Method 1: Direct Python Integration (Recommended)

Create a Python module that Kryth can import and call directly.

**File: `kryth_browser_bridge.py`**

```python
import asyncio
import os
from typing import Dict, Any, Optional
from browser_agent import Agent, BrowserSession, ChatNVIDIA, ChatOpenAI
from browser_agent.agent.views import AgentSettings

class KrythBrowserBridge:
    """Bridge between Kryth and browser_agent."""
    
    def __init__(
        self,
        llm_provider: str = "nvidia",
        model_name: str = "meta/llama-3.2-90b-vision-instruct",
        api_key: Optional[str] = None,
        headless: bool = True,
        max_steps: int = 20,
    ):
        self.llm_provider = llm_provider
        self.model_name = model_name
        self.api_key = api_key or self._get_api_key()
        self.headless = headless
        self.max_steps = max_steps
        
    def _get_api_key(self) -> str:
        """Get API key from environment based on provider."""
        if self.llm_provider == "nvidia":
            key = os.getenv("NVIDIA_API_KEY")
        elif self.llm_provider == "openai":
            key = os.getenv("OPENAI_API_KEY")
        else:
            raise ValueError(f"Unsupported provider: {self.llm_provider}")
            
        if not key:
            raise ValueError(f"Missing API key for {self.llm_provider}")
        return key
    
    def _create_llm(self):
        """Create LLM instance based on provider."""
        if self.llm_provider == "nvidia":
            return ChatNVIDIA(
                model=self.model_name,
                api_key=self.api_key,
                temperature=0.7,
                max_tokens=2048,
            )
        elif self.llm_provider == "openai":
            return ChatOpenAI(
                model=self.model_name,
                api_key=self.api_key,
                temperature=0.7,
                max_tokens=2048,
            )
        else:
            raise ValueError(f"Unsupported provider: {self.llm_provider}")
    
    async def execute_task(self, task: str, **kwargs) -> Dict[str, Any]:
        """
        Execute a browser automation task.
        
        Args:
            task: Natural language task description
            **kwargs: Additional options (headless, max_steps, etc.)
            
        Returns:
            Dictionary with results, history, and metadata
        """
        # Override defaults with kwargs
        headless = kwargs.get("headless", self.headless)
        max_steps = kwargs.get("max_steps", self.max_steps)
        use_vision = kwargs.get("use_vision", True)
        
        # Create components
        llm = self._create_llm()
        browser = BrowserSession(headless=headless)
        settings = AgentSettings(
            max_steps=max_steps,
            use_vision=use_vision,
        )
        
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
            return {
                "success": False,
                "error": str(e),
                "final_result": None,
                "steps": 0,
            }
        finally:
            await browser.close()
    
    def run_sync(self, task: str, **kwargs) -> Dict[str, Any]:
        """Synchronous wrapper for execute_task."""
        return asyncio.run(self.execute_task(task, **kwargs))


# Example usage as a standalone tool
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("task", help="Task to execute")
    parser.add_argument("--provider", default="nvidia", choices=["nvidia", "openai"])
    parser.add_argument("--model", default="meta/llama-3.2-90b-vision-instruct")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-steps", type=int, default=20)
    
    args = parser.parse_args()
    
    bridge = KrythBrowserBridge(
        llm_provider=args.provider,
        model_name=args.model,
        headless=args.headless,
        max_steps=args.max_steps,
    )
    
    result = bridge.run_sync(args.task)
    print(f"Success: {result['success']}")
    print(f"Result: {result['final_result']}")
```

### Method 2: REST API Bridge

If Kryth communicates via HTTP, create a FastAPI wrapper:

**File: `browser_api_server.py`**

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from kryth_browser_bridge import KrythBrowserBridge

app = FastAPI(title="Browser Agent API for Kryth")

class TaskRequest(BaseModel):
    task: str
    provider: str = "nvidia"
    model: str = "meta/llama-3.2-90b-vision-instruct"
    headless: bool = True
    max_steps: int = 20

class TaskResponse(BaseModel):
    success: bool
    final_result: Optional[str] = None
    steps: int
    error: Optional[str] = None

@app.post("/execute", response_model=TaskResponse)
async def execute_task(request: TaskRequest):
    """Execute a browser automation task."""
    try:
        bridge = KrythBrowserBridge(
            llm_provider=request.provider,
            model_name=request.model,
            headless=request.headless,
            max_steps=request.max_steps,
        )
        result = await bridge.execute_task(request.task)
        return TaskResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Run with: `python browser_api_server.py`

Then Kryth can call: `POST http://localhost:8000/execute` with JSON body.

---

## Step-by-Step Setup

### 1. Install Dependencies

```bash
# Install browser_agent in editable mode
pip install -e .

# Install additional dependencies for bridge
pip install fastapi uvicorn  # For REST API
# or just use direct integration (no extra deps)
```

### 2. Set Environment Variables

```bash
# For NVIDIA
export NVIDIA_API_KEY="your-nvidia-api-key"

# For OpenAI
export OPENAI_API_KEY="your-openai-api-key"
```

### 3. Install Playwright Browsers

```bash
playwright install chromium
```

### 4. Test the Bridge

```python
# test_bridge.py
from kryth_browser_bridge import KrythBrowserBridge

bridge = KrythBrowserBridge(
    llm_provider="nvidia",
    model_name="meta/llama-3.2-90b-vision-instruct",
    headless=True,
)

result = bridge.run_sync("Go to example.com and tell me the title")
print(result)
```

---

## API Reference

### KrythBrowserBridge Class

#### `__init__(llm_provider, model_name, api_key, headless, max_steps)`

Initialize the bridge.

- `llm_provider`: "nvidia" or "openai"
- `model_name`: Model identifier (provider-specific)
- `api_key`: Optional, overrides environment variable
- `headless`: Run browser without UI (default: True)
- `max_steps`: Maximum agent steps (default: 20)

#### `async execute_task(task, **kwargs) -> Dict`

Execute a browser automation task.

- `task`: Natural language description
- `**kwargs`: Override settings (headless, max_steps, use_vision)
- Returns: Dict with keys: success, final_result, steps, history/error

#### `run_sync(task, **kwargs) -> Dict`

Synchronous wrapper for `execute_task`.

---

## Examples

### Example 1: Simple Task

```python
bridge = KrythBrowserBridge(headless=True)
result = bridge.run_sync("Go to https://news.ycombinator.com and get the top 5 headlines")
print(result["final_result"])
```

### Example 2: With Custom Model

```python
bridge = KrythBrowserBridge(
    llm_provider="openai",
    model_name="gpt-4o",
    max_steps=30,
)
result = bridge.run_sync("Fill out the contact form at https://example.com/contact")
```

### Example 3: From Kryth Tool

```python
# In your Kryth agent's tools module
class BrowserTool:
    def __init__(self):
        self.bridge = KrythBrowserBridge()
    
    def __call__(self, task: str) -> str:
        """Tool that Kryth can invoke."""
        result = self.bridge.run_sync(task)
        if result["success"]:
            return result["final_result"] or "Task completed"
        else:
            return f"Error: {result['error']}"
```

### Example 4: REST API Call

```bash
curl -X POST "http://localhost:8000/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "task": "Search for AI news on Google",
    "provider": "nvidia",
    "model": "meta/llama-3.2-90b-vision-instruct",
    "max_steps": 15
  }'
```

---

## Troubleshooting

### Issue: "No module named 'browser_agent'"

**Solution**: Ensure you've installed the package in editable mode:
```bash
pip install -e .
```

### Issue: "NVIDIA_API_KEY not set"

**Solution**: Set the environment variable before running:
```bash
export NVIDIA_API_KEY="your-key"
```

### Issue: "is not a multimodal model"

**Solution**: Use a vision-capable model. For NVIDIA, use:
- `meta/llama-3.2-90b-vision-instruct`
- `microsoft/phi-3-vision-128k-instruct`

Not all models support vision. Check NVIDIA's documentation.

### Issue: Playwright browsers not installed

**Solution**:
```bash
playwright install chromium
```

### Issue: Browser hangs or times out

**Solution**:
- Increase timeout in AgentSettings
- Check network connectivity
- Try non-headless mode for debugging: `headless=False`

---

## Performance Tips

1. **Reuse BrowserSession**: For multiple tasks, keep the same browser session to avoid startup overhead.
2. **Use Text-Only**: If vision isn't needed, set `use_vision=False` for faster/cheaper execution.
3. **Limit Steps**: Set appropriate `max_steps` to prevent infinite loops.
4. **Cache LLM**: Consider caching the LLM instance if running many tasks.

---

## Security Considerations

1. **API Keys**: Never hardcode API keys. Use environment variables or secure vaults.
2. **Sandboxing**: Run browser automation in isolated environments when processing untrusted tasks.
3. **Permissions**: The browser can access local files if given file:// URLs. Restrict what URLs can be visited if needed.
4. **Cost Control**: Monitor LLM usage, especially with expensive models. Set max_steps and token limits.

---

## Next Steps

1. **Customize**: Adapt `KrythBrowserBridge` to your specific needs (add logging, metrics, etc.)
2. **Integrate**: Import the bridge into your Kryth agent's tool system
3. **Test**: Run end-to-end tests with your Kryth workflows
4. **Monitor**: Add telemetry to track success rates and performance

---

## Support

- browser_use docs: https://docs.browser-use.com
- NVIDIA NIM docs: https://docs.nvidia.com/nim
- Kryth documentation: (your internal docs)

For issues with this bridge, check the browser_agent repository: https://github.com/browser-use/browser-use