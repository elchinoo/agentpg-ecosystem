"""Drawing for the components video (Pillow only, 1920x1080). Re-uses the primitives of ../video/slides.py and the terminal replay of
../video/record.py; adds code cards, a terminal renderer with a source label, and the component slides.

Every picture is drawn once and held still: no pan, zoom or drift. Long sentences are broken up by revealing or highlighting.
"""
import re
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "video"))
import record as R  # noqa: E402
import slides as S  # noqa: E402

W, H = S.W, S.H
LOCAL, CAPT, SLIDE = "LOCAL: real code run on this machine", "CAPTURED from the live AgentPG fleet, 28 Oct 2026", "SLIDE"
GREEN, BLUE, AMBER, PURPLE, TEAL, RED = S.GREEN, S.BLUE, S.AMBER, S.PURPLE, S.TEAL, S.RED
STATUS = {"lib": ("library", GREEN), "svc": ("service", BLUE), "copy": ("copy the pattern", AMBER)}
FOOT_L, FOOT_R = "AgentPG components  |  agentpg-kit", "Synthetic data only"


def base(title, subtitle=None, tag=None, right=FOOT_R):
    img = Image.new("RGB", (W, H), S.BG)
    d = ImageDraw.Draw(img)
    d.text((60, 36), title, font=S.font("sans", 46, True), fill=S.TXT)
    if subtitle:
        d.text((62, 98), subtitle, font=S.font("sans", 26), fill=S.MUTED)
    d.line((0, 1040, W, 1040), fill=S.PANEL2, width=2)
    d.text((60, 1060), FOOT_L, font=S.font("sans", 20), fill=S.FAINT, anchor="lm")
    d.text((W - 60, 1060), right, font=S.font("sans", 20), fill=S.FAINT, anchor="rm")
    if tag:
        f = S.font("sans", 20, True)
        tw = f.getlength(tag) + 36
        S.rrect(d, (W - 60 - tw, 40, W - 60, 82), outline=AMBER, width=2, r=21)
        d.text((W - 60 - tw / 2, 61), tag, font=f, fill=AMBER, anchor="mm")
    return img, d


S.base = base                                            # the shared slide helpers (capture_slide, text_slide) now carry this footer


def caption(d, text, y0=905, y1=1005):
    S.rrect(d, (60, y0, W - 60, y1), fill=S.PANEL, outline=AMBER, width=3, r=14)
    lines = S.wrap(text, S.font("sans", 34), W - 190)[:2]
    for i, ln in enumerate(lines):
        d.text((96, (y0 + y1) // 2 if len(lines) == 1 else y0 + 30 + i * 44), ln, font=S.font("sans", 34), fill=S.TXT, anchor="lm")


# ------------------------------------------------------------------------------------------------------------ pictures
def shot(path, text, crop=None, title="", sub="", tag=LOCAL):
    return S.capture_slide(path, text, crop, title=title, sub=sub, tag=tag)


# ------------------------------------------------------------------------------------------------------------ code card
KW = {"from", "import", "def", "return", "with", "as", "try", "except", "if", "else", "for", "in", "print", "lambda", "not", "and", "or"}


def _colour_line(d, x, y, text, mono, cw, bold_mono):
    """Very small Python highlighter: comments, strings, keywords, numbers."""
    pos = 0
    for m in re.finditer(r"(#.*$)|(\"[^\"]*\"|'[^']*')|(@[\w.]+)|\b(\w+)\b", text):
        if m.start() > pos:
            d.text((x + pos * cw, y), text[pos:m.start()], font=mono, fill=S.TXT)
        t = m.group(0)
        col = S.FAINT if m.group(1) else (214, 190, 140) if m.group(2) else TEAL if m.group(3) else \
            PURPLE if t in KW else S.BLUE if t.isdigit() else S.TXT
        d.text((x + m.start() * cw, y), t, font=mono, fill=col)
        pos = m.end()
    if pos < len(text):
        d.text((x + pos * cw, y), text[pos:], font=mono, fill=S.TXT)


def code_card(name, lines, hl=(), cap=None, tag=LOCAL, title="The code", sub="", numbers_from=1, gaps=()):
    """lines: source lines (shown as is). hl: 0-based indexes of highlighted lines. gaps: indexes after which a '...' separator is drawn."""
    img, d = base(title, sub or name, tag)
    widest = max(len(ln) for ln in lines)
    fs = 30
    while S.font("mono", fs).getlength("M") * (widest + 6) > 1740 and fs > 18:
        fs -= 1
    mono = S.font("mono", fs)
    cw, lh = mono.getlength("M"), int(fs * 1.42)
    top = 130
    bottom = 880 if cap else 1010
    n = len(lines) + len(gaps)
    while lh * n + 100 > bottom - top and fs > 16:
        fs -= 1
        mono = S.font("mono", fs)
        cw, lh = mono.getlength("M"), int(fs * 1.42)
    S.rrect(d, (60, top, W - 60, min(bottom, top + 80 + lh * n)), fill=S.PANEL, outline=S.LINE, width=2, r=14)
    S.rrect(d, (60, top, W - 60, top + 52), fill=S.PANEL2, outline=S.LINE, width=2, r=14)
    d.text((92, top + 26), name, font=S.font("sans", 22, True), fill=S.TXT, anchor="lm")
    y = top + 70
    for i, ln in enumerate(lines):
        if i in hl:
            d.rectangle((62, y - 3, W - 62, y + lh - 5), fill=S.mix(AMBER, S.PANEL, 0.2))
            d.rectangle((62, y - 3, 70, y + lh - 5), fill=AMBER)
        d.text((130, y), f"{numbers_from + i:>2}", font=mono, fill=AMBER if i in hl else S.FAINT, anchor="ra")
        _colour_line(d, 160, y, ln, mono, cw, mono)
        y += lh
        if i in gaps:
            d.text((160, y), "...", font=mono, fill=S.FAINT)
            y += lh
    if cap:
        caption(d, cap)
    return img


# ------------------------------------------------------------------------------------------------------------ terminal
def _segments(text, kind):
    if kind == "cmd":
        return [(text[:2], S.GREEN, True), (text[2:], S.TXT, False)] if text.startswith("$ ") else [(text, S.TXT, False)]
    segs = []
    for part in re.split(r"(HTTP 2\d\d|HTTP 40[13]|HTTP 429|HTTP 413|\b401\b|\b403\b|\b429\b|\b200\b|refused|denied|ERROR:|Traceback|"
                         r"locked|accepted|unlocked|connected:|answer:)", text):
        if not part:
            continue
        if re.fullmatch(r"HTTP 2\d\d|200|accepted|unlocked|connected:|answer:", part):
            segs.append((part, S.GREEN, True))
        elif re.fullmatch(r"HTTP 4\d\d|401|403|429|413|refused|denied|ERROR:|Traceback|locked", part):
            segs.append((part, S.AMBER if part != "ERROR:" else S.RED, True))
        else:
            segs.append((part, (200, 204, 212), False))
    return segs


def term_renderer(fs=24, title="terminal"):
    cols, rows, cw, LH = R._geometry(fs)
    mono, monob = S.font("mono", fs), S.font("mono", fs, True)

    def render(state):
        img = Image.new("RGB", (W, H), (22, 24, 29))
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, W, R.CHROME), fill=(38, 41, 49))
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse((24 + i * 32, 18, 44 + i * 32, 38), fill=c)
        d.text((W // 2, R.CHROME // 2), title, font=S.font("sans", 22), fill=S.MUTED, anchor="mm")
        if state["badge"]:
            f = S.font("sans", 20, True)
            tw = f.getlength(state["badge"]) + 32
            S.rrect(d, (W - 30 - tw, 12, W - 30, 44), outline=AMBER, width=2, r=16)
            d.text((W - 30 - tw / 2, 28), state["badge"], font=f, fill=AMBER, anchor="mm")
        d.line((0, R.FOOT, W, R.FOOT), fill=S.PANEL2, width=2)
        d.text((60, 1060), FOOT_L, font=S.font("sans", 20), fill=S.FAINT, anchor="lm")
        d.text((W - 60, 1060), FOOT_R, font=S.font("sans", 20), fill=S.FAINT, anchor="rm")
        disp = []
        for li, (text, kind) in enumerate(state["lines"]):
            lead = len(text) - len(text.lstrip())
            parts = (textwrap.wrap(text, cols, subsequent_indent="    ", break_long_words=True, break_on_hyphens=False) if kind == "cmd" else textwrap.wrap(text, cols, subsequent_indent=" " * (lead + 4), replace_whitespace=False,
                                                                break_long_words=True, break_on_hyphens=False)) or [""]
            disp += [(p, kind, li) for p in parts]
        disp = disp[-rows:]
        y = R.CHROME + 20
        for text, kind, li in disp:
            if li in state["hl"]:
                d.rectangle((14, y - 3, W - 14, y + LH - 5), fill=S.mix(AMBER, (22, 24, 29), 0.18))
            x = R.PAD
            for seg, col, bold in _segments(text, kind):
                d.text((x, y), seg, font=monob if bold else mono, fill=col)
                x += len(seg) * cw
            y += LH
        if disp:
            d.rectangle((R.PAD + len(disp[-1][0]) * cw, y - LH + 3, R.PAD + len(disp[-1][0]) * cw + cw * 0.7, y - 5), fill=(200, 204, 212))
        ov = state["overlay"]
        if isinstance(ov, str):
            S.rrect(d, (120, 900, W - 120, 1010), fill=(30, 34, 44), outline=AMBER, width=3)
            ls = S.wrap(ov, S.font("sans", 34), W - 320)[:2]
            for i, ln in enumerate(ls):
                d.text((160, 955 if len(ls) == 1 else 928 + i * 44), ln, font=S.font("sans", 34), fill=S.TXT, anchor="lm")
        return img

    return render, cols


class Term:
    """Builds a terminal timeline in scene time. run(t, cmd, text, type_s, out_s) types a command at t and then shows its output."""

    def __init__(self, tag=LOCAL):
        self.ts, self.tag = R.TermScript(), tag
        self.t0 = None

    def start(self, t):
        self.t0 = t
        self.ts.at(t, "badge", self.tag)

    def clear(self, t):
        self.ts.clear(t)

    def run(self, t, cmd, text, type_s=1.2, out_s=1.0, prompt_after=False):
        """Returns the time at which the output is complete."""
        end = self.ts.type_cmd(t, cmd, type_s)
        lines = text.split("\n") if text else []
        te = end + 0.25
        for k, ln in enumerate(lines):
            self.ts.at(te + out_s * (k + 1) / max(len(lines), 1), "line", (ln, "out"))
        done = te + out_s
        if prompt_after:
            self.ts.at(done + 0.05, "line", ("$ ", "cmd"))
        return done

    def overlay(self, t, text):
        self.ts.at(t, "overlay", text)

    def hl(self, t, idx):
        self.ts.at(t, "hl", tuple(idx))

    def badge(self, t, text):
        self.ts.at(t, "badge", text)

    def frames(self, render, until):
        return [(t, img) for t, img in self.ts.replay(render, until) if self.t0 is None or t >= self.t0 - 1e-6]


# ------------------------------------------------------------------------------------------------------------ slides
def box(d, xy, title, sub=None, col=S.LINE, on=True, tfs=28, sfs=20, fill=S.PANEL):
    S.rrect(d, xy, fill=fill, outline=col if on else S.mix(col, S.BG, 0.45), width=4 if on else 2)
    x0, y0, x1, y1 = xy
    if sub:
        d.text(((x0 + x1) // 2, y0 + (y1 - y0) * 0.36), title, font=S.font("sans", tfs, True), fill=S.TXT if on else S.FAINT, anchor="mm")
        d.text(((x0 + x1) // 2, y0 + (y1 - y0) * 0.72), sub, font=S.font("sans", sfs), fill=S.MUTED if on else S.FAINT, anchor="mm")
    else:
        d.text(((x0 + x1) // 2, (y0 + y1) // 2), title, font=S.font("sans", tfs, True), fill=S.TXT if on else S.FAINT, anchor="mm")


APPS5 = [("TrustDesk", "compliance"), ("PagerLens", "incidents"), ("CityPulse", "smart city"), ("PayBroker", "payments"), ("ShopFront", "web shop")]
KIT_MODULES = ["auth", "vault", "db", "migrate", "llm", "netguard", "guard", "masking", "insights", "metrics", "ui"]


def blocks_slide(show=("apps",), hl=(), lower=None):
    """Scene 1: five apps on top of shared blocks. show: subset of apps, kit, services. hl: apps, kit, services."""
    img, d = base("Templates on top, blocks underneath", "What every app is built from", SLIDE)
    if "apps" in show:
        for i, (t, s) in enumerate(APPS5):
            x0 = 60 + i * 364
            box(d, (x0, 190, x0 + 340, 330), t, s, S.BLUE, on="apps" in hl or not hl)
        d.text((60, 352), "Five apps: templates, Apache License 2.0", font=S.font("sans", 26, True), fill=S.MUTED)
    if "kit" in show:
        S.rrect(d, (60, 420, W - 60, 640), fill=S.PANEL, outline=GREEN if "kit" in hl else S.LINE, width=5 if "kit" in hl else 2)
        d.text((92, 452), "agentpg-kit: a library, import name apgkit, Apache License 2.0", font=S.font("sans", 30, True), fill=GREEN, anchor="lm")
        x, y = 92, 505
        for m in KIT_MODULES:
            f = S.font("mono", 26)
            tw = f.getlength(m) + 36
            S.rrect(d, (x, y, x + tw, y + 48), fill=S.mix(GREEN, S.PANEL, 0.18), outline=GREEN, width=2, r=12)
            d.text((x + tw / 2, y + 24), m, font=f, fill=S.TXT, anchor="mm")
            x += tw + 14
            if x > W - 300:
                x, y = 92, y + 62
    if "services" in show:
        for i, (t, s) in enumerate([("identity", "OpenID Connect provider: a service"), ("llm-gateway", "OpenAI-compatible gateway: a service")]):
            x0 = 60 + i * 920
            box(d, (x0, 700, x0 + 880, 840), t, s, BLUE, on="services" in hl or not hl, tfs=32, sfs=24)
    if lower:
        d_ = ImageDraw.Draw(img)
        caption(d_, lower, 900, 1000)
    return img


GROUPS = [
    ("Identity and access", [("auth", "lib"), ("passwords, rate limiter", "lib"), ("identity service", "svc")]),
    ("Data and Postgres", [("db: wake-up retry, pgvector", "lib"), ("migrate and settings", "lib"), ("config and ids", "lib"), ("accent-folded fuzzy search", "copy")]),
    ("AI access", [("llm client and SSRF guard", "lib"), ("llm-gateway", "svc"), ("hybrid search (RRF)", "copy"), ("Ask loop, verified citations", "copy"),
                   ("verified-quote check", "copy"), ("typo-tolerant suggest", "copy")]),
    ("Security and governance", [("vault", "lib"), ("security middleware", "lib"), ("masking", "lib"), ("audit ledger", "copy"), ("webhook signing", "copy"),
                                 ("row-level security", "copy"), ("idempotency keys", "copy")]),
    ("Observability and tuning", [("insights", "lib"), ("metrics names", "lib"), ("propose-and-approve tuning", "copy")]),
    ("Web and UI", [("shared UI at /kit/", "lib")]),
    ("Deployment and operations", [("Dockerfile, health, migrations", "copy"), ("notes example app", "copy"), ("ecoctl", "copy"), ("Ansible modules", "copy")]),
    ("Patterns inside the apps", [("live updates with tcn", "copy"), ("PostGIS areas, Nearby", "copy"), ("one SQL, five data models", "copy"),
                                  ("pattern memory", "copy"), ("real-city placement", "copy")]),
]


def groups_slide(lit=(), full=False, legend=False, title="The component catalogue", sub="Eight groups", tag=SLIDE, hl_status=None):
    """lit: indexes of groups drawn lit (others dim). full: list the blocks with a status dot."""
    img, d = base(title, sub, tag)
    gw, gh, gx, gy = 435, 395 if full else 340, 20, 20
    for i, (name, items) in enumerate(GROUPS):
        r, c = divmod(i, 4)
        x0, y0 = 60 + c * (gw + gx), 150 + r * (gh + gy)
        on = (i in lit) if lit != "all" else True
        col = S.mix(S.BLUE, S.BG, 1.0) if on else S.LINE
        S.rrect(d, (x0, y0, x0 + gw, y0 + gh), fill=S.PANEL if on else S.mix(S.PANEL, S.BG, 0.5), outline=AMBER if on and not full else col, width=3 if on else 1)
        d.text((x0 + 22, y0 + 38), name, font=S.font("sans", 28 if not full else 26, True), fill=S.TXT if on else S.FAINT, anchor="lm")
        if full or on:
            for k, (it, st) in enumerate(items):
                yy = y0 + 86 + k * (46 if not full else 42)
                dim = hl_status is not None and st != hl_status
                colr = STATUS[st][1]
                d.ellipse((x0 + 22, yy - 9, x0 + 40, yy + 9), fill=S.mix(colr, S.BG, 0.3) if dim else colr)
                d.text((x0 + 54, yy), it, font=S.font("sans", 22 if full else 24), fill=S.FAINT if dim else S.TXT, anchor="lm")
    if full or legend:
        x = 60
        for key in ("lib", "svc", "copy"):
            name, col = STATUS[key]
            dim = hl_status is not None and key != hl_status
            d.ellipse((x, 1000 - 10, x + 20, 1000 + 10), fill=S.mix(col, S.BG, 0.3) if dim else col)
            d.text((x + 32, 1000), name, font=S.font("sans", 24, True), fill=S.FAINT if dim else S.TXT, anchor="lm")
            x += 290
    return img


def status_slide(n):
    """The three statuses; n cards revealed."""
    img, d = base("Three statuses, said plainly", "Every block in the catalogue carries one", SLIDE)
    cards = [("lib", "A library", ["A package you import", "pip install, import, configure by environment", "Lives in agentpg-kit"]),
             ("svc", "A service", ["Runs alone as its own process", "Only a Postgres behind it", "identity and llm-gateway"]),
             ("copy", "A pattern to copy", ["Good code that lives inside an app", "A file that lifts out as is, or a design to re-implement", "We say which, and where it lives"])]
    for i, (key, head, lines) in enumerate(cards):
        x0 = 60 + i * 610
        col = STATUS[key][1]
        on = i < n
        S.rrect(d, (x0, 190, x0 + 580, 800), fill=S.PANEL if on else S.BG, outline=col if on else S.LINE, width=5 if on else 1)
        if on:
            d.ellipse((x0 + 30, 230, x0 + 70, 270), fill=col)
            d.text((x0 + 90, 250), head, font=S.font("sans", 38, True), fill=S.TXT, anchor="lm")
            y = 330
            for ln in lines:
                for sl in S.wrap(ln, S.font("sans", 30), 500):
                    d.text((x0 + 40, y), sl, font=S.font("sans", 30), fill=S.TXT)
                    y += 42
                y += 22
    return img


def labels_slide(n):
    img, d = base("Where each picture comes from", "Every screen is labelled", SLIDE)
    cards = [("LOCAL", "Real code, run on this machine, in a throw-away environment", GREEN),
             ("CAPTURED", "A still from the live AgentPG fleet, with its date", AMBER),
             ("SLIDE", "Drawn from the repositories and their documentation", S.BLUE)]
    for i, (tag, txt, col) in enumerate(cards):
        y0 = 220 + i * 230
        on = i < n
        S.rrect(d, (120, y0, W - 120, y0 + 190), fill=S.PANEL if on else S.BG, outline=col if on else S.LINE, width=4 if on else 1)
        if on:
            f = S.font("sans", 34, True)
            tw = f.getlength(tag) + 56
            S.rrect(d, (170, y0 + 60, 170 + tw, y0 + 130), outline=col, width=3, r=35)
            d.text((170 + tw / 2, y0 + 95), tag, font=f, fill=col, anchor="mm")
            d.text((170 + tw + 50, y0 + 95), txt, font=S.font("sans", 36), fill=S.TXT, anchor="lm")
    return img


def list_slide(title, sub, rows, n=None, hl=None, tag=SLIDE, fs=34, cap=None):
    """rows: [(text, sub[, status])] revealed one by one (n visible); hl marks one; cap is a caption bar."""
    img, d = base(title, sub, tag)
    n = len(rows) if n is None else n
    y = 170
    for i, row in enumerate(rows[:n]):
        t, s = row[0], row[1]
        st = row[2] if len(row) > 2 else None
        on = hl is None or i == hl
        S.rrect(d, (80, y, W - 80, y + 100), fill=S.PANEL, outline=AMBER if i == hl else S.LINE, width=4 if i == hl else 2)
        x = 120
        if st:
            d.ellipse((120, y + 24, 144, y + 48), fill=STATUS[st][1])
            x = 164
        d.text((x, y + 34), t, font=S.font("sans", fs, True), fill=S.TXT if on else S.MUTED, anchor="lm")
        if s:
            d.text((x, y + 74), s, font=S.font("sans", 24), fill=S.MUTED, anchor="lm")
        y += 118
    if cap:
        caption(d, cap)
    return img


def matrix_slide(n):
    """Operational surface per repository (checked 2026-10-29): all rows have every column."""
    img, d = base("The same operational surface everywhere", "Checked in each repository", SLIDE)
    cols = ["Dockerfile", "health", "readiness", "migrations", "metrics"]
    rows = ["trustdesk", "pagerlens", "CityPulse", "paybroker", "shopfront", "identity", "llm-gateway"]
    x0, y0 = 80, 170
    S.rrect(d, (x0, y0, W - 80, y0 + 60 + 82 * len(rows)), fill=S.PANEL, outline=S.LINE, width=2)
    d.text((x0 + 30, y0 + 32), "repository", font=S.font("sans", 26, True), fill=S.MUTED, anchor="lm")
    for j, c in enumerate(cols):
        d.text((x0 + 560 + j * 235, y0 + 32), c, font=S.font("sans", 26, True), fill=S.MUTED, anchor="mm")
    for i, r in enumerate(rows):
        yy = y0 + 60 + i * 82
        d.line((x0 + 10, yy, W - 90, yy), fill=S.PANEL2, width=1)
        d.text((x0 + 30, yy + 41), r, font=S.font("mono", 30), fill=S.TXT, anchor="lm")
        for j in range(len(cols)):
            if j < n:
                d.text((x0 + 560 + j * 235, yy + 41), "yes", font=S.font("sans", 30, True), fill=GREEN, anchor="mm")
    return img


def endcard():
    img, d = base("Thanks for watching")
    d.text((W // 2, 300), "Take one block. Or take an app.", font=S.font("sans", 66, True), fill=S.TXT, anchor="mm")
    d.text((W // 2, 400), "Either way, you do not start from zero.", font=S.font("sans", 44), fill=S.BLUE, anchor="mm")
    S.rrect(d, (240, 500, W - 240, 780), fill=S.PANEL, outline=AMBER, width=3)
    d.text((W // 2, 560), "Apache License 2.0: the kit, identity, llm-gateway and the five apps", font=S.font("sans", 30, True), fill=AMBER, anchor="mm")
    d.text((W // 2, 640), "The catalogue, with the effort for each block:", font=S.font("sans", 30), fill=S.TXT, anchor="mm")
    d.text((W // 2, 700), "docs/COMPONENTS.md in the ecosystem repository", font=S.font("mono", 32), fill=S.TXT, anchor="mm")
    d.text((W // 2, 900), "Synthetic data only", font=S.font("sans", 28), fill=S.MUTED, anchor="mm")
    return img
