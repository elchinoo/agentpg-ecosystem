# Components video: sources and build

A customer-facing video (about eight minutes) about the reusable blocks of the AgentPG ecosystem: what each one solves, how to take only that
block, what the apps use it for and what you would otherwise build. The catalogue it presents is [../COMPONENTS.md](../COMPONENTS.md).

Why this folder is in the ecosystem repository and not in `agentpg-kit/docs/video`: the kit's folder is the Playwright recorder of the "build an app
in ten minutes" tutorial (same file names, a different pipeline). This video re-uses the ecosystem's Pillow slides and terminal replay
(`../video/slides.py`, `record.py`, `build.py`).

```bash
python3 docs/video-components/build.py --work /some/scratch/dir --deliver /where/to/copy      # --name components
```

Output: `<work>/components.mp4` (1920x1080, 30 fps, H.264 yuv420p crf 18, AAC, soft `mov_text` captions) and `<work>/components.srt`.
`--keep-audio` and `--keep-capture` reuse the previous speech and the previous real runs; `--sync-script` rewrites the "Components video" section of
`../VIDEO-SCRIPT.md` from `narration.txt`.

## What is real

`capture.py` runs the real code in a throw-away environment under the work folder (it deletes only that folder) and records the output:

| Step | What runs |
|---|---|
| install | `uv pip install ../agentpg-kit` into a fresh virtualenv, then `import apgkit` |
| authentication | `demos/protect.py` under uvicorn: `Authenticator.require("things:read")` with `LocalAuth` API tokens (401, 403, 200) |
| identity | the real `identity` service on its own database; a service client gets a token with `TokenSource`; the same file accepts it when `DEMO_OIDC_ISSUER` is set; a changed token is refused |
| vault, database | `Vault`, `Database.connect` retrying while a second cluster starts 2.5 s late, `migrate.run`, `settings_store`, pgvector |
| LLM | `LLMClient` and `NetGuard` against the real `llm-gateway` (its built-in echo model); the gateway console and the identity console in Playwright |
| security | `SecurityGuard` headers, CSRF, body cap, metrics; the failed-login limiter; `masking.mask_view` with a role named `a1p_readonly`; PayBroker's `007_audit_ledger.sql` |
| patterns | ShopFront's `webhooks.py`, CityPulseSimulator's `realcities.py`, the notes example app |

Not run locally: Insights, the CityPulse map and the live-fleet masking view need the EDB extensions or the live fleet. They are stills from
`videos/live-captures` (28 and 29 Oct 2026) and are labelled CAPTURED. Frames of our own app videos are labelled LOCAL, drawn slides SLIDE.

No secret is printed: keys are generated per run, passed by environment or file, and the OCR scan of the final frames found none.
The tokens in the terminal scenes are read from files (`$(cat read.token)`), never echoed.

## Files

- `narration.txt`: `## <scene> <voice>` sections. Voices: am_liam (opener), af_heart, am_eric; male and female alternate, no voice twice in a row.
- `pronounce.json`: voice-only respellings (captions keep the written text), verified with the phonemizer against letter names.
- `build.py`: narration, capture, render, mux, checks. Scenes are functions; keyframes are tied to paragraphs, sentences and phrases of the narration.
- `comp.py`: code cards, terminal renderer, catalogue and list slides.
- `capture.py`, `shots.mjs`, `demos/`: the real runs and the files they run (the code cards show these files as they are).

Stillness and encoding follow the ecosystem build: nothing pans or zooms; a picture is held and long sentences are broken up by reveals and highlights;
every scene lasts audio + 0.7 s; x264 with no B-frames and an I-frame at every picture change.
