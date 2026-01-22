# SmolAgents Error Rate Analysis

## Summary

Analysis of exp-1 SUP logs reveals a significant discrepancy in error rates across SmolAgents configurations. The error rates vary dramatically based on the underlying LLM, with llama and gemma models exhibiting 70-81% error rates among meaningful events, while deepseek remains functional at 11-15%.

## Data Source

- **Database**: `/mnt/AXES2U1/SUP_LOGS/exp1_logs.duckdb`
- **Analysis Date**: 2026-01-09
- **Event Period**: 2025-12-19 to 2026-01-08

## Error Rates by SUP Configuration

| SUP Configuration | Error Count | Error Rate | Status |
|-------------------|-------------|------------|--------|
| S1-llama (Tesla V100) | 5,042 | **81.30%** | Broken |
| S1-llama (RTX 2080 TI) | 6,266 | **75.10%** | Broken |
| S2-gemma (Tesla V100) | 67,488 | **70.78%** | Broken |
| S2-gemma (RTX 2080 TI) | 46,511 | **70.54%** | Broken |
| S3-deepseek (Tesla V100) | 1,336 | 11.26% | Functional |
| S3-deepseek (RTX 2080 TI) | 1,376 | 14.78% | Functional |

*Note: Percentages calculated from "meaningful events" (workflow_start, workflow_end, session_start, browser_action, error) - not raw log lines.*

## Primary Error Type

The dominant error across all SmolAgents configurations is:

```
Error in code parsing:
```

This error occurs when the LLM's output does not match the expected Python code format that SmolAgents requires for parsing agent actions.

### Error Distribution by Type

| Error Category | Occurrences | Notes |
|----------------|-------------|-------|
| Code parsing errors | 111,562 | LLM output format mismatch |
| Search engine timeouts | ~100,000+ | Network/API issues (uncontrollable) |
| PyString conversion | Various | Model outputting `...` instead of code |
| Network/connection | Various | SSL EOF, connection resets |

## Analysis

### Why Deepseek Works

The deepseek-r1:8b model successfully produces output in the format SmolAgents expects, resulting in significantly lower error rates (11-15% vs 70-81%). This suggests:

1. **Model-specific behavior**: The issue is not with SmolAgents itself, but with how different LLMs format their responses
2. **Code generation capability**: Deepseek's training appears to better align with SmolAgents' expected output format
3. **Reasoning tokens**: Deepseek-r1 includes explicit reasoning which may help structure outputs correctly

### Why Llama and Gemma Fail

The llama3.1:8b and gemma3:4b models frequently produce responses that:
- Don't follow the expected Python code block format
- Output ellipsis (`...`) instead of actual code
- Generate malformed or incomplete code snippets

## Implications for exp-2

### Recommendation: Preserve Current Results

The exp-1 results should be preserved as-is because:

1. **Reproducibility**: exp-2 can validate whether these error patterns are consistent across deployments
2. **LLM characteristic**: The errors reflect inherent model behavior, not infrastructure issues
3. **Comparative value**: High error rates for llama/gemma vs low rates for deepseek provide meaningful comparison data
4. **Research validity**: Modifying configurations mid-experiment would compromise the control/treatment structure

### Expected exp-2 Behavior

If the experiment repeats itself:
- S4.llama, S5.gemma should exhibit similar high error rates
- S6.deepseek should remain functional
- PHASE timing improvements may not address fundamental LLM output format issues

## Raw Event Counts (Full Dataset)

For reference, when counting ALL events (including info logs):

| Model | Total Events | Errors | Raw Error Rate |
|-------|-------------|--------|----------------|
| S1.llama | 3,172,899 | 11,308 | 0.36% |
| S2.gemma | 9,636,469 | 113,999 | 1.18% |
| S3.deepseek | 1,959,812 | 2,712 | 0.14% |

The discrepancy between raw error rates (0.14-1.18%) and meaningful event error rates (11-81%) highlights the importance of filtering to semantically relevant events for accurate analysis.

## Conclusion

SmolAgents error rates are primarily a function of the underlying LLM's ability to produce correctly-formatted code output, not the SmolAgents framework itself. Deepseek demonstrates compatibility while llama and gemma do not. This finding should be documented as evidence that LLM selection significantly impacts agentic framework reliability.
