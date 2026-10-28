#!/usr/bin/env python3
"""One-command build of the review cut of the ecoctl tutorial video.

    python3 docs/video/build.py [--work DIR] [--name ecosystem] [--deliver DIR] [--voice am_michael] [--speed 0.95]
                                [--keep-audio] [--sync-script]

Steps
 1  narration:  videos/tts/narrate.py scenes docs/video/narration.txt  (Kokoro, local; one wav per scene + narration.json)
 2  capture:    the real offline commands (validate, plan --only, dry-run apply, cancelled prompts) via record.py, in throw-away
             directories under --work. Never touches .ecosystem/ of the fleet and never calls AgentPG.
 3  render:     every scene becomes keyframes at 1920x1080, timed from the narration: scene length = audio + 0.7 s (0.35 s lead,
             0.35 s tail). Terminal scenes are replayed from the captured output (record.py); slide scenes are drawn with Pillow
             (slides.py). Scenes that need a live fleet are diagram slides, never invented terminal output.
 4  mux:        ffmpeg: H.264 yuv420p 30 fps + AAC + soft subtitles (mov_text); <name>.srt is written from narration.json.
 5  checks:     audio and video lengths, the longest silence inside each scene, silencedetect over the final audio.

Needs python3 with Pillow, ffmpeg/ffprobe, and the Kokoro venv of videos/tts (nothing is installed or downloaded).
--sync-script rewrites the NARRATION column of docs/VIDEO-SCRIPT.md from narration.txt and exits.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
import record  # noqa: E402
import slides as S  # noqa: E402

TTS = REPO.parent / "videos" / "tts"
SCRIPT = REPO / "docs" / "VIDEO-SCRIPT.md"
LEAD, TAIL, FPS = 0.35, 0.35, 30
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
TAG = "REVIEW CUT: slide stands in for a live capture"
LIVE_SCENES = {6: "title for the live segment", 7: "apply --execute --yes --only trustdesk (live)", 8: "ecoctl status (live)",
               9: "first sign-in in the identity console and launcher (browser)", 10: "interaction tour, seeded apps (browser)",
               11: "TrustDesk request, ShopFront checkout, PayBroker payment (browser)"}


def run(*a, **kw):
    return subprocess.run([str(x) for x in a], check=True, **kw)


# ------------------------------------------------------------------------------------------------------- narration text
def sentences(p):
    return [s for s in re.split(r"(?<=[.!?])\s+", p) if s]


def parse_narration(path):
    """{scene id: [paragraph text]} with the same paragraph rules as narrate.py (blank line, `...` and [pause] end a paragraph)."""
    out, cur, para = {}, None, []

    def flush():
        if para and cur is not None:
            out[cur].append(" ".join(para).strip())
        para.clear()

    for line in Path(path).read_text().splitlines():
        m = re.match(r"^##\s+(\S+)(?:\s+\S+)?\s*$", line)
        s = line.strip()
        if m:
            flush()
            cur = m.group(1)
            out[cur] = []
        elif not s or s == "..." or re.fullmatch(r"\[pause\s+[\d.]+\]", s):
            flush()
        else:
            para.append(s)
    flush()
    return out


class Audio:
    """Timing of one scene, in scene time. ps/pe: start/end of paragraph n; cs: start of sentence s of paragraph n."""

    def __init__(self, sid, info, paras):
        self.sid, self.paras, self.cues = sid, paras, info["cues"]
        self.audio = info["duration"]
        self.D = round(LEAD + self.audio + TAIL, 3)
        self.first, k = [], 0
        for p in paras:
            self.first.append(k)
            k += len(sentences(p))
        if k != len(self.cues):
            sys.exit(f"scene {sid}: {k} sentences in the text but {len(self.cues)} cues")
        self.n = len(paras)

    def ps(self, n):
        return round(LEAD + self.cues[self.first[n]]["start"], 3)

    def pe(self, n):
        last = self.first[n] + len(sentences(self.paras[n])) - 1
        return round(LEAD + self.cues[last]["end"], 3)

    def cs(self, n, s):
        return round(LEAD + self.cues[self.first[n] + s]["start"], 3)


class Frames:
    """Writes keyframe PNGs once per distinct image."""

    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.seen = {}

    def save(self, img):
        h = hashlib.md5(img.tobytes()).hexdigest()
        if h not in self.seen:
            p = self.dir / f"f{len(self.seen):05d}.png"
            img.save(p, compress_level=1)
            self.seen[h] = str(p)
        return self.seen[h]


def staged(A, stages, F, lead_in=0.0):
    """stages: {key: image}; key is a paragraph n or (n, s) for sentence s of paragraph n. Shown just before the voice reaches it;
    the first key also covers t=0."""
    out, first = [], min(stages, key=lambda k: (k, 0) if isinstance(k, int) else k)
    for k in sorted(stages, key=lambda k: (k, 0) if isinstance(k, int) else k):
        n, sn = (k, 0) if isinstance(k, int) else k
        t = 0.0 if k == first else max((A.ps(n) if sn == 0 else A.cs(n, sn)) - 0.15, 0.0)
        out.append((t, F.save(stages[k])))
    return out


# --------------------------------------------------------------------------------------------------------------- scenes
ALL = ("pagerlens", "citypulse", "cpsim", "trustdesk", "paybroker", "shopfront")
GW = tuple("gw_" + k for k in S.LLM_APPS)


def scene1(A, F, cap):
    sub = "Sample apps for EDB AIDB and AgentPG, deployed by ecoctl"
    D = lambda **k: S.diagram("The AgentPG ecosystem", sub, **k)  # noqa: E731
    full = ("eco", "idp", "gw", "model") + ALL
    st = {
        0: D(lower="ecoctl: one manifest, one command"),
        1: D(frame=True),
        (1, 1): D(frame=True, nodes=("trustdesk",)),
        2: D(frame=True, nodes=("eco",), hot=("ecoctl_fleet",), steps=("ecoctl deploys the whole fleet from a single file",)),
        3: D(frame=True, nodes=("eco", "trustdesk", "pagerlens", "citypulse")),
        4: D(frame=True, nodes=("eco",) + ALL),
        5: D(frame=True, nodes=("eco", "idp") + ALL, hot=("idp",), steps=("identity: sign-in, and short-lived service tokens",)),
        (5, 2): D(frame=True, nodes=full, warm=("idp",) + GW + ("gw_model",), lower="LLM gateway: one key per app, models behind it"),
        6: D(frame=True, nodes=full, warm=("idp",) + GW, hot=("sim_cp", "cp_pl"),
             steps=("Simulator sends readings to CityPulse", "CityPulse reports trouble to PagerLens")),
        7: D(frame=True, nodes=full, warm=("idp", "sim_cp", "cp_pl") + GW, hot=("sf_pb", "pb_sf"),
             steps=("ShopFront sends payments to PayBroker", "PayBroker sends a signed webhook back")),
        (7, 1): D(frame=True, nodes=full, warm=("idp", "sim_cp", "cp_pl", "sf_pb", "pb_sf") + GW, hot=("gw_trustdesk",),
                  steps=("TrustDesk asks the gateway to check requests against policy",)),
        8: D(frame=True, nodes=full, warm=("idp", "sim_cp", "cp_pl", "sf_pb", "pb_sf", "sim_td") + GW,
             lower="8 sandboxes: 6 apps, identity, gateway"),
        (8, 1): D(frame=True, nodes=full, warm=("idp", "sim_cp", "cp_pl", "sf_pb", "pb_sf", "sim_td") + GW,
                  lower="Plan it. Run it. Watch it work."),
    }
    return staged(A, st, F)


def scene2(A, F, cap):
    return record.validate_frames(A, cap, F.save)


def yaml_lines():
    raw = (REPO / "ecosystem.yaml").read_text().splitlines()
    return [(i + 1, re.sub(r"\s+#.*$", "", ln)) for i, ln in enumerate(raw)]


def find(L, pattern, start=0):
    for i in range(start, len(L)):
        if re.search(pattern, L[i][1]):
            return i
    raise SystemExit(f"manifest anchor not found: {pattern}")


def scene3(A, F, cap):
    L = yaml_lines()
    td0 = find(L, r"^  trustdesk:")
    td = L[td0:find(L, r"exposes: \[trustdesk", td0) + 1]
    num = lambda block, pat: tuple(n for n, t in block if re.search(pat, t))  # noqa: E731
    cp0 = find(L, r"^  citypulse:")
    cp = L[cp0:find(L, r"pagerlens: \[pagerlens\.ingest\]", cp0) + 1]
    sim0 = find(L, r"exposes: \[cpsim")
    sim = L[sim0:find(L, r"^    uses:", sim0) + 1]
    ac = L[find(L, r"^access:"):]
    Y = S.yaml_slide
    callcap = "calls: the scopes this app may ask other apps for. CityPulse: only pagerlens.ingest"
    st = {
        0: Y(td, num(td, r"extensions|roles:"), "One app: a name, its database extensions and its roles"),
        (1, 0): Y(td, num(td, r"exposes:"), "exposes: the scopes this app offers"),
        (1, 1): Y(cp, num(cp, r"calls:|pagerlens: \["), callcap),
        (2, 1): Y(cp, num(cp, r"calls:|pagerlens: \["), "A typo here fails validation, not production"),
        3: Y(sim, num(sim, r"uses:"), "uses: another app's address, with no scope. The simulator uses PayBroker, with a merchant key"),
        4: Y(td, num(td, r"llm:"), "llm: true wires the app to the LLM gateway"),
        5: Y(td, num(td, r"generate:"), "No secret is ever written here: generate names get a random value from ecoctl"),
        6: Y(ac, num(ac, r"grants:"), "access.groups: people to roles across the whole fleet. Access is a line in this file"),
    }
    return staged(A, st, F)


def scene4(A, F, cap):
    return record.plan_frames(A, cap, F.save)


def scene5(A, F, cap):
    t = max(A.ps(3) - 0.15, 0)
    term = record.apply_frames(A, cap, F.save, t - 0.01)
    cards = [("Safe to run again", ["Finds what exists, by name", "Never rotates a secret unless you ask"], S.BLUE),
             ("If a step fails", ["Progress is saved", "Run the same command again"], S.BLUE),
             ("Named after the fleet prefix", ["One account can hold several fleets", "A deleted sandbox is noticed, and created again"], S.BLUE)]
    sl = lambda i: F.save(S.flow("apply is safe to repeat", "What ecoctl apply guarantees", [], cards=cards, active_card=i, tag=None))  # noqa: E731
    return term + [(t, sl(0)), (max(A.ps(4) - 0.15, 0), sl(1)), (max(A.ps(5) - 0.15, 0), sl(2))]


def scene6(A, F, cap):
    img = S.flow("The live segment", "Recorded once on a demo account, fleet name: demo",
                 [("ecoctl apply --execute", "create the fleet, for TrustDesk only"), ("ecoctl status", "check that it is healthy"),
                  ("First sign-in", "open the apps through one launcher")],
                 cards=[("Why it is recorded", ["Real databases take minutes to create, and each run prints new addresses.",
                                                "The waits are cut and a timer says how much was sped up."], S.BLUE)], tag=TAG)
    return [(0.0, F.save(img))]


def scene7(A, F, cap):
    steps = [("apply --execute --yes", "for TrustDesk only"), ("Identity provider", "database, sandbox, issuer pinned"),
             ("LLM gateway", "registered, database, sandbox"), ("TrustDesk", "registered, own gateway key and limits"),
             ("TrustDesk sandbox", "the key goes into its environment"), ("Done", "addresses, and a reminder to join a group")]
    cards = [("What the real output looks like", ["One line for each step.", "Each line names the step and ends with what happened to it.",
                                                  "At the end: the list of addresses and a reminder to join a group."], S.BLUE),
             ("Where the secrets live", ["One private file next to the manifest, never shown on screen.", "Git-ignored, and the only copy: back it up."], S.AMBER)]
    fl = lambda a, c=None: S.flow("Deploying TrustDesk, step by step", "What apply --execute does, in order", steps, active=a, done=range(a),  # noqa: E731
                                  cards=cards, active_card=c, tag=TAG)
    st = {0: fl(0), 1: fl(1), (1, 1): fl(2), 2: fl(3), 3: fl(4), 4: fl(5, 0), 5: fl(5, 1)}
    return staged(A, st, F)


def scene8(A, F, cap):
    steps = [("Apps", "every app answers health and ready"), ("Gateway", "clients, limits, and a tiny chat call"),
             ("Identity registry", "matches what is deployed"), ("Exit code", "0 means green")]
    cards = [("What the real output looks like", ["A table: one row per app, with the health and ready codes.",
                                                  "A gateway section: one row per client, and one tiny chat call per app.",
                                                  "Identity registry rows, each marked ok."], S.BLUE),
             ("Drift", ["A limit changed by hand in the gateway makes status warn.", "ecoctl llm sync shows the difference.",
                        "With its apply flag it restores the manifest values."], S.AMBER)]
    act = {0: (None, None), 1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (3, None), 5: (1, 1)}
    st = {n: S.flow("ecoctl status", "The check you will use most", steps, active=a, done=[i for i in range(4) if a is not None and i < a],
                    cards=cards, active_card=c, tag=TAG) for n, (a, c) in act.items()}
    return staged(A, st, F)


def scene9(A, F, cap):
    steps = [("Identity console", "create yourself as a user"), ("Join a group", "ecosystem-admins"),
             ("Launcher", "open /apps: one tile per app"), ("One sign-in", "click a tile and land signed in")]
    cards = [("Groups come from the manifest", ["ecosystem-admins: admin in every app", "risk-team: risk manager in PayBroker, viewer in PagerLens",
                                                "support: approver in TrustDesk"], S.BLUE),
             ("Optional", ["Google or GitHub login is switched on in the same console.", "Nothing changes in the apps."], S.AMBER)]
    fl = lambda a, c=None: S.flow("First sign-in", "The one manual step is you", steps, active=a, done=range(a) if a is not None else (),  # noqa: E731
                                  cards=cards, active_card=c, tag=TAG)
    st = {0: fl(0), (1, 0): fl(0), (1, 1): fl(1), 2: fl(2), (2, 1): fl(3), 3: fl(None, 0), (3, 2): fl(None, 1)}
    return staged(A, st, F)


def scene10(A, F, cap):
    allnodes = ("idp", "gw", "model") + ALL
    D = lambda **k: S.diagram("The apps work together", "One city, one burst, one incident", frame=True, nodes=allnodes, tag=TAG, **k)  # noqa: E731
    st = {
        0: D(lower="Synthetic city, synthetic data"),
        1: D(warm=("idp",), hot=("sim_cp",), steps=("Simulator sends readings to CityPulse: short-lived token, scope citypulse.ingest",)),
        2: D(warm=("idp", "sim_cp"), hot=("cp_pl",), steps=("Burst: CityPulse degrades and reports to PagerLens, scope pagerlens.ingest",)),
        3: D(warm=("idp", "sim_cp", "cp_pl"), marks=("pagerlens",), lower="PagerLens: incident, evidence, ranked causes"),
        4: D(warm=("idp", "sim_cp", "cp_pl"), hot=("gw_pagerlens",), steps=("PagerLens asks a model through the gateway, with its own key",)),
        5: D(warm=("idp", "sim_cp", "cp_pl", "gw_pagerlens"), hot=("sim_pb", "sim_td"),
             steps=("Simulator pays through PayBroker: a merchant key, not a scope", "Simulator sends TrustDesk requests: scope trustdesk.requests.write")),
        6: D(warm=("idp", "sim_cp", "cp_pl", "gw_pagerlens", "sim_pb", "sim_td"), lower="Every call: only the scope the manifest granted"),
    }
    return staged(A, st, F)


def scene11(A, F, cap):
    allnodes = ("eco", "idp", "gw", "model") + ALL
    D = lambda **k: S.diagram("Policy checks and payments", "TrustDesk, ShopFront and PayBroker", frame=True, nodes=allnodes, tag=TAG, **k)  # noqa: E731
    st = {
        0: D(hot=("gw_trustdesk",), steps=("TrustDesk asks the gateway to check the request. Every quote is verified word for word",)),
        1: D(warm=("gw_trustdesk",), marks=("trustdesk",), lower="A person still decides"),
        2: D(warm=("gw_trustdesk",), hot=("sf_pb",), steps=("Test card tokenized in the browser: it never touches the shop's server",)),
        3: D(warm=("gw_trustdesk", "sf_pb"), hot=("pb_sf",), steps=("PayBroker scores the payment and signs a webhook back to the shop",)),
        4: D(warm=("gw_trustdesk", "sf_pb", "pb_sf"), marks=("shopfront", "paybroker", "eco"), lower="ecoctl connect: merchant, origin, keys, webhook"),
        5: D(warm=GW + ("gw_model",), marks=("gw",), lower="chat-default: one alias, every app follows"),
    }
    return staged(A, st, F)


def scene12(A, F, cap):
    t_diag = max(A.ps(4) - 0.15, 0)
    term = [(t, p) for t, p in record.recreate_frames(A, cap, F.save, t_diag - 0.01)]
    allnodes = ("eco", "idp", "gw", "model") + ALL
    dia = S.diagram("One manifest, one state file, one guide", "The whole fleet, from one file", frame=True, nodes=allnodes,
                    warm=("idp", "sim_cp", "cp_pl", "sf_pb", "pb_sf", "sim_td") + GW, lower="Add an app by adding a few lines")
    dia1 = S.diagram("One manifest, one state file, one guide", "The whole fleet, from one file", frame=True, nodes=allnodes,
                     warm=("idp", "sim_cp", "cp_pl", "sf_pb", "pb_sf", "sim_td") + GW, lower="One manifest, one state file, one guide")
    return term + [(t_diag, F.save(dia1)), (max(A.ps(5) - 0.15, 0), F.save(dia)), (max(A.ps(6) - 0.15, 0), F.save(S.endcard()))]


SCENES = {1: scene1, 2: scene2, 3: scene3, 4: scene4, 5: scene5, 6: scene6, 7: scene7, 8: scene8, 9: scene9, 10: scene10, 11: scene11, 12: scene12}


# --------------------------------------------------------------------------------------------------------- script mirror
def sync_script(paras):
    """Rewrite the NARRATION cell of every row of the scene table in VIDEO-SCRIPT.md from narration.txt."""
    lines, total = SCRIPT.read_text().splitlines(), 0
    for i, line in enumerate(lines):
        m = re.match(r"\|\s*(\d+)\s*\|", line)
        if not m or m.group(1) not in paras:
            continue
        cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
        if len(cells) < 5:
            continue
        text = " ".join(paras[m.group(1)])
        n = len(text.split())
        total += n
        cells[4] = f' "{text}" ({n} words) '
        lines[i] = "|" + "|".join(cells) + "|"
    SCRIPT.write_text("\n".join(lines) + "\n")
    print(f"VIDEO-SCRIPT.md narration column updated ({total} words)")


# ------------------------------------------------------------------------------------------------------------------ mux
def srt_time(t):
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def chunks(sentence, limit=84):
    out, cur = [], ""
    for w in sentence.split():
        if cur and len(cur) + 1 + len(w) > limit:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return out + [cur]


def write_srt(path, order, narr, starts):
    n, out = 0, []
    for sid in order:
        for c in narr[sid]["cues"]:
            parts = chunks(c["text"])
            tot, t = sum(len(p) for p in parts), starts[sid] + LEAD + c["start"]
            for p in parts:
                d = (c["end"] - c["start"]) * len(p) / tot
                n += 1
                out.append(f"{n}\n{srt_time(t)} --> {srt_time(t + d)}\n{p}\n")
                t += d
    Path(path).write_text("\n".join(out), encoding="utf-8")


def probe(path):
    return float(subprocess.check_output([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], text=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=str(HERE / "out" / "build"))
    ap.add_argument("--name", default="ecosystem")
    ap.add_argument("--deliver", default=None)
    ap.add_argument("--voice", default="am_michael")
    ap.add_argument("--speed", default="0.95")
    ap.add_argument("--keep-audio", action="store_true", help="reuse work/audio from a previous run")
    ap.add_argument("--sync-script", action="store_true")
    a = ap.parse_args()
    paras = parse_narration(HERE / "narration.txt")
    if a.sync_script:
        sync_script(paras)
        return 0
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    adir = work / "audio"
    if not (a.keep_audio and (adir / "narration.json").exists()):
        run(TTS / ".venv" / "bin" / "python", TTS / "narrate.py", "scenes", HERE / "narration.txt", adir, "--voice", a.voice,
            "--speed", a.speed, "--pronounce", HERE / "pronounce.json")
    narr = json.loads((adir / "narration.json").read_text())
    order = sorted(narr, key=int)
    if sorted(map(int, order)) != sorted(map(int, paras)):
        sys.exit("narration.txt scenes differ from narration.json")
    cap_root = work / "capture"
    shutil.rmtree(cap_root, ignore_errors=True)               # only ever a directory this script created
    cap_root.mkdir()
    cap = record.capture(cap_root)
    fdir = work / "frames"
    shutil.rmtree(fdir, ignore_errors=True)
    F = Frames(fdir)
    timeline, starts, t0, auds = [], {}, 0.0, {}
    for sid in order:
        A = Audio(sid, narr[sid], paras[sid])
        auds[sid] = A
        starts[sid] = t0
        kf = sorted(SCENES[int(sid)](A, F, cap))
        ts_ = [t for t, _ in kf] + [A.D]
        gaps_ = [(ts_[i + 1] - ts_[i], ts_[i]) for i in range(len(kf)) if kf[i][1] and ts_[i + 1] - ts_[i] > 8.0]
        for g, at in gaps_:
            print(f"  static picture: scene {sid} {g:.1f} s from {at:.1f} s")
        assert kf and kf[0][0] == 0.0, f"scene {sid}: no frame at t=0"
        timeline += [(round(t0 + t, 3), p) for t, p in kf if t < A.D]
        t0 = round(t0 + A.D, 3)
        print(f"scene {sid}: {A.D:6.2f} s, {len(kf)} keyframes")
    total = t0
    # frames list (concat demuxer) at constant 30 fps
    lst = work / "frames.txt"
    with open(lst, "w") as f:
        for (t, p), nxt in zip(timeline, [x[0] for x in timeline[1:]] + [total], strict=True):
            f.write(f"file '{p}'\nduration {nxt - t:.3f}\n")
        f.write(f"file '{timeline[-1][1]}'\n")
    # audio: each scene wav delayed by LEAD and padded to its scene length
    parts = []
    for sid in order:
        out = adir / f"pad-{sid}.wav"
        run(FFMPEG, "-v", "error", "-y", "-i", adir / f"scene-{sid}.wav", "-af", f"adelay={int(LEAD * 1000)}:all=1,apad=whole_dur={auds[sid].D}",
            "-ar", "24000", "-ac", "1", out)
        parts.append(out)
    (adir / "concat.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    run(FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", adir / "concat.txt", "-c", "copy", work / "audio.wav")
    srt = work / f"{a.name}.srt"
    write_srt(srt, order, narr, starts)
    mp4 = work / f"{a.name}.mp4"
    run(FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", work / "audio.wav", "-i", srt,
        "-map", "0:v", "-map", "1:a", "-map", "2:s", "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", "-t", f"{total:.3f}", "-movflags", "+faststart", mp4)
    v, au = probe(mp4), probe(work / "audio.wav")
    print(f"{mp4}: {v:.2f} s video, {au:.2f} s audio, planned {total:.2f} s")
    # checks: longest silence inside each scene between voiced sentences (explicit [pause] included), then silencedetect on the result
    for sid in order:
        c = narr[sid]["cues"]
        gaps = [c[i + 1]["start"] - c[i]["end"] for i in range(len(c) - 1)]
        print(f"  scene {sid}: voice gap max {max(gaps, default=0):.2f} s, scene {auds[sid].D:.1f} s")
    sd = subprocess.run([FFMPEG, "-hide_banner", "-i", str(mp4), "-af", "silencedetect=n=-45dB:d=2.5", "-f", "null", "-"], capture_output=True, text=True).stderr
    print("silences over 2.5 s in the final audio:", re.findall(r"silence_duration: ([\d.]+)", sd) or "none")
    if a.deliver:
        d = Path(a.deliver)
        d.mkdir(parents=True, exist_ok=True)
        for p in (mp4, srt):
            shutil.copy(p, d / p.name)
        print(f"delivered to {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
