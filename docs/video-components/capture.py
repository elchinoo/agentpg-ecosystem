#!/usr/bin/env python3
"""Run the real code of the components video and keep what it printed.

    python3 capture.py WORKDIR

Everything is local and throw-away, inside WORKDIR (a directory this script owns; it is emptied first):
  * two Postgres 17 clusters (port 55511, and 55513 which starts late to show the wake-up retry), UTF-8, scram passwords
  * a virtualenv with the kit installed from the sibling checkout (`uv pip install ../agentpg-kit`)
  * the identity service (identity/.venv) and the llm-gateway (llm-gateway/.venv), each on its own database, with the built-in echo model
  * the demo apps in demos/ (uvicorn), the notes example app, and Playwright stills of the gateway console and of notes
Output: WORKDIR/capture.json  {step id: {"cmd": what is shown, "text": output}} and WORKDIR/shots/*.png.
Nothing here touches AgentPG, the live fleet or ecoctl. Secrets are generated per run and never printed.
"""
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent                       # the folder that holds all the sibling repositories
PGBIN = Path(os.environ.get("PGBIN", "/Users/charly.batista/app/bin"))
PG_PORT, PG2_PORT = 55511, 55513
PORTS = {"identity": 8621, "plain": 8622, "fed": 8623, "limit": 8624, "guard": 8625, "notes": 8626, "gateway": 8631}
procs: list[subprocess.Popen] = []


def sh(cmd, env=None, cwd=None, timeout=180, check=True):
    p = subprocess.run(cmd, shell=True, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                       executable="/bin/bash")
    if check and p.returncode:
        sys.exit(f"command failed ({p.returncode}): {cmd}\n{p.stdout}")
    return p.stdout


def wait_http(url, tries=60):
    for _ in range(tries):
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    sys.exit(f"{url} did not come up")


def start(cmd, env, cwd, log):
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT, executable="/bin/bash",
                         preexec_fn=os.setsid)
    procs.append(p)
    return p


def stop_all():
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for d in ("pg", "pg2"):
        if (WORK / d).exists():
            subprocess.run([str(PGBIN / "pg_ctl"), "-D", str(WORK / d), "-m", "fast", "stop"], capture_output=True)


def cluster(name, port, pw):
    d = WORK / name
    (WORK / f"{name}.pw").write_text(pw)
    sh(f"{PGBIN}/initdb -D {d} -U postgres -E UTF8 --auth=scram-sha-256 --pwfile={WORK}/{name}.pw", check=True)
    return d


def pg_start(d, port):
    sh(f"{PGBIN}/pg_ctl -D {d} -o \"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories=''\" -l {d}.log -w start")


WORK: Path
steps: dict[str, dict] = {}


def main():
    global WORK
    WORK = Path(sys.argv[1]).resolve()
    if WORK.exists():
        shutil.rmtree(WORK)
    (WORK / "shots").mkdir(parents=True)
    pw = secrets.token_hex(8)
    base = {**os.environ, "PATH": f"{PGBIN}:{WORK}/venv/bin:{os.environ['PATH']}", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"}
    pgenv = {"PGHOST": "127.0.0.1", "PGPORT": str(PG_PORT), "PGUSER": "postgres", "PGPASSWORD": pw, "PGDATABASE": "demo"}
    env = {**base, **pgenv}
    try:
        d1 = cluster("pg", PG_PORT, pw)
        pg_start(d1, PG_PORT)
        for db in ("demo", "identity", "llmgw", "notes"):
            sh(f"psql -X -h 127.0.0.1 -p {PG_PORT} -U postgres -d postgres -qc 'CREATE DATABASE {db}'", env=env)
        d2 = cluster("pg2", PG2_PORT, pw)                    # starts, gets its database, stops: the "sleeping" database
        pg_start(d2, PG2_PORT)
        sh(f"psql -X -h 127.0.0.1 -p {PG2_PORT} -U postgres -d postgres -qc 'CREATE DATABASE demo'", env=env)
        sh(f"{PGBIN}/pg_ctl -D {d2} -m fast stop")
        demo = WORK / "demo"
        shutil.copytree(HERE / "demos", demo)
        shutil.copy(ROOT / "shopfront/src/shopfront/webhooks.py", demo / "webhooks.py")
        shutil.copy(ROOT / "paybroker/src/paybroker/migrations/007_audit_ledger.sql", demo / "007_audit_ledger.sql")

        def rec(sid, shown, cmd, e=env, cwd=demo, timeout=180):
            out = sh(cmd, env=e, cwd=cwd, timeout=timeout, check=False).rstrip("\n")
            steps[sid] = {"cmd": shown, "text": out}
            print(f"[{sid}] {shown}\n{out}\n")

        # ---- install the kit from the sibling checkout, in a fresh virtualenv -----------------------------------
        sh(f"uv venv -q --python 3.12 {WORK}/venv", env=base)
        rec("install", "uv pip install ../agentpg-kit uvicorn", f"uv pip install --python {WORK}/venv/bin/python {ROOT}/agentpg-kit uvicorn 2>&1 | "
            f"grep -E 'Installed|agentpg-kit' | sed 's#{ROOT}#..#'", base)
        rec("version", "python -c \"import apgkit; print(apgkit.__version__)\"", "python -c 'import apgkit; print(apgkit.__version__)'")

        # ---- identity (real service, local) ---------------------------------------------------------------------
        idk, gwk = "idk-" + secrets.token_hex(12), "gwk-" + secrets.token_hex(12)
        ienv = {**base, "DATABASE_URL": f"postgresql://postgres:{pw}@127.0.0.1:{PG_PORT}/identity", "IDENTITY_PUBLIC_URL": f"http://127.0.0.1:{PORTS['identity']}",
                "IDENTITY_API_KEY": idk, "IDENTITY_ENCRYPTION_KEY": "enc-" + secrets.token_hex(16), "HOST": "127.0.0.1", "PORT": str(PORTS["identity"])}
        start(f"{ROOT}/identity/.venv/bin/identity start", ienv, ROOT / "identity", WORK / "identity.log")
        wait_http(f"http://127.0.0.1:{PORTS['identity']}/readyz")
        import httpx
        ih = {"Authorization": f"Bearer {idk}"}
        iss = f"http://127.0.0.1:{PORTS['identity']}"
        httpx.post(f"{iss}/api/apps", headers=ih, json={"client_id": "demo", "name": "Demo app", "kind": "web", "redirect_uris": ["http://localhost:8623/auth/callback"],
                   "roles": [{"name": "admin", "description": ""}]}).raise_for_status()
        r = httpx.post(f"{iss}/api/apps", headers=ih, json={"client_id": "caller", "name": "A calling service", "kind": "service",
                       "allowed_scopes": ["demo.read"], "allowed_audiences": ["demo"]})
        r.raise_for_status()
        caller_secret = r.json()["client_secret"]

        # ---- gateway (real service, local; the built-in echo model needs no network) ----------------------------
        genv = {**base, "DATABASE_URL": f"postgresql://postgres:{pw}@127.0.0.1:{PG_PORT}/llmgw", "LLMGW_API_KEY": gwk, "LLMGW_ENCRYPTION_KEY": "enc-" + secrets.token_hex(16),
                "LLMGW_PUBLIC_URL": f"http://127.0.0.1:{PORTS['gateway']}", "HOST": "127.0.0.1", "PORT": str(PORTS["gateway"])}
        sh(f"{ROOT}/llm-gateway/.venv/bin/llmgw migrate", env=genv, cwd=ROOT / "llm-gateway")
        start(f"{ROOT}/llm-gateway/.venv/bin/llmgw serve", genv, ROOT / "llm-gateway", WORK / "gateway.log")
        wait_http(f"http://127.0.0.1:{PORTS['gateway']}/readyz")
        gh = {"Authorization": f"Bearer {gwk}"}
        gb = f"http://127.0.0.1:{PORTS['gateway']}"
        cl = httpx.post(f"{gb}/api/clients", headers=gh, json={"name": "notes-app", "description": "the notes example", "allowed_aliases": ["echo-chat", "chat-default"],
                        "rpm_limit": 30, "monthly_token_budget": 100000})
        cl.raise_for_status()
        client_key = cl.json()["key"] if isinstance(cl.json().get("key"), str) else cl.json()["key"]["key"]
        httpx.post(f"{gb}/api/clients", headers=gh, json={"name": "pagerlens", "description": "incident investigator", "allowed_aliases": ["chat-default"],
                   "rpm_limit": 60, "monthly_token_budget": 500000, "budget_mode": "soft"}).raise_for_status()

        # ---- A. authentication and authorization ---------------------------------------------------------------
        def uv(port, extra="", name="protect"):
            e = {**env, **extra} if isinstance(extra, dict) else env
            start(f"python -m uvicorn {name}:app --port {port} --log-level warning", e, demo, WORK / f"{name}-{port}.log")
            wait_http(f"http://127.0.0.1:{port}/openapi.json" if name == "protect" else f"http://127.0.0.1:{port}/metrics")

        rec("mktoken_read", "python mktoken.py read", "python mktoken.py read")
        rec("mktoken_other", "python mktoken.py other", "python mktoken.py other")
        uv(PORTS["plain"])
        U = f"http://127.0.0.1:{PORTS['plain']}/things"
        c = lambda hdr="": f"curl -s -w ' HTTP %{{http_code}}\\n' {hdr} {{url}}"  # noqa: E731
        rec("curl_none", "curl -s -w ' HTTP %{http_code}' localhost:8000/things",
            f"curl -s -w ' HTTP %{{http_code}}\\n' {U}")
        rec("curl_other", "curl -s -w ' HTTP %{http_code}' -H \"Authorization: Bearer $(cat other.token)\" localhost:8000/things",
            f"curl -s -w ' HTTP %{{http_code}}\\n' -H \"Authorization: Bearer $(cat other.token)\" {U}")
        rec("curl_read", "curl -s -w ' HTTP %{http_code}' -H \"Authorization: Bearer $(cat read.token)\" localhost:8000/things",
            f"curl -s -w ' HTTP %{{http_code}}\\n' -H \"Authorization: Bearer $(cat read.token)\" {U}")
        # same endpoint, federation turned on by two environment variables
        fenv = {"DEMO_OIDC_ISSUER": iss, "DEMO_OIDC_CLIENT_ID": "demo"}
        uv(PORTS["fed"], fenv)
        FU = f"http://127.0.0.1:{PORTS['fed']}/things"
        rec("svc_token", "python service_token.py", "python service_token.py", {**env, **fenv, "CALLER_SECRET": caller_secret})
        rec("curl_svc", "curl -s -w ' HTTP %{http_code}' -H \"Authorization: Bearer $(cat svc.token)\" localhost:8000/things",
            f"curl -s -w ' HTTP %{{http_code}}\\n' -H \"Authorization: Bearer $(cat svc.token)\" {FU}")
        rec("curl_tampered", "curl -s -w ' HTTP %{http_code}' -H \"Authorization: Bearer $(cat svc.token)x\" localhost:8000/things",
            f"curl -s -w ' HTTP %{{http_code}}\\n' -H \"Authorization: Bearer $(cat svc.token)x\" {FU}")
        rec("curl_local_still", "curl -s -w ' HTTP %{http_code}' -H \"Authorization: Bearer $(cat read.token)\" localhost:8000/things",
            f"curl -s -w ' HTTP %{{http_code}}\\n' -H \"Authorization: Bearer $(cat read.token)\" {FU}")
        uv(PORTS["limit"])
        rec("ratelimit", "for i in $(seq 12); do curl -s -o /dev/null -w '%{http_code} ' -H 'Authorization: Bearer dm_wrong' localhost:8000/things; done",
            f"for i in $(seq 12); do curl -s -o /dev/null -w '%{{http_code}} ' -H 'Authorization: Bearer dm_wrong' http://127.0.0.1:{PORTS['limit']}/things; done; echo")

        # ---- B. vault -----------------------------------------------------------------------------------------
        rec("vault", "python vault_demo.py", "python vault_demo.py")
        stored = sh("psql -X -At -c \"SELECT key || ' = ' || left(value, 22) || '...' FROM app_setting WHERE key LIKE 'vault%' ORDER BY key\"", env=env, cwd=demo).rstrip()
        steps["vault_rows"] = {"cmd": "psql -c \"SELECT key, left(value, 22) FROM app_setting WHERE key LIKE 'vault%'\"", "text": stored}

        # ---- C. database ---------------------------------------------------------------------------------------
        wenv = {**env, "PGPORT": str(PG2_PORT)}
        late = subprocess.Popen(f"sleep 2.5; {PGBIN}/pg_ctl -D {WORK}/pg2 -o \"-p {PG2_PORT} -c listen_addresses=127.0.0.1 -c unix_socket_directories=''\" "
                                f"-l {WORK}/pg2.log -w start >/dev/null", shell=True, executable="/bin/bash")
        rec("wake", "python wake.py 2>&1 | cut -c1-104", "python wake.py 2>&1 | cut -c1-104", wenv)
        late.wait()
        rec("data", "python data_demo.py", "python data_demo.py")
        rec("tables", "psql -c \"SELECT * FROM demo_migrations\"", "psql -X -c 'SELECT * FROM demo_migrations ORDER BY name' | sed -E 's/[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:.]+\\+[0-9]{2}/<time>/'")

        # ---- D. AI access --------------------------------------------------------------------------------------
        lenv = {**env, "GATEWAY_KEY": client_key}
        rec("llm_refused", "python llm_demo.py", "python llm_demo.py", lenv)
        rec("llm_ok", "DEMO_ALLOW_PRIVATE_URLS=true python llm_demo.py", "python llm_demo.py", {**lenv, "DEMO_ALLOW_PRIVATE_URLS": "true"})
        rec("gw_models", "curl -s -H \"Authorization: Bearer $KEY\" localhost:8000/v1/models",
            f"curl -s -H \"Authorization: Bearer {client_key}\" {gb}/v1/models | python -c \"import sys,json; print([m['id'] for m in json.load(sys.stdin)['data']])\"")

        # ---- E. security and governance ------------------------------------------------------------------------
        uv(PORTS["guard"], name="guarded")
        G = f"http://127.0.0.1:{PORTS['guard']}"
        rec("guard_headers", "curl -si localhost:8000/metrics | grep -iE '^(HTTP|x-|referrer|content-sec|cache)' | cut -c1-96", f"curl -si {G}/metrics | grep -iE '^(HTTP|x-|referrer|content-sec|cache)' | cut -c1-96 | tr -d '\\r'")
        rec("guard_csrf", "curl -s -w ' HTTP %{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' localhost:8000/api/notes",
            f"curl -s -w ' HTTP %{{http_code}}\\n' -X POST -H 'Content-Type: application/json' -d '{{}}' {G}/api/notes")
        rec("guard_csrf_ok", "curl -s -w ' HTTP %{http_code}' -X POST -H 'x-demo-csrf: 1' -H 'Content-Type: application/json' -d '{}' localhost:8000/api/notes",
            f"curl -s -w ' HTTP %{{http_code}}\\n' -X POST -H 'x-demo-csrf: 1' -H 'Content-Type: application/json' -d '{{}}' {G}/api/notes")
        rec("guard_big", "curl -s -w ' HTTP %{http_code}' -X POST -H 'x-demo-csrf: 1' -H 'Content-Type: application/json' --data-binary @big.json localhost:8000/api/notes",
            f"python -c \"print('{{\\\"x\\\": \\\"' + 'a'*2000 + '\\\"}}')\" > big.json; curl -s -w ' HTTP %{{http_code}}\\n' -X POST -H 'x-demo-csrf: 1' -H 'Content-Type: application/json' --data-binary @big.json {G}/api/notes")
        rec("guard_metrics", "curl -s localhost:8000/metrics | grep '^http_requests_total'",
            f"curl -s {G}/metrics | grep '^http_requests_total' | sed -E 's/app=\"demo\",//' ")
        sh("psql -X -qc 'CREATE ROLE a1p_readonly NOLOGIN'", env=env, cwd=demo, check=False)
        rec("mask", "python mask_demo.py", "python mask_demo.py")
        rec("mask_base", "psql -c \"SET ROLE a1p_readonly; SELECT * FROM person\"", "psql -X -c 'SET ROLE a1p_readonly' -c 'SELECT * FROM person' 2>&1 | grep -v '^SET'")
        rec("mask_view", "psql -c \"SET ROLE a1p_readonly; SELECT * FROM person_masked\"", "psql -X -c 'SET ROLE a1p_readonly' -c 'SELECT * FROM person_masked' 2>&1 | grep -v '^SET'")
        rec("ledger_apply", "psql -q -f 007_audit_ledger.sql", "psql -X -q -f 007_audit_ledger.sql 2>&1; echo done")
        rec("ledger_write", "psql -c \"SELECT audit_append('alice','refund','pay_'||g,'{\\\"amount\\\":10}') FROM generate_series(1,3) g\"",
            "psql -X -c \"SELECT audit_append('alice','refund','pay_'||g,'{\\\"amount\\\":10}') FROM generate_series(1,3) g\"")
        rec("ledger_verify", "psql -c \"SELECT entries, first_broken, reason FROM audit_verify_detail()\"",
            "psql -X -c 'SELECT entries, first_broken, reason FROM audit_verify_detail()'")
        rec("ledger_update", "psql -c \"UPDATE audit_ledger SET payload = '{\\\"amount\\\":1}' WHERE seq = 2\"",
            "psql -X -c \"UPDATE audit_ledger SET payload = '{\\\"amount\\\":1}' WHERE seq = 2\" 2>&1 | cut -c1-100")
        rec("ledger_tamper", "psql -c \"ALTER TABLE audit_ledger DISABLE TRIGGER USER\" -c \"UPDATE ...\" -c \"SELECT ... FROM audit_verify_detail()\"",
            "psql -X -c 'ALTER TABLE audit_ledger DISABLE TRIGGER USER' -c \"UPDATE audit_ledger SET payload = '{\\\"amount\\\":1}' WHERE seq = 2\" "
            "-c 'SELECT entries, first_broken, reason FROM audit_verify_detail()'")

        # ---- F. patterns inside the apps ---------------------------------------------------------------------
        rec("webhook", "python webhook_demo.py", "python webhook_demo.py")
        shutil.copy(HERE / "demos" / "place_demo.py", demo / "place_demo.py")
        rec("place", "python place_demo.py", f"{ROOT}/CityPulseSimulator/.venv/bin/python place_demo.py")

        # ---- G. the notes example app + Playwright stills --------------------------------------------------------
        sh(f"uv pip install -q --python {WORK}/venv/bin/python -e {ROOT}/agentpg-kit/docs/examples/notes", env=base)
        nenv = {**base, "DATABASE_URL": f"postgresql://postgres:{pw}@127.0.0.1:{PG_PORT}/notes", "PORT": str(PORTS["notes"]), "NOTES_ENCRYPTION_KEY": "enc-" + secrets.token_hex(16)}
        start("notes", nenv, demo, WORK / "notes.log") if False else start(f"{WORK}/venv/bin/notes", nenv, demo, WORK / "notes.log")
        wait_http(f"http://127.0.0.1:{PORTS['notes']}/healthz")
        code = [ln for ln in (WORK / "notes.log").read_text().splitlines() if "Setup code" in ln]
        for _ in range(20):
            code = [ln for ln in (WORK / "notes.log").read_text().splitlines() if "Setup code" in ln]
            if code:
                break
            time.sleep(0.5)
        shots = HERE / "shots.mjs"
        out = sh(f"node {shots}", env={**base, "SHOTS": str(WORK / "shots"), "NOTES": f"http://127.0.0.1:{PORTS['notes']}", "SETUP": code[0].split(":")[-1].strip(),
                                        "GATEWAY": gb, "GWKEY": gwk, "IDENTITY": iss, "IDKEY": idk}, cwd=HERE, timeout=240)
        print(out)
        steps["notes_files"] = {"cmd": "cd src/notes && ls && wc -l app.py core.py migrations/*.sql static/*",
                                "text": sh(f"cd {ROOT}/agentpg-kit/docs/examples/notes/src/notes && ls && wc -l app.py core.py migrations/*.sql static/*", env=base).strip()}
        steps["notes_health"] = {"cmd": "curl -s localhost:8000/healthz; curl -s localhost:8000/readyz",
                                 "text": sh(f"curl -s http://127.0.0.1:{PORTS['notes']}/healthz; echo; curl -s http://127.0.0.1:{PORTS['notes']}/readyz", env=base)}
        (WORK / "capture.json").write_text(json.dumps(steps, indent=1))
        print("wrote", WORK / "capture.json")
    finally:
        stop_all()


if __name__ == "__main__":
    main()
