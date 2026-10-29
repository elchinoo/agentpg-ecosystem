# Tutorial video: recorder and build

One command builds the review cut from `narration.txt` and the real offline commands:

```bash
python3 docs/video/build.py --work /some/scratch/dir --deliver /where/to/copy     # --name ecosystem --voice am_liam --speed 1.0
```

Output: `<work>/ecosystem.mp4` (1920x1080, 30 fps, H.264 yuv420p, AAC, soft `mov_text` captions) and `<work>/ecosystem.srt` (written text, timed from
the narration). Everything is local. `--keep-audio` reuses `<work>/audio` (speech takes about 4 minutes); `--sync-script` rewrites the narration
column of `../VIDEO-SCRIPT.md` from `narration.txt` and exits.

Needs: macOS, `python3` with Pillow, `ffmpeg`/`ffprobe`, the repo `.venv` (for `ecoctl`), and the Kokoro venv and models in `../../../videos/tts`
(nothing is installed or downloaded) and the stills in `../../../videos/live-captures/` (including `app-video-frames/`). Not committed: `out/` is git-ignored.

Files
- `narration.txt`: one `## <scene id> <voice>` section per scene of `VIDEO-SCRIPT.md` (apps first: hook, map, one scene per app, integration, platform features, AIDB roadmap, deploy, recap). Three voices only, alternating male and female, never the same voice twice in a row: am_liam (opener), af_heart, am_eric. Speed 1.0 (the default of `build.py`; this video does not use the shared 1.08). Blank line = pause, `...` = beat, `[pause 1.5]` = a deliberate pause (the shared tool halves it, cap 1.5 s).
- `pronounce.json`: respellings for the voice only (captions keep the written text); merged over `videos/tts/pronounce.json`.
- `build.py`: narration -> capture -> render -> mux -> checks. Scene definitions (which slide or terminal beat appears at which paragraph) are in it.
- `record.py`: runs the real offline commands in throw-away directories under `<work>/capture` (empty `.ecosystem/`, or a made-up fixture state for `recreate`; the real fleet state is never opened, no call to AgentPG) and replays the output on a terminal canvas paced to the narration. Run on its own (`record.sh`) it writes an asciinema cast and the transcripts to `out/`.
- `slides.py`: Pillow drawing of the interaction diagram (GUIDE section 3), the manifest editor view, step/flow slides and the end card.

Timing: every scene lasts audio + 0.7 s (0.35 s lead, 0.35 s tail); keyframes are placed at paragraph or sentence starts of the narration, typing and output scroll are
spread over the sentences that describe them. Long output lines are wrapped at word boundaries with an indent for legibility.

Pictures: CAPTURED stills from `../../../videos/live-captures` (tag "CAPTURED from the live AgentPG fleet, 28 Oct 2026"), LOCAL frames from our own app videos in `../../../videos/live-captures/app-video-frames/` (tag "LOCAL"), slides (tag "SLIDE" or "ROADMAP"), and the real offline output of `ecoctl validate`. See `../VIDEO-SCRIPT.md`.

Stillness: slides and stills are never moved or rescaled after they are drawn (a still smaller than the slide is scaled once, with lanczos, into the slide canvas). `build.py` holds each picture for its whole keyframe at a constant 30 fps and breaks long sentences up with reveals, highlights, crops or caption changes (`staged` accepts a key `(paragraph, sentence, fraction)` for a change part-way through a sentence). The mux uses x264 `crf 18`, `-bf 0`, `mbtree=0:aq-mode=0:scenecut=0` and forces an I-frame at every picture change, so a held picture does not shimmer.
