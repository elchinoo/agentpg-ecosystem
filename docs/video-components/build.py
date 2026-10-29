#!/usr/bin/env python3
"""One command from narration.txt and the real local runs to the finished components video (mp4 + srt).

    python3 docs/video-components/build.py [--work DIR] [--name components] [--deliver DIR] [--keep-audio] [--keep-capture] [--sync-script]

Steps
 1  narration:  videos/tts/narrate.py scenes narration.txt  (Kokoro, local; shared tool at its default speed; one wav per scene + narration.json)
 2  capture:    capture.py runs the REAL code in a throw-away environment (two Postgres clusters, the kit installed from the sibling checkout,
             the identity service, the llm-gateway, the demo apps, the notes example) and keeps its output and Playwright stills
 3  render:     every scene = audio + 0.7 s (0.35 s lead, 0.35 s tail); Pillow frames at 1920x1080, nothing moves; terminal scenes replay the
             captured output, typed and revealed in step with the narration
 4  mux:        ffmpeg libx264 crf 18, no B-frames, a forced I-frame at every picture change, AAC, soft subtitles
 5  checks:     longest still picture, longest voice gap, silences, audio and video lengths
Needs: macOS, Pillow, ffmpeg, uv, Google Chrome + node (Playwright from agentpg-kit/docs/video/node_modules), the sibling repositories with their
.venv (identity, llm-gateway, CityPulseSimulator), a Postgres 17 install (PGBIN) and the Kokoro venv of videos/tts. Nothing is downloaded by the build
except what `uv pip install` finds in its cache.
"""
import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ECO = HERE.parent.parent
ROOT = ECO.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ECO / "docs" / "video"))
import comp as C  # noqa: E402

spec = importlib.util.spec_from_file_location("eco_build", ECO / "docs" / "video" / "build.py")
EB = importlib.util.module_from_spec(spec)
spec.loader.exec_module(EB)
S = C.S
TTS = ROOT / "videos" / "tts"
CAPS = ROOT / "videos" / "live-captures"
FPS, LEAD, TAIL = 30, EB.LEAD, EB.TAIL
SCRIPT = ECO / "docs" / "VIDEO-SCRIPT.md"
TITLE = "Reusable components of the AgentPG ecosystem"


# ------------------------------------------------------------------------------------------------------------------ helpers
def tf(A, n, s, f=0.0, lead=0.0):
    """Time inside sentence s of paragraph n, at fraction f of the sentence, minus a small lead."""
    return round(max(A.cs(n, s) + f * (A.ce(n, s) - A.cs(n, s)) - lead, 0.0), 3)


def pf(A, n, s, phrase, lead=0.25):
    """Time at which `phrase` is spoken in sentence s of paragraph n (position in the text as a share of the sentence)."""
    text = EB.sentences(A.paras[n])[s]
    k = text.find(phrase)
    assert k >= 0, f"{phrase!r} not in {text!r}"
    return tf(A, n, s, k / max(len(text), 1), lead)


def cap_img(name, caption, crop=None, title="", sub="", date=None):
    tag = "CAPTURED from the live AgentPG fleet, " + (date or ("29 Oct 2026" if "2026-10-29" in name else "28 Oct 2026"))
    path = CAPS / (name if name.endswith(".jpg") else f"{name}-2026-10-28.jpg")
    return C.shot(path, caption, crop, title, sub, tag=tag)


def app_img(app, name, caption, crop=None, title="", sub=""):
    return C.shot(CAPS / "app-video-frames" / f"{app}-{name}-from-app-video.jpg", caption, crop, title, sub, tag="LOCAL: frame from our own app video (local run)")


class Scene:
    def __init__(self, A, F, cap, shots):
        self.A, self.F, self.cap, self.shots = A, F, cap, shots
        self.kf = []

    def at(self, t, img):
        self.kf.append((round(t, 3), self.F.save(img)))

    def shot(self, name, text, crop=None, title="", sub=""):
        return C.shot(self.shots / f"{name}.png", text, crop, title, sub)

    def term(self, t0, tag=C.LOCAL, fs=24, title="terminal"):
        return TermRun(self, t0, tag, fs, title)


class TermRun:
    def __init__(self, sc, t0, tag, fs, title):
        self.sc, self.tm = sc, C.Term(tag)
        self.tm.start(t0)
        self.render, self.cols = C.term_renderer(fs, title)
        self.t0, self.free = t0, t0
        self.tm.ts.at(t0, "line", ("$ ", "cmd"))
        self.fresh = True

    def cmd(self, t_out, step, type_s=None, out_s=0.7, text=None, shown=None):
        """Type the captured command so that its first output line appears at t_out."""
        c = self.sc.cap[step]
        cmd = shown or c["cmd"]
        body = c["text"] if text is None else text
        lines = body.split("\n") if body else []
        nominal = type_s or min(1.6, 0.025 * len(cmd) + 0.4)
        floor = max(self.free + 0.05, self.t0)
        room = t_out - 0.25 - out_s / max(len(lines), 1) - floor            # time left to type before the output must start
        type_s = max(0.3, min(nominal, room))                                # typed faster when the narration is tight
        start = max(t_out - type_s - 0.25 - out_s / max(len(lines), 1), floor)
        if self.fresh:                                   # the prompt line already exists: type onto it
            end = start + type_s
            for i, ch in enumerate(cmd):
                self.tm.ts.at(start + type_s * (i + 1) / len(cmd), "char", ch)
            self.fresh = False
        else:
            end = self.tm.ts.type_cmd(start, cmd, type_s)
        te = end + 0.25
        for k, ln in enumerate(lines):
            self.tm.ts.at(te + out_s * (k + 1) / max(len(lines), 1), "line", (ln, "out"))
        self.free = te + out_s + 0.05
        self.tm.ts.at(self.free, "line", ("$ ", "cmd"))
        late = te + out_s / max(len(lines), 1) - t_out
        if late > 0.6:
            print(f"  warning: terminal output {late:.2f} s after its cue ({step})")
        return self.free

    def clear(self, t):
        t = max(t, self.free)
        self.tm.ts.clear(t)
        self.tm.ts.at(t, "line", ("$ ", "cmd"))
        self.free = max(self.free, t)
        self.fresh = True

    def overlay(self, t, text):
        self.tm.ts.at(t, "overlay", text)

    def hl(self, t, idx):
        self.tm.ts.at(t, "hl", tuple(idx))

    def done(self, until):
        for t, img in self.tm.frames(self.render, until):
            self.sc.at(t, img)


def demo_lines(name):
    return (HERE / "demos" / name).read_text().rstrip("\n").split("\n")


NOTES_CORE = (ROOT / "agentpg-kit/docs/examples/notes/src/notes/core.py").read_text().split("\n")


# ------------------------------------------------------------------------------------------------------------------ scenes
def s1(sc):
    A = sc.A
    B = C.blocks_slide
    sc.at(0, B(("apps",), ("apps",), "Five apps: templates, Apache License 2.0"))
    sc.at(tf(A, 0, 1, 0, 0.1), B(("apps",), ("apps",), "Take one, adapt it, run it"))
    sc.at(tf(A, 1, 0, 0, 0.1), B(("apps", "kit", "services"), (), "Underneath: shared building blocks"))
    sc.at(tf(A, 1, 1, 0, 0.1), B(("apps", "kit", "services"), ("kit", "services"), "The building blocks can be used alone"))
    sc.at(tf(A, 1, 2, 0, 0.1), B(("apps", "kit", "services"), ("kit", "services"), "Only sign-in. Only a vault. Only one door to models."))
    rows = [("What problem it solves", ""), ("How to take only that block", ""), ("What the apps use it for", ""), ("What you would otherwise build", "")]
    sc.at(tf(A, 2, 0, 0, 0.1), C.list_slide("A tour of the blocks", "For each one", rows, n=0))
    for i in range(4):
        sc.at(pf(A, 2, 1, ["problem", "how to take", "the apps use", "otherwise"][i], 0.15) if i else tf(A, 2, 1, 0, 0.1),
              C.list_slide("A tour of the blocks", "For each one", rows, n=i + 1, hl=i))
    sc.at(tf(A, 2, 2, 0, 0.1), C.labels_slide(1))
    sc.at(pf(A, 2, 3, "captured", 0.15), C.labels_slide(2))
    sc.at(pf(A, 2, 3, "or slide", 0.15), C.labels_slide(3))


def s2(sc):
    A = sc.A
    names = ["identity and access", "data and Postgres", "AI access", "security and governance", "observability and tuning", "web and UI",
             "deployment and operations", "patterns inside the apps"]
    sc.at(0, C.groups_slide(lit=(), title="Eight groups of blocks", sub="The catalogue, group by group"))
    for i, nm in enumerate(names):
        sc.at(pf(A, 0, 0, nm, 0.2), C.groups_slide(lit=tuple(range(i + 1)), title="Eight groups of blocks", sub="The catalogue, group by group"))
    sc.at(tf(A, 1, 0, 0, 0.1), C.status_slide(0))
    sc.at(tf(A, 1, 1, 0, 0.1), C.status_slide(1))
    sc.at(tf(A, 1, 2, 0, 0.1), C.status_slide(2))
    sc.at(tf(A, 1, 3, 0, 0.1), C.status_slide(3))
    sc.at(tf(A, 1, 4, 0, 0.1), C.groups_slide(lit="all", full=True, title="Every block carries a status", sub="Library, service, or pattern to copy", hl_status="copy"))
    sc.at(tf(A, 2, 0, 0, 0.1), C.blocks_slide(("kit",), ("kit",), "agentpg-kit: Apache License 2.0, import name apgkit"))
    tr = sc.term(tf(A, 2, 0, 0.6, 0))
    tr.cmd(tf(A, 2, 1, 0.1, 0), "install", out_s=0.8)
    tr.cmd(tf(A, 2, 2, 0.6, 0.3), "version", out_s=0.2)
    tr.overlay(tf(A, 2, 2, 0.75), "Installed from the sibling checkout of the kit")
    tr.done(A.D)


def s3(sc):
    A = sc.A
    rows = [("auth: AppAuth, Principal, LocalAuth, Authenticator", "sessions, API tokens, the static key and federated tokens behind one require()", "lib"),
            ("TokenVerifier, RelyingParty, TokenSource, from_env", "strict JWT checks, browser login, service tokens, federation from environment", "lib"),
            ("identity: the OpenID Connect provider", "a service; swappable for any provider that sends the roles", "svc")]
    G = lambda n, h=None, c=None: C.list_slide("Identity and access", "Authentication and authorization", rows, n=n, hl=h, cap=c)  # noqa: E731
    sc.at(0, G(0, None, "Do we have authentication as a component?"))
    sc.at(pf(A, 0, 1, "Yes", 0.05), G(2, None, "Yes: a kit module, and a provider behind it"))
    sc.at(tf(A, 0, 2, 0, 0.1), G(3, None, "The kit module, and the identity service behind it"))
    code = demo_lines("protect.py")
    K = lambda hl, cap_: C.code_card("protect.py", code, hl=hl, cap=cap_, title="Protect an endpoint", sub="LOCAL: the whole file")  # noqa: E731
    sc.at(tf(A, 1, 0, 0, 0.1), K((), "A whole protected endpoint: fifteen lines"))
    sc.at(tf(A, 1, 1, 0, 0.1), K((5, 6, 7, 8), "The app says what roles and scopes mean, as permissions"))
    sc.at(tf(A, 1, 2, 0, 0.1), K((15,), "The route asks for one permission"))
    sc.at(tf(A, 1, 3, 0, 0.1), K((9, 10, 11, 15), "One line: session, API token, or identity-provider token"))
    tr = sc.term(tf(A, 1, 3, 0.5, 0.4))
    tr.cmd(tf(A, 1, 3, 0.62, 0), "mktoken_read", out_s=0.2)
    tr.cmd(tf(A, 1, 3, 0.85, 0), "mktoken_other", out_s=0.2)
    tr.overlay(tf(A, 1, 3, 0.62, 0), "Two API tokens: one with scope read, one with a scope that carries no permission")
    tr.cmd(tf(A, 2, 0, 0, -0.1), "curl_none", out_s=0.2)
    tr.overlay(tf(A, 2, 0, 0, 0), None)
    tr.cmd(tf(A, 2, 1, 0, -0.1), "curl_other", out_s=0.2)
    tr.cmd(tf(A, 2, 2, 0, -0.1), "curl_read", out_s=0.2)
    tr.done(tf(A, 3, 0, 0, 0.15))
    R = [("Sessions", ""), ("Hashed API tokens", ""), ("Scopes and roles mapped to permissions", ""), ("A permission check on every route", ""),
         ("A limit on failed logins", "")]
    for i in range(5):
        sc.at(tf(A, 3, 0, 0, 0.1) if i == 0 else tf(A, 3, 0, i / 5, 0.1), C.list_slide("What you would otherwise build", "Authentication", R, n=i + 1, hl=i))
    sc.at(tf(A, 3, 1, 0, 0.1), C.list_slide("What you would otherwise build", "Authentication", R, cap="Every app here uses this one module for all of it"))


def s4(sc):
    A = sc.A
    code = demo_lines("protect.py")
    sc.at(0, C.code_card("protect.py", code, hl=(10, 11), cap="The same file: from_env turns federation on", title="The same endpoint", sub="LOCAL"))
    sc.at(tf(A, 0, 1, 0, 0.1), sc.shot("identity-admin", "The identity service, running locally", (460, 90, 1460, 530), "Identity service", "LOCAL: console of the real service"))
    sc.at(tf(A, 1, 0, 0, 0.1), sc.shot("identity-apps", "Two apps registered: the demo app and a calling service", (460, 90, 1460, 910), "Identity service", "LOCAL: console of the real service"))
    tr = sc.term(tf(A, 1, 1, 0, 0.5))
    tr.cmd(tf(A, 1, 1, 0, -0.1), "svc_token", out_s=0.5)
    tr.overlay(tf(A, 1, 2, 0, 0.05), "Two variables: DEMO_OIDC_ISSUER and DEMO_OIDC_CLIENT_ID. The code does not change.")
    tr.overlay(tf(A, 2, 0, 0, 0.1), None)
    tr.clear(tf(A, 2, 0, 0, 0.12))
    tr.cmd(tf(A, 2, 0, 0, -0.1), "curl_svc", out_s=0.2)
    tr.cmd(tf(A, 2, 1, 0, -0.1), "curl_tampered", out_s=0.2)
    tr.cmd(tf(A, 2, 2, 0, -0.1), "curl_local_still", out_s=0.2)
    tr.done(tf(A, 3, 0, 0, 0.15))
    rows = [("Any OpenID Connect provider that sends the roles the app expects", "roles namespaced by app name; permissions come from the app's own map"),
            ("TokenSource: short-lived service tokens", "the client credentials grant, cached until near expiry")]
    sc.at(tf(A, 3, 0, 0, 0.1), C.list_slide("Identity is swappable", "Federation by environment", rows, n=1, hl=0))
    sc.at(tf(A, 3, 1, 0, 0.1), C.list_slide("Identity is swappable", "Federation by environment", rows, n=2, hl=1))


def s5(sc):
    A = sc.A
    code = demo_lines("vault_demo.py")
    sc.at(0, C.code_card("vault_demo.py", code, cap="vault: the secrets an app must store", title="Vault", sub="LOCAL: the whole file"))
    sc.at(tf(A, 0, 1, 0, 0.1), C.code_card("vault_demo.py", code, hl=(6, 7), cap="Model keys, webhook secrets, private notes", title="Vault", sub="LOCAL: the whole file"))
    tr = sc.term(tf(A, 1, 0, 0, 0.9))
    c = sc.cap["vault"]["text"].split("\n")
    tr.tm.ts.at(tf(A, 1, 0, 0, 0.0), "overlay", None)
    shown = sc.cap["vault"]["cmd"]
    # the real command prints four lines: they appear as the narration reaches them (stored and decrypted first, then locked and unlocked)
    tr.cmd(tf(A, 1, 1, 0.05, 0), "vault", out_s=0.4, text="\n".join(c[:2]))
    t = tf(A, 2, 0, 0, 0.05)
    for k, ln in enumerate(c[2:]):
        tr.tm.ts.at(t + 0.4 * (k + 1), "line", (ln, "out"))
    tr.free = t + 1.2
    tr.tm.ts.at(tr.free, "line", ("$ ", "cmd"))
    tr.overlay(tf(A, 1, 1, 0.3, 0), "A random data key, wrapped under the admin password")
    tr.overlay(tf(A, 2, 0, 0, 0.1), "After a restart: locked, until someone unlocks it")
    tr.cmd(tf(A, 2, 1, 0.0, 0), "vault_rows", out_s=0.3)
    tr.overlay(tf(A, 2, 1, 0, 0.1), "Stored in Postgres: only wrapped keys, never the data key itself")
    tr.done(A.D)


def s6(sc):
    A = sc.A
    code = demo_lines("wake.py")
    sc.at(0, C.code_card("wake.py", code, hl=(2,), cap="db.connect(): retries with backoff", title="Database access", sub="LOCAL: the whole file"))
    sc.at(tf(A, 0, 1, 0, 0.1), C.code_card("wake.py", code, hl=(2,), cap="A scaled-to-zero database: the first connection can beat the wake-up", title="Database access", sub="LOCAL: the whole file"))
    tr = sc.term(tf(A, 1, 0, 0, 0.5))
    tr.cmd(tf(A, 1, 0, 0.3, 0), "wake", out_s=2.6)
    tr.overlay(tf(A, 1, 1, 0, 0.1), "Retries 1 s, 2 s, 4 s: it says what is happening")
    tr.done(tf(A, 2, 0, 0, 0.12))
    d = demo_lines("data_demo.py")
    sc.at(tf(A, 2, 0, 0, 0.1), C.code_card("data_demo.py", d, hl=(6, 7, 8), cap="migrate.run: plain numbered .sql files, one table per app", title="Migrations, settings, pgvector", sub="LOCAL"))
    sc.at(pf(A, 2, 0, "settings table", 0.2), C.code_card("data_demo.py", d, hl=(11, 14), cap="settings_store, and the pgvector adapter", title="Migrations, settings, pgvector", sub="LOCAL"))
    tr = sc.term(tf(A, 2, 1, 0, 1.2))
    tr.cmd(tf(A, 2, 1, 0.0, 0), "data", out_s=0.7)
    tr.overlay(tf(A, 2, 1, 0.3, 0), "The second run applies nothing: each migration runs once")
    tr.done(tf(A, 3, 0, 0, 0.15))
    R = [("Retry logic for a waking database", ""), ("A migration tool", ""), ("A settings table", ""), ("The pgvector adapter set-up", "")]
    for i in range(4):
        sc.at(tf(A, 3, 1, i / 4, 0.1) if i else tf(A, 3, 0, 0, 0.1), C.list_slide("What you would otherwise write", "Again, for each app", R, n=i + 1, hl=i, cap="Used for their connections by the apps" if i == 0 else None))


def s7(sc):
    A = sc.A
    d = demo_lines("llm_demo.py")
    sc.at(0, C.code_card("llm_demo.py", d, hl=(5, 6), cap="The kit's chat client, with a guard for the address it calls", title="LLM client", sub="LOCAL: the whole file"))
    sc.at(tf(A, 0, 1, 0, 0.1), C.code_card("llm_demo.py", d, hl=(4,), cap="NetGuard: the SSRF guard for every URL the server will call", title="LLM client", sub="LOCAL: the whole file"))
    tr = sc.term(tf(A, 1, 0, 0, 0.9))
    tr.cmd(tf(A, 1, 0, 0.1, 0), "llm_refused", out_s=0.4)
    tr.cmd(tf(A, 1, 1, 0.1, 0), "llm_ok", out_s=0.4)
    tr.hl(tf(A, 1, 2, 0, 0.1), (7, 8))
    tr.done(tf(A, 2, 0, 0, 0.12))
    sc.at(tf(A, 2, 0, 0, 0.1), sc.shot("gw-clients", "llm-gateway console: each app is a client", (360, 60, 1560, 370), "LLM gateway", "LOCAL: console of the real service"))
    sc.at(pf(A, 2, 1, "its own revocable keys", 0.2), sc.shot("gw-clients", "Own keys, own models, requests per minute, a monthly token budget", (360, 60, 1560, 370), "LLM gateway", "LOCAL: console of the real service"))
    sc.at(tf(A, 3, 0, 0, 0.1), sc.shot("gw-models", "Aliases: one name, ordered provider targets", None, "LLM gateway", "LOCAL: console of the real service"))
    R = [("Provider keys held by every app", ""), ("Limits and budgets written into every app", "")]
    sc.at(tf(A, 3, 1, 0, 0.1), C.list_slide("What you would otherwise build", "Model access", R, n=1, hl=0))
    sc.at(pf(A, 3, 1, "limits", 0.1), C.list_slide("What you would otherwise build", "Model access", R, n=2, hl=1))


def s8(sc):
    A = sc.A
    g = demo_lines("guarded.py")
    sc.at(0, C.code_card("guarded.py", g, hl=(6,), cap="One line: the security middleware", title="Security middleware", sub="LOCAL: the whole file"))
    tr = sc.term(tf(A, 0, 1, 0, 1.2))
    tr.cmd(tf(A, 0, 1, 0.25, 0), "guard_headers", out_s=0.9)
    tr.cmd(tf(A, 1, 0, 0.1, 0), "guard_csrf", out_s=0.2)
    tr.cmd(tf(A, 1, 0, 0.7, 0), "guard_csrf_ok", out_s=0.2)
    tr.cmd(tf(A, 1, 1, 0.1, 0), "guard_big", out_s=0.2)
    tr.clear(tf(A, 2, 0, 0, 0.12))
    tr.cmd(tf(A, 2, 0, 0.1, 0), "guard_metrics", out_s=0.5)
    tr.clear(tf(A, 2, 1, 0.12, 0.0))
    tr.cmd(tf(A, 2, 1, 0.7, 0), "ratelimit", out_s=0.6)
    tr.done(tf(A, 3, 0, 0, 0.15))
    R = [("Security headers and a strict CSP", ""), ("A body cap and a CSRF check", ""), ("Request metrics", ""), ("A failed-login limiter", "")]
    for i in range(4):
        sc.at(tf(A, 3, 0, i / 4, 0.1), C.list_slide("What you would otherwise write", "Once per app, and the copies drift", R, n=i + 1, hl=i))


def s9(sc):
    A = sc.A
    m = demo_lines("mask_demo.py")
    sc.at(0, C.code_card("mask_demo.py", m, hl=(9,), cap="mask_view: the base table away, a masked view instead", title="Masking", sub="LOCAL: the whole file"))
    tr = sc.term(tf(A, 0, 1, 0, 1.4))
    tr.cmd(tf(A, 0, 1, 0.3, 0), "mask", out_s=0.2)
    tr.cmd(tf(A, 1, 0, 0.05, 0), "mask_base", out_s=0.2)
    tr.cmd(tf(A, 1, 0, 0.5, 0), "mask_view", out_s=0.6)
    tr.overlay(tf(A, 1, 1, 0, 0.1), "masking.reassert_forever(): re-applies the policy on a timer")
    tr.done(tf(A, 1, 2, 0, 0.12))
    sc.at(tf(A, 1, 2, 0, 0.1), cap_img("trustdesk-insights-top-queries-masking", "Live fleet: the masked views the kit created, seen in the query monitor",
                                         (273, 540, 1195, 812), "Masking", "Captured, not a mock-up"))
    tr = sc.term(tf(A, 2, 0, 0, 0.9))
    tr.cmd(tf(A, 2, 0, 0.12, 0), "ledger_apply", out_s=0.2)
    tr.cmd(tf(A, 2, 0, 0.4, 0), "ledger_write", out_s=0.6)
    tr.cmd(tf(A, 2, 0, 0.78, 0), "ledger_verify", out_s=0.5)
    tr.clear(tf(A, 2, 1, 0, 0.35))
    tr.cmd(tf(A, 2, 1, 0.1, 0), "ledger_update", out_s=0.3)
    tr.cmd(tf(A, 2, 2, 0.45, 0), "ledger_tamper", out_s=0.6)
    tr.overlay(tf(A, 2, 0, 0, 0.1), "paybroker: migrations/007_audit_ledger.sql, applied to an empty database")
    tr.done(tf(A, 3, 0, 0, 0.15))
    R = [("paybroker/migrations/007_audit_ledger.sql", "one file, core Postgres only"), ("audit_append(...) in your own transaction", "the entry commits or rolls back with the change"),
         ("audit_verify_detail()", "names the first broken entry")]
    for i in range(3):
        sc.at(tf(A, 3, 0, i / 3, 0.1), C.list_slide("A pattern to copy", "The file lifts out as is", R, n=i + 1, hl=i, tag="SLIDE"))


def s10(sc):
    A = sc.A
    t = dict(title="Insights", sub="Captured: needs the EDB extensions")
    sc.at(0, cap_img("trustdesk-insights-top-queries-masking", "Top queries: edb_stat_monitor", (273, 120, 1195, 560), **t))
    sc.at(pf(A, 0, 0, "tuner settings", 0.2), cap_img("trustdesk-insights-tuner-advisor", "Settings: edb_pg_tuner", (273, 140, 1195, 380), **t))
    sc.at(pf(A, 0, 0, "index suggestions", 0.2), cap_img("trustdesk-insights-tuner-advisor", "Index suggestions: query_advisor", (273, 380, 1195, 750), **t))
    sc.at(tf(A, 0, 1, 0, 0.1), cap_img("trustdesk-insights-top-queries-masking", "TrustDesk, Database page, live fleet", **t))
    R = [("top_queries()", "edb_stat_monitor, or pg_stat_statements"), ("tuner()", "edb_pg_tuner settings"), ("advisor()", "query_advisor index suggestions"),
         ("Missing extension?", "that source returns available: false, with the reason")]
    sc.at(tf(A, 1, 0, 0, 0.1), C.list_slide("Insights degrade politely", "apgkit.insights", R, n=3))
    sc.at(tf(A, 1, 1, 0, 0.1), C.list_slide("Insights degrade politely", "apgkit.insights", R, n=4, hl=3))
    M = [("http_requests_total, http_request_duration_seconds", "per app, method, route and status"), ("app_info, db_up", "version, and whether the database answers"),
         ("masking_last_ok_timestamp, masking_drift_tables", "the masking policy kept in force")]
    for i in range(3):
        sc.at(tf(A, 2, 0, i / 3, 0.1), C.list_slide("The same metric names in every app", "apgkit.metrics", M, n=i + 1, hl=i, cap="One dashboard fits all" if i == 2 else None))
    sc.at(tf(A, 2, 1, 0, 0.1), C.list_slide("The same metric names in every app", "apgkit.metrics", M, cap="Otherwise: a query page and a metrics convention for each app"))


def s11(sc):
    A = sc.A
    F5 = [("ui.js", "223 lines: tables, badges, toasts, loading and error states, confirmDialog and promptDialog"), ("base.css", "225 lines: light and dark theme"),
          ("session.js", "144 lines: session bootstrap and the vault unlock banner"), ("insights.js", "106 lines: the Insights page"), ("api.js", "38 lines: fetch with the CSRF header")]
    sc.at(0, C.list_slide("The shared UI, served at /kit/", "agentpg-kit/src/apgkit/static", F5, n=1, hl=0))
    for i in range(1, 5):
        sc.at(tf(A, 0, 0, i / 5, 0.1), C.list_slide("The shared UI, served at /kit/", "agentpg-kit/src/apgkit/static", F5, n=i + 1, hl=i))
    sc.at(tf(A, 0, 1, 0, 0.1), sc.shot("notes-list", "The notes example, styled by /kit/ only", (520, 90, 1400, 690), "Shared UI", "LOCAL: the running example app"))
    T = [("Every badge and status colour: 4.5 to 1 contrast, in light and dark", "tests/ui/contrast.mjs"), ("Every form control has an accessible name", "tests/ui/a11y.mjs"),
         ("A test fails if shared code calls a native dialog", "tests/test_ui.py")]
    for i in range(3):
        sc.at(tf(A, 1, 0, i / 3, 0.1), C.list_slide("Tested in the kit", "Accessibility", T, n=i + 1, hl=i))
    sc.at(tf(A, 1, 1, 0, 0.1), C.list_slide("Tested in the kit", "Accessibility", T, cap="Otherwise every app grows its own look, and its own bugs"))


def s12(sc):
    A = sc.A
    sc.at(0, C.matrix_slide(0))
    for j, w in enumerate(["Docker file", "health endpoint", "readiness endpoint", "numbered migrations", "metrics endpoint"]):
        sc.at(pf(A, 0, 0, w, 0.2), C.matrix_slide(j + 1))
    tr = sc.term(tf(A, 0, 1, 0, 0.5))
    tr.cmd(tf(A, 0, 1, 0.1, 0), "notes_health", out_s=0.3)
    tr.overlay(tf(A, 0, 1, 0.1, 0), "The notes example: the same two endpoints")
    tr.done(tf(A, 1, 0, 0, 0.12))
    R = [("ecoctl: one manifest, a dry run first", "creates and wires a fleet on AgentPG; ecoctl/agentpg.py is a stdlib-only wrapper"),
         ("Ansible: agentpg_instance, agentpg_sandbox, agentpg_extension", "idempotent, resumable, with check mode"),
         ("Patterns to adapt, not packages", "docs/COMPONENTS.md says where each one lives")]
    for i in range(3):
        sc.at(tf(A, 1, i, 0, 0.1), C.list_slide("Deploying fleets", "Patterns to adapt", R, n=i + 1, hl=i, tag="SLIDE"))


def s13(sc):
    A = sc.A
    sc.at(0, C.groups_slide(lit=(7,), title="Patterns inside the apps", sub="Labelled honestly", legend=True, hl_status="copy"))
    t = dict(title="Search", sub="ShopFront")
    sc.at(tf(A, 1, 0, 0, 0.1), app_img("shopfront", "hybrid-search", "Words and meaning in one SQL statement: reciprocal rank fusion", **t))
    sc.at(tf(A, 1, 1, 0, 0.1), app_img("shopfront", "did-you-mean", "Typo-tolerant did-you-mean: kettel, kettle", **t))
    R = [("trustdesk/search.py", "vectors, full text, trigrams"), ("pagerlens/retrieval.py", "runbook search"), ("shopfront/search.py", "products"),
         ("Written three times", "a kit module is the obvious next step")]
    for i in range(4):
        sc.at(tf(A, 1, 2, i / 4, 0.1), C.list_slide("Reciprocal rank fusion", "A pattern to copy", R, n=i + 1, hl=i))
    sc.at(tf(A, 2, 0, 0, 0.1), cap_img("citypulse-map-brussels-2026-10-29.jpg", "CityPulse: PostGIS areas and a nearby query", None, "Maps", "Captured, not a mock-up"))
    sc.at(tf(A, 2, 1, 0, 0.1), cap_img("citypulse-map-brussels-2026-10-29.jpg", "Real commune outlines from OpenStreetMap: attribution required", None, "Maps", "Captured, not a mock-up"))
    sc.at(tf(A, 3, 0, 0, 0.1), app_img("shopfront", "payment-trail", "ShopFront: the order updates live through Postgres tcn", title="Live updates", sub="No message broker"))
    tr = sc.term(tf(A, 4, 0, 0, 0.5))
    tr.cmd(tf(A, 4, 1, 0.25, 0), "webhook", out_s=0.5)
    tr.overlay(tf(A, 4, 1, 0.3, 0), "shopfront/src/shopfront/webhooks.py, copied as is")
    tr.clear(tf(A, 4, 2, 0, 0.2))
    tr.cmd(tf(A, 4, 2, 0.35, 0), "place", out_s=0.3)
    tr.overlay(tf(A, 4, 2, 0.3, 0), "cpsim/realcities.py: standard library only")
    tr.done(A.D)


def s14(sc):
    A = sc.A
    core = NOTES_CORE[23:32]
    sc.at(0, C.code_card("notes/core.py (lines 24 to 32)", core, hl=(0, 2, 4, 5, 7), cap="The kit's blocks, wired in nine lines", title="A new app from the blocks", sub="The notes example", numbers_from=24, tag="SLIDE: lines of the real file"))
    tr = sc.term(tf(A, 0, 1, 0, 1.4), tag="LOCAL: the example's real files")
    tr.cmd(tf(A, 0, 1, 0.1, 0), "notes_files", out_s=0.8)
    tr.done(tf(A, 1, 0, 0, 0.12))
    sc.at(tf(A, 1, 0, 0, 0.1), sc.shot("notes-setup", "First-run setup: a one-time code from the log", (690, 100, 1230, 860), "The notes example", "LOCAL: running from docs/examples/notes"))
    sc.at(tf(A, 1, 1, 0, 0.1), C.code_card("notes/core.py (lines 24 to 32)", core, hl=tuple(range(9)), cap="Everything else is the kit", title="A new app from the blocks", sub="The notes example", numbers_from=24, tag="SLIDE: lines of the real file"))
    sc.at(tf(A, 1, 2, 0, 0.1), sc.shot("notes-list", "Running: notes, with a private note encrypted by the vault", (520, 90, 1400, 690), "The notes example", "LOCAL: running from docs/examples/notes"))


def s15(sc):
    A = sc.A
    T = dict(title="The whole catalogue", sub="Library, service, or pattern to copy", full=True, lit="all")
    sc.at(0, C.groups_slide(**T))
    sc.at(tf(A, 0, 1, 0, 0.1), C.groups_slide(hl_status="lib", **T))
    sc.at(tf(A, 0, 2, 0, 0.1), C.groups_slide(hl_status="svc", **T))
    sc.at(tf(A, 0, 3, 0, 0.1), C.groups_slide(hl_status="copy", **T))
    R = [("name, where it lives, what it does", ""), ("how to use it alone", ""), ("what lifting it out costs, and its status", "")]
    for i in range(3):
        sc.at(tf(A, 1, 0, i / 3 if i else 0, 0.1), C.list_slide("docs/COMPONENTS.md", "In the ecosystem repository", R, n=i + 1, hl=i))
    sc.at(tf(A, 1, 1, 0, 0.1), C.list_slide("Apache License 2.0", "agentpg-kit, identity, llm-gateway and the five apps", [
        ("agentpg-kit", "the shared library"), ("identity, llm-gateway", "the two services"), ("TrustDesk, PagerLens, CityPulse, PayBroker, ShopFront", "the five apps")]))
    sc.at(tf(A, 2, 0, 0, 0.1), C.endcard())


SCENES = {i + 1: f for i, f in enumerate([s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12, s13, s14, s15])}


# ------------------------------------------------------------------------------------------------------------------ script mirror
def sync_script(paras):
    """Replace the 'Components video' section of docs/VIDEO-SCRIPT.md with the scene table built from narration.txt."""
    marker = "## Components video"
    text = SCRIPT.read_text() if SCRIPT.exists() else ""
    head = text.split(marker)[0].rstrip() + "\n\n" if text else ""
    rows = ["| # | Voice | Sources | Narration |", "|---|---|---|---|"]
    voices = dict(re.findall(r"(?m)^##\s+(\S+)\s+(\S+)\s*$", (HERE / "narration.txt").read_text()))
    src = {1: "SLIDE", 2: "SLIDE, LOCAL terminal", 3: "SLIDE, LOCAL terminal", 4: "LOCAL terminal and console", 5: "LOCAL terminal", 6: "LOCAL terminal",
           7: "LOCAL terminal and console", 8: "LOCAL terminal", 9: "LOCAL terminal, CAPTURED still (28 Oct 2026)", 10: "CAPTURED stills (28 Oct 2026)", 11: "SLIDE, LOCAL screen",
           12: "SLIDE, LOCAL terminal", 13: "LOCAL terminal, LOCAL app frames, CAPTURED still (29 Oct 2026)", 14: "SLIDE, LOCAL terminal and screens", 15: "SLIDE"}
    total = 0
    for k in sorted(paras, key=int):
        t = " ".join(paras[k])
        total += len(t.split())
        rows.append(f"| {k} | {voices.get(k, '')} | {src[int(k)]} | {t} |")
    body = (f"{marker}\n\n`docs/video-components/` builds `components.mp4` (about eight minutes, {total} words of narration) with `python3 docs/video-components/build.py`.\n"
            "Source of the narration: `docs/video-components/narration.txt`; the real runs are in `capture.py` and `demos/`. Voices: am_liam, af_heart, am_eric only,\n"
            "alternating male and female, never the same voice twice in a row.\n\n" + "\n".join(rows) + "\n")
    SCRIPT.write_text(head + body)
    print(f"VIDEO-SCRIPT.md: components section written ({total} words)")


# ------------------------------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=str(HERE / "out" / "build"))
    ap.add_argument("--name", default="components")
    ap.add_argument("--deliver", default=None)
    ap.add_argument("--keep-audio", action="store_true")
    ap.add_argument("--keep-capture", action="store_true")
    ap.add_argument("--sync-script", action="store_true")
    a = ap.parse_args()
    paras = EB.parse_narration(HERE / "narration.txt")
    if a.sync_script:
        sync_script(paras)
        return 0
    work = Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    adir = work / "audio"
    if not (a.keep_audio and (adir / "narration.json").exists()):
        EB.run(TTS / ".venv" / "bin" / "python", TTS / "narrate.py", "scenes", HERE / "narration.txt", adir, "--voice", "am_liam", "--pronounce", HERE / "pronounce.json")
    narr = json.loads((adir / "narration.json").read_text())
    order = sorted(narr, key=int)
    assert sorted(map(int, order)) == sorted(map(int, paras)), "narration.txt scenes differ from narration.json"
    cdir = work / "capture"
    if not (a.keep_capture and (cdir / "capture.json").exists()):
        shutil.rmtree(cdir, ignore_errors=True)
        EB.run(sys.executable, HERE / "capture.py", cdir)
    cap = json.loads((cdir / "capture.json").read_text())
    fdir = work / "frames"
    shutil.rmtree(fdir, ignore_errors=True)
    F = EB.Frames(fdir)
    timeline, starts, t0, auds = [], {}, 0.0, {}
    for sid in order:
        A = EB.Audio(sid, narr[sid], paras[sid])
        auds[sid], starts[sid] = A, t0
        sc = Scene(A, F, cap, cdir / "shots")
        SCENES[int(sid)](sc)
        kf = sorted(sc.kf, key=lambda x: x[0])
        assert kf and kf[0][0] == 0.0, f"scene {sid}: no frame at t=0"
        ts_ = [t for t, _ in kf] + [A.D]
        worst = max(ts_[i + 1] - ts_[i] for i in range(len(kf)))
        timeline += [(round(t0 + t, 3), p) for t, p in kf if t < A.D]
        t0 = round(t0 + A.D, 3)
        print(f"scene {sid}: {A.D:6.2f} s, {len(kf)} keyframes, longest still {worst:.1f} s")
    total = t0
    lst = work / "frames.txt"
    with open(lst, "w") as f:
        for (t, p), nxt in zip(timeline, [x[0] for x in timeline[1:]] + [total], strict=True):
            f.write(f"file '{p}'\nduration {nxt - t:.3f}\n")
        f.write(f"file '{timeline[-1][1]}'\n")
    parts = []
    for sid in order:
        out = adir / f"pad-{sid}.wav"
        EB.run(EB.FFMPEG, "-v", "error", "-y", "-i", adir / f"scene-{sid}.wav", "-af", f"adelay={int(LEAD * 1000)}:all=1,apad=whole_dur={auds[sid].D}",
               "-ar", "24000", "-ac", "1", out)
        parts.append(out)
    (adir / "concat.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    EB.run(EB.FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", adir / "concat.txt", "-c", "copy", work / "audio.wav")
    srt = work / f"{a.name}.srt"
    EB.write_srt(srt, order, narr, starts)
    mp4 = work / f"{a.name}.mp4"
    force_keys = ",".join(f"{t:.3f}" for t, _ in timeline)
    EB.run(EB.FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", work / "audio.wav", "-i", srt,
           "-map", "0:v", "-map", "1:a", "-map", "2:s", "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-bf", "0", "-g", "100000", "-force_key_frames", force_keys, "-x264-params", "mbtree=0:aq-mode=0:scenecut=0",
           "-c:a", "aac", "-b:a", "160k", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", "-metadata", f"title={TITLE}",
           "-t", f"{total:.3f}", "-movflags", "+faststart", mp4)
    v, au = EB.probe(mp4), EB.probe(work / "audio.wav")
    print(f"{mp4}: {v:.2f} s video ({int(v // 60)}:{v % 60:04.1f}), {au:.2f} s audio, planned {total:.2f} s")
    for sid in order:
        c = narr[sid]["cues"]
        gaps = [c[i + 1]["start"] - c[i]["end"] for i in range(len(c) - 1)]
        print(f"  scene {sid}: voice gap max {max(gaps, default=0):.2f} s, scene {auds[sid].D:.1f} s")
    sd = subprocess.run([EB.FFMPEG, "-hide_banner", "-i", str(mp4), "-af", "silencedetect=n=-45dB:d=1.5", "-f", "null", "-"], capture_output=True, text=True).stderr
    print("silences over 1.5 s in the final audio:", re.findall(r"silence_duration: ([\d.]+)", sd) or "none")
    if a.deliver:
        d = Path(a.deliver)
        d.mkdir(parents=True, exist_ok=True)
        for p in (mp4, srt):
            shutil.copy(p, d / p.name)
        print(f"delivered to {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
