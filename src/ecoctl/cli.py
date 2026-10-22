"""ecoctl: validate, plan, apply, status, env."""
import argparse
import sys
from pathlib import Path

import httpx

from .agentpg import AgentPG, AgentPGError
from .identity_client import Identity, IdentityError
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
        except (AgentPGError, IdentityError, httpx.HTTPError) as exc:
            ctx.state.save()
            print(f"      FAILED: {exc}\n\nProgress is saved; fix the cause and run the same command again.", file=sys.stderr)
            return 2
    ctx.state.save()
    print("\nDone. Open the identity provider's admin console, add yourself to the group(s) above, then open the apps:")
    for key, entry in sorted(ctx.state.data["apps"].items()):
        if entry.get("url"):
            print(f"  {key:<14} {entry['url']}")
    return 0


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
    ident_url = ctx.state.data["apps"].get("identity", {}).get("url")
    if ident_url:
        try:
            registered = Identity(ident_url, ctx.state.admin_key()).apps()
        except (IdentityError, httpx.HTTPError) as exc:
            print(f"\nidentity registry: {exc}")
            return 1
        print("\nidentity registry")
        for key, app in ctx.manifest.apps.items():
            for cid in ([key] if app.web else []) + ([app.service_client_id] if app.service else []):
                r = registered.get(cid)
                drift = ""
                if r is None:
                    drift = "NOT REGISTERED"
                elif app.web and cid == key and r["url"] != ctx.state.app(key).get("url", ""):
                    drift = f"launcher URL differs ({r['url'] or 'empty'})"
                print(f"  {cid:<22}{r['kind'] if r else '-':<9}{drift or 'ok'}")
                bad += bool(drift)
    return 1 if bad else 0


def cmd_env(args) -> int:
    ctx = _ctx(args)
    app = ctx.manifest.everything().get(args.app)
    if app is None:
        print(f"no such app: {args.app}", file=sys.stderr)
        return 1
    env, _ = app_env(ctx, app)
    print("# contains secrets: do not paste into a chat or commit it", file=sys.stderr)
    for k, v in sorted(env.items()):
        print(f"export {k}={v!r}")
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
    sub.add_parser("status", help="health of every app and drift from the identity registry")
    en = sub.add_parser("env", help="print the environment an app is created with")
    en.add_argument("app")
    args = p.parse_args(argv)
    try:
        return {"validate": cmd_validate, "plan": cmd_plan, "apply": cmd_apply, "status": cmd_status, "env": cmd_env}[args.cmd](args)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
