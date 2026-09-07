# Packaged video page

`video.html` loads the immutable assigned HTTPS HLS URL. Native HLS is used
where supported; otherwise the local hls.js build is used. No runtime CDN fetch.

`hls-1.7.2.min.js` is the unmodified hls.js 1.7.2 distribution from
https://cdn.jsdelivr.net/npm/hls.js@1.7.2/dist/hls.min.js.
Upstream release: https://github.com/video-dev/hls.js/releases/tag/v1.7.2.
The bundled upstream license is `hls-LICENSE`.

The existing recursive phase_workflow installer copy includes these assets.
Playback is invoked once by the owning workflow after readiness; the page does
not initiate playback itself. Fatal errors are latched for setup/final checks.
