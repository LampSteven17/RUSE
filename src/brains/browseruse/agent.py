"""
BrowserUse Brain - AI-powered browser automation agent.

Supports prompt configuration for content/behavior control.
"""
import json
import os
import asyncio
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger


def log(msg: str):
    """Print with timestamp."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")

from browser_use import Agent, ChatOllama
from browser_use.browser.session import BrowserSession

from common.config.model_config import get_model, get_ollama_seed
from brains.browseruse.prompts import BUPrompts, DEFAULT_PROMPTS

# LLM timeout in seconds - 5 minutes for CPU models
LLM_TIMEOUT = 300

# Mapping from browser_use action types to unified step names.
# browser_use LLM responses contain JSON with an "action" list where each
# element is a dict like {"go_to_url": {"url": "..."}} or {"click_element": {"index": 5}}.
_BU_ACTION_MAP = {
    "go_to_url": ("navigate", "browser"),
    "open_tab": ("navigate", "browser"),
    "switch_tab": ("navigate", "browser"),
    "go_back": ("navigate", "browser"),
    "click_element": ("click", "browser"),
    "input_text": ("type_text", "browser"),
    "send_keys": ("type_text", "browser"),
    "scroll_down": ("scroll", "browser"),
    "scroll_up": ("scroll", "browser"),
    "scroll_to_text": ("scroll", "browser"),
    "search_google": ("search", "browser"),
    "extract_content": ("scroll", "browser"),
    "extract_page_content": ("scroll", "browser"),
    "save_file": ("save_document", "browser"),
    "upload_file": ("download_file", "browser"),
    "wait": ("wait", "browser"),
    # "done" is intentionally omitted — it signals workflow completion, not a step
}


def _parse_bu_action(response_content: str):
    """Parse browser_use LLM response to detect the action type.

    Returns (step_name, category, message) or None if no action detected.
    """
    if not response_content:
        return None
    try:
        data = json.loads(response_content)
        actions = data.get("action", [])
        if not actions or not isinstance(actions, list):
            return None
        action = actions[0]
        if not isinstance(action, dict):
            return None
        action_type = next(iter(action))
        mapped = _BU_ACTION_MAP.get(action_type)
        if mapped is None:
            return None
        step_name, category = mapped
        # Build a short message from the action params
        params = action[action_type]
        if isinstance(params, dict):
            msg = params.get("url") or params.get("text") or params.get("query") or str(params)[:100]
        else:
            msg = str(params)[:100]
        return step_name, category, msg
    except (json.JSONDecodeError, KeyError, IndexError, StopIteration):
        return None


def create_logged_chat_ollama(model: str, logger: Optional["AgentLogger"] = None, timeout: int = LLM_TIMEOUT):
    """
    Create a browser_use.ChatOllama instance with logging wrapped around ainvoke.

    browser_use uses its own ChatOllama class (not LangChain) which directly calls
    ollama.AsyncClient. We wrap the Ollama client's chat method to capture token
    counts before browser_use discards them.

    Each LLM response is also parsed for browser_use action types (go_to_url,
    click_element, input_text, etc.) and logged as mechanical steps using the
    unified step vocabulary (navigate, click, type_text, scroll, etc.).

    Args:
        model: Ollama model name
        logger: Optional AgentLogger for logging LLM calls
        timeout: LLM request timeout in seconds (default 300s for CPU models)
    """
    llm = ChatOllama(model=model, timeout=timeout)

    if logger is None:
        # Even without logging, inject seed if configured
        ollama_seed = get_ollama_seed()
        if ollama_seed is not None:
            original_get_client_nolog = llm.get_client
            def get_seeded_client():
                client = original_get_client_nolog()
                original_chat_nolog = client.chat
                async def seeded_chat(*args, **kwargs):
                    kwargs.setdefault('options', {})
                    kwargs['options']['seed'] = ollama_seed
                    kwargs['options']['temperature'] = 0
                    return await original_chat_nolog(*args, **kwargs)
                client.chat = seeded_chat
                return client
            llm.get_client = get_seeded_client
        return llm

    # Store the original get_client method
    original_get_client = llm.get_client
    ollama_seed = get_ollama_seed()

    def get_logged_client():
        """Return an Ollama client with logging wrapped around chat()."""
        client = original_get_client()
        original_chat = client.chat

        async def logged_chat(*args, **kwargs):
            """Wrapper that logs requests/responses with token counts."""
            # Inject Ollama seed for deterministic generation
            if ollama_seed is not None:
                kwargs.setdefault('options', {})
                kwargs['options']['seed'] = ollama_seed
                kwargs['options']['temperature'] = 0

            # Log the request
            messages = kwargs.get('messages', args[1] if len(args) > 1 else [])
            action = "generate"
            if messages and len(messages) > 0:
                last_msg = messages[-1]
                content = last_msg.get('content', '') if isinstance(last_msg, dict) else str(last_msg)
                action = content[:100] if len(content) > 100 else content

            logger.llm_request(
                action=action,
                model=model,
                input_data={"message_count": len(messages), "model": model}
            )

            # Call the original method
            start_time = time.time()
            try:
                response = await original_chat(*args, **kwargs)
                duration_ms = int((time.time() - start_time) * 1000)

                # Extract output and token counts from Ollama response
                output = ""
                if hasattr(response, 'message') and hasattr(response.message, 'content'):
                    output = str(response.message.content)[:500] if response.message.content else ""

                # Ollama provides token counts in the response
                tokens = None
                input_tokens = getattr(response, 'prompt_eval_count', None)
                output_tokens = getattr(response, 'eval_count', None)
                if input_tokens is not None or output_tokens is not None:
                    tokens = {
                        "input": input_tokens,
                        "output": output_tokens,
                        "total": (input_tokens or 0) + (output_tokens or 0)
                    }

                logger.llm_response(
                    output=output,
                    duration_ms=duration_ms,
                    model=model,
                    tokens=tokens
                )

                # Parse the LLM response for browser actions and log as steps
                if output:
                    parsed = _parse_bu_action(output)
                    if parsed:
                        step_name, category, msg = parsed
                        logger.step_start(step_name, category=category, message=msg)
                        logger.step_success(step_name)

                return response
            except Exception as e:
                logger.llm_error(error=str(e), action=f"llm_call_{model}", fatal=True)
                raise

        client.chat = logged_chat
        return client

    # Replace get_client with our logged version
    llm.get_client = get_logged_client

    return llm


class BrowserUseAgent:
    """
    BrowserUse agent with three-prompt support.

    Configurations:
    - B1.llama: DEFAULT_PROMPTS + llama3.1:8b
    - B2.gemma: DEFAULT_PROMPTS + gemma3:4b
    - B3.deepseek: DEFAULT_PROMPTS + deepseek-r1:8b
    - B?.model+: PHASE_PROMPTS + any model (POST-PHASE)
    """

    def __init__(
        self,
        prompts: BUPrompts = DEFAULT_PROMPTS,
        model: str = None,
        headless: bool = True,
        max_steps: int = 10,
        logger: Optional["AgentLogger"] = None,
    ):
        self.prompts = prompts
        self.model_name = get_model(model)
        self.headless = headless
        self.max_steps = max_steps
        self.logger = logger
        self._llm = None
        self._browser_session = None

    def _get_llm(self):
        """Lazy-load the LLM with logging wrapper."""
        if self._llm is None:
            # Use browser_use's native ChatOllama with our logging wrapper
            # This properly integrates with browser_use's custom LLM architecture
            self._llm = create_logged_chat_ollama(self.model_name, self.logger)
        return self._llm

    def _get_browser_session(self):
        """Create browser session with container-safe configuration."""
        return BrowserSession(
            headless=self.headless,
            channel="chromium",
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-extensions',
                '--disable-gpu',
            ]
        )

    async def run_async(self, task: str) -> Optional[str]:
        """
        Run a task asynchronously with configured prompts.

        Args:
            task: The specific task to perform (e.g., "Search for Python tutorials")

        Returns:
            Result from the agent, or None on error
        """
        # Build full prompt from task + content guidelines
        full_prompt = BUPrompts(
            task=task,
            content=self.prompts.content,
        ).build_full_prompt()

        log(f"Starting BrowserUse agent with model: {self.model_name}")
        log(f"Task: {task}")

        # Use the task itself as workflow name for better queryability
        workflow_name = task[:100] if len(task) > 100 else task

        if self.logger:
            self.logger.workflow_start(workflow_name, params={
                "agent_type": "browseruse",
                "model": self.model_name
            })

        try:
            browser_session = self._get_browser_session()
            agent = Agent(
                task=full_prompt,
                llm=self._get_llm(),
                browser_session=browser_session,
            )
            result = await agent.run(max_steps=self.max_steps)
            log("Task completed successfully!")

            if self.logger:
                self.logger.workflow_end(workflow_name, success=True,
                                        result=str(result)[:500] if result else None)
            return result
        except Exception as e:
            log(f"Error running agent: {e}")
            import traceback
            traceback.print_exc()

            if self.logger:
                self.logger.workflow_end(workflow_name, success=False, error=str(e))
            return None

    def run(self, task: str) -> Optional[str]:
        """
        Run a task synchronously.

        Args:
            task: The specific task to perform

        Returns:
            Result from the agent, or None on error
        """
        return asyncio.run(self.run_async(task))


def run(task: str, model: str = None, prompts: BUPrompts = DEFAULT_PROMPTS,
        headless: bool = True, max_steps: int = 10) -> Optional[str]:
    """Convenience function to run BrowserUse agent."""
    agent = BrowserUseAgent(
        prompts=prompts,
        model=model,
        headless=headless,
        max_steps=max_steps,
    )
    return agent.run(task)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='BrowserUse Agent')
    parser.add_argument('task', nargs='?', default="Visit google.com and search for 'OpenAI news'")
    parser.add_argument('--model', type=str, default=None, help='Model key: llama, gemma, deepseek')
    parser.add_argument('--phase', action='store_true', help='Use PHASE-improved prompts')
    parser.add_argument('--max-steps', type=int, default=10)
    parser.add_argument('--headless', action='store_true', default=True)
    args = parser.parse_args()

    from brains.browseruse.prompts import PHASE_PROMPTS

    prompts = PHASE_PROMPTS if args.phase else DEFAULT_PROMPTS

    run(
        task=args.task,
        model=args.model,
        prompts=prompts,
        headless=args.headless,
        max_steps=args.max_steps,
    )
