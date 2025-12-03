# SUP Experiment Configuration Matrix

This document details the experimental configurations for evaluating SUP (Synthetic User Persona) realism and performance.

### Research Questions

**RQ1: What makes SUPs realistic?** What parameters can improve a SUP's performance?

- **Backend**: Scripted (MCHP) vs. AI-enabled (SmolAgents, BrowserUse)?
- **Timings**: Scripted behaviors vs. PHASE-informed patterns?
- **Hardware**: How much does hardware affect performance?
- **Model Augmentation**: Can different LLM models affect performance?

---

## PRE-PHASE Configurations
- - - - - -

- **Brain Type** — The main API responsible for controlling the SUP
- **Content Controller** — What generates or creates the content to fill in specific workflows
- **Mechanics Controller** — What handles placing content, browser movement, navigation, and behavioral pauses
- **Model** — LLM model to use, if any

| **Config Key** | **Brain Type** | **Content Controller** | **Mechanics Controller** | **Model** |
|:---------------|:---------------|:-----------------------|:-------------------------|:----------|
| M1             | MCHP           | MCHP                   | MCHP                     | None      |
| M2.llama       | MCHP           | SmolAgents             | SmolAgents               | llama3.1:8b |
| M2a.llama      | MCHP           | SmolAgents             | MCHP                     | llama3.1:8b |
| M2b.llama      | MCHP           | MCHP                   | SmolAgents               | llama3.1:8b |
| M3.llama       | MCHP           | BrowserUse             | BrowserUse               | llama3.1:8b |
| M3a.llama      | MCHP           | BrowserUse             | MCHP                     | llama3.1:8b |
| M3b.llama      | MCHP           | MCHP                   | BrowserUse               | llama3.1:8b |
|                |                |                        |                          |           |
| B1.llama       | BrowserUse     | BrowserUse             | BrowserUse               | llama3.1:8b |
| B2.gemma       | BrowserUse     | BrowserUse             | BrowserUse               | gemma3:4b |
| B3.deepseek    | BrowserUse     | BrowserUse             | BrowserUse               | deepseek-r1:8b |
|                |                |                        |                          |           |
| S1.llama       | SmolAgents     | SmolAgents             | SmolAgents               | llama3.1:8b |
| S2.gemma       | SmolAgents     | SmolAgents             | SmolAgents               | gemma3:4b |
| S3.deepseek    | SmolAgents     | SmolAgents             | SmolAgents               | deepseek-r1:8b |

### Limitations

- **Single model for MCHP augmentations** — The M series uses only llama3.1:8b as a baseline for LLM-augmented configurations. This simplifies deployment and establishes a consistent comparison point. Additional models may be tested if results warrant further exploration.

- **Uniform controllers only** — Content and Mechanics controllers are always the same within a configuration (e.g., both SmolAgents or both BrowserUse). Mixing different controllers for content vs. mechanics is technically feasible but significantly increases implementation complexity.

- **Model variation limited to LLM brains** — The B and S series test different models (llama, gemma, deepseek) because these LLM-native brains have additional prompt engineering avenues to explore. These optimizations will be informed by PHASE experiment results.


## POST-PHASE Configurations
- - - - - - -

The lowest-performing configuration from each series (M*, B*, S*) will be selected for PHASE improvements. Using insights gained from evaluating all PRE-PHASE configurations, we apply PHASE-informed timing patterns and behavioral refinements to measure potential performance gains.

| **Config Key** | **Brain Type** | **Content Controller**    | **Mechanics Controller**   | **Model** |
|:---------------|:---------------|:--------------------------|:---------------------------|:----------|
| M?.llama+      | MCHP           | *TBD from PRE-PHASE*      | *TBD from PRE-PHASE*       | llama3.1:8b |
| B?.model+      | BrowserUse     | PHASE Prompt Engineered   | PHASE Prompt Engineered    | *TBD*     |
| S?.model+      | SmolAgents     | PHASE Prompt Engineered   | PHASE Prompt Engineered    | *TBD*     |


## Configuration Notes

### MCHP Series (M1-M3)
- **M1**: Pure MCHP baseline - no LLM augmentation
- **M2**: MCHP brain with SmolAgents controlling both content and mechanics
- **M2a**: MCHP brain with SmolAgents content only
- **M2b**: MCHP brain with SmolAgents mechanics only
- **M3**: MCHP brain with BrowserUse controlling both content and mechanics
- **M3a**: MCHP brain with BrowserUse content only
- **M3b**: MCHP brain with BrowserUse mechanics only

### BrowserUse Series (B1-B3)
- **B1.llama**: BrowserUse baseline with Llama 3.1
- **B2.gemma**: BrowserUse with Gemma 3
- **B3.deepseek**: BrowserUse with DeepSeek R1

### SmolAgents Series (S1-S3)
- **S1.llama**: SmolAgents baseline with Llama 3.1
- **S2.gemma**: SmolAgents with Gemma 3
- **S3.deepseek**: SmolAgents with DeepSeek R1

### Post-PHASE Notation
- `+` suffix indicates PHASE timing and behavioral improvements applied
- "Prompt Engineered" indicates PHASE improvements delivered via enhanced prompts


## Summary Statistics

| **Metric**            | **Value**                                |
|:----------------------|:-----------------------------------------|
| Total Configurations  | 16 (13 PRE-PHASE + 3 POST-PHASE)         |
| Brain Types           | 3 (MCHP, BrowserUse, SmolAgents)         |
| Models                | 4 (None, llama3.1:8b, gemma3:4b, deepseek-r1:8b) |
| PRE-PHASE Variants    | 13 (7 MCHP + 3 BrowserUse + 3 SmolAgents) |
| POST-PHASE Variants   | 3 (1 per brain type, selected from lowest performers) |