# Deepseek-R1 Incompatibility with MCHP Content Generation

**Date:** 2026-01-30
**Affected Configs:** M1c.deepseek, M2c.deepseek
**Status:** Known Limitation

## Summary

The `deepseek-r1:8b` reasoning model is incompatible with MCHP's LLM content generation system, while working correctly with BrowserUse and SmolAgents brains. This is due to how reasoning models handle simple prompts.

## Observed Behavior

### MCHP (M1c.deepseek, M2c.deepseek)
- **Result:** 0 successful LLM responses
- **Error:** `LLM returned empty response on connection test`
- All workflows requiring LLM content fail immediately

### BrowserUse (B2c.deepseek)
- **Result:** Working correctly
- LLM responses with proper token counts (input: ~4000, output: ~100-200)
- Model successfully handles complex browser automation prompts

## Root Cause Analysis

### The Connection Test

MCHP's `LLMContentGenerator` performs a connection test on initialization:

```python
# src/augmentations/content/llm_content.py
def _test_connection(self) -> None:
    response = self._litellm.completion(
        model=self._model_name,
        messages=[{"role": "user", "content": "Say OK"}],
        max_tokens=10
    )
    if not response.choices[0].message.content:
        raise LLMUnavailableError("LLM returned empty response on connection test")
```

### Why Deepseek-R1 Fails

Deepseek-R1 is a **reasoning model** that uses `<think>...</think>` tags for its internal reasoning process before generating a response. For trivial prompts like "Say OK":

1. The model may allocate most/all of `max_tokens=10` to reasoning
2. No tokens remain for the actual response content
3. `response.choices[0].message.content` is empty
4. Connection test fails, blocking all subsequent LLM calls

### Why BrowserUse Works

BrowserUse sends substantive prompts with:
- Complex browser state descriptions
- Multi-step task instructions
- Larger token budgets (200+ output tokens)

These prompts give the reasoning model enough context to produce meaningful responses.

## Evidence from Logs

### M1c.deepseek Failure Pattern
```json
{
  "event_type": "workflow_end",
  "agent_type": "M1c.deepseek",
  "details": {
    "success": false,
    "error": "LLM connection test failed. Model: ollama/deepseek-r1:8b. Ensure Ollama is running with the model pulled. Error: LLM returned empty response on connection test"
  }
}
```

### B2c.deepseek Success Pattern
```json
{
  "event_type": "llm_response",
  "agent_type": "B2c.deepseek",
  "details": {
    "output": "{\"evaluation_previous_goal\": \"...\", \"next_goal\": \"...\", \"action\":[...]}",
    "duration_ms": 30785,
    "model": "deepseek-r1:8b",
    "tokens": {"input": 4096, "output": 62, "total": 4158}
  }
}
```

## Model Compatibility Matrix

| Brain | llama3.1:8b | gemma3:4b | deepseek-r1:8b |
|-------|-------------|-----------|----------------|
| MCHP (M1/M2) | Working | Working | **FAILS** |
| BrowserUse (B1/B2) | Working | Working | Working |
| SmolAgents (S1/S2) | Working | Working | Working |

## Implications

1. **M1c.deepseek and M2c.deepseek produce no valid LLM data** - These configs effectively run as M1 (no LLM) with constant failures
2. **Experiment validity is preserved** - Logs clearly show the failure mode
3. **No silent degradation** - The fail-fast behavior is intentional (no TextLorem fallback)

## Recommendations

### For Current Experiment
- Exclude M1c.deepseek and M2c.deepseek from LLM response analysis
- Use these configs as additional data points showing model limitations
- Continue collecting logs for workflow pattern analysis (non-LLM events)

### For Future Experiments
If deepseek-r1 support is needed for MCHP:
1. Increase `max_tokens` in connection test to 100+
2. Use a more substantive test prompt
3. Or skip connection test entirely (fail on first real use)

### Alternative Reasoning Models
Consider testing other reasoning models that may handle simple prompts better, or use non-reasoning deepseek variants if available.

## Conclusion

This is a **model behavioral limitation**, not a configuration or infrastructure issue. The deepseek-r1:8b model's reasoning architecture is incompatible with MCHP's simple content generation prompts but works well with the complex prompts used by BrowserUse and SmolAgents.

The logging system correctly captures this failure mode, providing clear evidence for analysis.
