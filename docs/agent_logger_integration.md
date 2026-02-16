# Agent Logger Integration Guide

## Overview

All RUSE agent types (M, B, S) use the `AgentLogger` framework to emit structured JSONL events for experiment analysis. This document describes the logging integration architecture and patterns.

## Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Runner          │────▶│  Agent       │────▶│  Workflows      │
│ (run_*.py)      │     │              │     │                 │
│                 │     │ Passes       │     │ Logs events via │
│ Creates         │     │ logger to    │     │ logger.*()      │
│ AgentLogger     │     │ workflows    │     │                 │
└─────────────────┘     └──────────────┘     └─────────────────┘
```

## Event Types

| Event Type | Description | Used By |
|------------|-------------|---------|
| `session_start` | Agent session begins | All |
| `session_end` | Agent session ends | All |
| `workflow_start` | Workflow execution begins | All |
| `workflow_end` | Workflow execution ends (success/failure) | All |
| `browser_action` | Browser navigation, clicks, searches | M, B |
| `gui_action` | GUI application interactions | M |
| `timing_delay` | Sleep/delay events | All |
| `error` | Error events with exception details | All |
| `warning` | Warning events | All |
| `info` | Informational events | All |

## MCHP (M Behaviors)

### Workflow Logger Integration

All MCHP workflows inherit from `BaseWorkflow` and accept an optional `logger` parameter:

```python
def action(self, extra=None, logger=None):
    """Execute the workflow action."""
    if logger:
        logger.browser_action("navigate", target="https://example.com")
```

### Event Types by Workflow

| Workflow | Event Type | Action Values |
|----------|------------|---------------|
| browse_web.py | browser_action | navigate, click |
| browse_youtube.py | browser_action | navigate |
| google_search.py | browser_action | navigate, search, click, browse |
| download_files.py | browser_action | download |
| execute_command.py | gui_action | execute_command |
| ms_paint.py | gui_action | open_application |
| open_office_calc.py | gui_action | open_application |
| open_office_writer.py | gui_action | open_application |
| spawn_shell.py | gui_action | spawn_shell |

### Error Logging

All exception handlers include structured error logging:

```python
except TimeoutException as error:
    print(f"Timeout loading {url}: {error}")
    if logger:
        logger.browser_action("click", target=url, success=False)
        logger.error("Timeout during navigation", exception=error)
```

## BrowserUse (B Behaviors)

### Integration Points

- **agent.py**: Logs `workflow_start`/`workflow_end` for each task
- **loop.py**: Logs workflow loading, errors, startup/termination

### BrowserUse Library Logging

The BrowserUse library internally logs browser actions to stdout in a structured format:
```
INFO     [tools] 🔗 Navigated to https://example.com
```

These messages are parsed by the log collection pipeline into `browser_action` events.

## SmolAgents (S Behaviors)

### Integration Points

- **agent.py**: Logs `workflow_start`/`workflow_end` for each task
- **loop.py**: Logs workflow loading, errors, startup/termination

### Tool Execution

SmolAgents uses tools (DuckDuckGoSearchTool, etc.) which output to stdout. The library's logging is captured and parsed by the log collection pipeline.

## Log Output Location

Logs are written to JSONL files at:
```
/opt/ruse/deployed_sups/{config_key}/logs/session_{timestamp}_{session_id}.jsonl
```

A symlink `latest.jsonl` always points to the most recent session.

## Log Collection

The log collection pipeline (`log_retrieval/collect_sup_logs.py`) collects:
1. **JSONL files**: Native structured logs from AgentLogger
2. **systemd logs**: stdout/stderr from agent processes

Both are parsed and loaded into DuckDB for analysis.

## Verification Queries

Check event distribution after deployment:

```sql
-- Event counts by behavior type
SELECT
    sup_behavior,
    event_type,
    COUNT(*) as count
FROM unified_events
GROUP BY sup_behavior, event_type
ORDER BY sup_behavior, count DESC;

-- Verify browser_action events for M behaviors
SELECT sup_behavior, COUNT(*) as browser_actions
FROM unified_events
WHERE event_type = 'browser_action'
  AND sup_behavior LIKE 'M%'
GROUP BY sup_behavior;

-- Workflow completion rates
SELECT
    sup_behavior,
    SUM(CASE WHEN event_type = 'workflow_start' THEN 1 ELSE 0 END) as starts,
    SUM(CASE WHEN event_type = 'workflow_end' THEN 1 ELSE 0 END) as ends,
    ROUND(100.0 * SUM(CASE WHEN event_type = 'workflow_end' THEN 1 ELSE 0 END) /
          NULLIF(SUM(CASE WHEN event_type = 'workflow_start' THEN 1 ELSE 0 END), 0), 1) as completion_pct
FROM unified_events
GROUP BY sup_behavior
ORDER BY sup_behavior;
```

## Adding Logging to New Workflows

1. Accept `logger` parameter in `action()` method
2. Log start action: `logger.browser_action()` or `logger.gui_action()`
3. Log success/failure in exception handlers
4. Pass logger to private methods that perform actions

Example:

```python
def action(self, extra=None, logger=None):
    if logger:
        logger.browser_action("navigate", target=self.target_url)
    try:
        self._perform_action(logger=logger)
    except Exception as e:
        if logger:
            logger.error("Action failed", exception=e)
```

## Troubleshooting

### Missing Events

If events are missing, check:
1. Logger is passed to workflow: `workflow.action(logger=self.logger)`
2. Workflow accepts logger: `def action(self, extra=None, logger=None)`
3. JSONL files exist on VM: `ls /opt/ruse/deployed_sups/*/logs/*.jsonl`
4. Log collection includes JSONL: Check `collect_sup_logs.py` output

### TypeError: unexpected keyword argument 'logger'

This means the workflow doesn't accept the logger parameter. Update the workflow's `action()` method signature to include `logger=None`.
