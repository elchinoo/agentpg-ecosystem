#!/usr/bin/env python3
"""Terminal recorder for docs/VIDEO-SCRIPT.md: the offline scenes (2, 4, 5 and the first half of 12).

Two uses:

1. `docs/video/record.sh` (this file as a script): copies ecosystem.yaml into a throw-away directory, links the repo's ecoctl as
   `.venv/bin/ecoctl` there, runs the real commands with that directory as the working directory (so ecoctl's default `.ecosystem/` is
   the empty temporary one, never the real fleet's), answers the two confirmation prompts with `n`, and writes to docs/video/out/
   (git-ignored): `offline.cast` (asciinema v2) and `transcripts/*.txt` (the exact output of each command, what the script quotes).
2. `build.py` imports capture() and the *_frames() functions below: the same captured output is replayed on a 1920x1080 terminal
   canvas whose typing and line-by-line output are paced to the narration (each scene's hold is audio + 0.7 s, set by build.py).

Nothing here calls AgentPG, the identity provider or the gateway: `validate`, `plan`, the dry run and the cancelled prompts only read the
manifest and the state file. The recreate scene needs a state file that lists apps as deployed, so it gets a FIXTURE state with
made-up sandbox ids and no secrets; the command is cancelled at its prompt, before any `agentpg` call. The real .ecosystem/state.json
of the fleet is never opened.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = HERE / "out"
ECOCTL = REPO / ".venv" / "bin" / "ecoctl"

# (scene, slug, typed command, stdin answer or None, state: "empty" | "fixture")
STEPS = [
    (10, "validate", ".venv/bin/ecoctl validate", None, "empty"),
]

# terminal canvas (1920x1080): geometry shared by the renderer and by capture() (the plan is wrapped to the same column count)
CHROME, FOOT, PAD, LH, FS = 56, 1038, 44, 32, 22


def _geometry(fs=FS):
    sys.path.insert(0, str(HERE))
    import slides
    cw, lh = slides.font("mono", fs).getlength("M"), int(fs * 1.45)
    return int((slides.W - 2 * PAD) // cw), int((FOOT - CHROME - 40) // lh), cw, lh


def fixture_state(directory: Path) -> None:
    """Fake ids, no secrets: just enough for `recreate` to see which apps count as deployed."""
    apps = {k: {"sandbox_id": f"sbx-fixture-{i:02d}", "url": f"https://{k}.example.invalid"} for i, k in
            enumerate(["identity", "llmgw", "paybroker", "cpsim", "shopfront"], 1)}
    directory.mkdir(mode=0o700)
    (directory / "state.json").write_text(json.dumps({"apps": apps, "identity_admin_key": None}))


def run(cmd: str, stdin: str | None, work: Path, cols: int) -> tuple[str, int]:
    env = {**os.environ, "COLUMNS": str(cols), "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"}
    p = subprocess.run(cmd, shell=True, cwd=work, env=env, input=(stdin + "\n") if stdin else "", text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    return p.stdout, p.returncode


def capture(root: Path, cols: int = 110) -> dict:
    """Run every STEPS command for real, each in a fresh directory under `root` (which the caller owns).
    Returns {slug: {scene, cmd, stdin, text, code}}."""
    if not ECOCTL.exists():
        raise SystemExit(f"{ECOCTL} not found: run `uv venv && uv pip install -e \".[dev]\"` first")
    res = {}
    for scene, slug, cmd, stdin, state in STEPS:
        work = root / slug
        if state == "ansible":                              # a copy of ansible/ (no .venv, no .secrets): --syntax-check parses only
            shutil.copytree(REPO / "ansible", work / "ansible", ignore=shutil.ignore_patterns(".venv", ".secrets", "__pycache__"))
            shutil.copy(REPO / "ecosystem.yaml", work / "ecosystem.yaml")
            text, code = run(cmd, stdin, work / "ansible", cols)
            if code != 0:
                raise SystemExit(f"{slug} failed (exit {code}):\n{text}")
            res[slug] = {"scene": scene, "cmd": cmd, "stdin": stdin, "text": text, "code": code}
            continue
        (work / ".venv" / "bin").mkdir(parents=True)
        shutil.copy(REPO / "ecosystem.yaml", work / "ecosystem.yaml")
        (work / ".venv" / "bin" / "ecoctl").symlink_to(ECOCTL)
        if state == "fixture":
            fixture_state(work / ".ecosystem")
        text, code = run(cmd, stdin, work, cols)
        if state == "empty" and (work / ".ecosystem").exists():
            raise SystemExit(f"{slug} created {work / '.ecosystem'}: it must be read-only")
        res[slug] = {"scene": scene, "cmd": cmd, "stdin": stdin, "text": text, "code": code}
    return res


# ------------------------------------------------------------------------------------------------ legacy asciinema output
class Cast:
    def __init__(self):
        self.t, self.events = 0.0, []

    def wait(self, s: float) -> None:
        self.t += s

    def out(self, text: str) -> None:
        self.events.append([round(self.t, 3), "o", text.replace("\n", "\r\n")])

    def type(self, text: str) -> None:
        for ch in text:
            self.out(ch)
            self.wait(0.045)

    def write(self, path: Path, cols: int) -> None:
        head = {"version": 2, "width": cols, "height": 30, "timestamp": int(time.time()), "title": "ecoctl offline segments",
                "env": {"SHELL": "/bin/zsh", "TERM": "xterm-256color"}}
        path.write_text("\n".join([json.dumps(head)] + [json.dumps(e) for e in self.events]) + "\n")


def main() -> int:
    import tempfile
    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "transcripts").mkdir(parents=True)
    cols = 110
    with tempfile.TemporaryDirectory(prefix="ecoctl-rehearsal-") as tmp:
        caps = capture(Path(tmp), cols)
    cast = Cast()
    for slug, c in caps.items():
        cast.wait(1.5)
        cast.out("$ ")
        cast.type(c["cmd"])
        cast.out("\n")
        body, _, last = c["text"].partition("Continue? [y/N] ")
        for ln in body.splitlines(keepends=True):
            cast.out(ln)
            cast.wait(0.03)
        if c["stdin"]:
            cast.out("Continue? [y/N] ")
            cast.wait(1.5)
            cast.type(c["stdin"])
            cast.out("\n" + last.lstrip("\n"))
        cast.wait(1.5)
        (OUT / "transcripts" / f"scene{c['scene']:02d}-{slug}.txt").write_text(
            f"$ {c['cmd']}\n{c['text']}" + (f"[typed: {c['stdin']}]\n" if c["stdin"] else "") + f"[exit {c['code']}]\n")
    cast.write(OUT / "offline.cast", cols)
    print(f"wrote {OUT}/ ({len(cast.events)} events, {cast.t:.0f} s of cast time)")
    return 0


# ------------------------------------------------------------------------------------------------ terminal canvas renderer
class TermScript:
    """A timeline of terminal operations; replay() turns it into keyframes (t, image)."""

    def __init__(self):
        self.ev, self.n = [], 0

    def at(self, t, op, arg=None):
        self.ev.append((round(t, 3), self.n, op, arg))
        self.n += 1

    def clear(self, t):
        self.at(t, "clear")

    def type_cmd(self, t, cmd, dur):
        """New prompt line, the command typed over `dur` seconds; returns the end time."""
        self.at(t, "line", ("$ ", "cmd"))
        for i, ch in enumerate(cmd):
            self.at(t + dur * (i + 1) / len(cmd), "char", ch)
        return t + dur

    def inline(self, t, text, dur=0.25):
        for i, ch in enumerate(text):
            self.at(t + dur * (i + 1) / len(text), "char", ch)

    def reveal(self, lines, checkpoints):
        """lines appear following checkpoints [(t, n_visible)], linear in between."""
        for (ta, na), (tb, nb) in zip(checkpoints, checkpoints[1:], strict=False):
            for k in range(na, nb):
                self.at(ta + (tb - ta) * (k - na + 1) / max(nb - na, 1), "line", (lines[k], "out"))

    def replay(self, render, until):
        times = sorted({e[0] for e in self.ev} | {0.0})
        order = sorted(self.ev, key=lambda e: (e[0], e[1]))
        state = {"lines": [], "hl": (), "badge": None, "overlay": None}
        i, frames = 0, []
        for t in times:
            while i < len(order) and order[i][0] <= t:
                _, _, op, arg = order[i]
                if op == "clear":
                    state["lines"] = []
                elif op == "line":
                    state["lines"].append([arg[0], arg[1]])
                elif op == "char":
                    state["lines"][-1][0] += arg
                elif op in ("hl", "badge", "overlay"):
                    state[op] = arg
                i += 1
            if t <= until:
                frames.append((t, render(state)))
        return frames


STEP = re.compile(r"^\s*\d+\.\s")
TOKENS = re.compile(r"(\*\*\*|<[^>]+>|ok:|Cancelled\.|Dry run:|Nothing was changed\.|Continue\? \[y/N\])")


def _segments(text, kind):
    import slides as S
    if kind == "cmd":
        return [(text[:2], S.GREEN, True), (text[2:], S.TXT, False)]
    if STEP.match(text):
        return [(text, S.TXT, True)]
    segs = []
    for part in TOKENS.split(text):
        if not part:
            continue
        col = (S.AMBER, True) if part == "***" else (S.TEAL, False) if part.startswith("<") else (S.GREEN, True) \
            if part in ("ok:", "Dry run:", "Nothing was changed.") else (S.AMBER, True) if part in ("Cancelled.", "Continue? [y/N]") \
            else ((200, 204, 212), False)
        segs.append((part, col[0], col[1]))
    return segs


def make_renderer(title="ecoctl", overlay_draw=None, fs=FS):
    import slides as S
    from PIL import Image, ImageDraw
    cols, rows, cw, LH = _geometry(fs)
    mono, monob = S.font("mono", fs), S.font("mono", fs, True)

    def render(state):
        img = Image.new("RGB", (S.W, S.H), (22, 24, 29))
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, S.W, CHROME), fill=(38, 41, 49))
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse((24 + i * 32, 18, 44 + i * 32, 38), fill=c)
        d.text((S.W // 2, CHROME // 2), title, font=S.font("sans", 22), fill=S.MUTED, anchor="mm")
        if state["badge"]:
            f = S.font("sans", 20, True)
            tw = f.getlength(state["badge"]) + 32
            S.rrect(d, (S.W - 30 - tw, 12, S.W - 30, 44), outline=S.AMBER, width=2, r=16)
            d.text((S.W - 30 - tw / 2, 28), state["badge"], font=f, fill=S.AMBER, anchor="mm")
        d.line((0, FOOT, S.W, FOOT), fill=S.PANEL2, width=2)
        d.text((60, 1060), "The AgentPG ecosystem  |  ecoctl", font=S.font("sans", 20), fill=S.FAINT, anchor="lm")
        d.text((S.W - 60, 1060), "Synthetic data, test cards only", font=S.font("sans", 20), fill=S.FAINT, anchor="rm")
        disp = []                                           # (text, kind, logical index)
        for li, (text, kind) in enumerate(state["lines"]):
            lead = len(text) - len(text.lstrip())
            parts = textwrap.wrap(text, cols, subsequent_indent=" " * (lead + 4), replace_whitespace=False,
                                  break_long_words=True, break_on_hyphens=False) or [""]
            if kind == "cmd":
                parts = [text]
            disp += [(p, kind, li) for p in parts]
        disp = disp[-rows:]
        y = CHROME + 20
        for text, kind, li in disp:
            if li in state["hl"]:
                d.rectangle((14, y - 3, S.W - 14, y + LH - 5), fill=S.mix(S.AMBER, (22, 24, 29), 0.18))
            x = PAD
            for seg, col, bold in _segments(text, kind):
                d.text((x, y), seg, font=monob if bold else mono, fill=col)
                x += len(seg) * cw
            y += LH
        if disp:                                            # block cursor after the last row
            d.rectangle((PAD + len(disp[-1][0]) * cw, y - LH + 3, PAD + len(disp[-1][0]) * cw + cw * 0.7, y - 5), fill=(200, 204, 212))
        if isinstance(state["overlay"], str):               # a caption panel over the lower part of the terminal
            S.rrect(d, (120, 880, S.W - 120, 1000), fill=(30, 34, 44), outline=S.AMBER, width=3)
            for i, ln in enumerate(S.wrap(state["overlay"], S.font("sans", 36), S.W - 320)[:2]):
                d.text((160, 940 if i == 0 and len(S.wrap(state["overlay"], S.font("sans", 36), S.W - 320)) == 1 else 915 + i * 50), ln,
                       font=S.font("sans", 36), fill=S.TXT, anchor="lm")
        elif state["overlay"] and overlay_draw:
            overlay_draw(img, d, state["overlay"])
        return img

    return render


def _split(text):
    """Output lines before the prompt, and whether the command ended at a `Continue? [y/N]` prompt."""
    body, sep, last = text.partition("Continue? [y/N] ")
    return body.rstrip("\n").split("\n") if body.strip() else [], bool(sep), last.strip()


def _checkpoints(a, b, lines, marks):
    """Step markers: `marks` maps a time to the step number whose title must be visible by then (all lines above it)."""
    idx = {int(m.group(1)): i for i, ln in enumerate(lines) if (m := re.match(r"\s*(\d+)\.\s", ln))}
    idx[max(idx) + 1] = len(lines)
    cps = [(a, 0)]
    for t, upto in marks:
        cps.append((t, idx[upto] if upto in idx else len(lines)))
    cps[-1] = (cps[-1][0], len(lines))
    return cps, idx


def validate_frames(A, cap, save):
    import slides as S
    c = cap["validate"]
    lines, _, _ = _split(c["text"])
    items = ["The agentpg command line tool, logged in", "uv, with Python 3.11", "Container images AgentPG can pull (public)",
             "Install: uv venv  and  uv pip install -e \".[dev]\""]

    def overlay(img, d, n):
        S.rrect(d, (360, 190, 1560, 190 + 150 + 62 * n), fill=(30, 34, 44), outline=S.AMBER, width=3)
        d.text((410, 232), "Before you start", font=S.font("sans", 36, True), fill=S.AMBER, anchor="lm")
        for i in range(n):
            S.chip(d, (420, 305 + i * 62), i + 1)
            d.text((460, 305 + i * 62), items[i], font=S.font("sans", 32), fill=S.TXT, anchor="lm")

    ts = TermScript()
    ts.at(0, "line", ("$ ", "cmd"))                         # an idle prompt while the prerequisites are on screen
    for k, t in enumerate([A.cs(1, 0), A.cs(1, 1), A.cs(1, 2), A.cs(2, 0)]):
        ts.at(t, "overlay", k + 1)
    t0 = A.cs(2, 1)
    ts.at(t0 - 0.05, "overlay", None)
    ts.inline(t0, c["cmd"], 1.0)
    tout = t0 + 1.35
    for ln in lines:
        ts.at(tout, "line", (ln, "out"))
    ts.at(tout, "line", ("$ ", "cmd"))
    ts.at(tout + 0.1, "hl", (1,))
    ts.at(A.ps(3), "overlay", "Gateway first: the others depend on it")
    ts.at(A.cs(3, 1), "overlay", "pagerlens-agent: registered here, but it runs outside AgentPG")
    ts.at(A.ps(4), "overlay", "The creation order is worked out by ecoctl")
    return [(t, save(img)) for t, img in ts.replay(make_renderer(overlay_draw=overlay, fs=26), A.D)]


def plan_frames(A, cap, save):
    c = cap["plan-only-trustdesk"]
    lines, _, _ = _split(c["text"])
    ts = TermScript()
    ts.at(0, "badge", "plan = read-only")
    ts.at(0, "line", ("$ ", "cmd"))
    ts.inline(A.ps(0) + 0.3, c["cmd"], 1.6)
    marks = [(A.pe(1), 6), (A.pe(2), 7), (A.pe(3), 9), (A.pe(4), 99)]
    cps, _ = _checkpoints(A.ps(1), A.pe(4), lines, marks)
    ts.reveal(lines, cps)
    return [(t, save(img)) for t, img in ts.replay(make_renderer(), A.D)]


def apply_frames(A, cap, save, until=None):
    c1, c2 = cap["apply-dry-run"], cap["apply-execute-cancel"]
    l1, _, _ = _split(c1["text"])
    l2, prompt, last = _split(c2["text"])
    ts = TermScript()
    ts.at(0, "badge", "dry run: nothing is changed")
    ts.at(0, "line", ("$ ", "cmd"))
    t = A.ps(0) + 0.2
    ts.inline(t, c1["cmd"], 1.4)
    te = t + 1.7
    end = max(min(A.pe(0) - 0.4, te + 3.4), te + 1.5)
    ts.reveal(l1, [(te, 0), (end, len(l1))])
    t = A.ps(1)
    ts.clear(t)
    ts.at(t, "badge", "execute: asks before it spends")
    te = ts.type_cmd(t + 0.1, c2["cmd"], 1.7) + 0.3
    end = max(A.pe(1) - 0.4, te + 2.0)
    ts.reveal(l2, [(te, 0), (end, len(l2))])
    ts.at(end + 0.2, "line", ("Continue? [y/N] ", "out"))
    ts.inline(A.pe(1) + 0.45, c2["stdin"], 0.2)
    ts.at(A.pe(1) + 0.95, "line", (last or "Cancelled.", "out"))
    ts.at(A.pe(1) + 0.95, "badge", "answered n: nothing was created")
    return [(t, save(img)) for t, img in ts.replay(make_renderer(), until or A.D)]


def recreate_frames(A, cap, save, until):
    c = cap["recreate-cancel"]
    lines, prompt, last = _split(c["text"])
    ts = TermScript()
    ts.at(0, "badge", "sample state: made-up sandbox ids")
    ts.at(0, "line", ("$ ", "cmd"))
    ts.inline(A.ps(0) + 0.9, c["cmd"], 1.5)
    te = A.ps(0) + 2.7
    ts.reveal(lines, [(max(A.ps(1), te) + 0.2, 0), (max(A.ps(1), te) + 1.4, len(lines))])
    ts.at(max(A.ps(1), te) + 1.5, "overlay", "A sandbox's address contains its id: recreate it and the address changes")
    ts.at(A.cs(2, 0) + 0.2, "hl", (1,))
    ts.at(A.cs(2, 0) + 0.2, "overlay", "The cascade is listed first, then ecoctl asks")
    ts.at(A.pe(2) - 0.1, "overlay", None)
    ts.at(A.pe(2) - 0.1, "line", ("Continue? [y/N] ", "out"))
    ts.inline(A.pe(2) + 0.3, c["stdin"], 0.2)
    ts.at(A.pe(2) + 0.7, "line", (last or "Cancelled.", "out"))
    return [(t, save(img)) for t, img in ts.replay(make_renderer(fs=24), until)]


if __name__ == "__main__":
    raise SystemExit(main())
