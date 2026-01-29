"""
LLM Callback Handlers for DOLOS-DEPLOY

Provides callback handlers for LiteLLM and LangChain to log LLM interactions
to the AgentLogger framework.

Usage:
    # For LiteLLM (SmolAgents):
    from common.logging.llm_callbacks import setup_litellm_callbacks
    setup_litellm_callbacks(logger)  # Registers globally with litellm module

    # For LangChain (BrowserUse):
    from common.logging.llm_callbacks import create_langchain_callback
    handler = create_langchain_callback(logger)
    callbacks = [handler] if handler else None

    # IMPORTANT: Use langchain_ollama.ChatOllama, NOT browser_use.ChatOllama
    # browser_use's ChatOllama wrapper doesn't support callbacks
    from langchain_ollama import ChatOllama
    llm = ChatOllama(model=model, callbacks=callbacks)
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
    Custom callback handler for LiteLLM using the CustomLogger interface.

    LiteLLM's CustomLogger supports these methods:
    - log_pre_api_call(model, messages, kwargs)
    - log_success_event(kwargs, response_obj, start_time, end_time)
    - log_failure_event(kwargs, response_obj, start_time, end_time)

    Also implements async variants for completeness.
    """

    def __init__(self, logger: "AgentLogger"):
        self.logger = logger
        self._start_times: Dict[str, float] = {}
        self._request_data: Dict[str, Dict] = {}

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

        # Store request data for pairing with response
        self._request_data[call_id] = {
            "action": action,
            "model": model,
            "message_count": len(messages),
        }

        self.logger.llm_request(
            action=action,
            model=model,
            input_data={
                "message_count": len(messages),
                "model": model,
            }
        )

    async def async_log_pre_api_call(self, model: str, messages: List[Dict], kwargs: Dict):
        """Async version of log_pre_api_call."""
        self.log_pre_api_call(model, messages, kwargs)

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

        # Extract token usage - try multiple formats
        tokens = self._extract_tokens(response_obj, kwargs)

        self.logger.llm_response(
            output=output[:500] if output else "",
            duration_ms=duration_ms,
            model=model,
            tokens=tokens if any(v is not None for v in tokens.values()) else None
        )

    def _extract_tokens(self, response_obj: Any, kwargs: Dict) -> Dict[str, Optional[int]]:
        """Extract token usage from response, handling multiple formats."""
        tokens = {"input": None, "output": None, "total": None}

        # Try 1: Standard OpenAI format (response_obj.usage)
        if hasattr(response_obj, "usage") and response_obj.usage:
            usage = response_obj.usage
            tokens["input"] = getattr(usage, "prompt_tokens", None)
            tokens["output"] = getattr(usage, "completion_tokens", None)
            tokens["total"] = getattr(usage, "total_tokens", None)
            if any(v is not None for v in tokens.values()):
                return tokens

        # Try 2: Ollama format in response object (eval_count, prompt_eval_count)
        if hasattr(response_obj, "eval_count"):
            tokens["output"] = getattr(response_obj, "eval_count", None)
        if hasattr(response_obj, "prompt_eval_count"):
            tokens["input"] = getattr(response_obj, "prompt_eval_count", None)
        if tokens["input"] is not None and tokens["output"] is not None:
            tokens["total"] = tokens["input"] + tokens["output"]
            return tokens

        # Try 3: Ollama format in _raw_response (LiteLLM sometimes stores it here)
        raw = getattr(response_obj, "_raw_response", None) or kwargs.get("original_response", {})
        if isinstance(raw, dict):
            if "eval_count" in raw:
                tokens["output"] = raw.get("eval_count")
            if "prompt_eval_count" in raw:
                tokens["input"] = raw.get("prompt_eval_count")
            if tokens["input"] is not None and tokens["output"] is not None:
                tokens["total"] = tokens["input"] + tokens["output"]
                return tokens

        # Try 4: Check kwargs for additional_kwargs or response metadata
        additional = kwargs.get("additional_kwargs", {})
        if "eval_count" in additional:
            tokens["output"] = additional.get("eval_count")
            tokens["input"] = additional.get("prompt_eval_count")
            if tokens["input"] is not None and tokens["output"] is not None:
                tokens["total"] = tokens["input"] + tokens["output"]

        return tokens

    async def async_log_success_event(self, kwargs: Dict, response_obj: Any, start_time: float, end_time: float):
        """Async version of log_success_event."""
        self.log_success_event(kwargs, response_obj, start_time, end_time)

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

    async def async_log_failure_event(self, kwargs: Dict, response_obj: Any, start_time: float, end_time: float):
        """Async version of log_failure_event."""
        self.log_failure_event(kwargs, response_obj, start_time, end_time)


def setup_litellm_callbacks(logger: "AgentLogger") -> Optional[LiteLLMLoggingCallback]:
    """
    Set up LiteLLM callbacks for logging.

    Uses LiteLLM's CustomLogger interface which supports:
    - log_pre_api_call / async_log_pre_api_call
    - log_success_event / async_log_success_event
    - log_failure_event / async_log_failure_event

    Args:
        logger: AgentLogger instance to log to

    Returns:
        The callback handler instance, or None if LiteLLM unavailable
    """
    try:
        import litellm

        callback = LiteLLMLoggingCallback(logger)

        # Register the callback with LiteLLM's callbacks list
        # This is the modern way - LiteLLM inspects objects for log_* methods
        if not hasattr(litellm, "callbacks") or litellm.callbacks is None:
            litellm.callbacks = []

        # Avoid duplicate registration
        existing_types = [type(c).__name__ for c in litellm.callbacks]
        if "LiteLLMLoggingCallback" not in existing_types:
            litellm.callbacks.append(callback)

        # Also register with success/failure callback lists for broader compatibility
        if hasattr(litellm, "success_callback"):
            if litellm.success_callback is None:
                litellm.success_callback = []
            if callback.log_success_event not in litellm.success_callback:
                litellm.success_callback.append(callback.log_success_event)

        if hasattr(litellm, "failure_callback"):
            if litellm.failure_callback is None:
                litellm.failure_callback = []
            if callback.log_failure_event not in litellm.failure_callback:
                litellm.failure_callback.append(callback.log_failure_event)

        # Set callbacks at module level to ensure they're used
        litellm.set_verbose = True  # Enables callback invocation

        logger.info("LiteLLM callbacks registered", details={
            "callback_type": "LiteLLMLoggingCallback",
            "litellm_version": getattr(litellm, "__version__", "unknown")
        })
        return callback

    except ImportError:
        logger.warning("LiteLLM not available, callbacks not registered")
        return None


# =============================================================================
# LangChain Callbacks (for BrowserUse)
# =============================================================================

# Try to import BaseCallbackHandler - required for LangChain to recognize callbacks
try:
    from langchain_core.callbacks import BaseCallbackHandler
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    # Fallback for when langchain_core is not installed
    BaseCallbackHandler = object
    _LANGCHAIN_AVAILABLE = False


class LangChainLoggingHandler(BaseCallbackHandler):
    """
    LangChain callback handler for logging LLM interactions.

    IMPORTANT: Must inherit from BaseCallbackHandler for LangChain to
    recognize and invoke the callback methods.

    Implements the key methods: on_llm_start, on_llm_end, on_llm_error
    """

    def __init__(self, logger: "AgentLogger"):
        super().__init__()
        self.logger = logger
        self._start_times: Dict[str, float] = {}
        self._models: Dict[str, str] = {}

    @property
    def ignore_llm(self) -> bool:
        """Don't ignore LLM events - we want to capture them."""
        return False

    @property
    def ignore_chain(self) -> bool:
        """Ignore chain events to reduce noise."""
        return True

    @property
    def ignore_agent(self) -> bool:
        """Ignore agent events to reduce noise."""
        return True

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
        if serialized:
            if "kwargs" in serialized:
                model = serialized["kwargs"].get("model", model)
            if "id" in serialized:
                # Extract model from serialization ID like ["langchain_ollama", "llama3.1:8b"]
                id_parts = serialized.get("id", [])
                if len(id_parts) > 1:
                    model = id_parts[-1]
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

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[Any]],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Called when chat model starts - used by ChatOllama."""
        run_id_str = str(run_id)
        self._start_times[run_id_str] = time.time()

        # Extract model name
        model = "unknown"
        if serialized:
            if "kwargs" in serialized:
                model = serialized["kwargs"].get("model", model)
            # Try to get from id path
            id_parts = serialized.get("id", [])
            if id_parts and len(id_parts) > 0:
                # Last part often contains model info
                for part in reversed(id_parts):
                    if ":" in str(part) or part not in ["ChatOllama", "langchain_ollama", "chat_models"]:
                        model = str(part)
                        break
        if "invocation_params" in kwargs:
            model = kwargs["invocation_params"].get("model", model)

        self._models[run_id_str] = model

        # Extract action from messages
        action = "chat"
        total_messages = sum(len(msg_list) for msg_list in messages)
        if messages and messages[0]:
            first_msg = messages[0][-1] if messages[0] else None
            if first_msg and hasattr(first_msg, "content"):
                content = str(first_msg.content)
                action = content[:100] if len(content) > 100 else content

        self.logger.llm_request(
            action=action,
            model=model,
            input_data={
                "message_count": total_messages,
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

        if hasattr(response, "generations") and response.generations:
            # LangChain LLMResult format
            for gen_list in response.generations:
                for gen in gen_list:
                    if hasattr(gen, "text"):
                        output += gen.text
                    elif hasattr(gen, "message") and hasattr(gen.message, "content"):
                        # ChatGeneration format
                        output += str(gen.message.content)

        # Extract token usage - try multiple formats
        tokens = self._extract_tokens(response, kwargs)

        self.logger.llm_response(
            output=output[:500] if output else "",
            duration_ms=duration_ms,
            model=model,
            tokens=tokens if any(v is not None for v in tokens.values()) else None
        )

    def _extract_tokens(self, response: Any, kwargs: Dict) -> Dict[str, Optional[int]]:
        """Extract token usage from LangChain response, handling multiple formats."""
        tokens = {"input": None, "output": None, "total": None}

        # Try 1: Standard LangChain llm_output.token_usage
        if hasattr(response, "llm_output") and response.llm_output:
            llm_output = response.llm_output
            if "token_usage" in llm_output:
                usage = llm_output["token_usage"]
                tokens["input"] = usage.get("prompt_tokens")
                tokens["output"] = usage.get("completion_tokens")
                tokens["total"] = usage.get("total_tokens")
                if any(v is not None for v in tokens.values()):
                    return tokens

            # Try Ollama format in llm_output
            if "eval_count" in llm_output:
                tokens["output"] = llm_output.get("eval_count")
                tokens["input"] = llm_output.get("prompt_eval_count")
                if tokens["input"] is not None and tokens["output"] is not None:
                    tokens["total"] = tokens["input"] + tokens["output"]
                    return tokens

        # Try 2: Check generation_info for Ollama tokens
        if hasattr(response, "generations") and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    gen_info = getattr(gen, "generation_info", {}) or {}
                    if "eval_count" in gen_info:
                        tokens["output"] = gen_info.get("eval_count")
                        tokens["input"] = gen_info.get("prompt_eval_count")
                        if tokens["input"] is not None and tokens["output"] is not None:
                            tokens["total"] = tokens["input"] + tokens["output"]
                            return tokens

                    # Also check message.response_metadata for ChatOllama
                    if hasattr(gen, "message"):
                        msg = gen.message
                        metadata = getattr(msg, "response_metadata", {}) or {}
                        if "eval_count" in metadata:
                            tokens["output"] = metadata.get("eval_count")
                            tokens["input"] = metadata.get("prompt_eval_count")
                            if tokens["input"] is not None and tokens["output"] is not None:
                                tokens["total"] = tokens["input"] + tokens["output"]
                                return tokens

        # Try 3: Check kwargs for any token info
        if "eval_count" in kwargs:
            tokens["output"] = kwargs.get("eval_count")
            tokens["input"] = kwargs.get("prompt_eval_count")
            if tokens["input"] is not None and tokens["output"] is not None:
                tokens["total"] = tokens["input"] + tokens["output"]

        return tokens

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


def create_langchain_callback(logger: "AgentLogger") -> Optional[LangChainLoggingHandler]:
    """
    Create a LangChain callback handler for logging.

    Args:
        logger: AgentLogger instance to log to

    Returns:
        LangChainLoggingHandler instance to pass to LangChain components,
        or None if LangChain is not available
    """
    if not _LANGCHAIN_AVAILABLE:
        logger.warning("langchain_core not available, LangChain callbacks disabled")
        return None

    handler = LangChainLoggingHandler(logger)
    logger.info("LangChain callback handler created", details={
        "callback_type": "LangChainLoggingHandler",
        "base_class": "BaseCallbackHandler"
    })
    return handler
