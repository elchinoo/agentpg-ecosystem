#!/usr/bin/env python3
"""One-command build of the review cut of the ecoctl tutorial video.

    python3 docs/video/build.py [--work DIR] [--name ecosystem] [--deliver DIR] [--voice am_liam] [--speed 1.0]
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
        s = min(s, len(sentences(self.paras[n])) - 1)
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


def motion(kf, D, F, fps=6, scale=1.03, min_len=3.0):
    """No picture stays still: a keyframe lasting `min_len` s or more gets a very slow pan (3 % enlarged, drifting about 2 % of the width and 2 %
    of the height, alternately one way and back), drawn above the footer line with the footer fixed. A pan keeps the area of every element
    constant, so there is no colour or brightness drift. Frames are written at `fps` as PNG, like every other keyframe."""
    from PIL import Image
    out, i = [], 0
    W_, H_, FOOT_ = S.W, S.H, 1040
    for k, (t, p) in enumerate(kf):
        nxt = kf[k + 1][0] if k + 1 < len(kf) else D
        dur = nxt - t
        if dur < min_len:
            out.append((t, p))
            continue
        im = Image.open(p).convert("RGB")
        top, foot = im.crop((0, 0, W_, FOOT_)), im.crop((0, FOOT_, W_, H_))
        w, h = W_ / scale, FOOT_ / scale
        fx, fy = W_ - w, FOOT_ - h                          # free room: 56 x 31 px
        n = int(dur * fps)
        rev = (i % 2 == 1)
        i += 1
        for j in range(n):
            u = j / max(n - 1, 1)
            u = 1 - u if rev else u
            x0, y0 = fx * (0.18 + 0.64 * u), fy * (0.15 + 0.7 * u)
            fr = top.resize((W_, FOOT_), Image.BICUBIC, box=(x0, y0, x0 + w, y0 + h))
            canvas = Image.new("RGB", (W_, H_))
            canvas.paste(fr, (0, 0))
            canvas.paste(foot, (0, FOOT_))
            q = Path(p).with_name(f"{Path(p).stem}-m{j:04d}.png")
            canvas.save(q, compress_level=1)
            out.append((round(t + j / fps, 3), str(q)))
    return out


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
CAPS = REPO.parent / "videos" / "live-captures"
SCAF3 = ("idp", "gw", "vault")
LOCAL = "LOCAL: frame from the app video (local run, not the live fleet)"
SL = "SLIDE: from the docs"


def cap_img(name, caption, crop=None, **kw):
    return S.capture_slide(CAPS / f"{name}-2026-10-28.jpg", caption, crop, **kw)


def app_img(app, name, caption, crop=None, **kw):
    return S.capture_slide(CAPS / "app-video-frames" / f"{app}-{name}-from-app-video.jpg", caption, crop, tag=LOCAL, **kw)


def scene1(A, F, cap):
    five = ("pagerlens", "citypulse", "cpsim", "trustdesk", "paybroker", "shopfront")
    T = lambda lines, **k: S.text_slide("Five templates, not only demos", "Apache License 2.0 for the five apps", lines, tag=SL, mono=False, fs=38, **k)  # noqa: E731
    parts = ["Sign-in (OIDC)", "A model gateway", "Secrets", "Service-to-service integration", "Webhooks", "A full deploy process"]
    D = lambda low: S.diagram("Five apps, five templates", "TrustDesk, PagerLens, CityPulse, PayBroker, ShopFront", frame=True, nodes=five, cats=False, lower=low)  # noqa: E731
    st = {
        (0, 0): S.problem(0), (0, 1): S.problem(1), (0, 2): S.problem(2),
        (1, 0): D("Five apps, already wired"),
        (1, 1): D("Not only demos of AgentPG"),
        (1, 2): D("Templates: take one, adapt it, run it"),
        (2, 0): T(parts),
        (2, 1): T(parts, hl=(0, 1, 2, 3, 4, 5)),
        3: S.diagram("The AgentPG ecosystem", "Platform, reference scaffolding, sample apps", legend=True, frame=True, plat=True,
                     nodes=SCAF3 + five, lower="One manifest, eight sandboxes, about fourteen minutes"),
    }
    return staged(A, st, F)


def scene2(A, F, cap):
    D = lambda **k: S.diagram("The map of the ecosystem", "Platform, reference scaffolding, sample apps", legend=True, **k)  # noqa: E731
    full = SCAF3 + ("model",) + ALL
    st = {
        0: D(frame=True),
        1: D(frame=True, plat=True, lower="PLATFORM = EDB Postgres on AgentPG"),
        (2, 0): D(frame=True, nodes=SCAF3, lower="REFERENCE SCAFFOLDING"),
        (2, 1): D(frame=True, nodes=SCAF3 + ("model",), marks=("idp", "gw", "vault"), lower="All swappable: your OIDC, gateway, vault"),
        (3, 0): D(frame=True, nodes=full, lower="SAMPLE APPS"),
        (3, 1): D(frame=True, nodes=full, hot=("sim_cp",), steps=("The simulator sends readings to CityPulse",)),
        (3, 2): D(frame=True, nodes=full, warm=("sim_cp",), hot=("cp_pl",), steps=("CityPulse reports trouble to PagerLens",)),
        (3, 3): D(frame=True, nodes=full, warm=("sim_cp", "cp_pl"), hot=("sf_pb", "pb_sf"),
                  steps=("ShopFront sends payments to PayBroker", "PayBroker sends a signed webhook back")),
        (3, 4): D(frame=True, nodes=full, warm=("sim_cp", "cp_pl", "sf_pb", "pb_sf"), hot=("gw_trustdesk",),
                  steps=("Every app asks the gateway for its model",)),
        4: D(frame=True, nodes=full, warm=("sim_cp", "cp_pl", "sf_pb", "pb_sf") + GW, hot=("idp",),
             steps=("Everything signs in through identity: short-lived service tokens",)),
    }
    return staged(A, st, F)


def appdia(app, hot=(), warm=(), steps=(), lower=None, extra=()):
    return S.diagram("How it fits", "", frame=True, nodes=SCAF3 + ("model",) + ALL, marks=(app,) + tuple(extra), hot=hot, warm=warm, steps=steps,
                     lower=lower, tag=SL)


def scene3(A, F, cap):
    t = dict(title="TrustDesk", sub="Compliance workspace: pgvector, full-text, pg_trgm, masked views")
    st = {
        0: app_img("trustdesk", "requests-first-opinion", "Requests, each with a first opinion from a model", **t),
        (1, 0): cap_img("trustdesk-req-000016-rejection", "REQ-000016: fifteen days of leave, short notice", **t),
        (1, 1): cap_img("trustdesk-req-000016-rejection", "AI pre-validation: suggests rejection, with numbered reasons", crop=(685, 140, 1180, 330), **t),
        (2, 0): cap_img("trustdesk-req-000016-verified-quotes", "Quote found word for word in the document", crop=(700, 120, 1165, 420), **t),
        (2, 1): cap_img("trustdesk-req-000016-verified-quotes", "The AI advises; a person decides", crop=(685, 490, 1180, 770), **t),
        (3, 0): app_img("trustdesk", "search-typo-tolerant", "Search: pgvector and full-text together, pg_trgm for typos", **t),
        (3, 1): cap_img("trustdesk-insights-top-queries-masking", "Masked views for the people data", crop=(273, 540, 1195, 812), **t),
        4: appdia("trustdesk", hot=("gw_trustdesk",), warm=("idp",), steps=("TrustDesk signs in through identity and asks the gateway for its model",)),
    }
    return staged(A, st, F)


def scene4(A, F, cap):
    t = dict(title="PagerLens", sub="Incident investigator: evidence, causes, verified citations")
    st = {
        0: app_img("pagerlens", "incident-investigate-no-model", "An incident page: investigate with rules, no model, no cost", **t),
        (1, 0): app_img("pagerlens", "report-manual-steps", "Evidence, ranked hypotheses, and manual steps only", **t),
        2: app_img("pagerlens", "ask-verified-citations", "Ask PagerLens: each citation marked verified (stand-in model)", **t),
        (3, 0): app_img("pagerlens", "incident-investigate-no-model", "Live incident pages: Postgres tcn notifications, no message broker", **t),
        (3, 1): app_img("pagerlens", "report-manual-steps", "Propose and approve: nothing is applied by default", **t),
        4: appdia("pagerlens", hot=("cp_pl",), warm=("idp",), steps=("CityPulse reports to PagerLens with a scope; PagerLens signs in through identity",)),
    }
    return staged(A, st, F)


def scene5(A, F, cap):
    t = dict(title="CityPulse and the Simulator", sub="Smart-city sensors: PostGIS, time series, JSON, vectors")
    st = {
        0: app_img("citypulse", "overview-simulator", "CityPulse: thousands of simulated sensors, one city verdict", **t),
        (1, 0): app_img("citypulse", "overview-simulator", "The simulator feeds it with a short-lived token", **t),
        (1, 1): app_img("citypulse", "burst-degraded-reports", "Burst: the city degrades", **t),
        2: appdia("citypulse", hot=("cp_pl",), warm=("sim_cp", "idp"), steps=("CityPulse reports its health to PagerLens, which opens an incident",), extra=("pagerlens",)),
        (3, 0): cap_img("citypulse-explain-top", "Explain: one SQL statement, five data models", **t),
        (3, 1): cap_img("citypulse-explain-geo-vector", "Geographic and vector data in the same statement", **t),
    }
    return staged(A, st, F)


def scene6(A, F, cap):
    t = dict(title="PayBroker", sub="Payment broker: audit chain, row-level security, masked views")
    st = {
        0: appdia("paybroker", warm=("sf_pb", "pb_sf"), lower="One payment API, simulated processors, test cards only"),
        (1, 0): app_img("paybroker", "ledger-chain-intact", "Hash-chained audit ledger: every hash recomputed by the database", **t),
        (1, 1): app_img("paybroker", "ledger-tamper-named", "One old row changed: the first broken entry is named", **t),
        (2, 0): cap_img("paybroker-insights-masking-sql-in-monitor", "Masked views: REVOKE on base tables, views as the way in", **t),
        (2, 1): cap_img("paybroker-insights-masking-sql-in-monitor", "In the database, not in every service", crop=(248, 150, 1220, 270), **t),
        3: appdia("paybroker", hot=("sf_pb", "pb_sf"), steps=("ShopFront sends payments to PayBroker", "PayBroker sends a signed webhook back")),
    }
    return staged(A, st, F)


def scene7(A, F, cap):
    t = dict(title="ShopFront", sub="A small web shop: hybrid search, live orders, payments through PayBroker")
    st = {
        0: app_img("shopfront", "did-you-mean", "A small shop on Postgres", **t),
        (1, 0): app_img("shopfront", "did-you-mean", "kettel: did you mean kettle? (fuzzystrmatch, pg_trgm)", **t),
        (1, 1): app_img("shopfront", "hybrid-search", "Matched by words and by meaning, in one SQL statement", **t),
        (2, 0): app_img("shopfront", "checkout-token", "The card goes from the browser to PayBroker, not to the shop", **t),
        (2, 1): app_img("shopfront", "payment-trail", "Payment trail from PayBroker; the order updates live (tcn), no broker", **t),
        3: appdia("shopfront", hot=("sf_pb", "pb_sf"), steps=("The easiest one to copy: catalog, cart, orders, payments",)),
    }
    return staged(A, st, F)


def scene8(A, F, cap):
    L = ["Sign-in: OIDC, with roles per app", "Model access: one gateway, a key and limits per app",
         "Service calls: short-lived tokens that carry scopes", "Webhooks: signed, ShopFront and PayBroker",
         "Secrets: the kit vault, never in code or in files", "From the shared kit: Insights tab, health checks, masking"]
    T = lambda hl=(): S.text_slide("How the apps integrate", "All swappable", L, tag=SL, mono=False, fs=38, hl=hl)  # noqa: E731
    st = {0: T()}
    for i in range(6):
        st[i + 1] = T((i,))
    return staged(A, st, F)


def scene9(A, F, cap):
    t = dict(title="The platform in every app", sub="EDB extensions, PostGIS, pgvector, masking, MCP")
    mcp = ["App MCP: each app's own tools, behind our identity (roles and scopes)",
           "AgentPG database MCP: generic, over a read-only database user",
           "Use the first for anything verifiable, or that changes data", "Use the second to explore data and ask about slow queries"]
    one = ["Search: pgvector + full-text + pg_trgm   instead of a search engine and a vector database",
           "Maps: PostGIS   instead of a GIS server", "Live updates: tcn   instead of a message broker",
           "Monitoring and tuning: edb_stat_monitor, edb_pg_tuner, query_advisor   instead of an external tool",
           "Masking and row-level security: in the database   instead of in every service"]
    O = lambda hl=(): S.text_slide("One Postgres instead of five tools", "What each app uses", one, tag=SL, mono=False, fs=30, hl=hl)  # noqa: E731
    st = {
        0: cap_img("trustdesk-insights-top-queries-masking", "TrustDesk, Database page", **t),
        (0, 1): cap_img("trustdesk-insights-top-queries-masking", "Top queries: edb_stat_monitor", crop=(273, 120, 1195, 560), **t),
        (0, 2): cap_img("trustdesk-insights-tuner-advisor", "Settings: edb_pg_tuner. Indexes: query_advisor", **t),
        1: cap_img("trustdesk-insights-tuner-advisor", "Suggestions only: a person decides", crop=(273, 380, 1195, 750), **t),
        (2, 0): cap_img("paybroker-insights-masking-sql-in-monitor", "PayBroker: masking statements in the monitor", **t),
        (2, 1): cap_img("trustdesk-insights-top-queries-masking", "Masked views, kept in force by the kit", crop=(273, 540, 1195, 812), **t),
        (2, 2): cap_img("paybroker-insights-masking-sql-in-monitor", "A masked view: the actor name is cut to a letter and stars", crop=(248, 150, 1220, 270), **t),
        3: O(),
        4: S.text_slide("Two MCP routes", "docs/08-two-mcp-routes.md", mcp, tag=SL, mono=False, fs=34),
    }
    return staged(A, st, F)


def scene10(A, F, cap):
    lines = ["Pipelines chunk and embed documents inside the database", "and keep them in step when the source table changes",
             "TrustDesk policies, PagerLens runbooks and incident memory, CityPulse questions in plain language",
             "Agents inside Postgres, with tools that act as a database role"]
    R = lambda hl=(), n=4: S.text_slide("Next on the roadmap: EDB AIDB", "What we are integrating into the apps", lines[:n], tag="ROADMAP", mono=False, fs=34, hl=hl)  # noqa: E731
    st = {0: R((), 0) if False else S.text_slide("Next on the roadmap: EDB AIDB", "We are integrating it into the apps", ["EDB AIDB"], tag="ROADMAP", mono=False, fs=60),
          (1, 0): R((0,), 2), (1, 1): R((1,), 2), 2: R((2,), 3), 3: R((3,), 4)}
    return staged(A, st, F)


def scene11(A, F, cap):
    T = lambda title, sub, lines, **k: S.text_slide(title, sub, lines, tag=SL, mono=False, fs=38, **k)  # noqa: E731
    c = cap["validate"]
    lines, _, _ = record._split(c["text"])
    ts = record.TermScript()
    ts.at(0, "line", ("$ ", "cmd"))
    t0 = A.ps(1) + 0.1
    ts.at(t0 - 0.05, "overlay", "ecoctl: an internal tool")
    ts.inline(t0, c["cmd"], 1.0)
    for ln in lines:
        ts.at(t0 + 1.3, "line", (ln, "out"))
    ts.at(A.cs(1, 1), "overlay", "One manifest, one command, the whole fleet")
    ts.at(A.cs(1, 2), "overlay", "Built to show how to wrap your own solution around the agentpg client")
    term = [(t, F.save(img)) for t, img in ts.replay(record.make_renderer(fs=26), max(A.ps(2) - 0.16, t0 + 3))]
    first = [(0.0, F.save(T("Run them yourself", "Each repository", ["A README quick start", "A Dockerfile", "A full deploy process"], hl=(0,))))]
    tail = [
        (max(A.ps(2) - 0.15, 0), F.save(T("Ansible recipes", "ansible/ in the ecosystem repository",
                                          ["Deploy the whole fleet on AgentPG", "Tested, and they work", "All eight apps from empty: about fourteen minutes"], hl=(0,)))),
        (max(A.cs(2, 1) - 0.15, 0), F.save(T("Ansible recipes", "ansible/ in the ecosystem repository",
                                             ["Deploy the whole fleet on AgentPG", "Tested, and they work", "All eight apps from empty: about fourteen minutes"], hl=(1, 2)))),
        (max(A.ps(3) - 0.15, 0), F.save(T("A sample to reuse", "Fit it to your company's own deployment",
                                          ["The same manifest as ecoctl", "ansible-core only", "Three playbooks: site, verify, destroy"]))),
        (max(A.cs(3, 1) - 0.15, 0), F.save(T("A sample to reuse", "Fit it to your company's own deployment",
                                             ["The same manifest as ecoctl", "ansible-core only", "Three playbooks: site, verify, destroy"], hl=(0, 1, 2)))),
    ]
    return first + term + tail


def scene12(A, F, cap):
    allnodes = SCAF3 + ("model",) + ALL
    mk = lambda txt, **k: S.diagram("Five templates", "Fork one, swap the scaffolding, run it, change it", frame=True, nodes=allnodes, legend=True,  # noqa: E731
                                    warm=("idp",) + GW, lower=txt, **k)
    st = {(0, 0): mk("Five apps, and not only demos"), (0, 1): mk("They are templates"),
          (1, 0): mk("Take one", marks=("trustdesk",)), (1, 1): mk("Swap identity, gateway or vault", marks=("idp", "gw", "vault")),
          (1, 2): mk("Adapt it, run it for a real use case"),
          2: mk("Sign-in, gateway, secrets, calls, deploy: in place", plat=True), 3: S.endcard()}
    return staged(A, st, F)


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
    ap.add_argument("--voice", default="am_liam")  # scenes name their own voice in narration.txt: am_liam, af_heart, am_eric
    ap.add_argument("--speed", default="1.0")  # this video is spoken at 1.0 (the shared default of videos/tts is 1.08)
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
        kf = motion(sorted(SCENES[int(sid)](A, F, cap)), A.D, F)
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
