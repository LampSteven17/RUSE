"""
LLM Callback Handlers for DOLOS-DEPLOY

Provides callback handlers for LiteLLM and LangChain to log LLM interactions
to the AgentLogger framework.

Usage:
    # For LiteLLM (SmolAgents):
    from common.logging.llm_callbacks import setup_litellm_callbacks
    setup_litellm_callbacks(logger)

    # For LangChain (BrowserUse):
    from common.logging.llm_callbacks import LangChainLoggingHandler
    llm = ChatOllama(model=model, callbacks=[LangChainLoggingHandler(logger)])
"""

import time
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from common.logging.agent_logger import AgentLogger


# =============================================================================
# LiteLLM Callbacks (for SmolAgents)
# =============================================================================

class LiteLLMLoggingCallback:
    """
    Custom callback handler for LiteLLM.

    LiteLLM supports custom callback classes with these methods:
    - log_success_event(kwargs, response_obj, start_time, end_time)
    - log_failure_event(kwargs, response_obj, start_time, end_time)
    """

    def __init__(self, logger: "AgentLogger"):
        self.logger = logger
        self._start_times: Dict[str, float] = {}

    def log_pre_api_call(self, model: str, messages: List[Dict], kwargs: Dict):
        """Called before the API call is made."""
        call_id = kwargs.get("litellm_call_id", str(id(messages)))
        self._start_times[call_id] = time.time()

        # Extract action from the last message if possible
        action = "generate"
        if messages and len(messages) > 0:
            last_msg = messages[-1].get("content", "")
            if isinstance(last_msg, str):
                action = last_msg[:100] if len(last_msg) > 100 else last_msg

        self.logger.llm_request(
            action=action,
            model=model,
            input_data={
                "message_count": len(messages),
                "model": model,
            }
        )

    def log_success_event(self, kwargs: Dict, response_obj: Any, start_time: float, end_time: float):
        """Called on successful LLM response."""
        duration_ms = int((end_time - start_time) * 1000)

        # Extract response content
        output = ""
        tokens = {}
        model = kwargs.get("model", "unknown")

        if hasattr(response_obj, "choices") and response_obj.choices:
            choice = response_obj.choices[0]
            if hasattr(choice, "message") and hasattr(choice.message, "content"):
                output = choice.message.content or ""

        # Extract token usage if available
        if hasattr(response_obj, "usage") and response_obj.usage:
            usage = response_obj.usage
            tokens = {
                "input": getattr(usage, "prompt_tokens", None),
                "output": getattr(usage, "completion_tokens", None),
                "total": getattr(usage, "total_tokens", None),
            }

        self.logger.llm_response(
            output=output[:500] if output else "",
            duration_ms=duration_ms,
            model=model,
            tokens=tokens if any(tokens.values()) else None
        )

    def log_failure_event(self, kwargs: Dict, response_obj: Any, start_time: float, end_time: float):
        """Called on LLM error."""
        error_msg = str(response_obj) if response_obj else "Unknown error"
        model = kwargs.get("model", "unknown")

        # Determine if this is a fatal error (affects experiment validity)
        fatal = True  # Most LLM errors are fatal for experiment validity

        self.logger.llm_error(
            error=error_msg,
            action=f"llm_call_{model}",
            fatal=fatal
        )


def setup_litellm_callbacks(logger: "AgentLogger") -> LiteLLMLoggingCallback:
    """
    Set up LiteLLM callbacks for logging.

    Args:
        logger: AgentLogger instance to log to

    Returns:
        The callback handler instance
    """
    try:
        import litellm

        callback = LiteLLMLoggingCallback(logger)

        # Register the callback with LiteLLM
        # LiteLLM uses a callbacks list that can contain custom objects
        if not hasattr(litellm, "callbacks") or litellm.callbacks is None:
            litellm.callbacks = []

        litellm.callbacks.append(callback)

        # Also set success/failure callbacks for older litellm versions
        if hasattr(litellm, "success_callback"):
            if litellm.success_callback is None:
                litellm.success_callback = []
            litellm.success_callback.append(callback.log_success_event)

        if hasattr(litellm, "failure_callback"):
            if litellm.failure_callback is None:
                litellm.failure_callback = []
            litellm.failure_callback.append(callback.log_failure_event)

        logger.info("LiteLLM callbacks registered", details={"callback_type": "LiteLLMLoggingCallback"})
        return callback

    except ImportError:
        logger.warning("LiteLLM not available, callbacks not registered")
        return None


# =============================================================================
# LangChain Callbacks (for BrowserUse)
# =============================================================================

class LangChainLoggingHandler:
    """
    LangChain callback handler for logging LLM interactions.

    Compatible with LangChain's BaseCallbackHandler interface.
    Implements the key methods: on_llm_start, on_llm_end, on_llm_error
    """

    def __init__(self, logger: "AgentLogger"):
        self.logger = logger
        self._start_times: Dict[str, float] = {}
        self._models: Dict[str, str] = {}

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM starts processing."""
        run_id_str = str(run_id)
        self._start_times[run_id_str] = time.time()

        # Extract model name from serialized data or kwargs
        model = "unknown"
        if serialized and "kwargs" in serialized:
            model = serialized["kwargs"].get("model", model)
        if "invocation_params" in kwargs:
            model = kwargs["invocation_params"].get("model", model)

        self._models[run_id_str] = model

        # Get action from first prompt
        action = "generate"
        if prompts and len(prompts) > 0:
            action = prompts[0][:100] if len(prompts[0]) > 100 else prompts[0]

        self.logger.llm_request(
            action=action,
            model=model,
            input_data={
                "prompt_count": len(prompts),
                "model": model,
            }
        )

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM finishes processing."""
        run_id_str = str(run_id)
        start_time = self._start_times.pop(run_id_str, time.time())
        model = self._models.pop(run_id_str, "unknown")
        duration_ms = int((time.time() - start_time) * 1000)

        # Extract output from response
        output = ""
        tokens = {}

        if hasattr(response, "generations") and response.generations:
            # LangChain LLMResult format
            for gen_list in response.generations:
                for gen in gen_list:
                    if hasattr(gen, "text"):
                        output += gen.text

        # Extract token usage if available
        if hasattr(response, "llm_output") and response.llm_output:
            llm_output = response.llm_output
            if "token_usage" in llm_output:
                usage = llm_output["token_usage"]
                tokens = {
                    "input": usage.get("prompt_tokens"),
                    "output": usage.get("completion_tokens"),
                    "total": usage.get("total_tokens"),
                }

        self.logger.llm_response(
            output=output[:500] if output else "",
            duration_ms=duration_ms,
            model=model,
            tokens=tokens if any(v is not None for v in tokens.values()) else None
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Called when LLM encounters an error."""
        run_id_str = str(run_id)
        model = self._models.pop(run_id_str, "unknown")
        self._start_times.pop(run_id_str, None)

        self.logger.llm_error(
            error=str(error),
            action=f"llm_call_{model}",
            fatal=True
        )

    # Required no-op methods for LangChain compatibility
    def on_chain_start(self, *args, **kwargs) -> None:
        pass

    def on_chain_end(self, *args, **kwargs) -> None:
        pass

    def on_chain_error(self, *args, **kwargs) -> None:
        pass

    def on_tool_start(self, *args, **kwargs) -> None:
        pass

    def on_tool_end(self, *args, **kwargs) -> None:
        pass

    def on_tool_error(self, *args, **kwargs) -> None:
        pass

    def on_text(self, *args, **kwargs) -> None:
        pass

    def on_agent_action(self, *args, **kwargs) -> None:
        pass

    def on_agent_finish(self, *args, **kwargs) -> None:
        pass


def create_langchain_callback(logger: "AgentLogger") -> LangChainLoggingHandler:
    """
    Create a LangChain callback handler for logging.

    Args:
        logger: AgentLogger instance to log to

    Returns:
        LangChainLoggingHandler instance to pass to LangChain components
    """
    handler = LangChainLoggingHandler(logger)
    logger.info("LangChain callback handler created", details={"callback_type": "LangChainLoggingHandler"})
    return handler
