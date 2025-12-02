"""
BU Backend Configuration for MCHP-HYBRID

This module configures the BU (browser-use/ChatOllama) backend for HYBRID agents.
Import this module at agent startup to configure the environment.
"""

import os
import sys

# Set environment for BU backend
os.environ["HYBRID_LLM_BACKEND"] = "bu"
os.environ.setdefault("OLLAMA_MODEL", "llama3.1:8b")


def get_model_name() -> str:
    """Get the configured model name."""
    return os.getenv("OLLAMA_MODEL", "llama3.1:8b")


def test_connection() -> bool:
    """
    Test LLM connection at startup.

    Returns:
        True if connection successful, raises exception otherwise.
    """
    try:
        from langchain_ollama import ChatOllama

        model_name = get_model_name()
        print(f"[BU Backend] Testing connection to {model_name}...")

        llm = ChatOllama(model=model_name)
        response = llm.invoke("Say OK")

        if response.content:
            print(f"[BU Backend] Connection successful!")
            return True
        else:
            raise RuntimeError("LLM returned empty response")

    except ImportError as e:
        print(f"[BU Backend] ERROR: langchain-ollama not installed. Run: pip install langchain-ollama")
        raise
    except Exception as e:
        print(f"[BU Backend] ERROR: Connection test failed: {e}")
        print(f"[BU Backend] Ensure Ollama is running and model is pulled:")
        print(f"    ollama pull {get_model_name()}")
        raise


def get_chat_ollama():
    """
    Get a configured ChatOllama instance.

    For use with browser-use Agent.
    """
    try:
        from langchain_ollama import ChatOllama
        return ChatOllama(model=get_model_name())
    except ImportError:
        raise ImportError("langchain-ollama not installed. Run: pip install langchain-ollama")


# Auto-test on import if running as main
if __name__ == "__main__":
    print("Testing BU backend configuration...")
    test_connection()
    print("Configuration OK!")
