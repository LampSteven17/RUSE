"""
Model configuration for SUP agents.

Provides a unified interface for selecting LLM models across all brain types.
"""
import os
import shutil
import subprocess

# Default model
DEFAULT_MODEL = "llama3.1:8b"

# Available models for experiments
MODELS = {
    # GPU-optimized models
    "llama": "llama3.1:8b",
    "gemma": "gemma4:26b",        # V100 32GB sweet spot (MoE: 25.2B params, 3.8B active)
    "gemmac": "gemma4:e2b",       # CPU edge-optimized (2.3B effective params)
    "deepseek": "deepseek-r1:8b",
    # CPU-friendly models
    "lfm": "lfm2.5-thinking:latest",
    "ministral": "ministral-3:3b",
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


def get_ollama_seed():
    """Get the Ollama seed from environment, or None if not set.

    Returns:
        int seed value, or None for non-deterministic (default).
    """
    val = os.environ.get("SUP_OLLAMA_SEED")
    if val is not None:
        return int(val)
    return None


# Tier-aware Ollama context window sizes.
# Both brains send large contexts (DOM dumps for BrowserUse, tool-use traces
# for SmolAgents). Ollama's default num_ctx (4096 on CPU) silently truncates
# these and breaks workflows. We pick a value based on the hardware available
# at runtime so V100 cards aren't capped at the conservative CPU value.
NUM_CTX_GPU = 32768   # V100 32GB and above
NUM_CTX_CPU = 16384   # CPU-only VMs (28GB RAM, ~1-2 GB KV cache budget)


def get_num_ctx() -> int:
    """Pick a num_ctx value based on available hardware.

    Returns 32768 if a GPU is detected via nvidia-smi, otherwise 16384.
    Override with SUP_NUM_CTX env var if you want to force a specific value.
    """
    override = os.environ.get("SUP_NUM_CTX")
    if override:
        try:
            return int(override)
        except ValueError:
            pass

    # Detect GPU via nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and "GPU" in result.stdout:
                return NUM_CTX_GPU
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return NUM_CTX_CPU
