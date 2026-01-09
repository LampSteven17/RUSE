# PHASE SUP Improvement Plan

**Generated:** 2026-01-09
**Analysis Source:** `/home/ubuntu/PHASE/inference_scripts/inference_results/v3.6.4/`
**Status:** Active Development

---

## Executive Summary

Based on PHASE v3.6.4 semantic analysis of 19 SUP configurations over 19 days, only **S2-gemma (Tesla)** achieves human classification (score 0.509). All other configurations score below 0.5 and are classified as non-human. This document outlines the improvement plan for M/B/S agents.

**Key Finding:** Workflow diversity is the primary differentiator. M-series has diverse activities while B/S-series are 100% web browsing only.

---

## Current Performance Summary

### Classification Scores (Average across 9 models)

| Rank | Config | Agent | Score | Classification |
|------|--------|-------|-------|----------------|
| 1 | S2-gemma (Tesla) | SmolAgents | **0.509** | **human** |
| 2 | M3-llama (Tesla) | MCHP | 0.469 | non_human |
| 3 | M3b-llama (Tesla) | MCHP | 0.468 | non_human |
| 4 | M2a-llama (Tesla) | MCHP | 0.462 | non_human |
| 5 | S1-llama (Tesla) | SmolAgents | 0.446 | non_human |
| 6 | M3a-llama (Tesla) | MCHP | 0.446 | non_human |
| 7 | M1 (Tesla) | MCHP | 0.441 | non_human |
| 8 | M2b-llama (Tesla) | MCHP | 0.455 | non_human |
| 9 | M2-llama (Tesla) | MCHP | 0.419 | non_human |
| 10 | S2-gemma (RTX) | SmolAgents | 0.415 | non_human |
| 11 | B1-llama (RTX) | BrowserUse | 0.331 | non_human |
| 12 | B3-deepseek (RTX) | BrowserUse | 0.320 | non_human |
| 13 | B2-gemma (Tesla) | BrowserUse | 0.309 | non_human |
| 14 | S1-llama (RTX) | SmolAgents | 0.300 | non_human |
| 15 | B1-llama (Tesla) | BrowserUse | 0.300 | non_human |
| 16 | S3-deepseek (RTX) | SmolAgents | 0.285 | non_human |
| 17 | S3-deepseek (Tesla) | SmolAgents | 0.278 | non_human |
| 18 | B2-gemma (RTX) | BrowserUse | 0.260 | non_human |
| 19 | B3-deepseek (Tesla) | BrowserUse | 0.251 | non_human |

---

## Key Problems Identified

### 1. Workflow Diversity (Critical)

| Agent | Web Browsing | Documents | File Ops | Shell | Video |
|-------|-------------|-----------|----------|-------|-------|
| **M-series** | 22% | 33% | 22% | 11% | 11% |
| **B-series** | **100%** | 0% | 0% | 0% | 0% |
| **S-series** | **100%** | 0% | 0% | 0% | 0% |

**Impact:** B and S agents perform only web browsing, lacking the activity diversity that characterizes real human behavior.

### 2. Error Rates

| Agent | Error Rate | Status |
|-------|-----------|--------|
| M-series | ~4.5% | Healthy |
| B-series | 0-0.3% | Too perfect (suspicious) |
| S1-llama | **75-81%** | Broken |
| S2-gemma | **70%** | Broken |
| S3-deepseek | 11-15% | Acceptable |

### 3. SHAP Feature Analysis

Top features distinguishing human activity (ordered by importance):
1. `resp_ip_bytes` - Response IP bytes
2. `conn_state` - Connection state diversity
3. `orig_pkts` - Origin packets (burstiness)
4. `history` - Connection history patterns
5. `id.orig_p` - Origin port diversity
6. `duration` - Connection duration variance

**Temporal Pattern:** Human activity peaks 10AM-5PM, minimal 2AM-6AM.

---

## Improvement Plan

### Control Configuration

> **IMPORTANT:** M0 is the upstream MITRE pyhuman (https://github.com/mitre/human) running unmodified as the control. M1-M3 are DOLOS MCHP configurations that can be improved. M0 MUST NOT be modified.

---

### MCHP Improvements (M1-M3)

**Current State:** Best performers (0.42-0.47) but still below human threshold.

**Completed Changes:**

#### 1. Time-of-Day Scheduling (DONE)
Implemented via `--phase-timing` flag in `run_mchp.py`:
- Reduces activity during 2AM-6AM (sleep hours)
- Peak activity during 9AM-5PM (work hours)
- Gradual ramp-up/ramp-down at transitions
- Uses existing `src/common/timing/phase_timing.py` module

```bash
# Usage
python -m runners.run_mchp --phase-timing
```

#### 2. Timing Jitter Enhancement (DONE)
Implemented in PhaseTiming class:
- Variability in `task_delay` based on activity level
- Longer delays during low-activity hours
- Break detection after sustained activity (`should_take_break()`)

#### 3. Pending: Connection Diversity
- Vary port usage patterns
- Introduce connection state variety
- Simulate natural connection duration variance

**Files Modified:**
- `src/brains/mchp/agent.py` - Added `use_phase_timing` parameter
- `src/runners/run_mchp.py` - Added `--phase-timing` CLI flag

---

### BrowserUse Improvements (B1-B3 → B4-B6)

**Current State:** Lowest performers (0.25-0.33), 100% web browsing.

**Completed Changes:**

#### 1. MCHP Workflow Integration (DONE)
Created workflow infrastructure allowing BrowserUse to run MCHP-style:

```
src/brains/browseruse/
├── workflows/
│   ├── __init__.py      # Workflow exports
│   ├── base.py          # BUWorkflow base class
│   ├── browsing.py      # Browsing task workflow
│   └── loader.py        # MCHP workflow importer
└── loop.py              # BrowserUseLoop (MCHP-style execution)
```

**New Configurations:**
- `--loop` flag enables MCHP-style continuous execution
- `--no-mchp` disables MCHP workflow integration
- Default loop mode includes MCHP workflows for diversity

**Usage:**
```bash
# Single task mode (original B1-B3 behavior)
python -m runners.run_browseruse "Search for Python tutorials"

# Loop mode with MCHP workflows (B4-B6 behavior)
python -m runners.run_browseruse --loop --model llama

# Loop mode without MCHP workflows
python -m runners.run_browseruse --loop --no-mchp
```

**Configuration Mapping:**
- `B1.llama` → Single task mode, llama model
- `B4.llama` → Loop mode + MCHP workflows, llama model (PHASE timing)
- Same pattern for gemma (B2→B5) and deepseek (B3→B6)

**Files Created:**
- `src/brains/browseruse/workflows/__init__.py`
- `src/brains/browseruse/workflows/base.py`
- `src/brains/browseruse/workflows/browsing.py`
- `src/brains/browseruse/workflows/loader.py`
- `src/brains/browseruse/loop.py`

**Files Modified:**
- `src/brains/browseruse/__init__.py` - Added BrowserUseLoop export
- `src/runners/run_browseruse.py` - Added loop mode support

#### 2. PHASE Timing Integration (DONE)
Loop mode now includes PHASE timing by default:
- Time-of-day activity awareness
- Variable cluster sizes and delays
- Break detection after sustained activity

```bash
# Usage (PHASE timing enabled by default)
python -m runners.run_browseruse --loop --model llama

# Disable PHASE timing (use random timing)
python -m runners.run_browseruse --loop --model llama --no-phase-timing
```

#### 3. Pending: Natural Browsing Mechanics
- Add scroll hesitation and variable speeds
- Implement "reading time" before clicks
- Add occasional back-navigation and tab switching
- Introduce realistic typos in search queries

#### 3. Pending: Error Introduction
- Current 0-0.3% error rate is suspiciously low
- Target ~3-5% natural error rate
- Add timeout handling that mimics human retry behavior

---

### SmolAgents Improvements (S1-S3)

**Current State:** High variance (0.28-0.51), massive error rates (70-81%).

**Completed Changes:**

#### 1. MCHP Workflow Integration (DONE)
Created workflow infrastructure allowing SmolAgents to run MCHP-style:

```
src/brains/smolagents/
├── workflows/
│   ├── __init__.py      # Workflow exports
│   ├── base.py          # SmolWorkflow base class
│   ├── research.py      # Research task workflow
│   └── loader.py        # MCHP workflow importer
└── loop.py              # SmolAgentLoop (MCHP-style execution)
```

**New Configurations:**
- `--loop` flag enables MCHP-style continuous execution
- `--no-mchp` disables MCHP workflow integration
- Default loop mode includes MCHP workflows for diversity

**Usage:**
```bash
# Single task mode (original S1-S3 behavior)
python -m runners.run_smolagents "What is quantum computing?"

# Loop mode with MCHP workflows (S4-S6 behavior)
python -m runners.run_smolagents --loop --model llama

# Loop mode without MCHP workflows (research only)
python -m runners.run_smolagents --loop --no-mchp
```

**Configuration Mapping:**
- `S1.llama` → Single task mode, llama model
- `S4.llama` → Loop mode + MCHP workflows, llama model (PHASE timing)
- Same pattern for gemma (S2→S5) and deepseek (S3→S6)

#### 3. Pending: Error Rate Fixes
- S1-llama (75-81% errors) - Investigate DuckDuckGo tool failures
- S2-gemma (70% errors) - Same investigation
- Root cause likely: model capability issues with tool calling

#### 4. Pending: Browser Action Integration
- Current SmolAgents uses API-based search (no browser_action events)
- Consider adding Playwright/Selenium for actual browser interactions
- This would generate network traffic matching human patterns

**Files Modified:**
- `src/brains/smolagents/__init__.py` - Added SmolAgentLoop export
- `src/runners/run_smolagents.py` - Added loop mode support

**Files Created:**
- `src/brains/smolagents/workflows/__init__.py`
- `src/brains/smolagents/workflows/base.py`
- `src/brains/smolagents/workflows/research.py`
- `src/brains/smolagents/workflows/loader.py`
- `src/brains/smolagents/loop.py`

#### 2. PHASE Timing Integration (DONE)
Loop mode now includes PHASE timing by default:
- Time-of-day activity awareness
- Variable cluster sizes and delays
- Break detection after sustained activity

```bash
# Usage (PHASE timing enabled by default)
python -m runners.run_smolagents --loop --model llama

# Disable PHASE timing (use random timing)
python -m runners.run_smolagents --loop --model llama --no-phase-timing
```

---

## Implementation Priority

### Phase 1: Workflow Diversity (DONE)
1. [x] SmolAgents MCHP workflow integration
2. [x] BrowserUse MCHP workflow integration
3. [ ] Test workflow distribution matches M-series (~22/33/22/11/11)

### Phase 2: Timing Improvements (DONE)
1. [x] Time-of-day scheduling for all agents (PHASE timing module)
2. [x] Timing jitter and fatigue simulation (via PhaseTiming)
3. [x] Natural pauses and "thinking time" (break detection)

### Phase 3: Error Handling
1. [ ] Investigate S1/S2 high error rates
2. [ ] Add natural error injection to B-series
3. [ ] Implement retry patterns that mimic human behavior

### Phase 4: Network Pattern Optimization
1. [ ] Connection state diversity
2. [ ] Port usage patterns
3. [ ] Duration variance

---

## Metrics & Validation

### Success Criteria
- All configurations achieve ≥0.5 average score (human classification)
- Workflow distribution within 5% of M-series baseline
- Error rates between 3-6% (natural range)

### Validation Process
1. Deploy improved configurations
2. Run 7+ days of activity logging
3. Re-run PHASE inference
4. Compare scores against baseline

---

## Configuration Reference

### Current SUP Configurations

| Config | Agent | Model | GPU | Notes |
|--------|-------|-------|-----|-------|
| M0 | MCHP | - | No | **CONTROL - DO NOT MODIFY** |
| M1 | MCHP | - | Tesla | Pure MCHP baseline |
| M2-llama | MCHP | llama3.1:8b | Tesla | MCHP + SmolAgents augmentation |
| M2a-llama | MCHP | llama3.1:8b | Tesla | MCHP + SmolAgents content only |
| M2b-llama | MCHP | llama3.1:8b | Tesla | MCHP + SmolAgents mechanics only |
| M3-llama | MCHP | llama3.1:8b | Tesla | MCHP + BrowserUse augmentation |
| M3a-llama | MCHP | llama3.1:8b | Tesla | MCHP + BrowserUse content only |
| M3b-llama | MCHP | llama3.1:8b | Tesla | MCHP + BrowserUse mechanics only |
| B1-llama | BrowserUse | llama3.1:8b | Tesla/RTX | BrowserUse baseline |
| B2-gemma | BrowserUse | gemma3:4b | Tesla/RTX | BrowserUse + gemma |
| B3-deepseek | BrowserUse | deepseek-r1:8b | Tesla/RTX | BrowserUse + deepseek |
| S1-llama | SmolAgents | llama3.1:8b | Tesla/RTX | SmolAgents baseline |
| S2-gemma | SmolAgents | gemma3:4b | Tesla/RTX | SmolAgents + gemma |
| S3-deepseek | SmolAgents | deepseek-r1:8b | Tesla/RTX | SmolAgents + deepseek |

### Planned New Configurations (Exp2)

Per the exp2-plan.md naming convention, improved configurations use incremental numbers:

| Config | Agent | Description |
|--------|-------|-------------|
| **M0** | Upstream MITRE | Original pyhuman (CONTROL - unmodified) |
| **S4.llama** | SmolAgentLoop | SmolAgents + MCHP workflows + PHASE timing |
| **S5.gemma** | SmolAgentLoop | SmolAgents + MCHP workflows + PHASE timing |
| **S6.deepseek** | SmolAgentLoop | SmolAgents + MCHP workflows + PHASE timing |
| **B4.llama** | BrowserUseLoop | BrowserUse + MCHP workflows + PHASE timing |
| **B5.gemma** | BrowserUseLoop | BrowserUse + MCHP workflows + PHASE timing |
| **B6.deepseek** | BrowserUseLoop | BrowserUse + MCHP workflows + PHASE timing |

**Naming Convention:**
- `B1-B3` = Baseline BrowserUse (different models)
- `B4-B6` = Improved BrowserUse with PHASE timing and MCHP workflow integration
- `S1-S3` = Baseline SmolAgents (different models)
- `S4-S6` = Improved SmolAgents with PHASE timing and MCHP workflow integration
- `M0` = Upstream MITRE pyhuman (control)
- `M1` = DOLOS MCHP baseline
- `M2/M3` = MCHP with LLM augmentations

---

## Appendix: SHAP Feature Details

### Human Activity Signature
- Peak activity: 10AM-5PM across all features
- Minimal activity: 2AM-6AM
- Bursty patterns in `orig_pkts` and `resp_ip_bytes`
- Diverse `conn_state` values

### Non-Human Activity Signature
- Uniform activity across all hours
- Consistent, non-bursty packet patterns
- Limited connection state diversity
- Predictable timing patterns

---

*Document maintained by PHASE improvement team*
