# Plan: Integrate AgentLogger into Runners

## Goal
Replace simple `print()` statements in runners with structured `AgentLogger` so future experiments produce JSONL logs suitable for semantic analysis.

## Current State
- `AgentLogger` exists at `src/common/logging/agent_logger.py` (fully implemented)
- Runners use `print()` → stdout captured by systemd as plain text
- No structured event logging for workflows, LLM calls, actions

## Files to Modify

### 1. `src/runners/run_mchp.py`
```python
# Add at top
from common.logging.agent_logger import AgentLogger

# In run_mchp():
logger = AgentLogger(agent_type=config.config_key)
logger.session_start(config=vars(config))

# Pass logger to MCHPAgent
agent = MCHPAgent(logger=logger)
agent.run()

logger.session_end()
```

### 2. `src/runners/run_browseruse.py`
Same pattern - initialize AgentLogger and pass to agent.

### 3. `src/runners/run_smolagents.py`
Same pattern.

### 4. `src/brains/mchp/agent.py`
```python
# In _emulation_loop():
self.logger.workflow_start(workflow_name)
try:
    self.workflows[index].action(self.extra)
    self.logger.workflow_end(workflow_name, success=True)
except Exception as e:
    self.logger.workflow_end(workflow_name, success=False, error=str(e))
```

### 5. `src/brains/browseruse/agent.py`
Add logging for task execution, LLM calls.

### 6. `src/brains/smolagents/agent.py`
Add logging for task execution, LLM calls.

### 7. `src/augmentations/content/llm_content.py`
Already has optional AgentLogger - make it required and use consistently.

## Event Types to Log
- `session_start` / `session_end` - agent lifecycle
- `workflow_start` / `workflow_end` - task execution with duration/success
- `llm_request` / `llm_response` - LLM calls with model/tokens/duration
- `browser_action` - Selenium navigation, clicks
- `gui_action` - pyautogui interactions
- `decision` - workflow selection (random vs LLM-guided)
- `error` / `warning` - failures and issues

## Output
Logs will be written to:
`/opt/dolos-deploy/deployed_sups/{config_key}/logs/session_{timestamp}_{session_id}.jsonl`

## Priority
Medium - do this before next experiment run.
