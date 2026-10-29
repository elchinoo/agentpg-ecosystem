"""Slide drawing for the ecosystem tutorial video (Pillow only, 1920x1080, no network, no browser).

Three kinds of slide, all drawn from data in the repository, never from invented terminal output:
  diagram()     the interaction diagram of docs/GUIDE.md section 3, in stages (nodes, edges, highlighted steps)
  yaml_slide()  an editor view of real lines of ecosystem.yaml
  flow()        labelled steps plus note cards, used where the real capture needs a live fleet (status, connect, sign-in)
"""
from functools import cache

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
BG, PANEL, PANEL2, LINE = (20, 22, 28), (31, 35, 45), (40, 45, 58), (74, 80, 98)
TXT, MUTED, FAINT = (232, 234, 238), (150, 157, 172), (96, 102, 118)
BLUE, PURPLE, TEAL, AMBER = (96, 165, 250), (167, 139, 250), (45, 212, 191), (251, 191, 36)
GREEN, RED, GREY = (74, 222, 128), (248, 113, 113), (130, 137, 152)
FONTS = {"sans": "/System/Library/Fonts/Helvetica.ttc", "mono": "/System/Library/Fonts/Menlo.ttc"}


@cache
def font(kind: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONTS[kind], size, index=1 if bold else 0)


def mix(c, d, a):
    return tuple(int(c[i] * a + d[i] * (1 - a)) for i in range(3))


def rrect(d, box, fill=None, outline=None, width=2, r=18):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def wrap(text, fnt, maxw):
    out, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if cur and fnt.getlength(t) > maxw:
            out.append(cur)
            cur = w
        else:
            cur = t
    return out + [cur] if cur else out


def arrow(d, pts, color, width=6, head=16):
    d.line(pts, fill=color, width=width, joint="curve")
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    dx, dy = (x1 - x0), (y1 - y0)
    n = max((dx * dx + dy * dy) ** 0.5, 1)
    ux, uy = dx / n, dy / n
    px, py = -uy, ux
    d.polygon([(x1, y1), (x1 - ux * head + px * head * 0.6, y1 - uy * head + py * head * 0.6),
               (x1 - ux * head - px * head * 0.6, y1 - uy * head - py * head * 0.6)], fill=color)


def chip(d, c, n, color=AMBER):
    x, y = c
    d.ellipse((x - 19, y - 19, x + 19, y + 19), fill=color, outline=BG, width=3)
    d.text((x, y), str(n), font=font("sans", 24, True), fill=BG, anchor="mm")


def base(title, subtitle=None, tag=None, right="Synthetic data, test cards only"):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((60, 36), title, font=font("sans", 46, True), fill=TXT)
    if subtitle:
        d.text((62, 98), subtitle, font=font("sans", 26), fill=MUTED)
    d.line((0, 1040, W, 1040), fill=PANEL2, width=2)
    d.text((60, 1060), "The AgentPG ecosystem  |  ecoctl", font=font("sans", 20), fill=FAINT, anchor="lm")
    d.text((W - 60, 1060), right, font=font("sans", 20), fill=FAINT, anchor="rm")
    if tag:
        f = font("sans", 20, True)
        tw = f.getlength(tag) + 36
        rrect(d, (W - 60 - tw, 40, W - 60, 82), outline=AMBER, width=2, r=21)
        d.text((W - 60 - tw / 2, 61), tag, font=f, fill=AMBER, anchor="mm")
    return img, d


# --------------------------------------------------------------------------------------------------------------- diagram
APPS = ["pagerlens", "citypulse", "cpsim", "trustdesk", "paybroker", "shopfront"]
XS = {k: 85 + i * 302 for i, k in enumerate(APPS)}
AW = 240
NODES = {
    "pagerlens": ("PagerLens", "incident investigation", BLUE), "citypulse": ("CityPulse", "smart-city sensors", BLUE),
    "cpsim": ("Simulator", "feeds and loads the fleet", BLUE), "trustdesk": ("TrustDesk", "policy checks", BLUE),
    "paybroker": ("PayBroker", "payments and fraud", BLUE), "shopfront": ("ShopFront", "sample web shop", BLUE),
}
LLM_APPS = ["pagerlens", "cpsim", "trustdesk", "paybroker", "shopfront"]
GW_BOX, IDP_BOX, ECO_BOX, MODEL_BOX = (700, 720, 1220, 830), (700, 160, 1220, 260), (60, 160, 440, 250), (1440, 720, 1860, 830)
VAULT_BOX, ANS_BOX = (1290, 160, 1550, 260), (1590, 160, 1860, 260)
PLAT, SCAF, SAMP = GREEN, PURPLE, BLUE                  # the three labels: platform, reference scaffolding, sample apps
CAT = {"plat": ("PLATFORM", PLAT), "scaf": ("REFERENCE SCAFFOLDING", SCAF), "samp": ("SAMPLE APP", SAMP)}


def cx(k):
    return XS[k] + AW // 2


def edge_geometry():
    e = {}
    e["ecoctl_idp"] = ([(440, 205), (700, 205)], (570, 170))
    e["ecoctl_fleet"] = ([(250, 250), (250, 305)], (290, 278))
    e["idp"] = ([(960, 260), (960, 360)], (990, 312))
    e["sim_cp"] = ([(XS["cpsim"], 470), (XS["citypulse"] + AW, 470)], (XS["cpsim"] - 31, 430))
    e["cp_pl"] = ([(XS["citypulse"], 470), (XS["pagerlens"] + AW, 470)], (XS["citypulse"] - 31, 430))
    e["sim_td"] = ([(XS["cpsim"] + AW, 470), (XS["trustdesk"], 470)], (XS["trustdesk"] - 31, 430))
    mid = (cx("cpsim") + 30 + cx("paybroker") - 30) // 2
    e["sim_pb"] = ([(cx("cpsim") + 30, 540), (cx("cpsim") + 30, 585), (cx("paybroker") - 30, 585), (cx("paybroker") - 30, 540)], (mid, 585))
    e["sf_pb"] = ([(XS["shopfront"], 448), (XS["paybroker"] + AW, 448)], (XS["shopfront"] - 31, 418))
    e["pb_sf"] = ([(XS["paybroker"] + AW, 505), (XS["shopfront"], 505)], (XS["shopfront"] - 31, 538))
    for k in LLM_APPS:
        e["gw_" + k] = ([(cx(k), 540), (cx(k), 640), (960, 640), (960, 720)], (cx(k), 600))
    e["gw_model"] = ([(1220, 775), (1440, 775)], (1330, 740))
    return e


EDGES = edge_geometry()


def pill(d, x, y, text, color):
    f = font("sans", 16, True)
    tw = f.getlength(text) + 20
    rrect(d, (x, y - 22, x + tw, y), fill=mix(color, BG, 0.25), outline=color, width=2, r=11)
    d.text((x + tw / 2, y - 11), text, font=f, fill=color, anchor="mm")


def node_box(d, box, title, sub, color, mark=False, sub2=None, cat=None):
    if cat:
        pill(d, box[0], box[1] - 3, CAT[cat][0], CAT[cat][1])
    rrect(d, box, fill=PANEL, outline=AMBER if mark else color, width=6 if mark else 3)
    x0, y0, x1, y1 = box
    mx = (x0 + x1) // 2
    d.text((mx, y0 + (x1 - x0 > 300 and 36 or 40)), title, font=font("sans", 30, True), fill=TXT, anchor="mm")
    d.text((mx, y0 + (x1 - x0 > 300 and 76 or 78)), sub, font=font("sans", 20), fill=MUTED, anchor="mm")


def diagram(title="The AgentPG ecosystem", subtitle=None, nodes=(), frame=False, hot=(), warm=(), marks=(), steps=(),
            lower=None, tag=None, ecoctl_edges=False, legend=False, plat=None, cats=True):
    """nodes: keys to draw (apps, 'idp', 'gw', 'model', 'eco'); hot/warm: edge ids (hot are numbered in order); steps: caption lines
    matching the hot edges one to one; marks: nodes outlined in amber."""
    img, d = base(title, subtitle, tag)
    nodes, hot, warm = set(nodes), list(hot), list(warm)
    if legend:
        x = W - 60
        for key in ("samp", "scaf", "plat"):
            txt, col = CAT[key]
            f = font("sans", 20, True)
            x -= f.getlength(txt)
            d.text((x, 112), txt, font=f, fill=col, anchor="lm")
            d.ellipse((x - 26, 102, x - 8, 120), fill=col)
            x -= 64
    if frame:
        on = plat is not None
        rrect(d, (60, 300, 1860, 650), fill=(24, 27, 35), outline=PLAT if on else LINE, width=5 if on else 2, r=26)
        if cats:
            pill(d, 80, 300 + 2, "PLATFORM", PLAT)
        d.text((86, 322), "EDB Postgres on AgentPG: each app in its own sandbox, with its own Postgres database", font=font("sans", 22),
               fill=PLAT if on else MUTED)
    for eid in warm:
        draw_edge(d, eid, mix(GREY, BG, 0.8), 4, None, nodes)
    for i, eid in enumerate(hot):
        draw_edge(d, eid, AMBER, 7, i + 1, nodes)
    for k in APPS:
        if k in nodes:
            t, s, c = NODES[k]
            node_box(d, (XS[k], 400, XS[k] + AW, 540), t, s, c, mark=k in marks, cat="samp" if cats else None)
    if "idp" in nodes:
        node_box(d, IDP_BOX, "identity (OIDC)", "sign-in and service tokens", SCAF, mark="idp" in marks, cat="scaf" if cats else None)
    if "gw" in nodes:
        node_box(d, GW_BOX, "llm-gateway", "OpenAI-compatible, one key per app", SCAF, mark="gw" in marks, cat="scaf" if cats else None)
    if "model" in nodes:
        node_box(d, MODEL_BOX, "model provider", "yours, or the built-in echo model", GREY)
    if "eco" in nodes:
        node_box(d, ECO_BOX, "ecoctl", "deploys the fleet from one file", SCAF, mark="eco" in marks, cat="scaf" if cats else None)
    if "vault" in nodes:
        node_box(d, VAULT_BOX, "kit vault", "secrets, or your own", SCAF, mark="vault" in marks, cat="scaf" if cats else None)
    if "ansible" in nodes:
        node_box(d, ANS_BOX, "Ansible recipe", "or your own automation", SCAF, mark="ansible" in marks, cat="scaf" if cats else None)
    # caption panel
    if steps:
        rrect(d, (60, 862, 1860, 1012), fill=PANEL, outline=LINE, width=2)
        lh = 46 if len(steps) <= 3 else 38
        y = 886 if len(steps) <= 3 else 876
        for i, s in enumerate(steps):
            chip(d, (100, y + 20 + i * lh), i + 1)
            d.text((136, y + 20 + i * lh), s, font=font("sans", 30 if len(steps) <= 3 else 28), fill=TXT, anchor="lm")
    elif lower:
        rrect(d, (60, 905, 60 + font("sans", 36, True).getlength(lower) + 60, 975), fill=PANEL, outline=AMBER, width=3, r=14)
        d.text((90, 940), lower, font=font("sans", 36, True), fill=AMBER, anchor="lm")
    return img


def draw_edge(d, eid, color, width, number, nodes):
    pts, cpos = EDGES[eid]
    if eid == "idp":                                    # identity: trunk, bus, one drop per visible app
        d.line([(960, 260), (960, 360)], fill=color, width=width)
        xs = [cx(k) for k in APPS]
        d.line([(min(xs), 360), (max(xs), 360)], fill=color, width=width)
        for k in APPS:
            arrow(d, [(cx(k), 360), (cx(k), 398)], color, width, 12)
        if number:
            chip(d, cpos, number)
        return
    arrow(d, pts, color, width)
    if number:
        chip(d, cpos, number)


# ------------------------------------------------------------------------------------------------------------------ yaml
def yaml_slide(lines, hl=(), caption=None, tab="ecosystem.yaml", sub="the real manifest; comments trimmed for the screen"):
    """lines: [(line number, text)]; hl: line numbers to mark."""
    img, d = base("One file describes the fleet", sub)
    top = 130
    rrect(d, (80, top, 1840, 840), fill=PANEL, outline=LINE, width=2, r=14)
    rrect(d, (80, top, 1840, top + 52), fill=PANEL2, outline=LINE, width=2, r=14)
    d.text((112, top + 26), tab, font=font("sans", 22, True), fill=TXT, anchor="lm")
    mono, lh, y = font("mono", 28), 42, top + 76
    cw = mono.getlength("M")
    for no, text in lines:
        marked = no in hl
        if marked:
            d.rectangle((82, y - 4, 1838, y + lh - 8), fill=mix(AMBER, PANEL, 0.2))
            d.rectangle((82, y - 4, 90, y + lh - 8), fill=AMBER)
        d.text((150, y), f"{no:>3}", font=mono, fill=AMBER if marked else FAINT, anchor="ra")
        x = 180
        body = text.rstrip()
        indent = len(body) - len(body.lstrip())
        x += indent * cw
        stripped = body.lstrip()
        if stripped.startswith("- "):
            d.text((x, y), "- ", font=mono, fill=MUTED)
            x += 2 * cw
            stripped = stripped[2:]
        if ":" in stripped and not stripped.startswith("{"):
            key, _, val = stripped.partition(":")
            d.text((x, y), key + ":", font=mono, fill=BLUE if not marked else AMBER)
            x += (len(key) + 1) * cw
            d.text((x, y), val, font=mono, fill=TXT if marked else (214, 190, 140))
        else:
            d.text((x, y), stripped, font=mono, fill=(214, 190, 140))
        y += lh
    if caption:
        rrect(d, (80, 870, 1840, 1000), fill=PANEL, outline=AMBER, width=3)
        cl = wrap(caption, font("sans", 34), 1680)[:2]
        for i, ln in enumerate(cl):
            d.text((116, (935 if len(cl) == 1 else 910) + i * 48), ln, font=font("sans", 34), fill=TXT, anchor="lm")
    return img


# ------------------------------------------------------------------------------------------------------------------- flow
def flow(title, subtitle, steps, active=None, done=(), cards=(), active_card=None, tag="REVIEW CUT: slide stands in for a live capture"):
    """steps: [(head, sub)] drawn as a row of numbered boxes; cards: [(title, [lines], accent)] drawn under the row."""
    img, d = base(title, subtitle, tag)
    n = len(steps)
    gap = 54
    bw = (1800 - gap * (n - 1)) // max(n, 1)
    top, bot = (450, 820) if n else (200, 700)
    for i, (head, sub) in enumerate(steps):
        x0 = 60 + i * (bw + gap)
        on, was = active == i, i in done
        col = AMBER if on else (GREEN if was else LINE)
        rrect(d, (x0, 160, x0 + bw, 400), fill=mix(AMBER, PANEL, 0.16) if on else PANEL, outline=col, width=5 if on else 2)
        chip(d, (x0 + 40, 200), i + 1, AMBER if on else (GREEN if was else GREY))
        y = 246
        for ln in wrap(head, font("sans", 28, True), bw - 40):
            d.text((x0 + 22, y), ln, font=font("sans", 28, True), fill=TXT if (on or was) else MUTED)
            y += 36
        for ln in wrap(sub, font("sans", 22), bw - 40):
            d.text((x0 + 22, y + 4), ln, font=font("sans", 22), fill=MUTED)
            y += 29
        if i < n - 1:
            arrow(d, [(x0 + bw + 8, 280), (x0 + bw + gap - 8, 280)], LINE if not was else GREEN, 4, 12)
    m = len(cards)
    if m:
        cgap = 40
        cw_ = (1800 - cgap * (m - 1)) // m
        for j, (ctitle, clines, accent) in enumerate(cards):
            x0 = 60 + j * (cw_ + cgap)
            on = active_card == j
            rrect(d, (x0, top, x0 + cw_, bot), fill=PANEL, outline=AMBER if on else accent, width=5 if on else 2)
            d.text((x0 + 30, top + 40), ctitle, font=font("sans", 34, True), fill=accent if not on else AMBER)
            y = top + 98
            for ln in clines:
                lines = wrap(ln, font("sans", 29), cw_ - 100)
                d.text((x0 + 30, y), "-", font=font("sans", 29), fill=accent)
                for sl in lines:
                    d.text((x0 + 60, y), sl, font=font("sans", 29), fill=TXT)
                    y += 40
                y += 12
    return img


def problem(stage):
    """Opening slide: stage 0 the ask, 1 the needs, 2 the cost."""
    img, d = base("Several AI apps on Postgres", "What you want to show or run", right="Synthetic data, test cards only")
    rrect(d, (60, 200, 1860, 330), fill=PANEL, outline=LINE, width=2)
    d.text((100, 265), "A demo, or a pilot, with several AI apps on Postgres", font=font("sans", 44, True), fill=TXT, anchor="lm")
    items = [("Sign-in", "one login for every app"), ("Model gateway", "keys, limits, one door"), ("Secrets", "none in code or files"),
             ("A database each", "extensions, users, backups")]
    for i, (h, sub) in enumerate(items):
        x0 = 60 + i * 460
        on = stage >= 1
        rrect(d, (x0, 400, x0 + 420, 600), fill=PANEL, outline=AMBER if on else FAINT, width=3 if on else 1)
        d.text((x0 + 30, 470), h if on else "?", font=font("sans", 40, True), fill=TXT if on else FAINT, anchor="lm")
        if on:
            d.text((x0 + 30, 540), sub, font=font("sans", 26), fill=MUTED, anchor="lm")
    if stage >= 2:
        rrect(d, (60, 700, 1860, 880), fill=mix(RED, PANEL, 0.18), outline=RED, width=4)
        d.text((W // 2, 790), "Weeks of plumbing before the first demo?", font=font("sans", 56, True), fill=TXT, anchor="mm")
    return img


def capture_slide(path, caption, crop=None, title="Real screens from the live fleet", sub="Captured, not a mock-up"):
    """A real screenshot, fitted below the header; crop=(x0,y0,x1,y1) in the source's pixels zooms into a region."""
    img, d = base(title, sub, tag="CAPTURED from the live AgentPG fleet, 28 Oct 2026", right="Synthetic data, test cards only")
    src = Image.open(path).convert("RGB")
    if crop:
        src = src.crop(crop)
    box_w, box_h = 1800, 770
    k = min(box_w / src.width, box_h / src.height)
    src = src.resize((int(src.width * k), int(src.height * k)), Image.LANCZOS)
    x0, y0 = (W - src.width) // 2, 140
    rrect(d, (x0 - 6, y0 - 6, x0 + src.width + 6, y0 + src.height + 6), outline=AMBER, width=3, r=8)
    img.paste(src, (x0, y0))
    d = ImageDraw.Draw(img)
    rrect(d, (60, 940, 1860, 1022), fill=PANEL, outline=LINE, width=2, r=14)
    d.text((96, 981), caption, font=font("sans", 32), fill=TXT, anchor="lm")
    return img


def text_slide(title, subtitle, lines, caption=None, tag=None, mono=True, hl=(), fs=32, lh=56):
    """A panel of text lines (commands in mono), highlighted lines in amber, optional caption bar."""
    img, d = base(title, subtitle, tag)
    h = 90 + lh * len(lines)
    rrect(d, (80, 170, 1840, 170 + h), fill=PANEL, outline=LINE, width=2, r=14)
    f = font("mono" if mono else "sans", fs)
    for i, ln in enumerate(lines):
        y = 215 + i * lh
        if i in hl:
            d.rectangle((84, y - 22, 1836, y + 30), fill=mix(AMBER, PANEL, 0.2))
        d.text((120, y + 4), ln, font=f, fill=AMBER if i in hl else TXT, anchor="lm")
    if caption:
        rrect(d, (80, 900, 1840, 1010), fill=PANEL, outline=AMBER, width=3)
        cl = wrap(caption, font("sans", 32), 1680)[:2]
        for i, ln in enumerate(cl):
            d.text((116, (955 if len(cl) == 1 else 930) + i * 44), ln, font=font("sans", 32), fill=TXT, anchor="lm")
    return img


def endcard():
    img, d = base("Thanks for watching")
    d.text((W // 2, 300), "One manifest. One state file. One guide.", font=font("sans", 60, True), fill=TXT, anchor="mm")
    d.text((W // 2, 420), "validate  |  plan  |  apply  |  status  |  connect  |  recreate", font=font("mono", 34), fill=BLUE, anchor="mm")
    rrect(d, (360, 540, 1560, 720), fill=PANEL, outline=AMBER, width=3)
    d.text((W // 2, 600), "Read the full guide", font=font("sans", 36, True), fill=AMBER, anchor="mm")
    d.text((W // 2, 662), "docs/GUIDE.md in the ecosystem repository", font=font("mono", 34), fill=TXT, anchor="mm")
    d.text((W // 2, 840), "Synthetic data, test cards only", font=font("sans", 28), fill=MUTED, anchor="mm")
    return img
