"""ecoctl: validate, plan, apply, status, env."""
import argparse
import json
import sys
from pathlib import Path

import httpx

from .agentpg import AgentPG, AgentPGError, token_from
from .connect import print_status, reconcile
from .connect_client import ConnectError
from .identity_client import Identity, IdentityError
from .llm_client import GatewayError
from .llm_sync import diff_app, table
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
        except (AgentPGError, IdentityError, GatewayError, ConnectError, httpx.HTTPError) as exc:
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
    bad += print_status(ctx)
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
    print(f"  {'app':<16}{'client':<12}{'limits':<10}{'chat (' + m.llm.chat_alias + ')'}")
    drifting = []
    for k in deployed:
        key = ctx.state.app(k).get("llm_key")
        have = k in clients
        ok = False
        chat = "no key in the state file (run `ecoctl apply --execute`)"
        if key:
            ok, chat = gw.chat_test(key, m.llm.chat_alias)
        bad += (not have) or not ok
        d = diff_app(m, k, clients.get(k))
        drifting += [k] if have and d.drift else []
        print(f"  {k:<16}{('ok' if have else 'MISSING'):<12}{('-' if not have else 'DRIFT' if d.drift else 'ok'):<10}{chat}")
    if drifting:
        print(f"  warning: the limits or aliases of {', '.join(drifting)} differ from the manifest (an operator's live change, or a hand-made client): "
              "`ecoctl llm sync` shows the difference, `ecoctl llm sync --apply <app>` brings the gateway in line")
    return bad


def cmd_llm_sync(args) -> int:
    """Compare each consumer's gateway client with the manifest; with --apply, PATCH the named apps (or --all), only the differing fields.
    Extra aliases are kept unless --prune-aliases. Exit 1 on drift without --apply (for CI), 2 on a gateway error."""
    ctx = _ctx(args)
    m = ctx.manifest
    names = list(args.apps or [])
    bad = [n for n in names if n not in m.apps or not m.apps[n].llm]
    if bad:
        print(f"not an app with `llm: true` in the manifest: {', '.join(bad)}", file=sys.stderr)
        return 1
    if args.apply and not (names or args.all):
        print("--apply needs the app(s) to change, or --all", file=sys.stderr)
        return 1
    if names and args.all:
        print("name apps or use --all, not both", file=sys.stderr)
        return 1
    if args.prune_aliases and not args.apply:
        print("note: --prune-aliases only matters with --apply; showing what it would remove", file=sys.stderr)
    targets = m.consumers() if (args.all or not names) else sorted(set(names))
    if not ctx.state.app(m.llm.gateway).get("url"):
        print(f"the LLM gateway ({m.llm.gateway}) is not deployed yet (run `ecoctl apply --execute`)", file=sys.stderr)
        return 1
    gw = ctx.gateway()
    try:
        clients = gw.clients()
    except (GatewayError, httpx.HTTPError) as exc:
        print(f"cannot read the gateway's clients: {exc}", file=sys.stderr)
        return 2
    diffs = [diff_app(m, k, clients.get(k), args.prune_aliases) for k in targets]
    rows = [r for d in diffs for r in d.rows]
    if not rows:
        print(f"in sync: {', '.join(targets)} (limits and aliases match the manifest)")
        return 0
    print(table(rows))
    if not args.apply:
        print("\nNothing was changed. `ecoctl llm sync --apply <app...>` (or --all) sets the differing fields; "
              "missing aliases are added, extras are kept unless --prune-aliases. Keys are never touched.")
        return 1
    failed = 0
    for d in diffs:
        if d.missing:
            print(f"{d.app}: no client yet (run `ecoctl apply --execute`)")
        elif d.patch:
            try:
                gw.update_client(d.client_id, d.patch)
                print(f"{d.app}: updated {', '.join(sorted(d.patch))}")
            except (GatewayError, httpx.HTTPError) as exc:
                failed += 1
                print(f"{d.app}: FAILED: {exc}", file=sys.stderr)
        else:
            print(f"{d.app}: nothing to change (extra aliases kept)")
    return 2 if failed else 0


def cmd_llm(args) -> int:
    """`ecoctl llm test <app>`: one tiny chat completion through the gateway with that app's own key. `ecoctl llm sync`: see cmd_llm_sync."""
    if getattr(args, "action", "test") == "sync":
        return cmd_llm_sync(args)
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


def cmd_connect(args) -> int:
    """`ecoctl connect [app] [--rotate] [--dry-run]`: reconcile the connection(s) declared by `connect:` in the manifest (see connect.py)."""
    ctx = _ctx(args)
    m = ctx.manifest
    todo = [(k, p) for k, a in m.apps.items() for p in a.connect if not args.app or k == args.app]
    if args.app and args.app not in m.apps:
        print(f"no such app: {args.app}", file=sys.stderr)
        return 1
    if not todo:
        print(f"{args.app or 'no app'} has no `connect:` block in the manifest", file=sys.stderr)
        return 1
    for k, p in todo:
        print(f"{k} -> {p}{' (dry run: nothing is written)' if args.dry_run else ''}")
        try:
            changed = reconcile(ctx, k, p, dry_run=args.dry_run, rotate=args.rotate, emit=lambda s: print(f"  {s}", flush=True))
        except (ConnectError, httpx.HTTPError) as exc:
            ctx.state.save()
            print(f"  FAILED: {exc}\n\nProgress is saved; fix the cause and run the same command again.", file=sys.stderr)
            return 2
        ctx.state.save()
        print("  " + (("would change something: run without --dry-run" if args.dry_run else "connected") if changed else "nothing to change"))
    return 0


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
    cn = sub.add_parser("connect", help="connect apps by themselves (ShopFront to PayBroker: merchant, origin, keys, webhook, settings); safe to run again")
    cn.add_argument("app", nargs="?", help="the app whose `connect:` to reconcile (default: every app that has one)")
    cn.add_argument("--rotate", action="store_true", help="create the merchant keys and the webhook signing secret afresh and push them")
    cn.add_argument("--dry-run", action="store_true", help="show what would change without writing anything")
    sub.add_parser("status", help="health of every app, each LLM client and its test call, the connections, and drift from the identity registry")
    en = sub.add_parser("env", help="print the environment an app is created with (secret values are shown as *** unless --reveal)")
    en.add_argument("app")
    en.add_argument("--reveal", action="store_true", help="print secret values too (to configure something that runs elsewhere)")
    ll = sub.add_parser("llm", help="LLM gateway wiring").add_subparsers(dest="action", required=True)
    lt = ll.add_parser("test", help="one tiny chat call through the gateway with the app's own key")
    lt.add_argument("app")
    ls = ll.add_parser("sync", help="compare each app's gateway client (limits, aliases) with the manifest; --apply brings the gateway in line")
    ls.add_argument("apps", nargs="*", metavar="APP", help="apps to compare/change (default: all consumers when only comparing)")
    ls.add_argument("--all", action="store_true", help="with --apply: every app that uses an LLM")
    ls.add_argument("--apply", action="store_true", help="PATCH the differing fields (without it nothing changes; exit 1 if there is drift)")
    ls.add_argument("--prune-aliases", action="store_true", help="also remove aliases the manifest does not list (default: keep and report them)")
    args = p.parse_args(argv)
    try:
        commands = {"validate": cmd_validate, "plan": cmd_plan, "apply": cmd_apply, "status": cmd_status, "env": cmd_env, "db": cmd_db, "recreate": cmd_recreate,
                    "llm": cmd_llm, "connect": cmd_connect}
        return commands[args.cmd](args)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
