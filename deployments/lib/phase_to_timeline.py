#!/usr/bin/env python3
"""Translate PHASE behavioral configs into a GHOSTS timeline.json.

Reads available configs from a PHASE feedback directory and produces a
GHOSTS-compatible timeline with time-windowed browsing, weighted URLs,
realistic delays, and background DNS noise.

Usage:
    python3 phase_to_timeline.py <feedback_dir> [--output timeline.json]

Missing configs are handled gracefully — each mapping falls back to defaults.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

# ── Defaults (used when PHASE configs are missing) ──────────────────────

DEFAULT_URLS = [
    "http://google.com", "http://wikipedia.org", "http://reddit.com",
    "http://amazon.com", "http://cnn.com", "http://nytimes.com",
    "http://stackoverflow.com", "http://github.com", "http://espn.com",
    "http://weather.com", "http://webmd.com", "http://nih.gov",
    "http://usatoday.com", "http://wsj.com", "http://npr.org",
    "http://bloomberg.com", "http://reuters.com", "http://theguardian.com",
    "http://cmu.edu", "http://virginia.edu",
]

DEFAULT_DELAY_MS = 15000

BASH_COMMANDS = [
    "ls -la /tmp", "df -h", "uptime", "free -m",
    "ps aux | head -20", "cat /proc/loadavg", "who", "last -5",
]

# Map RUSE workflow names → GHOSTS handler types
WORKFLOW_TO_HANDLER = {
    "BrowseWeb": "BrowserFirefox",
    "GoogleSearch": "BrowserFirefox",
    "WebSearch": "BrowserFirefox",
    "BrowseYoutube": "BrowserFirefox",
    "DownloadFiles": "Curl",
    "SpawnShell": "Bash",
    "ExecuteCommand": "Bash",
    "OpenOfficeWriter": "Bash",
    "OpenOfficeCalc": "Bash",
}


# ── Config loading ──────────────────────────────────────────────────────

def _load(feedback_dir: Path, name: str) -> dict | None:
    path = feedback_dir / name
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


# ── URL list from site_config ───────────────────────────────────────────

def _build_urls(site_config: dict | None) -> list[str]:
    if not site_config:
        return list(DEFAULT_URLS)

    categories = site_config.get("site_categories", {})
    domain_cats = site_config.get("domain_categories", {})
    if not domain_cats:
        return list(DEFAULT_URLS)

    urls = []
    for domain, cat_name in domain_cats.items():
        cat = categories.get(cat_name, {})
        weight = cat.get("weight", 0.5) if isinstance(cat, dict) else 0.5
        repeat = max(1, round(weight * 10))
        urls.extend([f"http://{domain}"] * repeat)

    return urls if urls else list(DEFAULT_URLS)


# ── Time windows from timing_profile + activity_pattern ─────────────────

def _build_time_windows(
    timing: dict | None,
    activity: dict | None,
) -> list[dict]:
    """Split the day into peak and normal windows based on hourly fractions."""
    if not timing:
        return [{"start": 0, "end": 24, "intensity": 1.0}]

    fractions = timing.get("hourly_distribution", {}).get("mean_fraction", [])
    if len(fractions) != 24:
        return [{"start": 0, "end": 24, "intensity": 1.0}]

    # Active range from activity_pattern
    active_start, active_end = 0, 23
    if activity:
        rng = activity.get("daily_shape", {}).get("active_hour_range", [0, 23])
        if len(rng) == 2:
            active_start, active_end = int(rng[0]), int(rng[1])

    avg = sum(fractions) / 24
    peak_thresh = avg * 1.5

    peak_hours = sorted(
        h for h in range(active_start, active_end + 1)
        if fractions[h] > peak_thresh
    )
    normal_hours = sorted(
        h for h in range(active_start, active_end + 1)
        if h not in peak_hours and fractions[h] > avg * 0.3
    )

    windows = []
    for hours, label in [(peak_hours, "peak"), (normal_hours, "normal")]:
        for group in _consecutive_groups(hours):
            intensity = sum(fractions[h] for h in group) / len(group) / max(avg, 0.001)
            windows.append({
                "start": group[0],
                "end": group[-1] + 1,
                "intensity": intensity,
                "label": label,
            })

    return windows if windows else [{"start": 0, "end": 24, "intensity": 1.0}]


def _consecutive_groups(hours: list[int]) -> list[list[int]]:
    if not hours:
        return []
    groups: list[list[int]] = []
    current = [hours[0]]
    for h in hours[1:]:
        if h == current[-1] + 1:
            current.append(h)
        else:
            groups.append(current)
            current = [h]
    groups.append(current)
    return groups


# ── Delays from behavior_modifiers ──────────────────────────────────────

def _build_delays(modifiers: dict | None, intensity: float = 1.0) -> tuple[int, int]:
    """Returns (delay_after_ms, delay_before_ms)."""
    if not modifiers:
        return (DEFAULT_DELAY_MS, 0)

    dwell = modifiers.get("page_dwell", {})
    min_s = dwell.get("min_seconds", 5)
    max_s = dwell.get("max_seconds", 30)

    # Higher intensity → shorter delays
    base = int(((min_s + max_s) / 2) * 1000 / max(intensity, 0.5))
    jitter = int((max_s - min_s) * 500 / max(intensity, 0.5))
    return (base, jitter)


# ── Stickiness from behavior_modifiers ──────────────────────────────────

def _build_stickiness(modifiers: dict | None) -> tuple[int, int, int]:
    """Returns (stickiness_pct, depth_min, depth_max)."""
    if not modifiers:
        return (0, 1, 10)

    nav = modifiers.get("navigation_clicks", {})
    depth_min = nav.get("min", 1)
    depth_max = nav.get("max", 5)
    stickiness = min(100, depth_max * 3)
    return (stickiness, depth_min, depth_max)


# ── Handler weights from workflow_weights ───────────────────────────────

def _build_handler_weights(ww: dict | None) -> dict[str, float]:
    if not ww:
        return {"BrowserFirefox": 0.7, "Bash": 0.2, "Curl": 0.1}

    raw = ww.get("workflow_weights", ww)
    raw = {k: v for k, v in raw.items() if k != "metadata" and isinstance(v, (int, float))}

    weights: dict[str, float] = {}
    for wf, w in raw.items():
        handler = WORKFLOW_TO_HANDLER.get(wf, "BrowserFirefox")
        weights[handler] = weights.get(handler, 0) + w

    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}
    return weights if weights else {"BrowserFirefox": 0.7, "Bash": 0.2, "Curl": 0.1}


# ── DNS noise from diversity_injection ──────────────────────────────────

def _build_dns_events(diversity: dict | None, domains: list[str]) -> list[dict]:
    if not diversity:
        return []

    bg = diversity.get("background_services", {})
    if not bg.get("enabled"):
        return []

    dns_per_hour = bg.get("dns_queries_per_hour", [])
    avg_dns = sum(dns_per_hour) / max(len(dns_per_hour), 1)
    if avg_dns < 1:
        return []

    delay_ms = int(3600000 / avg_dns)
    lookup_domains = domains[:8] if domains else ["google.com", "github.com"]

    return [
        {"Command": f"nslookup {d}", "DelayAfter": delay_ms, "DelayBefore": 0}
        for d in lookup_domains
    ]


# ── Main generator ──────────────────────────────────────────────────────

def generate_timeline(feedback_dir: Path) -> dict:
    """Read PHASE configs and produce a GHOSTS timeline dict."""
    timing = _load(feedback_dir, "timing_profile.json")
    site_config = _load(feedback_dir, "site_config.json")
    modifiers = _load(feedback_dir, "behavior_modifiers.json")
    ww = _load(feedback_dir, "workflow_weights.json")
    activity = _load(feedback_dir, "activity_pattern.json")
    diversity = _load(feedback_dir, "diversity_injection.json")

    urls = _build_urls(site_config)
    windows = _build_time_windows(timing, activity)
    stickiness, depth_min, depth_max = _build_stickiness(modifiers)
    handler_weights = _build_handler_weights(ww)
    domains = list((site_config or {}).get("domain_categories", {}).keys())

    handlers = []

    # ── Browser handlers (one per time window) ──────────────────────────
    for window in windows:
        delay_after, delay_before = _build_delays(modifiers, window.get("intensity", 1.0))

        handlers.append({
            "HandlerType": "BrowserFirefox",
            "Initial": "about:blank",
            "UtcTimeOn": f"{window['start']:02d}:00:00",
            "UtcTimeOff": f"{window['end']:02d}:00:00",
            "Loop": True,
            "HandlerArgs": {
                "isheadless": "true",
                "blockimages": "true",
                "blockstyles": "false",
                "blockflash": "true",
                "blockscripts": "true",
                "stickiness": stickiness,
                "stickiness-depth-min": depth_min,
                "stickiness-depth-max": depth_max,
            },
            "TimeLineEvents": [{
                "Command": "random",
                "CommandArgs": urls,
                "DelayAfter": delay_after,
                "DelayBefore": delay_before,
            }],
        })

    # ── Bash handler ────────────────────────────────────────────────────
    bash_weight = handler_weights.get("Bash", 0.2)
    if bash_weight > 0.05:
        bash_delay = int(300000 / max(bash_weight * 5, 0.5))
        dns_events = _build_dns_events(diversity, domains)

        bash_events = [
            {"Command": cmd, "DelayAfter": bash_delay, "DelayBefore": 0}
            for cmd in random.sample(BASH_COMMANDS, min(5, len(BASH_COMMANDS)))
        ]
        bash_events.extend(dns_events)

        handlers.append({
            "HandlerType": "Bash",
            "Initial": "",
            "UtcTimeOn": "00:00:00",
            "UtcTimeOff": "24:00:00",
            "Loop": True,
            "TimeLineEvents": bash_events,
        })

    # ── Curl handler ────────────────────────────────────────────────────
    curl_weight = handler_weights.get("Curl", 0.1)
    if curl_weight > 0.05:
        curl_delay = int(60000 / max(curl_weight * 5, 0.5))
        curl_targets = [f"https://{d}" for d in (domains[:5] if domains else ["httpbin.org/get"])]

        handlers.append({
            "HandlerType": "Curl",
            "Initial": "",
            "UtcTimeOn": "00:00:00",
            "UtcTimeOff": "24:00:00",
            "Loop": True,
            "TimeLineEvents": [
                {"Command": url, "DelayAfter": curl_delay, "DelayBefore": 0}
                for url in curl_targets
            ],
        })

    return {"Status": "Run", "TimeLineHandlers": handlers}


# ── CLI entry point ─────────────────────────────────────────────────────

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: phase_to_timeline.py <feedback_dir> [--output file.json]", file=sys.stderr)
        return 1

    feedback_dir = Path(sys.argv[1])
    if not feedback_dir.is_dir():
        print(f"Not a directory: {feedback_dir}", file=sys.stderr)
        return 1

    output_path = None
    if len(sys.argv) > 3 and sys.argv[2] == "--output":
        output_path = Path(sys.argv[3])

    timeline = generate_timeline(feedback_dir)
    result = json.dumps(timeline, indent=2)

    if output_path:
        output_path.write_text(result + "\n")
        print(f"Written: {output_path} ({len(timeline['TimeLineHandlers'])} handlers)")
    else:
        print(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
