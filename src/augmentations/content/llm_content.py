"""
DOLOS-DEPLOY LLM Content Generator

LLM-powered content generation for MCHP augmentation.
Uses LiteLLM with Ollama for text generation.

IMPORTANT: NO FALLBACK BEHAVIOR
If the LLM is unavailable, this module raises LLMUnavailableError immediately.
Experiments must fail loudly to ensure data validity.

Usage:
    from augmentations.content.llm_content import llm_paragraph, llm_sentence, llm_search_query

    text = llm_paragraph()  # Generates a coherent paragraph
    query = llm_search_query("technology")  # Generates a search query

Environment Variables:
    LITELLM_MODEL: Model to use (default: "ollama/llama3.1:8b")
"""

import os
import time
import random
from typing import List, Optional, Any

try:
    from common.logging.agent_logger import AgentLogger
except ImportError:
    AgentLogger = None


class LLMUnavailableError(Exception):
    """
    Raised when LLM backend is unavailable.

    This is a FATAL error - experiments using LLM content generation
    must have working LLM connections. No fallback to TextLorem.
    """
    pass


class LLMContentGenerator:
    """LLM content generation using LiteLLM with Ollama."""

    def __init__(self, logger: Optional[Any] = None):
        self.logger = logger
        self._model_name = os.getenv("LITELLM_MODEL", "ollama/llama3.1:8b")

        # Import and validate litellm is available
        try:
            import litellm
            self._litellm = litellm
        except ImportError as e:
            raise LLMUnavailableError(
                f"LLM content generation requires 'litellm' package. "
                f"Install with: pip install litellm. Error: {e}"
            ) from e

        # Test connection on init
        self._test_connection()

    def _test_connection(self) -> None:
        """Test LLM connection at startup."""
        try:
            response = self._litellm.completion(
                model=self._model_name,
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=10
            )
            if not response.choices[0].message.content:
                raise LLMUnavailableError("LLM returned empty response on connection test")
        except Exception as e:
            raise LLMUnavailableError(
                f"LLM connection test failed. Model: {self._model_name}. "
                f"Ensure Ollama is running with the model pulled. Error: {e}"
            ) from e

    def _execute_query(self, prompt: str, max_tokens: int = 200) -> tuple:
        """Execute LiteLLM query and return (text, tokens)."""
        response = self._litellm.completion(
            model=self._model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens
        )
        text = response.choices[0].message.content

        # Extract token counts from LiteLLM response
        tokens = None
        if hasattr(response, 'usage') and response.usage:
            tokens = {
                "input": getattr(response.usage, 'prompt_tokens', None),
                "output": getattr(response.usage, 'completion_tokens', None),
                "total": getattr(response.usage, 'total_tokens', None)
            }

        return text, tokens

    def _query_llm(self, prompt: str, action: str, max_tokens: int = 200) -> str:
        """
        Execute LLM query with logging and error handling.

        IMPORTANT: Raises LLMUnavailableError on failure - NO FALLBACK.
        """
        if self.logger:
            self.logger.llm_request(action=action, input_data={"prompt": prompt}, model=self._model_name)

        start_time = time.time()

        try:
            result, tokens = self._execute_query(prompt, max_tokens)
            duration_ms = int((time.time() - start_time) * 1000)

            if self.logger:
                self.logger.llm_response(output=result, duration_ms=duration_ms, model=self._model_name, tokens=tokens)

            return result.strip()

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)

            if self.logger:
                self.logger.llm_error(error=str(e), action=action, fatal=True)

            raise LLMUnavailableError(
                f"LLM backend '{self._model_name}' is unavailable. "
                f"Experiment data is INVALID. Action: {action}. Error: {e}"
            ) from e

    def generate_paragraph(self) -> str:
        """Generate a coherent paragraph of text."""
        prompt = (
            "Write a short, coherent paragraph (3-4 sentences) about a random professional topic. "
            "It could be about business, technology, science, or everyday office work. "
            "Write ONLY the paragraph, no introduction or explanation."
        )
        return self._query_llm(prompt, action="generate_paragraph", max_tokens=300)

    def generate_sentence(self) -> str:
        """Generate a single professional sentence."""
        prompt = (
            "Write a single professional sentence suitable for a business document or email. "
            "Write ONLY the sentence, nothing else."
        )
        return self._query_llm(prompt, action="generate_sentence", max_tokens=100)

    def generate_word(self) -> str:
        """Generate a single common English word."""
        prompt = "Generate a single common English word. Reply with ONLY the word."
        result = self._query_llm(prompt, action="generate_word", max_tokens=20)
        # Ensure we return just one word
        return result.split()[0] if result else "document"

    def generate_filename(self, extension: str = "") -> str:
        """Generate a realistic filename."""
        prompt = (
            "Generate a realistic filename for a business document (no extension). "
            "Use lowercase letters and dashes. Examples: project-report, meeting-notes, budget-2024. "
            "Reply with ONLY the filename."
        )
        result = self._query_llm(prompt, action="generate_filename", max_tokens=50)
        # Clean up the filename
        name = result.replace(" ", "-").replace("_", "-").lower()
        name = "".join(c for c in name if c.isalnum() or c == "-")
        name = name.strip("-") or "document"
        return f"{name}{extension}" if extension else name

    def generate_search_query(self, context: str = "general") -> str:
        """Generate a realistic search query."""
        prompt = (
            f"Generate a single realistic Google search query that someone might type. "
            f"Context: {context}. "
            f"Reply with ONLY the search query, nothing else."
        )
        return self._query_llm(prompt, action="generate_search_query", max_tokens=50)

    def select_item(self, items: List[str], context: str = "") -> str:
        """Intelligently select an item from a list based on context."""
        if not items:
            raise ValueError("Cannot select from empty list")

        if len(items) == 1:
            return items[0]

        # For small lists, just pick randomly (more efficient)
        if len(items) <= 3:
            return random.choice(items)

        # For larger lists, use LLM to pick intelligently
        sample = random.sample(items, min(10, len(items)))
        items_str = "\n".join(f"- {item}" for item in sample)

        prompt = (
            f"From this list, select the most interesting or relevant item"
            f"{' for ' + context if context else ''}:\n{items_str}\n\n"
            f"Reply with ONLY the selected item exactly as it appears in the list."
        )

        result = self._query_llm(prompt, action="select_item", max_tokens=100)

        # Verify result is in original list, fall back to sample if not
        if result in items:
            return result
        # Try to find a close match
        result_lower = result.lower().strip()
        for item in items:
            if item.lower().strip() == result_lower:
                return item
        # If no match, return a random item from sample
        return random.choice(sample)

    def generate_comment(self, context: str = "document") -> str:
        """Generate a document comment or review note."""
        prompt = (
            f"Write a brief review comment for a {context}. "
            f"Keep it under 100 characters. Examples: 'Needs revision', 'Good work!', 'Please clarify this section'. "
            f"Write ONLY the comment."
        )
        result = self._query_llm(prompt, action="generate_comment", max_tokens=50)
        return result[:100] if len(result) > 100 else result

    def generate_spreadsheet_headers(self, count: int) -> List[str]:
        """Generate realistic spreadsheet column headers."""
        prompt = (
            f"Generate exactly {count} column headers for a business spreadsheet. "
            f"Examples: Name, Date, Amount, Status, Category, Notes. "
            f"Reply with ONLY the headers separated by commas."
        )
        result = self._query_llm(prompt, action="generate_spreadsheet_headers", max_tokens=100)

        headers = [h.strip() for h in result.split(",")]

        # Ensure we have exactly the right count
        if len(headers) >= count:
            return headers[:count]
        else:
            # Pad with generic headers if not enough
            while len(headers) < count:
                headers.append(f"Column{len(headers) + 1}")
            return headers


# Global logger instance (set by agent initialization)
_global_logger: Optional[Any] = None
# Cached backend instance
_cached_backend: Optional[LLMContentGenerator] = None


def set_logger(logger: Any) -> None:
    """
    Set the global logger for LLM content functions.

    IMPORTANT: This also resets the cached backend so it gets recreated
    with the new logger on next use.
    """
    global _global_logger, _cached_backend
    _global_logger = logger
    # Clear the cached backend so it gets recreated with the new logger
    _cached_backend = None


def _get_backend() -> LLMContentGenerator:
    """Get the LLM backend, creating it if necessary."""
    global _cached_backend

    if _cached_backend is None:
        _cached_backend = LLMContentGenerator(logger=_global_logger)

    return _cached_backend


def reset_backend() -> None:
    """Reset the cached backend (useful for testing or reconfiguration)."""
    global _cached_backend
    _cached_backend = None


# =============================================================================
# Drop-in replacement functions for workflows
# =============================================================================

def llm_paragraph() -> str:
    """
    Generate a paragraph of coherent text.

    Drop-in replacement for TextLorem().paragraph()
    """
    return _get_backend().generate_paragraph()


def llm_sentence() -> str:
    """
    Generate a single sentence.

    Drop-in replacement for TextLorem().sentence()
    """
    return _get_backend().generate_sentence()


def llm_word() -> str:
    """
    Generate a single word.

    Drop-in replacement for TextLorem()._word()
    """
    return _get_backend().generate_word()


def llm_filename(extension: str = "") -> str:
    """
    Generate a realistic filename.

    Drop-in replacement for TextLorem(wsep='-', srange=(1,3)).sentence()[:-1]
    """
    return _get_backend().generate_filename(extension)


def llm_search_query(context: str = "general") -> str:
    """
    Generate a realistic search query.

    Args:
        context: Context for the search (e.g., "technology", "YouTube videos")
    """
    return _get_backend().generate_search_query(context)


def llm_select(items: List[str], context: str = "") -> str:
    """
    Intelligently select an item from a list.

    Drop-in replacement for random.choice() with context awareness.
    """
    return _get_backend().select_item(items, context)


def llm_comment(context: str = "document") -> str:
    """
    Generate a document comment.

    Args:
        context: Type of document being commented on
    """
    return _get_backend().generate_comment(context)


def llm_spreadsheet_headers(count: int) -> List[str]:
    """
    Generate spreadsheet column headers.

    Args:
        count: Number of headers to generate
    """
    return _get_backend().generate_spreadsheet_headers(count)
