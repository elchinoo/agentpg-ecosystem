"""ecoctl: validate, plan, apply, recreate, unlock, status, env, insights, mcp."""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from . import devstack
from . import insights as insights_mod
from . import mcp as mcp_mod
from . import seal as seal_mod
from .agentpg import AgentPG, AgentPGError, token_from
from .connect import print_status, reconcile
from .connect_client import ConnectError
from .identity_client import Identity, IdentityError
from .llm_client import GatewayError
from .llm_sync import diff_app, table
from .manifest import ManifestError, load
from .plan import Context, app_env, build, installed_extensions
from .state import DEFAULT_DIR, State, StateError
from .vault import ENV_PASSWORD, VaultError, describe, ensure_unlocked, probe

UNSEALED_WARNING = ("WARNING: agentpg.seal is false. Every secret of every app is put in its sandbox environment in the clear, readable by whoever can read the platform, "
                    "and the vault admin password must come from ECOCTL_ADMIN_PASSWORD or a prompt. Remove `seal: false` from the manifest to seal the fleet (the default).\n")


def _print_plan(steps, manifest=None) -> None:
    if manifest is not None and not manifest.seal:
        print(UNSEALED_WARNING)
    for i, s in enumerate(steps, 1):
        print(f"{i:>2}. {s.title}")
        for d in s.detail:
            print(f"      {d}")


def _needs_passphrase_now(args) -> bool:
    """Commands that create things and save what they learn ask for the passphrase up front (the encrypted state cannot be saved without it)."""
    cmd = getattr(args, "cmd", None)
    if cmd == "apply":
        return bool(args.execute)
    return cmd in {"recreate", "db", "connect", "mcp"}


def _ctx(args) -> Context:
    only = {a.strip() for a in args.only.split(",") if a.strip()} if getattr(args, "only", None) else None
    ctx = Context(load(args.file), State(Path(args.state)), AgentPG(), rotate=getattr(args, "rotate", False), only=only,
                  prompt_password=getattr(args, "admin_password_prompt", False))
    if only:
        ctx.manifest.closure(only)                      # unknown names fail now
    if _needs_passphrase_now(args):
        ctx.state.require_passphrase()                  # before anything is created: a missing or mistyped passphrase must not come after the work
    return ctx


def cmd_validate(args) -> int:
    m = load(args.file)
    print(f"ok: fleet '{m.fleet}', identity + {len(m.apps)} apps ({', '.join(a.key for a in m.order())})")
    return 0


def cmd_plan(args) -> int:
    ctx = _ctx(args)
    _print_plan(build(ctx), ctx.manifest)
    print("\nNothing was changed. `ecoctl apply --execute` runs this plan; it is safe to run again.")
    return 0


def cmd_apply(args) -> int:
    ctx = _ctx(args)
    steps = build(ctx)
    _print_plan(steps, ctx.manifest)
    if not args.execute:
        print("\nDry run: nothing was changed. Add --execute to create these on AgentPG and register them with the identity provider.")
        return 0
    if not args.yes:
        print("\nThis creates databases and sandboxes on your AgentPG account (they may use credits) and writes secrets to "
              f"{ctx.state.path}, encrypted with your passphrase (git-ignored).")
        if input("Continue? [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return 1
    for i, s in enumerate(steps, 1):
        print(f"[{i}/{len(steps)}] {s.title} ...", flush=True)
        try:
            print(f"      {s.run(ctx)}")
        except (AgentPGError, IdentityError, GatewayError, ConnectError, VaultError, httpx.HTTPError) as exc:
            ctx.state.save()
            print(f"      FAILED: {exc}\n\nProgress is saved; fix the cause and run the same command again.", file=sys.stderr)
            return 2
    ctx.state.save()
    pw_apps = [a.key for a in ctx.manifest.password_vault_apps() if ctx.state.app(a.key).get("url")]
    if pw_apps:
        print(f"\nThe vault of {', '.join(pw_apps)} is opened with the admin password, which the platform does not store: after a platform restart they stay locked "
              f"until you run `ecoctl unlock` (existing tokens keep working until they expire).")
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


def cmd_unlock(args, ctx: Context | None = None) -> int:
    """`ecoctl unlock [app ...]`: open the vault of the apps with `vault: password` (identity, the LLM gateway) with the operator's admin password.
    Needed after a platform restart (identity cannot sign tokens, and the gateway cannot use a stored provider key, until then). Never creates a
    vault and never makes up a password: `apply` creates them. The password comes from ECOCTL_ADMIN_PASSWORD, --admin-password-prompt or (sealed fleet) the seal passphrase; never kept."""
    ctx = ctx or _ctx(args)
    m = ctx.manifest
    known = m.everything()
    bad = [a for a in args.apps if a not in known]
    if bad:
        print(f"no such app: {', '.join(bad)}", file=sys.stderr)
        return 1
    plain = [a for a in args.apps if known[a].vault != "password"]
    for a in plain:
        print(f"{a}: `vault: platform`, the platform's key opens it by itself; nothing to unlock here")
    targets = [a for a in m.password_vault_apps() if not args.apps or a.key in args.apps]
    if not targets:
        print("no app with `vault: password` in this manifest" if not args.apps else "nothing to unlock")
        return 0
    failed = 0
    for app in targets:
        if not ctx.state.app(app.key).get("url"):
            print(f"{app.key}: not deployed yet (run `ecoctl apply --execute`)")
            failed += 1
            continue
        try:
            print(f"{app.key}: {ensure_unlocked(ctx, app, allow_init=False)}")
        except (VaultError, IdentityError, httpx.HTTPError) as exc:
            print(f"{app.key}: FAILED: {exc}", file=sys.stderr)
            failed += 1
    return 2 if failed else 0


def cmd_unseal(args, ctx: Context | None = None) -> int:
    """`ecoctl unseal [app ...]`: open sealed apps with the operator's passphrase (ECOCTL_SEAL_PASSPHRASE or a prompt; stored nowhere). Needed after every
    platform restart. The passphrase travels encrypted to a key that exists only in the app's process. Identity first, then the gateway, then the rest."""
    ctx = ctx or _ctx(args)
    m = ctx.manifest
    if not m.seal:
        print("this manifest does not use sealed boot (agentpg.seal: true)")
        return 0
    known = m.everything()
    bad = [a for a in args.apps if a not in known]
    if bad:
        print(f"no such app: {', '.join(bad)}", file=sys.stderr)
        return 1
    order = [m.identity, *m.order()]
    failed = 0
    for app in order:
        if not app.deploy or (args.apps and app.key not in args.apps):
            continue
        if not ctx.state.app(app.key).get("url"):
            print(f"{app.key}: not deployed yet (run `ecoctl apply --execute`)")
            failed += 1
            continue
        try:
            print(f"{app.key}: {seal_mod.unseal(ctx, app)}")
        except (seal_mod.SealError, httpx.HTTPError) as exc:
            print(f"{app.key}: FAILED: {exc}", file=sys.stderr)
            failed += 1
    return 2 if failed else 0


def cmd_dev(args) -> int:
    """`ecoctl dev ...`: the Docker development stack (docker/README.md). Needs docker compose; the state (generated secrets) is .ecosystem-dev/state.json, encrypted."""
    m = load(args.file)
    compose = Path(args.compose) if args.compose else Path(args.file).resolve().parent / "docker" / "compose.yml"
    try:
        if args.action == "render":
            text = devstack.render(m)
            if args.check:
                same = compose.exists() and compose.read_text() == text
                print("compose.yml matches the manifest" if same else f"{compose} differs from the manifest: run `ecoctl dev render`")
                return 0 if same else 1
            compose.write_text(text)
            print(f"wrote {compose}")
            return 0
        state = State(Path(args.dev_state))
        ctx = Context(m, state, None)                                                  # type: ignore[arg-type]  (no AgentPG in the Docker stack)
        stack = devstack.Stack(ctx, compose, include={a.strip() for a in args.include.split(",") if a.strip()} if args.include else None)
        if args.action == "up":
            stack.up(build=not args.no_build, recreate={a.strip() for a in args.recreate.split(",") if a.strip()} if args.recreate else None)
            return 0
        if args.action == "unseal":
            state.require_passphrase()
            bad = 0
            for app in stack.active():
                if args.apps and app.key not in args.apps:
                    continue
                try:
                    print(f"{app.key}: {stack.unseal(app)}")
                except (seal_mod.SealError, devstack.DevError, httpx.HTTPError) as exc:
                    print(f"{app.key}: FAILED: {exc}", file=sys.stderr)
                    bad += 1
            return 2 if bad else 0
        if args.action == "aidb-setup":
            state.require_passphrase()
            for key in args.apps or devstack.AIDB_SETUP_APPS:
                print(f"== {key} aidb-setup")
                print(stack.aidb_setup(key, agent=args.agent, reseal=args.reseal))
                note = stack.reopen_if_locked(key)
                if note:
                    print(note)
                print(stack.wait_aidb_ready(key))
            return 0
        if args.action == "status":
            return stack.status()
        if args.action == "test":
            results = devstack.smoke(stack)
            for name, ok, detail in results:
                print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
            return 0 if all(ok for _, ok, _ in results) else 1
        if args.action == "down":
            stack.down(args.volumes)
            return 0
        if args.action == "password":
            pw = (state.data.get("dev") or {}).get("admin_password")
            if not pw:
                print("no dev-admin password yet: run `ecoctl dev up`", file=sys.stderr)
                return 1
            print(pw)
            return 0
    except (devstack.DevError, seal_mod.SealError, IdentityError, GatewayError, ConnectError, httpx.HTTPError) as exc:
        print(f"dev: {exc}", file=sys.stderr)
        return 1
    return 1


def cmd_seal_passphrase(args) -> int:
    """A random passphrase (about 124 bits) for the state file and the sealed apps. Shown once, kept nowhere: put it in your password manager."""
    print(seal_mod._kit().generate_passphrase())
    return 0


def cmd_status(args) -> int:
    ctx = _ctx(args)
    bad = 0
    live = _live_metrics(ctx)
    print(f"{'app':<16}{'health':<10}{'ready':<10}{'vault':<17}{'cpu':<8}{'mem':<8}{'net in/out':<26}url")
    for key, app in ctx.manifest.everything().items():
        url = ctx.state.data["apps"].get(key, {}).get("url")
        if not app.deploy:
            continue
        if not url:
            print(f"{key:<16}{'-':<10}{'-':<10}{'-':<17}{'-':<8}{'-':<8}{'-':<18}(not deployed)")
            continue
        row = []
        for path in (app.health, "/readyz"):
            try:
                row.append(str(httpx.get(url + path, timeout=10).status_code))
            except httpx.HTTPError:
                row.append("down")
        bad += row[0] != "200"
        sealed_state = seal_mod.state_of(url) if ctx.manifest.seal else None
        if sealed_state == "sealed":
            vault_text, vault_ok = "SEALED", False
        else:
            vault_text, vault_ok = describe(app.vault, probe(url))
        bad += not vault_ok
        m = live.get(key, {})
        net = f"{m.get('network_in', '-')} / {m.get('network_out', '-')}" if "network_in" in m or "network_out" in m else "-"
        print(f"{key:<16}{row[0]:<10}{row[1]:<10}{vault_text:<17}{m.get('cpu', '-'):<8}{m.get('memory', '-'):<8}{net:<26}{url}")
    if ctx.manifest.password_vault_apps():
        print("\nvault: `unlocked` = open; `LOCKED` = after a restart, opened only by the admin password: run `ecoctl unlock` (identity cannot sign tokens, the gateway cannot "
              "use a stored provider key, until then); `auto` = the platform key opens it by itself")
    bad += _features_status(ctx)
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


def _live_metrics(ctx) -> dict[str, dict[str, str]]:
    """Latest sandbox metrics per deployed app: one `sandbox status --json` call each, run concurrently. Any failure gives {} for that app."""
    todo = {k: v["sandbox_id"] for k, v in ctx.state.data["apps"].items() if v.get("url") and v.get("sandbox_id")}
    if not todo:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
        futures = {k: pool.submit(ctx.agentpg.sandbox_metrics, sid) for k, sid in todo.items()}
        return {k: f.result() for k, f in futures.items()}


def _features_status(ctx) -> int:
    """Database features per app: the extensions ecoctl has created (from the state file, no call) against the manifest. Missing ones are a warning
    that `apply` fixes, not a failure of the app."""
    rows = []
    for key, app in ctx.manifest.everything().items():
        if not app.deploy or not ctx.state.app(key).get("instance_id"):
            continue
        have = installed_extensions(ctx.state, app)
        missing = [e for e in app.extensions if e not in have]
        rows.append((key, ", ".join(e for e in app.extensions if e in have) or "-", ", ".join(missing)))
    if not rows:
        return 0
    print("\ndatabase features (extensions ecoctl created, from the state file)")
    print(f"  {'app':<16}{'installed':<60}missing")
    for key, got, miss in rows:
        print(f"  {key:<16}{got:<60}{miss and miss + '  (run `ecoctl apply --execute`)'}")
    return 0


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
    if not args.dry_run and m.identity.vault == "password" and ctx.state.app("identity").get("url"):
        try:
            closed = not ctx.vault_admin(m.identity).state().unlocked
        except (VaultError, IdentityError, httpx.HTTPError):
            closed = False                                  # unreachable: the connection itself will say so
        if closed:
            print("identity is locked (its vault is opened with the admin password): it cannot issue the tokens `connect` needs. Run `ecoctl unlock`, then this again.", file=sys.stderr)
            return 2
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


SEALED_ENV_NOTE = ("sealed fleet: the sandbox holds only APGKIT_SEALED and the non-secret settings above. A sealed secret cannot be read back (it is encrypted under your passphrase and "
            "opened only inside the app); to change one, rotate it (`ecoctl apply --rotate`)")


UNSEALED_ENV_NOTE = "seal is off for this fleet: secrets are in the sandbox environment, readable by whoever can read the platform. Remove `seal: false` and recreate to seal them"


def cmd_env(args) -> int:
    ctx = _ctx(args)
    app = ctx.manifest.everything().get(args.app)
    if app is None:
        print(f"no such app: {args.app}", file=sys.stderr)
        return 1
    env, secret_names = app_env(ctx, app)
    sealed = ctx.manifest.seal
    for k, v in sorted(env.items()):
        if k in secret_names:
            print(f"{k}=*** (secret, {'inside the sealed blob' if sealed else 'in the sandbox environment'}; never shown)")
        else:
            print(f"{k}={v}")
    print(f"# {SEALED_ENV_NOTE if sealed else UNSEALED_ENV_NOTE}", file=sys.stderr)
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


def cmd_insights(args) -> int:
    """`ecoctl insights [app ...|--all] [--json]`: the query advisor's index and statistics recommendations per app database. Read-only."""
    ctx = _ctx(args)
    known = ctx.manifest.everything()
    if args.apps and args.all:
        print("name apps or use --all, not both", file=sys.stderr)
        return 1
    bad = [a for a in args.apps if a not in known]
    if bad:
        print(f"no such app: {', '.join(bad)}", file=sys.stderr)
        return 1
    apps = list(dict.fromkeys(args.apps)) or [k for k, a in known.items() if a.deploy and ctx.state.app(k).get("instance_id")]
    if not apps:
        print("no app with a database yet (run `ecoctl apply --execute`)", file=sys.stderr)
        return 1
    results = insights_mod.collect(ctx, apps)
    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2))
    else:
        print(insights_mod.render(results))
    return 1 if any(r.error for r in results) else 0


def cmd_mcp(args) -> int:
    ctx = _ctx(args)
    try:
        return _mcp(ctx, args)
    except (mcp_mod.McpError, AgentPGError) as exc:
        print(f"mcp: {exc}", file=sys.stderr)
        return 2


def _mcp(ctx, args) -> int:
    act = args.action
    apps = mcp_mod.select(ctx, args.apps, args.all, need_user=act != "users", emit=lambda s: print(s, file=sys.stderr))
    if act == "users":
        return mcp_mod.users(ctx, apps)
    if act == "config":
        path = mcp_mod.write_config(ctx, apps, args.config_file)
        print(f"wrote {path} (encrypted under your passphrase, mode 0600) for {', '.join(apps)}: restricted mode, read-only user {mcp_mod.DB_USER}, {mcp_mod.MAX_CONNECTIONS} connections each")
        print(f"`ecoctl mcp serve --from-file {args.config_file}` decrypts it in memory and gives it to the server through a pipe; `agentpg mcp serve --config` cannot read it directly")
        return 0
    if act == "serve":
        return mcp_mod.serve(ctx, apps, args.transport, args.port, args.host, args.from_file)
    if act == "claude":
        if args.write and not args.project:
            print("--write updates the project's .mcp.json: add --project", file=sys.stderr)
            return 1
        entries = mcp_mod.claude_entries(ctx, apps, args.file)
        scope = "project" if args.project else None
        if args.project:
            print(json.dumps({"mcpServers": entries}, indent=2))
        for name, entry in entries.items():
            print(mcp_mod.claude_add_command(name, entry, scope), file=sys.stderr if args.project else sys.stdout)
        if args.write:
            target = Path(".mcp.json")
            mcp_mod.merge_mcp_json(target, entries)
            print(f"updated {target.resolve()}: do not commit it (it names local paths); it holds no credential and no passphrase", file=sys.stderr)
        else:
            print("# the stanza holds no credential: `ecoctl mcp serve` reads them from the encrypted state. The passphrase comes from ECOCTL_SEAL_PASSPHRASE_CMD (set it to your "
                  "password manager's command before running this, and the stanza carries that command) or from ECOCTL_SEAL_PASSPHRASE in Claude Code's own environment "
                  "(which every tool Claude runs then inherits: prefer the command)", file=sys.stderr)
        return 0
    return mcp_mod.check(ctx, apps)


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
    prompt_help = f"ask for the admin password of the `vault: password` apps (identity, the LLM gateway) instead of {ENV_PASSWORD}; it is not kept (a sealed fleet uses the seal passphrase)"
    ap.add_argument("--admin-password-prompt", action="store_true", help=prompt_help)
    rc = sub.add_parser("recreate", help="delete sandboxes and create them afresh (new image or environment); cascades to apps that depend on them")
    rc.add_argument("apps", help="comma-separated app keys, or identity (which recreates everything)")
    rc.add_argument("--yes", action="store_true")
    rc.add_argument("--admin-password-prompt", action="store_true", help=prompt_help)
    us = sub.add_parser("unseal", help="open sealed apps (agentpg.seal: true) with the operator's passphrase; needed after every platform restart")
    us.add_argument("apps", nargs="*", metavar="APP", help="apps to unseal (default: every deployed app)")
    sub.add_parser("seal-passphrase", help="print a random passphrase for sealing (kept nowhere: store it in your password manager)")
    ul = sub.add_parser("unlock", help="open the vault of the apps with `vault: password` (identity, the LLM gateway) with the admin password; needed after a platform restart")
    ul.add_argument("apps", nargs="*", metavar="APP", help="apps to unlock (default: every app with `vault: password`)")
    ul.add_argument("--admin-password-prompt", action="store_true", help=prompt_help)
    db = sub.add_parser("db", help="print the database key (the database password) of an app's instance")
    db.add_argument("app")
    db.add_argument("--reset", action="store_true", help="rotate the instance's default key and keep the new one")
    cn = sub.add_parser("connect", help="connect apps by themselves (ShopFront to PayBroker: merchant, origin, keys, webhook, settings); safe to run again")
    cn.add_argument("app", nargs="?", help="the app whose `connect:` to reconcile (default: every app that has one)")
    cn.add_argument("--rotate", action="store_true", help="create the merchant keys and the webhook signing secret afresh and push them")
    cn.add_argument("--dry-run", action="store_true", help="show what would change without writing anything")
    sub.add_parser("status", help="health of every app, each LLM client and its test call, the connections, and drift from the identity registry")
    en = sub.add_parser("env", help="print the names and non-secret values of the environment an app is created with (secrets are only ever shown as ***)")
    en.add_argument("app")
    ll = sub.add_parser("llm", help="LLM gateway wiring").add_subparsers(dest="action", required=True)
    lt = ll.add_parser("test", help="one tiny chat call through the gateway with the app's own key")
    lt.add_argument("app")
    ls = ll.add_parser("sync", help="compare each app's gateway client (limits, aliases) with the manifest; --apply brings the gateway in line")
    ls.add_argument("apps", nargs="*", metavar="APP", help="apps to compare/change (default: all consumers when only comparing)")
    ls.add_argument("--all", action="store_true", help="with --apply: every app that uses an LLM")
    ls.add_argument("--apply", action="store_true", help="PATCH the differing fields (without it nothing changes; exit 1 if there is drift)")
    ls.add_argument("--prune-aliases", action="store_true", help="also remove aliases the manifest does not list (default: keep and report them)")
    ins = sub.add_parser("insights", help="index and statistics recommendations of AgentPG's query advisor per app database (read-only)")
    ins.add_argument("apps", nargs="*", metavar="APP", help="apps to look at (default: every deployed app)")
    ins.add_argument("--all", action="store_true", help="every deployed app (the default when none is named)")
    ins.add_argument("--json", action="store_true", help="machine-readable output")
    mc = sub.add_parser("mcp", help="read-only AI access to the app databases through AgentPG's database MCP server").add_subparsers(dest="action", required=True)
    for name, text in (("users", "create (once) a read-only database user per app instance; credentials go to the state file only"),
                       ("config", "write the AgentPG MCP config (restricted mode, read-only user) into the state directory as an encrypted blob"),
                       ("serve", "run `agentpg mcp serve`, giving it the decrypted config through a pipe, never a file (http always with a generated secret)"),
                       ("claude", "print (or with --project --write, merge) the Claude Code server entries: one stdio server per app"),
                       ("check", "start the server, list its tools, run `select current_user` and prove a write is refused")):
        sp = mc.add_parser(name, help=text)
        sp.add_argument("apps", nargs="*", metavar="APP", help="apps (default: all; for everything but `users`, those that have a read-only user)")
        sp.add_argument("--all", action="store_true", help="every app (identity is left out of --all: name it to include it)")
        if name == "config":
            sp.add_argument("--file", dest="config_file", default=mcp_mod.DEFAULT_FILE, help="blob file name inside the state directory (default databases.sealed)")
        if name == "serve":
            sp.add_argument("--from-file", dest="from_file", default=None, metavar="NAME", help="use the blob NAME from `mcp config` instead of building the config from the state")
            sp.add_argument("--transport", choices=["stdio", "http"], default="stdio")
            sp.add_argument("--port", type=int, default=8080, help="http only")
            sp.add_argument("--host", default="127.0.0.1", help="http only: address to bind (default loopback; AgentPG's own default is 0.0.0.0)")
        if name == "claude":
            sp.add_argument("--project", action="store_true", help="project scope: print the .mcp.json content")
            sp.add_argument("--write", action="store_true", help="with --project: merge the entries into ./.mcp.json")
    dv = sub.add_parser("dev", help="the Docker development stack: the whole fleet on this machine, sealed like the real one (docker/README.md)")
    dv.add_argument("action", choices=["render", "up", "unseal", "status", "test", "down", "password", "aidb-setup"],
                    help="render: write docker/compose.yml from the manifest (--check: only compare); up: build, start, seal, unseal, register, connect (safe to repeat); "
                         "unseal: after a restart; aidb-setup [APP...]: the apps' AIDB operator step; status; test: smoke checks; down: stop (-v: also data and state); password: the dev-admin login")
    dv.add_argument("apps", nargs="*", metavar="APP", help="unseal: only these apps")
    dv.add_argument("--compose", help="the compose file (default docker/compose.yml next to the manifest)")
    dv.add_argument("--dev-state", default=str(devstack.DEV_STATE_DIR), help="where the stack keeps its generated secrets (default .ecosystem-dev)")
    dv.add_argument("--check", action="store_true", help="render: do not write, exit 1 if the file differs")
    dv.add_argument("--no-build", action="store_true", help="up: do not build images (use the ones that exist)")
    dv.add_argument("--include", help="up: comma-separated apps that are skipped by default because they cannot boot sealed yet (devstack.NOT_SEALABLE)")
    dv.add_argument("--agent", action="store_true", help="aidb-setup: also register the gateway chat model and the agent (needs the gateway reachable from the database over http: see docs/10)")
    dv.add_argument("--reseal", action="store_true", help="aidb-setup: accept AIDB objects that no longer match their seal")
    dv.add_argument("--recreate", help="up: comma-separated apps whose container is created afresh (a new image, a new blob)")
    dv.add_argument("-v", "--volumes", action="store_true", help="down: also delete the database volume and the stack's state file")
    args = p.parse_args(argv)
    try:
        commands = {"validate": cmd_validate, "plan": cmd_plan, "apply": cmd_apply, "status": cmd_status, "env": cmd_env, "db": cmd_db, "recreate": cmd_recreate, "unlock": cmd_unlock,
                    "unseal": cmd_unseal, "seal-passphrase": cmd_seal_passphrase,
                    "llm": cmd_llm, "dev": cmd_dev, "connect": cmd_connect, "insights": cmd_insights, "mcp": cmd_mcp}
        return commands[args.cmd](args)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1
    except StateError as exc:
        print(f"state error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
