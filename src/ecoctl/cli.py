"""ecoctl: validate, plan, apply, status, env."""
import argparse
import json
import sys
from pathlib import Path

import httpx

from .agentpg import AgentPG, AgentPGError, token_from
from .identity_client import Identity, IdentityError
from .llm_client import GatewayError
from .manifest import ManifestError, load
from .plan import Context, app_env, build
from .state import DEFAULT_DIR, State


def _print_plan(steps) -> None:
    for i, s in enumerate(steps, 1):
        print(f"{i:>2}. {s.title}")
        for d in s.detail:
            print(f"      {d}")


def _ctx(args) -> Context:
    only = {a.strip() for a in args.only.split(",") if a.strip()} if getattr(args, "only", None) else None
    ctx = Context(load(args.file), State(Path(args.state)), AgentPG(), rotate=getattr(args, "rotate", False), only=only)
    if only:
        ctx.manifest.closure(only)                      # unknown names fail now
    return ctx


def cmd_validate(args) -> int:
    m = load(args.file)
    print(f"ok: fleet '{m.fleet}', identity + {len(m.apps)} apps ({', '.join(a.key for a in m.order())})")
    return 0


def cmd_plan(args) -> int:
    ctx = _ctx(args)
    _print_plan(build(ctx))
    print("\nNothing was changed. `ecoctl apply --execute` runs this plan; it is safe to run again.")
    return 0


def cmd_apply(args) -> int:
    ctx = _ctx(args)
    steps = build(ctx)
    _print_plan(steps)
    if not args.execute:
        print("\nDry run: nothing was changed. Add --execute to create these on AgentPG and register them with the identity provider.")
        return 0
    if not args.yes:
        print("\nThis creates databases and sandboxes on your AgentPG account (they may use credits) and writes secrets to "
              f"{ctx.state.path} (git-ignored).")
        if input("Continue? [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return 1
    for i, s in enumerate(steps, 1):
        print(f"[{i}/{len(steps)}] {s.title} ...", flush=True)
        try:
            print(f"      {s.run(ctx)}")
        except (AgentPGError, IdentityError, GatewayError, httpx.HTTPError) as exc:
            ctx.state.save()
            print(f"      FAILED: {exc}\n\nProgress is saved; fix the cause and run the same command again.", file=sys.stderr)
            return 2
    ctx.state.save()
    print("\nDone. Open the identity provider's admin console, add yourself to the group(s) above, then open the apps:")
    for key, entry in sorted(ctx.state.data["apps"].items()):
        if entry.get("url"):
            print(f"  {key:<14} {entry['url']}")
    return 0


def cmd_recreate(args) -> int:
    """Delete sandboxes so the next apply creates them afresh (to pull a new image or change the environment). A sandbox's URL contains
    its id, so a recreated app gets a new URL: apps that call it (or, for the LLM gateway, use it) are recreated too, and recreating
    the identity provider recreates all."""
    ctx = _ctx(args)
    m = ctx.manifest
    deployed = {k for k in m.apps if ctx.state.app(k).get("sandbox_id")}
    names = {a.strip() for a in args.apps.split(",") if a.strip()}
    unknown = names - set(m.apps) - {"identity"}
    if unknown:
        print(f"no such app: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1
    targets = (deployed | {"identity"}) if "identity" in names else ((names | m.dependents(names)) & deployed)
    if "identity" in names:
        print("Recreating the identity provider gives it a new URL (the sandbox id is part of it), so every deployed app is recreated too.")
    elif targets - names:
        print(f"Also recreated because they call or use {', '.join(sorted(names))}: {', '.join(sorted(targets - names))}")
    ids = {k: ctx.state.app(k).get("sandbox_id") for k in sorted(targets) if ctx.state.app(k).get("sandbox_id")}
    if not ids:
        print("Nothing deployed to recreate.")
        return 0
    print("Will delete the sandboxes (their databases are kept): " + ", ".join(f"{k} ({v})" for k, v in ids.items()))
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return 1
    for k, sid in ids.items():
        try:
            ctx.agentpg.run(AgentPG.delete_sandbox(sid))
        except AgentPGError as exc:
            print(f"could not delete {k} ({sid}): {exc}", file=sys.stderr)
            return 2
        for field in ("sandbox_id", "url", "env_hash"):
            ctx.state.app(k).pop(field, None)
        ctx.state.save()
    args.execute, args.only, args.rotate = True, ",".join(sorted(deployed - {"identity"})) or None, False
    return cmd_apply(args)


def cmd_status(args) -> int:
    ctx = _ctx(args)
    bad = 0
    print(f"{'app':<16}{'health':<10}{'ready':<10}url")
    for key, app in ctx.manifest.everything().items():
        url = ctx.state.data["apps"].get(key, {}).get("url")
        if not app.deploy:
            continue
        if not url:
            print(f"{key:<16}{'-':<10}{'-':<10}(not deployed)")
            continue
        row = []
        for path in (app.health, "/readyz"):
            try:
                row.append(str(httpx.get(url + path, timeout=10).status_code))
            except httpx.HTTPError:
                row.append("down")
        bad += row[0] != "200"
        print(f"{key:<16}{row[0]:<10}{row[1]:<10}{url}")
    bad += _llm_status(ctx)
    ident_url = ctx.state.data["apps"].get("identity", {}).get("url")
    if ident_url:
        try:
            registered = Identity(ident_url, ctx.state.admin_key()).apps()
        except (IdentityError, httpx.HTTPError) as exc:
            print(f"\nidentity registry: {exc}")
            return 1
        print("\nidentity registry")
        for key, app in ctx.manifest.apps.items():
            if not ctx.state.data["apps"].get(key, {}).get("registered") and not ctx.state.data["apps"].get(key, {}).get("service_secret"):
                continue                                       # not part of this deployment yet
            for cid in ([key] if app.web else []) + ([app.service_client_id] if app.service else []):
                r = registered.get(cid)
                drift = ""
                if r is None:
                    drift = "NOT REGISTERED"
                elif app.web and cid == key and r["url"] != ctx.state.app(key).get("url", ""):
                    drift = f"launcher URL differs ({r['url'] or 'empty'})"
                print(f"  {cid:<26}{r['kind'] if r else '-':<9}{drift or 'ok'}")
                bad += bool(drift)
    return 1 if bad else 0


def _llm_status(ctx) -> int:
    """For each app that uses an LLM: does its client exist in the gateway, and does one tiny chat call with its own key work?
    Returns the number of problems. Keys are read from the state file and never printed."""
    m = ctx.manifest
    deployed = [k for k in m.consumers() if ctx.state.app(k).get("sandbox_id")]
    if not deployed:
        return 0
    print(f"\nLLM gateway ({m.llm.gateway}): {ctx.state.app(m.llm.gateway).get('url') or '(not deployed)'}")
    if not ctx.state.app(m.llm.gateway).get("url"):
        return len(deployed)
    gw = ctx.gateway()
    try:
        clients = gw.clients()
    except (GatewayError, httpx.HTTPError) as exc:
        print(f"  cannot read the gateway's clients: {exc}")
        return len(deployed)
    bad = 0
    print(f"  {'app':<16}{'client':<12}{'chat (' + m.llm.chat_alias + ')'}")
    for k in deployed:
        key = ctx.state.app(k).get("llm_key")
        have = k in clients
        ok = False
        chat = "no key in the state file (run `ecoctl apply --execute`)"
        if key:
            ok, chat = gw.chat_test(key, m.llm.chat_alias)
        bad += (not have) or not ok
        print(f"  {k:<16}{('ok' if have else 'MISSING'):<12}{chat}")
    return bad


def cmd_llm(args) -> int:
    """`ecoctl llm test <app>`: one tiny chat completion through the gateway with that app's own key."""
    ctx = _ctx(args)
    m = ctx.manifest
    if args.app not in m.apps or not m.apps[args.app].llm:
        print(f"{args.app} is not an app with `llm: true` in the manifest", file=sys.stderr)
        return 1
    key = ctx.state.app(args.app).get("llm_key")
    if not key or not ctx.state.app(m.llm.gateway).get("url"):
        print(f"{args.app} has no gateway client yet (run `ecoctl apply --execute`)", file=sys.stderr)
        return 1
    ok, detail = ctx.gateway().chat_test(key, m.llm.chat_alias)
    print(f"{args.app} -> {m.llm.gateway} /v1/chat/completions model {m.llm.chat_alias}: {'ok' if ok else 'FAILED'} ({detail})")
    return 0 if ok else 1


def cmd_env(args) -> int:
    ctx = _ctx(args)
    app = ctx.manifest.everything().get(args.app)
    if app is None:
        print(f"no such app: {args.app}", file=sys.stderr)
        return 1
    env, secret_names = app_env(ctx, app)
    if args.reveal:
        print("# contains secrets: do not paste into a chat or commit it", file=sys.stderr)
    for k, v in sorted(env.items()):
        print(f"export {k}={(v if args.reveal or k not in secret_names else '***')!r}")
    return 0


def cmd_db(args) -> int:
    """The database key of an app's instance (it is also the database password). Kept from creation; --reset rotates the instance's
    `default` key, which nothing in the fleet uses (sandboxes get their own key from AgentPG), and keeps the new one."""
    ctx = _ctx(args)
    entry = ctx.state.app(args.app)
    iid = entry.get("instance_id")
    if not iid:
        print(f"{args.app} has no instance yet", file=sys.stderr)
        return 1
    if args.reset or not entry.get("db_key"):
        try:
            keys = json.loads(ctx.agentpg.run(AgentPG.list_keys(iid))).get("keys", [])
            default = next((k["id"] for k in keys if k.get("name") == "default"), None)
            if default is None:
                print("the instance has no key named `default` to reset", file=sys.stderr)
                return 1
            token = token_from(ctx.agentpg.run(AgentPG.reset_key(iid, default)))
        except (AgentPGError, ValueError) as exc:
            print(f"could not reset the key: {exc}", file=sys.stderr)
            return 2
        if not token:
            print("AgentPG did not return a new key", file=sys.stderr)
            return 2
        entry["db_key"] = token
        ctx.state.save()
        print("# new key saved to the state file", file=sys.stderr)
    print("# contains a secret: do not paste into a chat or commit it", file=sys.stderr)
    print(entry["db_key"])
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ecoctl", description="Describe, register and provision a fleet of apps on AgentPG.")
    p.add_argument("-f", "--file", default="ecosystem.yaml")
    p.add_argument("--state", default=str(DEFAULT_DIR), help="where ids and generated secrets are kept (default .ecosystem)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="check the manifest")
    pl = sub.add_parser("plan", help="show what apply would do (changes nothing)")
    pl.add_argument("--only", help="comma-separated apps to include (plus what they call); the identity provider is always included")
    ap = sub.add_parser("apply", help="show the plan; with --execute, run it")
    ap.add_argument("--execute", action="store_true", help="actually create and register (otherwise a dry run)")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--only", help="comma-separated apps to deploy (plus what they call); the identity provider is always included")
    ap.add_argument("--rotate", action="store_true", help="issue new client secrets for apps that already exist")
    rc = sub.add_parser("recreate", help="delete sandboxes and create them afresh (new image or environment); cascades to apps that depend on them")
    rc.add_argument("apps", help="comma-separated app keys, or identity (which recreates everything)")
    rc.add_argument("--yes", action="store_true")
    db = sub.add_parser("db", help="print the database key (the database password) of an app's instance")
    db.add_argument("app")
    db.add_argument("--reset", action="store_true", help="rotate the instance's default key and keep the new one")
    sub.add_parser("status", help="health of every app, each LLM client and its test call, and drift from the identity registry")
    en = sub.add_parser("env", help="print the environment an app is created with (secret values are shown as *** unless --reveal)")
    en.add_argument("app")
    en.add_argument("--reveal", action="store_true", help="print secret values too (to configure something that runs elsewhere)")
    ll = sub.add_parser("llm", help="LLM gateway wiring")
    ll.add_argument("action", choices=["test"], help="test: one tiny chat call through the gateway with the app's own key")
    ll.add_argument("app")
    args = p.parse_args(argv)
    try:
        return {"validate": cmd_validate, "plan": cmd_plan, "apply": cmd_apply, "status": cmd_status, "env": cmd_env, "db": cmd_db, "recreate": cmd_recreate, "llm": cmd_llm}[args.cmd](args)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
