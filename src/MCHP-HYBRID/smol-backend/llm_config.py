"""
SMOL Backend Configuration for MCHP-HYBRID

This module configures the SMOL (smolagents/LiteLLM) backend for HYBRID agents.
Import this module at agent startup to configure the environment.
"""

import os
import sys

# Set environment for SMOL backend
os.environ["HYBRID_LLM_BACKEND"] = "smol"
os.environ.setdefault("LITELLM_MODEL", "ollama/llama3.1:8b")


def get_model_id() -> str:
    """Get the configured model ID."""
    return os.getenv("LITELLM_MODEL", "ollama/llama3.1:8b")


def test_connection() -> bool:
    """
    Test LLM connection at startup.

    Returns:
        True if connection successful, raises exception otherwise.
    """
    try:
        import litellm

        model_id = get_model_id()
        print(f"[SMOL Backend] Testing connection to {model_id}...")

        response = litellm.completion(
            model=model_id,
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=10
        )

        if response.choices[0].message.content:
            print(f"[SMOL Backend] Connection successful!")
            return True
        else:
            raise RuntimeError("LLM returned empty response")

    except ImportError as e:
        print(f"[SMOL Backend] ERROR: litellm not installed. Run: pip install litellm")
        raise
    except Exception as e:
        print(f"[SMOL Backend] ERROR: Connection test failed: {e}")
        print(f"[SMOL Backend] Ensure Ollama is running and model is pulled:")
        print(f"    ollama pull {get_model_id().split('/')[-1]}")
        raise


def get_litellm_model():
    """
    Get a configured LiteLLM model instance.

    For use with smolagents CodeAgent.
    """
    try:
        from smolagents import LiteLLMModel
        return LiteLLMModel(model_id=get_model_id())
    except ImportError:
        raise ImportError("smolagents not installed. Run: pip install smolagents")


# Auto-test on import if running as main
if __name__ == "__main__":
    print("Testing SMOL backend configuration...")
    test_connection()
    print("Configuration OK!")
