"""
Model configuration for SUP agents.

Provides a unified interface for selecting LLM models across all brain types.
"""
import os

# Default model
DEFAULT_MODEL = "llama3.1:8b"

# Available models for experiments
MODELS = {
    # GPU-optimized models
    "llama": "llama3.1:8b",
    "gemma": "gemma3:4b",
    "deepseek": "deepseek-r1:8b",
    # CPU-friendly models
    "lfm": "lfm2.5-thinking:latest",
    "ministral": "ministral:3b",
    "qwen": "qwen2.5:3b",
}

# Reverse mapping for CLI display
MODEL_KEYS = {v: k for k, v in MODELS.items()}


def get_model(model_key: str = None) -> str:
    """
    Get model name from key or environment.

    Args:
        model_key: Short key like 'llama', 'gemma', 'deepseek'
                   If None, falls back to OLLAMA_MODEL env var or default.

    Returns:
        Full model name (e.g., 'llama3.1:8b')
    """
    if model_key and model_key in MODELS:
        return MODELS[model_key]
    return os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)


def get_model_key(model_name: str) -> str:
    """
    Get short key from full model name.

    Args:
        model_name: Full model name like 'llama3.1:8b'

    Returns:
        Short key like 'llama', or the original name if not found
    """
    return MODEL_KEYS.get(model_name, model_name)


def list_models() -> dict:
    """Return all available models."""
    return MODELS.copy()
