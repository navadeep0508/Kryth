#!/usr/bin/env python3
"""
Script to run a browser agent using NVIDIA's AI models.
This demonstrates how to use the browser_agent with NVIDIA's chat model.
"""

import asyncio
import os
import sys
from typing import Optional

# Add current directory to path to import browser_agent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from browser_agent import Agent, BrowserSession, ChatNVIDIA
    from browser_agent.agent.views import AgentSettings
except ImportError as e:
    print(f"Error importing browser_agent modules: {e}")
    print("Make sure you have installed the package: pip install -e .")
    sys.exit(1)


async def run_nvidia_agent(
    task: str,
    model_name: str = "stepfun-ai/step-3.7-flash",
    nvidia_api_key: Optional[str] = None,
    headless: bool = False,
    max_steps: int = 10,
    use_vision: bool = True,
) -> None:
    """
    Run a browser agent using NVIDIA's AI model.
    
    Args:
        task: The task description for the agent
        model_name: NVIDIA model name (default: meta/llama-3.2-90b-vision-instruct)
        nvidia_api_key: NVIDIA API key (if None, reads from NVIDIA_API_KEY env var)
        headless: Whether to run browser in headless mode
        max_steps: Maximum number of steps for the agent
        use_vision: Whether to use vision capabilities
    """
    # Get API key from environment if not provided
    if nvidia_api_key is None:
        nvidia_api_key = os.getenv("NVIDIA_API_KEY")
        if not nvidia_api_key:
            print("Error: NVIDIA_API_KEY environment variable not set")
            print("Please set it: export NVIDIA_API_KEY='your-api-key'")
            sys.exit(1)
    
    # Create the NVIDIA chat model
    llm = ChatNVIDIA(
        model=model_name,
        api_key=nvidia_api_key,
        temperature=0.7,
        max_tokens=1024,
    )
    
    # Create browser session
    browser = BrowserSession(headless=headless)
    
    # Create agent settings
    settings = AgentSettings(
        max_steps=max_steps,
        use_vision=use_vision,
        save_conversation_path="conversations/nvidia_agent",
    )
    
    # Create and run the agent
    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        settings=settings,
    )
    
    print(f"Starting NVIDIA agent with model: {model_name}")
    print(f"Task: {task}")
    print("-" * 50)
    
    try:
        history = await agent.run()
        
        print("\n" + "=" * 50)
        print("Agent completed!")
        print(f"Total steps: {len(history)}")
        print(f"Final result: {history.final_result()}")
        print("=" * 50)
        
    except Exception as e:
        print(f"Error running agent: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await browser.close()


def main():
    """Main entry point with argument parsing."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Run a browser agent using NVIDIA's AI models"
    )
    parser.add_argument(
        "task",
        nargs="?",
        default="Go to example.com and tell me what you see on the page",
        help="Task description for the agent"
    )
    parser.add_argument(
        "--model",
        default="stepfun-ai/step-3.7-flash",
        help="NVIDIA model name (default: meta/llama-3.2-90b-vision-instruct)"
    )
    parser.add_argument(
        "--api-key",
        help="NVIDIA API key (default: reads from NVIDIA_API_KEY env var)"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode"
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum number of steps (default: 10)"
    )
    parser.add_argument(
        "--no-vision",
        action="store_true",
        help="Disable vision (use text-only mode)"
    )
    
    args = parser.parse_args()
    
    # Create conversations directory if it doesn't exist
    os.makedirs("conversations", exist_ok=True)
    
    # Run the agent
    asyncio.run(run_nvidia_agent(
        task=args.task,
        model_name=args.model,
        nvidia_api_key=args.api_key,
        headless=args.headless,
        max_steps=args.max_steps,
        use_vision=not args.no_vision,
    ))


if __name__ == "__main__":
    main()