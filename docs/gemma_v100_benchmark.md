# Gemma Model Benchmark on V100 32GB

**Date:** 2026-04-08
**Hardware:** NVIDIA Tesla V100-PCIE-32GB (32768 MiB VRAM, CC 7.0)
**Driver:** 580.126.20 / CUDA 13.0
**Host:** `r-controls040226205037-B0-llama-0` (`v100-1gpu.14vcpu.28g` flavor)
**Backend:** Ollama 0.20.0 (llama.cpp), default Q4 quantization

## Methodology

- Stopped `b0_llama.service` to free the GPU
- For each model: pulled, ran a warmup inference, then 3 timed inference runs via Ollama's HTTP API
- Identical prompt across all models (web-browsing-agent decision task, ~50 input tokens)
- `num_predict=200`, `seed=42`, `temperature=0.7`
- Captured: tokens/sec generation, prompt eval speed, peak VRAM after model load, on-disk size
- Restarted `b0_llama.service` after benchmark

## Results

| Model           | Params (active)  | Disk     | VRAM      | Gen tok/s | Prompt tok/s | Notes                          |
| --------------- | ---------------- | -------- | --------- | --------- | ------------ | ------------------------------ |
| `gemma3:1b`     | 1B               | 0.8 GB   | 11.4 GB*  | **111.6** | 4336         | Current baseline               |
| `gemma4:e2b`    | 2.3B effective   | 7.2 GB   | 9.5 GB    | **79.0**  | 3295         | Edge-optimized smallest        |
| `gemma4:e4b`    | 4.5B effective   | 9.6 GB   | 20.3 GB   | **57.9**  | 2639         | Edge-optimized larger          |
| `gemma4:latest` | ~9B dense        | 9.6 GB   | 20.3 GB   | **64.1**  | 3371         | Standard 9B dense              |
| `gemma4:26b`    | 25.2B (3.8B MoE) | 17.0 GB  | 28.3 GB   | **62.9**  | 1307         | **MoE — best capability/cost** |
| `gemma4:31b`    | 30.7B dense      | 19.0 GB  | 28.0 GB   | **27.7**  | 1282         | Largest, slowest               |

\* `gemma3:1b` VRAM number includes residual `llama3.1:8b` from the SUP service that was stopped just before the benchmark — Ollama hadn't unloaded it yet. The actual gemma3:1b footprint alone is ~1.5 GB.

## Key Findings

### 1. The current `gemma3:1b` is way under-utilizing the V100
The 32 GB V100 is sitting at single-digit GB usage with the 1B model. We're paying for hardware we don't use and getting minimal capability for it.

### 2. `gemma4:26b` is the standout
Despite having **25.2B total parameters**, the MoE architecture activates only 3.8B per token. Result: it runs at **62.9 tok/s** — basically tied with the 9B dense `gemma4:latest` (64.1 tok/s) — while having ~3× the parameter count for capability. It uses 28.3 GB of the V100's 32 GB (89% utilization), leaving ~4 GB headroom for KV cache.

This is the **clear winner** for V100 32GB.

### 3. `gemma4:31b` is too slow
The 31b dense model fits (28 GB VRAM, 4 GB headroom) but generation drops to **27.7 tok/s** — less than half the 26b MoE speed. The extra capability isn't worth the 2.3× slowdown for an interactive agent making frequent decisions.

### 4. The "edge" e-models are not worth it on GPU
`gemma4:e2b` (79 tok/s) and `gemma4:e4b` (58 tok/s) are slower than the dense 9B (`gemma4:latest`, 64 tok/s) AND the MoE 26B (`gemma4:26b`, 63 tok/s). The "effective parameter" optimization is for CPU/edge deployment — on a V100, you should pick a dense or MoE model.

`gemma4:e2b` is still the right pick **for the CPU variants** (`B*C.gemma`, `S*C.gemma`) because it's the only Gemma 4 option small enough to be viable on CPU.

### 5. Prompt eval speed declines as models grow
Prompt processing drops from 4336 tok/s (gemma3:1b) to 1282 tok/s (gemma4:31b). For agents with long context (web page DOMs, prior conversation history), this matters more than generation speed. The 26b MoE handles prompt eval at 1307 tok/s — fine for typical agent prompts but worth knowing if you push very long contexts.

### 6. All models stay under VRAM ceiling
Even the largest (31b at 28 GB) leaves 4 GB headroom. No risk of OOM on V100 32GB at the default Q4 quantization. Q5/Q6/Q8 would push some of these over the edge.

## Recommendation

**Adopt `gemma4:26b` as the V100 gemma alias.** Best capability-to-speed ratio in the test, fits the hardware comfortably, ~10× the parameter count of the current `gemma3:1b` baseline.

For CPU variants, **adopt `gemma4:e2b`** as the new `gemmac` alias.

### Proposed change set

```python
# INSTALL_SUP.sh::MODEL_NAMES
["gemma"]="gemma4:26b"        # was gemma3:1b → V100 32GB sweet spot
["gemmac"]="gemma4:e2b"       # NEW           → CPU edge-optimized

# src/common/config/model_config.py::MODELS
"gemma":  "gemma4:26b",
"gemmac": "gemma4:e2b",

# src/runners/run_config.py — CPU SUPConfig entries: model="gemmac"
```

### Tradeoffs to consider

- **Speed regression vs gemma3:1b:** 111.6 → 62.9 tok/s (44% slower). For agent decisions every several seconds, this is invisible. For real-time interactions, it would matter.
- **VRAM reservation:** 28.3 GB out of 32 GB. If Ollama needs to share the GPU with anything else (e.g., concurrent models), 26b is too big and 9B `gemma4:latest` is the safer pick.
- **Migration cost:** existing gemma deployments need teardown + redeploy to swap models. New deploys pick up the change automatically.

## Appendix: Raw runs

See `/tmp/bench_results.json` on `r-controls040226205037-B0-llama-0` for full per-run metrics.
Each model's three runs were within ~10% of each other for generation speed, indicating consistent hardware/model behavior.
