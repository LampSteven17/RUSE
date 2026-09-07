# VideoViewing: direct HLS replacement

## Contract delta

VideoViewing uses new symbolic IDs with this resource shape:

```json
{
  "workflow": "VideoViewing",
  "kind": "hls_video",
  "url": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
  "play_seconds": 300
}
```

The exact resource fields are `workflow`, `kind`, `url`, and `play_seconds`.
The URL must be HTTPS with a host and without embedded credentials. Duration
remains exactly 300 seconds. Old YouTube resource IDs are not aliases and are
not reinterpreted. Historical plans must remain unchanged; old IDs fail loading
against this new catalog and require a newly generated plan for this runtime.

Both `controls-v2` and `feedback-v2` contain the same candidate pool:

| New resource ID | Assigned URL |
| --- | --- |
| `video_hls_mux_bbb` | https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8 |
| `video_hls_apple_bipbop` | https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_ts/master.m3u8 |
| `video_hls_unified_tos` | https://demo.unified-streaming.com/k8s/features/stable/video/tears-of-steel/tears-of-steel.ism/.m3u8 |

Qualification results must be read alongside this catalog; availability is not
permission for unlimited load. No fallback between these resources occurs.

## Exact PHASE-side changes needed

1. Update VideoViewing resources in both resource-profile catalogs to the new
   IDs/shapes above, using the same qualified pool for Control and Feedback.
   Preserve all non-video resources.
2. Change the MCHP capability's VideoViewing `resource_kinds` from
   `["youtube_video"]` to `["hls_video"]`. If PHASE separately validates resource
   shapes, replace `video_id` with the required HTTPS `url` for this kind.
3. Emit new generations referencing these new IDs. Do not rewrite historical
   generations or silently map old IDs at runtime.
4. **No schedule-schema change is needed:** entries already reference symbolic
   `resource_id`. **No instruction change is needed:** retain exactly
   `Open the assigned video and play it for five minutes.` for BrowserUse and
   SmolAgents; Scripted/MCHP continue forbidding instructions.

This does not settle PHASE's independent attribution question about expanding
`responder_heavy_transfer` into VideoViewing and FileDownload. RUSE does not
change that mapping, workflow selection, frequencies, timing or concurrency.

## Runtime implementation

- Scripted retains Selenium Chromium; MCHP retains its independently owned
  Firefox driver. BrowserUse retains the existing LLM-invoked bounded playback
  action and owned Playwright browser. No additional browser/session is added.
- MCHP installs Noble's `libavcodec60` without recommended packages. Live Firefox
  inspection demonstrated that the existing CPU installation lacked H.264
  decoding (`MediaSource.isTypeSupported` was false); adding the system decoder
  enabled it without GPU drivers or a service restart.
- Each browser loads one packaged `file:` HTML page. It uses native HLS when
  available, otherwise the locally bundled **hls.js 1.7.2** distribution; no
  runtime script CDN or local HTTP service is required.
- The immutable URL is assigned once. The page does not autoplay or loop.
  hls.js request retries are disabled; fatal player errors are latched, not
  recovered. Exactly one `play()` is called after bounded readiness.
- Existing start advancement, assigned wait, final error inspection and browser
  cleanup are retained. Success means playback started and no explicit failure
  was detected at the final inspection, **not uninterrupted playback proof**.
- SmolAgents still must invoke its zero-argument tool. FFmpeg consumes the
  assigned HLS URL directly with `-re`, `-t 300` and the existing 360-second
  process timeout. It must exit successfully. Progress is retained for inspection,
  not compared to an invented duration tolerance: its final timestamp describes
  the last packet, not that packet's end. No yt-dlp extraction, retries or
  alternate URL. This retains the existing FFmpeg success boundary; it does not
  add a proof against every possible early clean EOF from a provider.
- BrowserUse still requires the judge's successful result and exactly one
  successful action; SmolAgents prose/missing/repeated actions remain failures.
  Cancellation remains failure and worker cleanup precedes terminal/slot reuse.
  Live HLS qualification exposed that pinned BrowserUse's `is_successful()`
  reports the action's claim, not the judge verdict. RUSE now also requires
  `is_validated() is True`; a failed or missing verdict cannot become completed.

## Deployment boundary

Only existing isolated canary installations were eligible for this validation.
Production services, plans and PHASE files are not changed. Before deploying this
runtime to new fleets, PHASE must publish matching new resource references.
The existing recursive installer source copy packages the HTML, library and
license without a new installation stage. Roll back code and its matching
catalog together; never pair old YouTube plans with this HLS-only loader.
