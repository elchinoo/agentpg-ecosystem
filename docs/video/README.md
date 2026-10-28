# Tutorial video: recorder and build

One command builds the review cut from `narration.txt` and the real offline commands:

```bash
python3 docs/video/build.py --work /some/scratch/dir --deliver /where/to/copy     # --name ecosystem --voice am_michael --speed 0.95
```

Output: `<work>/ecosystem.mp4` (1920x1080, 30 fps, H.264 yuv420p, AAC, soft `mov_text` captions) and `<work>/ecosystem.srt` (written text, timed from
the narration). Everything is local. `--keep-audio` reuses `<work>/audio` (speech takes about 3.5 minutes); `--sync-script` rewrites the narration
column of `../VIDEO-SCRIPT.md` from `narration.txt` and exits.

Needs: macOS, `python3` with Pillow, `ffmpeg`/`ffprobe`, the repo `.venv` (for `ecoctl`), and the Kokoro venv and models in `../../../videos/tts`
(nothing is installed or downloaded). Not committed: `out/` is git-ignored.

Files
- `narration.txt`: one `## <scene id> <voice>` section (voice optional, default `--voice`; one voice per topic: 1-2 am_michael, 3-4 af_heart, 5-8 am_eric, 9-11 af_bella, 12 am_michael) per scene of `VIDEO-SCRIPT.md`. Blank line = pause, `...` = beat, `[pause 1.5]` = deliberate pause over something happening on screen.
- `pronounce.json`: respellings for the voice only (captions keep the written text); merged over `videos/tts/pronounce.json`.
- `build.py`: narration -> capture -> render -> mux -> checks. Scene definitions (which slide or terminal beat appears at which paragraph) are in it.
- `record.py`: runs the real offline commands in throw-away directories under `<work>/capture` (empty `.ecosystem/`, or a made-up fixture state for `recreate`; the real fleet state is never opened, no call to AgentPG) and replays the output on a terminal canvas paced to the narration. Run on its own (`record.sh`) it writes an asciinema cast and the transcripts to `out/`.
- `slides.py`: Pillow drawing of the interaction diagram (GUIDE section 3), the manifest editor view, step/flow slides and the end card.

Timing: every scene lasts audio + 0.7 s (0.35 s lead, 0.35 s tail); keyframes are placed at paragraph or sentence starts of the narration, typing and output scroll are
spread over the sentences that describe them. Long output lines are wrapped at word boundaries with an indent for legibility.

Live scenes (6 to 11) are slides in this cut, never invented terminal output; see "Review cut" in `../VIDEO-SCRIPT.md` for what needs a real capture.
