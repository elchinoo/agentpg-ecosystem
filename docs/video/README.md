# Tutorial video: recorder and build

One command builds the review cut from `narration.txt` and the real offline commands:

```bash
python3 docs/video/build.py --work /some/scratch/dir --deliver /where/to/copy     # --name ecosystem --voice am_liam --speed 1.08
```

Output: `<work>/ecosystem.mp4` (1920x1080, 30 fps, H.264 yuv420p, AAC, soft `mov_text` captions) and `<work>/ecosystem.srt` (written text, timed from
the narration). Everything is local. `--keep-audio` reuses `<work>/audio` (speech takes about 4 minutes); `--sync-script` rewrites the narration
column of `../VIDEO-SCRIPT.md` from `narration.txt` and exits.

Needs: macOS, `python3` with Pillow, `ffmpeg`/`ffprobe`, the repo `.venv` (for `ecoctl`), and the Kokoro venv and models in `../../../videos/tts`
(nothing is installed or downloaded). Not committed: `out/` is git-ignored.

Files
- `narration.txt`: one `## <scene id> <voice>` section per scene of `VIDEO-SCRIPT.md`. Three voices only, alternating male and female, never the same voice twice in a row: am_liam (opener), af_heart, am_eric; `--voice` (default am_liam) only applies to a section without a voice. Blank line = pause, `...` = beat, `[pause 1.5]` = a deliberate pause (the shared tool halves it, cap 1.5 s). Scene 6 (Ansible) states the verified complete eight-app apply (29 Oct 2026) and that the hosts target and recreate on a real fleet are not verified.
- `pronounce.json`: respellings for the voice only (captions keep the written text); merged over `videos/tts/pronounce.json`.
- `build.py`: narration -> capture -> render -> mux -> checks. Scene definitions (which slide or terminal beat appears at which paragraph) are in it.
- `record.py`: runs the real offline commands in throw-away directories under `<work>/capture` (empty `.ecosystem/`, or a made-up fixture state for `recreate`; the real fleet state is never opened, no call to AgentPG) and replays the output on a terminal canvas paced to the narration. Run on its own (`record.sh`) it writes an asciinema cast and the transcripts to `out/`.
- `slides.py`: Pillow drawing of the interaction diagram (GUIDE section 3), the manifest editor view, step/flow slides and the end card.

Timing: every scene lasts audio + 0.7 s (0.35 s lead, 0.35 s tail); keyframes are placed at paragraph or sentence starts of the narration, typing and output scroll are
spread over the sentences that describe them. Long output lines are wrapped at word boundaries with an indent for legibility.

Scenes 9 to 11 show real stills from `../../../videos/live-captures` (CityPulse, TrustDesk REQ-000016, Database pages, `ecoctl insights` text), tagged "CAPTURED from the live AgentPG fleet, 28 Oct 2026". Scene 6 shows the real offline output of `ansible-playbook <playbook> --syntax-check` (run on a copy of `ansible/` in the work directory). Scenes 7 and 8 and the rest of 6 are slides tagged "SLIDE": never invented terminal output. See "Review cut" in `../VIDEO-SCRIPT.md`.

Static pictures: `build.py` (`break_static`) flips any keyframe lasting 2 s or more between the slide and a copy tinted 4 % toward amber, and again every 5.5 s inside a longer gap, so freezedetect finds no frozen stretch over about 8 s.
