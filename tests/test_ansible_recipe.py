"""The Ansible recipe (ansible/) must stay in step with ecosystem.yaml and with ecoctl's contract. Runs without Ansible, the agentpg CLI or any
infrastructure: it loads the recipe's filter plugin and the shared code of its agentpg_* modules, renders its env template with Jinja2 and compares
everything with ecoctl.manifest, ecoctl.plan and ecoctl.agentpg (the modules also run against ecoctl's in-memory fake CLI)."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

from ecoctl.agentpg import AgentPG
from ecoctl.manifest import load
from ecoctl.plan import Context, _env_hash, app_env, ecoctl_spec, llm_admin_name, service_spec, web_spec
from ecoctl.state import State
from tests.fakes import FakeAgentPG

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = ROOT / "ansible"
MANIFEST = ROOT / "ecosystem.yaml"
ANSIBLE_ONLY = {"DATABASE_URL", "IDENTITY_PUBLIC_URL", "PORT", "SSL_CERT_FILE"}        # what AgentPG injects on its platform, a plain host must be told


def _eco():
    spec = importlib.util.spec_from_file_location("eco_filters", ANSIBLE / "plugins/filter/eco.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eco = _eco()


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ANSIBLE / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ap = _load("eco_agentpg", "module_utils/eco_agentpg.py")


@pytest.fixture(scope="module")
def manifest():
    return load(MANIFEST)


@pytest.fixture(scope="module")
def fleet():
    return eco.eco_fleet(yaml.safe_load(MANIFEST.read_text()))


def test_every_app_of_the_manifest_is_in_the_fleet(manifest, fleet):
    assert set(fleet["apps"]) == set(manifest.apps)
    assert set(fleet["deployed"]) == {k for k, a in manifest.apps.items() if a.deploy}
    assert fleet["selected"] == [a.key for a in manifest.order()]            # the creation order of ecoctl: callees before callers
    assert fleet["fleet"] == manifest.fleet


def test_defaults_match_ecoctl(manifest, fleet):
    for key, app in manifest.everything().items():
        mine = eco.eco_get(fleet, key)
        assert (mine["port"], mine["env_prefix"], mine["health"], mine["web"], mine["deploy"], mine["llm"]) == (app.port, app.env_prefix, app.health, app.web, app.deploy, app.llm), key
        assert mine["image_ref"] == manifest.image(app), key
        assert mine["url_vars"] == manifest.url_vars(app), key
        assert mine["service"] == app.service and mine["service_client_id"] == app.service_client_id, key
        assert mine["generate"] == list(app.generate) and mine["extensions"] == list(app.extensions) and mine["env"] == app.env, key
        if app.llm:
            lim = manifest.limits(key)
            assert mine["limits"] == {"rpm": lim.rpm, "budget": lim.budget, "budget_mode": lim.budget_mode, "aliases": list(lim.aliases)}, key
    assert fleet["llm"] == {"gateway": manifest.llm.gateway, "chat_alias": manifest.llm.chat_alias, "embed_alias": manifest.llm.embed_alias,
                            "embed_dimensions": manifest.llm.embed_dimensions, "rpm": manifest.llm.rpm, "budget": manifest.llm.budget,
                            "budget_mode": manifest.llm.budget_mode}
    assert fleet["consumers"] == manifest.consumers()


def test_identity_specs_match_ecoctl(manifest, fleet):
    for key, app in manifest.apps.items():
        mine = eco.eco_get(fleet, key)
        if app.web:
            assert eco.eco_web_spec(mine) == web_spec(app), key
        if app.service:
            assert eco.eco_service_spec(mine) == service_spec(app), key
    scopes, audiences = manifest.connect_scopes()
    assert (fleet["connect_scopes"], fleet["connect_audiences"]) == (scopes, audiences)
    mine, theirs = eco.eco_connect_spec(fleet), ecoctl_spec(manifest)
    for k in ("kind", "allowed_scopes", "allowed_audiences", "roles", "default_roles", "redirect_uris", "post_logout_uris", "url"):
        assert mine[k] == theirs[k], k
    assert [(c["app"], c["peer"], c["merchant"]) for c in fleet["connections"]] == [(k, p, a.connect[p].merchant) for k, a in manifest.apps.items() for p in a.connect]


def test_a_slice_pulls_in_what_it_needs(manifest):
    only = eco.eco_fleet(yaml.safe_load(MANIFEST.read_text()), ["shopfront"])
    assert set(only["selected"]) == manifest.closure(["shopfront"])
    assert only["selected"].index("llmgw") < only["selected"].index("shopfront") and only["selected"].index("paybroker") < only["selected"].index("shopfront")


def test_secret_names_cover_the_manifest(manifest, fleet):
    names, hosts_names = set(eco.eco_secret_names(fleet)), set(eco.eco_secret_names(fleet, "hosts"))
    for key, app in manifest.everything().items():
        for g in app.generate:
            assert f"{key}/{g}" in names
        assert f"{key}/DB_PASSWORD" not in names                          # on AgentPG every instance has its own key, shown once
        if key == "identity" or app.deploy:
            assert f"{key}/DB_PASSWORD" in hosts_names
    assert "identity/IDENTITY_API_KEY" in names and f"{manifest.llm.gateway}/{llm_admin_name(manifest)}" in names


def test_app_patch_never_resets_a_real_redirect():
    spec = {"client_id": "a", "kind": "web", "name": "A", "redirect_uris": [eco.PENDING_REDIRECT], "post_logout_uris": [], "url": ""}
    existing = {"client_id": "a", "kind": "web", "name": "A", "redirect_uris": ["https://a.example/auth/callback"], "post_logout_uris": ["https://a.example/"], "url": "https://a.example"}
    assert eco.eco_app_patch(existing, spec) == {}
    assert eco.eco_app_patch({**existing, "name": "Old"}, spec) == {"name": "A"}


def _render(template: str, **ctx) -> dict[str, str]:
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ANSIBLE / "roles/app/templates")), trim_blocks=True, keep_trailing_newline=True)    # Ansible's defaults
    out = env.get_template(template).render(**ctx)
    return dict(line.split("=", 1) for line in out.splitlines() if line)


@pytest.mark.parametrize("target", ["agentpg", "hosts"])
def test_env_template_reproduces_app_env_for_every_app(manifest, fleet, tmp_path, target):
    """The names (and, with the same inputs, the values) the template renders are the ones ecoctl's app_env builds: exactly, for the AgentPG
    target; plus only the four names AgentPG injects on its platform, for the hosts target."""
    st = State(tmp_path / "eco")
    gw = manifest.llm.gateway
    urls = {k: f"https://{k}.eco.example.com" for k in ("identity", *manifest.apps)}
    for k in urls:
        st.app(k)["url"] = urls[k]
    for key in manifest.apps:
        e = st.app(key)
        e["client_secret"], e["service_secret"], e["llm_key"] = f"cs-{key}", f"ss-{key}", f"lg-{key}"
    ctx = Context(manifest, st, None)
    secrets = {"identity/IDENTITY_API_KEY": st.admin_key(), f"{gw}/{llm_admin_name(manifest)}": st.secret(gw, llm_admin_name(manifest))}
    for key, app in manifest.everything().items():
        secrets.update({f"{key}/{g}": st.secret(key, g) for g in app.generate})
    issued = {f"{k}/{kind}": st.app(k)[kind] for k in manifest.apps for kind in ("client_secret", "service_secret", "llm_key")}
    for key, app in manifest.everything().items():
        expected, _ = app_env(ctx, app)
        got = _render("app.env.j2", eco_item=eco.eco_get(fleet, key), eco_fleet=fleet, eco_urls=urls, eco_secrets=secrets, eco_issued=issued,
                      eco_database_url="postgresql://x", eco_ca_path="", eco_identity_mode="managed", eco_oidc_clients={}, eco_llm_mode="managed",
                      eco_llm_api_key="", eco_target=target)
        assert set(got) - ANSIBLE_ONLY == set(expected), key
        assert {k: v for k, v in got.items() if k not in ANSIBLE_ONLY} == expected, key
        if target == "agentpg":
            assert got == expected, key                                    # nothing added: AgentPG injects DATABASE_URL itself
        else:
            assert got["DATABASE_URL"] == "postgresql://x"
            assert ("IDENTITY_PUBLIC_URL" in got) == (key == "identity")


def test_env_template_follows_the_swappable_parts(manifest, fleet):
    """oidc mode takes client ids and secrets from variables; openai mode takes the model endpoint and key from variables."""
    urls = {k: f"https://{k}.eco.example.com" for k in ("identity", *manifest.apps)}
    urls["identity"] = "https://login.corp.example/realm"             # eco_urls.identity is eco_identity_url in oidc mode
    secrets = {f"{k}/{g}": "s" for k, a in manifest.everything().items() for g in a.generate}
    got = _render("app.env.j2", eco_item=eco.eco_get(fleet, "trustdesk"), eco_fleet=fleet, eco_urls=urls, eco_secrets=secrets, eco_issued={},
                  eco_database_url="x", eco_ca_path="/ca.pem", eco_identity_mode="oidc", eco_target="hosts",
                  eco_oidc_clients={"trustdesk": {"client_id": "td", "client_secret": "from-vault"}}, eco_llm_mode="openai",
                  eco_llm_api_key="sk-vault", eco_llm_base_url="https://llm.corp.example/v1")
    assert got["TRUSTDESK_OIDC_ISSUER"] == "https://login.corp.example/realm" and got["TRUSTDESK_OIDC_CLIENT_ID"] == "td"
    assert got["TRUSTDESK_OIDC_CLIENT_SECRET"] == "from-vault" and got["LLM_API_KEY"] == "sk-vault" and got["LLM_BASE_URL"] == "https://llm.corp.example/v1"
    assert got["SSL_CERT_FILE"] == "/etc/eco/ca.crt"


def test_every_yaml_file_parses_and_the_stages_are_tagged():
    files = [p for p in ANSIBLE.rglob("*.yml") if not {".secrets", ".venv"} & set(p.parts)] + list(ANSIBLE.rglob("*.yml.example"))
    assert files
    for p in files:
        yaml.safe_load(p.read_text())
    plays = yaml.safe_load((ANSIBLE / "site.yml").read_text())
    infra = yaml.safe_load((ANSIBLE / "infra_hosts.yml").read_text())
    tags = {t for play in plays + infra for t in play.get("tags", [])}
    assert {"db", "proxy", "identity", "gateway", "apps", "connect", "verify", "hosts", "access"} <= tags
    assert (ANSIBLE / "verify.yml").exists() and (ANSIBLE / "requirements.yml").exists()


SECRETISH = re.compile(r"(?i)(secret|password|passwd|token|api_?key|encryption_?key)\w*[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-+/=]{16,})[\"']?\s*$")


def test_no_secret_literal_is_committed():
    ignore = (ANSIBLE / ".gitignore").read_text()
    assert ".secrets/" in ignore and "vault" in ignore
    assert not list(ANSIBLE.glob("inventories/*/group_vars/**/vault.yml")), "a plain vault.yml must not sit in the tree; only vault.yml.example is shipped"
    for p in ANSIBLE.rglob("*"):
        if not p.is_file() or {".secrets", ".venv", "__pycache__"} & set(p.parts) or p.suffix in {".pyc"}:
            continue
        text = p.read_text(errors="ignore")
        assert "BEGIN PRIVATE KEY" not in text and "BEGIN RSA PRIVATE KEY" not in text, p
        for line in text.splitlines():
            m = SECRETISH.search(line)
            assert not m or "{{" in line or "lookup(" in line or m.group(2).replace("_", "").isalpha(), f"{p}: looks like a committed secret: {line.strip()[:60]}"


# ---- eco_target: agentpg -------------------------------------------------------------------------------------------------------
def test_dependents_and_recreate_set_match_ecoctl(manifest, fleet):
    for keys in (["llmgw"], ["paybroker"], ["trustdesk"], ["pagerlens", "citypulse"]):
        assert eco.eco_dependents(fleet, keys) == sorted(manifest.dependents(keys)), keys
        assert set(eco.eco_recreate_set(fleet, keys)) == (set(keys) | manifest.dependents(keys)) & {k for k, a in manifest.apps.items() if a.deploy}, keys
    assert set(eco.eco_recreate_set(fleet, ["identity"])) == {"identity", *(k for k, a in manifest.apps.items() if a.deploy)}
    assert eco.eco_recreate_set(fleet, []) == []


def test_sandbox_and_instance_names_and_settings_match_ecoctl(manifest, fleet):
    for key, app in manifest.everything().items():
        mine = eco.eco_get(fleet, key)
        assert (mine["sandbox_name"], mine["instance_name"]) == (manifest.sandbox_name(key), manifest.instance_name(key)), key
        assert (mine["pause"], mine["plan_eff"]) == (manifest.pause_enabled(app), manifest.plan_for(app)), key
    assert fleet["agentpg"]["size"] == manifest.size


def test_module_command_shapes_equal_ecoctls():
    assert ap.create_instance_args("eco-x-db", "small") == list(AgentPG.create_instance("eco-x-db", "small").args)
    assert ap.extension_args("inst-1", "vector") == list(AgentPG.extension("inst-1", "vector").args)
    assert ap.delete_sandbox_args("sbx-1") == list(AgentPG.delete_sandbox("sbx-1").args)
    env = {"B": "2", "A": "1", "TOKEN": "s3cret"}
    for pause, plan in ((True, None), (False, None), (False, "medium"), (True, "large")):
        assert ap.create_sandbox_args("eco-x", "ghcr.io/o/x:latest", "inst-1", 8000, env, auto_pause=pause, plan=plan) == \
            list(AgentPG.create_sandbox("eco-x", "ghcr.io/o/x:latest", "inst-1", 8000, env, {"TOKEN"}, auto_pause=pause, plan=plan).args)
    assert ap.env_hash(env) == _env_hash(env)


def _cli(fake):
    return ap.Cli(run=fake, interval=0, sleep=lambda s: None)


def _deploy(cli, st, env, **kw):
    inst = ap.ensure_instance(cli, st, "eco-x-db", "small")
    ap.ensure_extensions(cli, st, inst["instance_id"], ["vector", "pg_trgm"])
    return inst, ap.ensure_sandbox(cli, st, "eco-x", "ghcr.io/o/x:latest", inst["instance_id"], 8000, env, **kw)


def test_modules_create_then_converge_and_return_the_database_key_once(tmp_path):
    fake = FakeAgentPG()
    fake.url_with_id = True
    fake.creating_polls = 3                                   # the wait loops really loop
    sleeps = []
    cli = ap.Cli(run=fake, interval=1, sleep=sleeps.append)
    st = ap.State(str(tmp_path / "s" / "x.json"))
    inst, sbx = _deploy(cli, st, {"A": "1"})
    assert inst["created"] and sbx["created"] and sbx["changed"] and sbx["url"] == "https://eco-x-sbx-1.sandbox.example" and sleeps
    assert "a1p_sk" not in str(sbx) and inst["new_db_key"].startswith("a1p_sk_")          # returned once, for the playbook to keep encrypted
    assert (tmp_path / "s" / "x.json").stat().st_mode & 0o777 == 0o600 and (tmp_path / "s").stat().st_mode & 0o777 == 0o700
    assert "db_key" not in ap.State(st.path).data and "a1p_sk" not in (tmp_path / "s" / "x.json").read_text()      # never in the per-app file
    assert ap.State(st.path).data["extensions"] == ["vector", "pg_trgm"]
    n = len(fake.calls)
    fake.calls.clear()
    st2 = ap.State(st.path)                                   # a new run reads the state again
    inst2, sbx2 = _deploy(cli, st2, {"A": "1"})
    assert not inst2["changed"] and not sbx2["changed"] and not sbx2["env_differs"] and inst2["new_db_key"] == ""
    assert not [c for c in fake.calls if c[1] in {"create", "extensions"} or c[1:3] in (["sandbox", "create"], ["sandbox", "delete"])], (n, fake.calls)


def test_changed_environment_is_reported_then_applied_by_recreating(tmp_path):
    fake = FakeAgentPG()
    fake.url_with_id = True
    st = ap.State(str(tmp_path / "x.json"))
    _, first = _deploy(_cli(fake), st, {"A": "1"})
    _, report = _deploy(_cli(fake), ap.State(st.path), {"A": "2"})
    assert report["env_differs"] and not report["changed"] and report["sandbox_id"] == first["sandbox_id"]
    assert not fake.commands("sandbox", "delete")
    _, applied = _deploy(_cli(fake), ap.State(st.path), {"A": "2"}, on_change="recreate")
    assert applied["recreated"] and applied["changed"] and applied["sandbox_id"] != first["sandbox_id"] and applied["url"] != first["url"]     # the id is in the URL
    assert len(fake.instances) == 1 and fake.extensions == [("inst-1", "vector"), ("inst-1", "pg_trgm")]          # the database and its extensions are kept
    _, forced = _deploy(_cli(fake), ap.State(st.path), {"A": "2"}, recreate=True)
    assert forced["recreated"] and forced["sandbox_id"] != applied["sandbox_id"]


def test_a_sandbox_deleted_behind_our_back_is_created_again(tmp_path):
    fake = FakeAgentPG()
    st = ap.State(str(tmp_path / "x.json"))
    _, first = _deploy(_cli(fake), st, {"A": "1"})
    fake.sandboxes.clear()
    _, again = _deploy(_cli(fake), ap.State(st.path), {"A": "1"})
    assert again["created"] and again["sandbox_id"] != first["sandbox_id"]


def test_an_existing_sandbox_is_found_not_duplicated(tmp_path):
    fake = FakeAgentPG()
    fake("agentpg create --name eco-x-db --size small --json".split())
    fake("agentpg sandbox create eco-x --image i --instance inst-1 --port 8000".split())
    inst, sbx = _deploy(_cli(fake), ap.State(str(tmp_path / "x.json")), {"A": "1"})
    assert not inst["created"] and not sbx["created"] and not sbx["changed"] and len(fake.sandboxes) == 1


def test_check_mode_only_reads(tmp_path):
    fake = FakeAgentPG()
    st = ap.State(str(tmp_path / "x.json"))
    cli = _cli(fake)
    inst = ap.ensure_instance(cli, st, "eco-x-db", "small", check=True)
    ext = ap.ensure_extensions(cli, st, inst["instance_id"], ["vector"], check=True)
    sbx = ap.ensure_sandbox(cli, st, "eco-x", "i", inst["instance_id"], 8000, {"A": "1"}, check=True)
    assert inst["changed"] and ext["changed"] and ext["created"] == ["vector"] and sbx["changed"] and "would create" in sbx["msg"]
    assert {tuple(c[1:3]) for c in fake.calls} <= {("instances", "list"), ("sandbox", "list")}, fake.calls         # nothing was created or written
    assert not (tmp_path / "x.json").exists()
    _deploy(cli, st, {"A": "1"})
    fake.calls.clear()
    st = ap.State(st.path)
    inst, sbx = ap.ensure_instance(cli, st, "eco-x-db", "small", check=True), ap.ensure_sandbox(cli, st, "eco-x", "i", "inst-1", 8000, {"A": "2"}, check=True, recreate=True)
    assert not inst["changed"] and sbx["changed"] and sbx["recreated"] and not fake.commands("sandbox", "delete")


def test_a_failed_sandbox_and_an_existing_extension_are_handled(tmp_path):
    fake = FakeAgentPG()
    st = ap.State(str(tmp_path / "x.json"))
    inst = ap.ensure_instance(_cli(fake), st, "eco-x-db", "small")
    real = fake.__call__

    def already(args):
        if args[1:3] == ["extensions", "create"]:
            raise ap.AgentPGError("extension already exists")
        return real(args)
    assert ap.ensure_extensions(ap.Cli(run=already, interval=0, sleep=lambda s: None), st, inst["instance_id"], ["vector"])["created"] == ["vector"]
    assert ap.State(st.path).data["extensions"] == ["vector"]
    with pytest.raises(ap.AgentPGError, match="still 'creating'"):
        clock = iter(range(0, 10_000, 100))
        fake.creating_polls = 10**6
        ap.ensure_sandbox(ap.Cli(run=fake, interval=0, timeout=300, sleep=lambda s: None, clock=lambda: next(clock)), st, "eco-x", "i", inst["instance_id"], 8000, {})


def test_the_module_files_document_the_options_they_accept():
    for name in ("agentpg_instance", "agentpg_extension", "agentpg_sandbox"):
        text = (ANSIBLE / "library" / f"{name}.py").read_text()
        compile(text, name, "exec")
        doc = yaml.safe_load(text.split('DOCUMENTATION = r"""')[1].split('"""')[0])
        assert doc["module"] == name
        spec = set(re.findall(r"(\w+)=dict\(", text)) - {"argument_spec"}
        assert set(doc["options"]) == spec, name
    assert "no_log=True" in (ANSIBLE / "library/agentpg_sandbox.py").read_text()


# ---- the AgentPG target needs nothing beyond ansible-core ------------------------------------------------------------------------
HOSTS_ONLY = ("infra_hosts.yml", "requirements.yml", "roles/postgres_db/", "roles/container_host/", "roles/proxy/", "inventories/own-infra", "roles/app/tasks/container.yml")
NON_CORE = re.compile(r"community\.|ansible\.posix|ansible\.utils|ansible\.netcommon|ansible\.windows|json_query|ipaddr|module_defaults|\bcollections:")


def _agentpg_path_files():
    for p in sorted(ANSIBLE.rglob("*")):
        rel = p.relative_to(ANSIBLE).as_posix()
        if p.is_file() and not any(rel.startswith(h) for h in HOSTS_ONLY) and not {".secrets", ".venv", "__pycache__"} & set(p.parts) and p.suffix not in {".pyc"}:
            yield rel, p


def test_the_agentpg_path_needs_only_ansible_core():
    """A venv with ansible-core alone (no collections) must load site.yml: module_defaults of a collection group, or any collection module, filter
    or lookup, outside the hosts-target files would fail at load time (it did once)."""
    seen = 0
    for rel, p in _agentpg_path_files():
        if rel in {".gitignore", ".ansible-lint"}:
            continue
        text = p.read_text(errors="ignore")
        seen += 1
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            assert not NON_CORE.search(line), f"{rel}:{n} needs more than ansible-core: {line.strip()[:80]}"
            m = re.match(r"^\s*(?:- )?([a-z_]+\.[a-z_]+\.[a-z_]+):", line)                  # a fully qualified module name used as a task key
            assert not m or m.group(1).startswith("ansible.builtin.") or m.group(1) == "ansible.builtin", f"{rel}:{n}: {m.group(1)}"
    assert seen > 20
    plays = yaml.safe_load((ANSIBLE / "site.yml").read_text()) + yaml.safe_load((ANSIBLE / "verify.yml").read_text())
    assert not [p for p in plays if "module_defaults" in p or "collections" in p]
    # the hosts target is where the collection belongs, and requirements.yml says so
    assert "community.postgresql" in (ANSIBLE / "requirements.yml").read_text()
    assert "hosts" in (ANSIBLE / "requirements.yml").read_text().lower()


# ---- teardown (destroy.yml) ----------------------------------------------------------------------------------------------------
class FakeWithDelete(FakeAgentPG):
    """ecoctl's fake CLI plus `instances delete`, which (like the platform, assumed) refuses an instance that still has sandboxes."""

    def __call__(self, args):
        if args[1:3] == ["instances", "delete"]:
            self.calls.append(args)
            name = next((n for n, i in self.instances.items() if i == args[3]), None)
            if name is None:
                raise ap.AgentPGError("instance not found")
            if any(s["instance"] == args[3] for s in self.sandboxes.values()):
                raise ap.AgentPGError("instance still has sandboxes")
            del self.instances[name]
            return "deleted\n"
        return super().__call__(args)


def _fleet_on(fake, tmp_path, fleet_name="ecoans", keys=("identity", "llmgw", "trustdesk")):
    plan = eco.eco_destroy_plan(eco.eco_fleet({**yaml.safe_load(MANIFEST.read_text()), "fleet": fleet_name}), list(keys))      # reverse order: build in reverse of it
    states = {}
    for item in reversed(plan):
        st = ap.State(str(tmp_path / "agentpg" / f"{item['key']}.json"))
        inst = ap.ensure_instance(_cli(fake), st, item["instance"], "small")
        ap.ensure_extensions(_cli(fake), st, inst["instance_id"], ["vector"])
        ap.ensure_sandbox(_cli(fake), st, item["sandbox"], "img", inst["instance_id"], 8000, {"A": "1"})
        states[item["key"]] = st
    return plan, states


def _destroy(fake, plan, tmp_path, check=False):
    names_s, names_i = [p["sandbox"] for p in plan], [p["instance"] for p in plan]
    out = []
    for p in plan:
        out.append(ap.ensure_sandbox_absent(_cli(fake), ap.State(str(tmp_path / "agentpg" / f"{p['key']}.json")), p["sandbox"], names_s, check))
    for p in plan:
        out.append(ap.ensure_instance_absent(_cli(fake), ap.State(str(tmp_path / "agentpg" / f"{p['key']}.json")), p["instance"], names_i, check))
    return out


def test_destroy_plan_uses_exact_names_in_reverse_creation_order(manifest):
    fleet = eco.eco_fleet({**yaml.safe_load(MANIFEST.read_text()), "fleet": "ecoans"})
    run = ["identity", *fleet["deployed"]]
    plan = eco.eco_destroy_plan(fleet, run)
    assert [p["key"] for p in plan] == list(reversed(run)) and plan[-1]["key"] == "identity"
    assert all(p["sandbox"] == f"ecoans-{p['key']}" and p["instance"] == f"ecoans-{p['key']}-db" for p in plan)
    assert [p["key"] for p in eco.eco_destroy_plan(fleet, run, ["trustdesk", "nosuch"])] == ["trustdesk"]          # only narrows, never widens
    assert "pagerlens-agent" not in {p["key"] for p in plan}                                                      # deploy: false is never created, never deleted


def test_destroy_empties_the_fleet_and_touches_nothing_else(tmp_path):
    fake = FakeWithDelete()
    fake("agentpg create --name eco-trustdesk-db --size small --json".split())                   # someone else's fleet 'eco', same app names
    fake("agentpg sandbox create eco-trustdesk --image foreign --instance inst-1 --port 8000".split())
    fake("agentpg create --name ecoans-trustdesk-db-old --size small --json".split())            # a similar name that is not ours: never matched by prefix
    plan, _ = _fleet_on(fake, tmp_path)
    before = set(fake.instances), set(fake.sandboxes)
    out = _destroy(fake, plan, tmp_path)
    assert [o["changed"] for o in out] == [True] * len(out)
    assert set(fake.instances) == {"eco-trustdesk-db", "ecoans-trustdesk-db-old"} and set(fake.sandboxes) == {"eco-trustdesk"}, before
    deletes = fake.commands("sandbox", "delete") + fake.commands("instances", "delete")
    assert len(deletes) == 2 * len(plan)
    order = [c[1] for c in fake.calls if c[2] == "delete"]
    assert order == ["sandbox"] * len(plan) + ["instances"] * len(plan)                       # sandboxes first, then instances
    assert not list((tmp_path / "agentpg").glob("*.json"))                                     # no state describing a deleted instance is left
    assert "a1p_sk" not in str(out)                                                           # no database key in any result


def test_a_second_destroy_changes_nothing(tmp_path):
    fake = FakeWithDelete()
    plan, _ = _fleet_on(fake, tmp_path)
    _destroy(fake, plan, tmp_path)
    fake.calls.clear()
    out = _destroy(fake, plan, tmp_path)
    assert not any(o["changed"] for o in out) and all("already gone" in o["msg"] for o in out)
    assert not [c for c in fake.calls if c[2:3] == ["delete"]]


def test_destroy_in_check_mode_deletes_nothing(tmp_path):
    fake = FakeWithDelete()
    plan, _ = _fleet_on(fake, tmp_path)
    n_inst, n_sbx = len(fake.instances), len(fake.sandboxes)
    out = _destroy(fake, plan, tmp_path, check=True)
    assert all(o["changed"] and o["msg"].startswith("would delete") for o in out)
    assert (len(fake.instances), len(fake.sandboxes)) == (n_inst, n_sbx) and not [c for c in fake.calls if c[2:3] == ["delete"]]
    assert len(list((tmp_path / "agentpg").glob("*.json"))) == len(plan)


def test_destroy_refuses_a_name_that_is_not_in_the_plan_and_ids_come_from_the_listing(tmp_path):
    fake = FakeWithDelete()
    fake("agentpg create --name eco-trustdesk-db --size small --json".split())
    st = ap.State(str(tmp_path / "x.json"))
    with pytest.raises(ap.AgentPGError, match="not one of this fleet's objects"):
        ap.ensure_instance_absent(_cli(fake), st, "eco-trustdesk-db", ["ecoans-trustdesk-db"])
    with pytest.raises(ap.AgentPGError, match="not one of this fleet's objects"):
        ap.ensure_sandbox_absent(_cli(fake), st, "eco-trustdesk", ["ecoans-trustdesk"])
    assert "eco-trustdesk-db" in fake.instances
    ap.State(str(tmp_path / "y.json"))
    plan, _ = _fleet_on(fake, tmp_path)
    p = ap.State(str(tmp_path / "agentpg" / "trustdesk.json"))
    p.data["instance_id"], p.data["sandbox_id"] = "inst-999", "sbx-999"                         # a stale state file: its ids must not be used
    p.save()
    out = ap.ensure_sandbox_absent(_cli(fake), p, "ecoans-trustdesk", ["ecoans-trustdesk"])
    assert out["changed"] and "sbx-999" in out["msg"] and "AgentPG's id is used" in out["msg"]
    assert ["sandbox", "delete", out["sandbox_id"], "--force"] == fake.commands("sandbox", "delete")[-1][1:]
    assert ap.delete_instance_args("inst-1") == ["instances", "delete", "inst-1", "--force"]


def test_destroy_playbook_guards_before_it_touches_anything():
    play = yaml.safe_load((ANSIBLE / "destroy.yml").read_text())[0]
    tasks = play["tasks"]
    names = [t["name"] for t in tasks]
    guard = next(i for i, t in enumerate(tasks) if "ansible.builtin.assert" in t)
    assert guard == 1 and "include_role" in " ".join(tasks[0])                                  # the manifest is read, then the guard, then everything else
    cond = " ".join(tasks[guard]["ansible.builtin.assert"]["that"])
    assert "eco_destroy_confirm" in cond and "eco_fleet.fleet" in cond and "agentpg" in cond
    assert "eco_destroy_confirm" in tasks[guard]["ansible.builtin.assert"]["fail_msg"]
    for t in tasks[guard + 1:]:
        for mod in ("agentpg_sandbox", "agentpg_instance"):
            if mod in t:
                assert t[mod]["state"] == "absent" and "allowed_names" in t[mod], t["name"]       # only deletions, each with its allow-list
    text = (ANSIBLE / "destroy.yml").read_text()
    assert "eco_destroy_secrets" in text and "basename == eco_fleet.fleet" in text and names
    assert "common" not in " ".join(str(r) for r in play.get("roles", []))                    # the `common` role would generate secrets for a fleet being dropped
    assert not yaml.safe_load((ANSIBLE / "roles/common/defaults/main.yml").read_text())["eco_destroy_confirm"]      # no default confirmation


def test_live_merchant_keys_are_not_mistaken_for_revoked():
    # PayBroker lists `revoked_at: null` for a live key; testing "defined" dropped every key and a second run created two more (seen live, 2026-10-28)
    keys = [{"id": 1, "revoked_at": None}, {"id": 2}, {"id": 3, "revoked_at": "2026-10-28T10:00:00Z"}]
    assert [k["id"] for k in eco.eco_active_keys(keys)] == [1, 2]
    assert eco.eco_active_keys(None) == []


def test_ensure_app_compares_against_the_full_record_not_the_list_summary():
    # the identity list has no scopes and counts the roles, so comparing with it patched every client on every run
    text = (ANSIBLE / "roles/common/tasks/ensure_app.yml").read_text()
    assert "eco_existing: \"{{ (eco_full.json | default({})) }}\"" in text
    assert "/api/apps/{{ eco_spec.client_id }}\"" in text and "register: eco_full" in text
    assert "defined" not in (ANSIBLE / "roles/connect/tasks/key.yml").read_text().split("eco_listed:")[1].splitlines()[0]


def test_stale_key_cleanup_never_deletes_the_key_we_keep():
    # PayBroker ids are numbers, the kept id is text from a file: comparing them directly deleted the live keys of ShopFront (seen live, 2026-10-28)
    active = [{"id": 3, "name": "ansible:shopfront:secret"}, {"id": 1, "name": "ansible:shopfront:secret"}, {"id": 9, "name": "ecoctl:shopfront:secret"}]
    assert [k["id"] for k in eco.eco_stale_keys(active, "ansible:shopfront:secret", "3")] == [1]
    assert eco.eco_stale_keys(active, "ansible:shopfront:secret", 3) == [active[1]]
    assert eco.eco_stale_keys(None, "x", "1") == []


def test_signin_redirect_is_compared_with_the_full_record():
    # the identity list has no redirect addresses, so every run patched every app's sign-in (7 changes on a run that changed nothing)
    text = (ANSIBLE / "roles/app/tasks/one_app.yml").read_text()
    assert "register: eco_signin_now" in text and "eco_signin_now.json.redirect_uris" in text
    assert "eco_registered.json | selectattr('client_id', 'equalto', eco_item.key) | map(attribute='redirect_uris')" not in text


# ---- vault: password (identity, the LLM gateway) -------------------------------------------------------------------------------
def _tasks(node):
    """Every task dict of a parsed playbook or task file, blocks flattened."""
    if isinstance(node, list):
        for item in node:
            yield from _tasks(item)
    elif isinstance(node, dict):
        if any(k in node for k in ("block", "rescue", "always")):
            for k in ("block", "rescue", "always"):
                yield from _tasks(node.get(k) or [])
        else:
            yield node
        for k in ("tasks", "pre_tasks", "post_tasks"):
            yield from _tasks(node.get(k) or [])


def test_the_filter_knows_the_vault_option_like_ecoctl(manifest, fleet):
    for key, app in manifest.everything().items():
        assert eco.eco_get(fleet, key)["vault"] == app.vault and eco.eco_get(fleet, key)["encryption_key_name"] == app.encryption_key_name
    assert fleet["vault_apps"] == [a.key for a in manifest.password_vault_apps()] == ["identity", "llmgw"]


def test_a_manifest_without_the_option_is_all_platform_in_the_recipe_too():
    doc = yaml.safe_load(MANIFEST.read_text())
    del doc["identity"]["vault"], doc["apps"]["llmgw"]["vault"]
    doc["identity"]["generate"] = ["IDENTITY_ENCRYPTION_KEY"]
    doc["apps"]["llmgw"]["generate"] = ["LLMGW_ENCRYPTION_KEY"]
    f = eco.eco_fleet(doc)
    assert f["vault_apps"] == [] and "identity/IDENTITY_ENCRYPTION_KEY" in eco.eco_secret_names(f) and "llmgw/LLMGW_ENCRYPTION_KEY" in eco.eco_secret_names(f)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["apps"]["trustdesk"].update(vault="password"), "not supported for trustdesk"),
    (lambda d: d["identity"].update(vault="kms"), "must be one of platform, password"),
    (lambda d: d["identity"].update(generate=["IDENTITY_ENCRYPTION_KEY"]), "must not be listed under `generate:`"),
    (lambda d: d["apps"]["pagerlens-agent"].update(vault="password"), "not supported for pagerlens-agent"),
])
def test_the_recipe_refuses_the_same_vault_settings_as_ecoctl(mutate, message):
    from ecoctl.manifest import ManifestError, parse
    doc = yaml.safe_load(MANIFEST.read_text())
    mutate(doc)
    with pytest.raises(ManifestError, match=message):
        parse(doc)
    with pytest.raises(ValueError, match=message):
        eco.eco_fleet(doc)


def test_no_encryption_key_for_identity_or_the_gateway_anywhere_in_the_recipes_secrets_or_env(manifest, fleet, tmp_path):
    names = eco.eco_secret_names(fleet)
    assert not [n for n in names if n.startswith(("identity/", "llmgw/")) and n.endswith("ENCRYPTION_KEY")]
    assert "trustdesk/TRUSTDESK_ENCRYPTION_KEY" in names and "llmgw/LLMGW_API_KEY" in names          # the others keep theirs; the operator key stays
    jinja2 = pytest.importorskip("jinja2")
    secrets_ = {n: "x" * 43 for n in names}
    for key in ("identity", "llmgw"):
        item = eco.eco_get(fleet, key)
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ANSIBLE / "roles/app/templates")), undefined=jinja2.StrictUndefined)
        env.filters.update({"eco_get": eco.eco_get})
        text = env.get_template("app.env.j2").render(eco_item=item, eco_fleet=fleet, eco_secrets=secrets_, eco_issued={}, eco_urls={"identity": "https://i", "llmgw": "https://g"},
                                                     eco_oidc_clients={}, eco_identity_mode="managed", eco_llm_mode="managed", eco_llm_base_url="", eco_llm_api_key="", eco_target="agentpg")
        assert "ENCRYPTION_KEY" not in text


@pytest.mark.parametrize("mode, state, label", [
    ("password", {"initialized": True, "unlocked": True}, "unlocked"), ("password", {"initialized": True, "unlocked": False}, "LOCKED"),
    ("password", {"initialized": False, "unlocked": False}, "not initialised"), ("password", None, "?"),
    ("platform", {"initialized": True, "unlocked": True, "auto_unlock": True}, "auto"), ("platform", {"initialized": True, "unlocked": False}, "LOCKED"),
    ("platform", {"initialized": False, "unlocked": False}, "empty"), ("platform", None, "?"),
])
def test_vault_labels_match_ecoctls(mode, state, label):
    from ecoctl.vault import describe, parse_state
    assert eco.eco_vault_label(mode, state) == label == describe(mode, parse_state({"vault": state} if state else {}))[0]


def test_locked_password_apps_are_found_from_the_probes(fleet):
    ok = {"item": "identity", "status": 200, "json": {"vault": {"initialized": True, "unlocked": True}}}
    shut = {"item": "llmgw", "status": 200, "json": {"vault": {"initialized": True, "unlocked": False}}}
    assert eco.eco_vault_failures(fleet, [ok, shut]) == ["llmgw"]
    assert eco.eco_vault_failures(fleet, [ok, {"item": "llmgw", "status": 503}]) == ["llmgw"]            # unreachable is not open
    assert eco.eco_vault_failures(fleet, [ok, {**shut, "json": {"vault": {"initialized": True, "unlocked": True}}}]) == []


def test_a_key_shown_once_survives_a_failure_after_creation():
    fake = FakeAgentPG()
    lists = []

    def run(args):
        if args[1:3] == ["instances", "list"]:
            lists.append(1)
            if len(lists) > 1:                                              # right after `create`
                raise ap.AgentPGError("the platform did not answer")
        return fake(args)

    with pytest.raises(ap.AgentPGError) as exc:
        ap.ensure_instance(ap.Cli(run=run, interval=0, sleep=lambda s: None), _MemState(), "eco-x-db", "small")
    assert exc.value.new_db_key.startswith("a1p_sk_")                      # the module hands it to the playbook even though it failed


class _MemState(ap.State):
    def __init__(self):
        self.path, self.data = "/dev/null", {}

    def save(self):
        pass


# ---- the encrypted state (module_utils/eco_state.py, plugins/action/eco_state.py) ---------------------------------------------------------------------
es = _load("eco_state", "module_utils/eco_state.py")
PW = "recipe-test-passphrase-long-enough-0"


def test_the_state_is_one_encrypted_blob_and_nothing_else_is_written(tmp_path):
    from apgkit import sealed
    path = tmp_path / "s" / ".secrets" / "state.sealed"
    st = es.EncState(str(path), PW)
    st.generate(["identity/IDENTITY_API_KEY", "trustdesk/TRUSTDESK_SESSION_SECRET"])
    st.put_issued("trustdesk/client_secret", "cs-very-secret-value")
    st.put_db_key("trustdesk", "a1p_sk_database_key")
    assert st.save() and not st.save()                                       # an unchanged state is not written again
    raw = path.read_text()
    assert raw.startswith("apgkit-seal1$") and "cs-very-secret" not in raw and "a1p_sk" not in raw
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700 and [p.name for p in path.parent.iterdir()] == ["state.sealed"]
    again = es.EncState(str(path), PW)
    assert again.issued(["trustdesk/client_secret"])["trustdesk/client_secret"] == "cs-very-secret-value" and again.db_key("trustdesk") == "a1p_sk_database_key"
    first = again.generate(["identity/IDENTITY_API_KEY"])["identity/IDENTITY_API_KEY"]
    assert len(first) == 43 and es.EncState(str(path), PW).generate(["identity/IDENTITY_API_KEY"])["identity/IDENTITY_API_KEY"] == first        # stable
    assert json.loads(sealed.open_blob(raw, PW)["state"])["issued"]                                                                            # the same blob format as ecoctl's state file
    with pytest.raises(es.EcoStateError, match="could not be decrypted"):
        es.EncState(str(path), "another-long-enough-passphrase-1")
    with pytest.raises(es.EcoStateError, match="at least"):
        es.passphrase("short")
    with pytest.raises(es.EcoStateError, match="ECOCTL_SEAL_PASSPHRASE"):
        es.passphrase(None, environ={})


def test_overrides_win_and_are_never_stored(tmp_path):
    st = es.EncState(str(tmp_path / "state.sealed"), PW)
    got = st.generate(["identity/IDENTITY_API_KEY"], {"identity/IDENTITY_API_KEY": "from-the-vault-file"})
    assert got["identity/IDENTITY_API_KEY"] == "from-the-vault-file" and "identity/IDENTITY_API_KEY" not in st.data["generated"]


def test_plaintext_files_of_an_older_recipe_are_moved_in_and_overwritten(tmp_path):
    d = tmp_path / ".secrets"
    (d / "generated/trustdesk").mkdir(parents=True)
    (d / "generated/trustdesk/TRUSTDESK_SESSION_SECRET").write_text("old-generated-secret-aaaaaaaaaaaaaaaa")
    (d / "issued/trustdesk").mkdir(parents=True)
    (d / "issued/trustdesk/client_secret").write_text("old-issued-secret\n")
    (d / "vault").mkdir()
    (d / "vault/admin_password").write_text("old-admin-password-xxxxxxxxxxxxxxxx")
    (d / "agentpg").mkdir()
    (d / "agentpg/trustdesk.json").write_text(json.dumps({"instance_id": "inst-1", "db_key": "a1p_sk_old", "url": "https://x"}))
    st = es.EncState(str(d / "state.sealed"), PW)
    moved = es.import_legacy(st, str(d))
    es.remove_empty_dirs(str(d))
    st.save()
    assert set(moved) == {"generated/trustdesk/TRUSTDESK_SESSION_SECRET", "issued/trustdesk/client_secret", "vault/admin_password", "agentpg/trustdesk/db_key"}
    again = es.EncState(str(d / "state.sealed"), PW)
    assert again.data["generated"]["trustdesk/TRUSTDESK_SESSION_SECRET"].startswith("old-generated") and again.data["issued"]["trustdesk/client_secret"] == "old-issued-secret"
    assert again.db_key("trustdesk") == "a1p_sk_old" and again.data["legacy"]["admin_password"].startswith("old-admin")
    assert sorted(p.name for p in d.iterdir()) == ["agentpg", "state.sealed"]                             # the plaintext files and their folders are gone
    assert json.loads((d / "agentpg/trustdesk.json").read_text()) == {"instance_id": "inst-1", "url": "https://x"}
    everything = "".join(p.read_text(errors="ignore") for p in d.rglob("*") if p.is_file())
    assert "old-" not in everything and "a1p_sk" not in everything


def test_the_sealed_blob_of_an_app_equals_ecoctls(manifest, tmp_path):
    from apgkit import sealed

    from ecoctl import seal as seal_mod
    describe = "connection_string: postgresql://admin:x@db.example.test:5432/appdb?sslmode=require"
    dsn = es.database_url(describe, "dbpass/with:odd chars")
    assert dsn == "postgresql://admin:dbpass%2Fwith%3Aodd%20chars@db.example.test:5432/appdb?sslmode=require"
    st = State(tmp_path / "eco")
    st.app("trustdesk").update(instance_id="inst-1", db_key="dbpass/with:odd chars")
    ctx = Context(manifest, st, AgentPG(run=lambda args: describe))
    assert seal_mod.database_url(ctx, manifest.apps["trustdesk"]) == dsn
    blob, notes = es.seal_env(PW, {"A_TOKEN": "t" * 12, "TRUSTDESK_ENCRYPTION_KEY": "k" * 12}, dsn, drop="TRUSTDESK_ENCRYPTION_KEY")
    assert sealed.open_blob(blob, PW) == {"A_TOKEN": "t" * 12, "DATABASE_URL": dsn} and notes == []
    assert es.database_url(describe, None) is None and es.database_url("no url here", "k") is None
    _, notes = es.seal_env(PW, {"A_TOKEN": "t" * 12}, None)
    assert "DATABASE_URL not sealed" in notes[0]
    assert es.seal_env(PW, {"A_TOKEN": "t"}, None, drop=None)[1] and es.seal_env(PW, {"DATABASE_URL": "postgresql://x/y"}, None)[1] == []         # a hosts-target URL is a secret in the blob


def test_the_environment_hash_matches_ecoctls(manifest):
    env = {"B": "2", "A": "1", "TOKEN": "s3cret"}
    assert eco.eco_env_hash(env) == _env_hash(env) == ap.env_hash(env)


def test_the_fleet_knows_seal_like_ecoctl(manifest, fleet):
    assert fleet["agentpg"]["seal"] is manifest.seal is True
    doc = yaml.safe_load(MANIFEST.read_text())
    doc.setdefault("agentpg", {})["seal"] = False
    assert eco.eco_fleet(doc)["agentpg"]["seal"] is False


def test_the_sealed_env_file_template_holds_the_blob_and_plain_settings_only():
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ANSIBLE / "roles/app/templates")), trim_blocks=True, keep_trailing_newline=True)
    out = env.get_template("app.sealed.env.j2").render(eco_settings_hash="abc123", eco_env_public={"B": "2", "APGKIT_SEALED": "apgkit-seal1$xyz", "A": "1"})
    assert out.splitlines() == ["# settings-hash: abc123", "A=1", "APGKIT_SEALED=apgkit-seal1$xyz", "B=2"]


def test_unseal_talks_the_kits_protocol_and_reports_a_wrong_passphrase():
    from apgkit import sealed as kit
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    blob = kit.seal({"DATABASE_URL": "postgresql://x/y"}, PW)
    ready = []
    s = kit.Sealed(blob)
    s.on_unsealed(lambda sealed, pw: ready.append(sealed.get("DATABASE_URL")))
    app = FastAPI()
    kit.install(app, s, trusted_hops=0, harden=False)
    client = TestClient(app)

    def http(method, url, body):
        r = client.request(method, url.replace("http://testserver", ""), json=body)
        return r.status_code, (r.json() if r.content else None)

    try:
        assert es.seal_state(http, "http://testserver") == "sealed"
        with pytest.raises(es.EcoStateError, match="did not open the blob") as exc:
            es.unseal(http, "http://testserver", "another-long-enough-passphrase-1")
        assert PW not in str(exc.value) and not ready
        assert es.unseal(http, "http://testserver", PW).startswith("unsealed (key fingerprint ")
        assert ready == ["postgresql://x/y"] and es.unseal(http, "http://testserver", PW) == "already open"
        assert es.unseal(lambda m, u, b: (404, None), "http://x", PW) == "sealing is not enabled in this app"
        clock = iter(range(0, 10_000, 100))
        with pytest.raises(es.EcoStateError, match="did not answer"):
            es.unseal(lambda m, u, b: (503, None), "http://x", PW, timeout=300, sleep=lambda s: None, clock=lambda: next(clock))
    finally:
        kit.set_active(None)


# ---- the playbooks: seal-first, nothing secret rendered or written -----------------------------------------------------------------------------------
def _all_task_files():
    return [*ANSIBLE.glob("*.yml"), *ANSIBLE.glob("roles/*/tasks/*.yml")]


SECRET_WORDS = re.compile(r"eco_secrets\b|eco_issued\b|eco_secret_env|eco_env_all|eco_sealed\.blob|eco_seal_passphrase|eco_issue_value|eco_inst\.new_db_key|eco_pw_|eco_dbkey")


def test_every_task_that_handles_a_secret_has_no_log():
    seen = 0
    for path in _all_task_files():
        for task in _tasks(yaml.safe_load(path.read_text())):
            body = {k: v for k, v in task.items() if k not in {"name", "when", "register", "changed_when", "failed_when", "until", "loop_control", "tags", "retries", "delay", "loop", "vars"}}
            if SECRET_WORDS.search(yaml.safe_dump(body)) and "ansible.builtin.fail" not in body and "ansible.builtin.debug" not in body and "ansible.builtin.assert" not in body:
                seen += 1
                assert task.get("no_log") is True, f"{path.relative_to(ANSIBLE)}: {task.get('name')} handles a secret without no_log: true"
    assert seen >= 25


def test_no_task_writes_a_secret_to_a_file_and_nothing_reads_the_old_plaintext_files():
    text = "\n".join(p.read_text() for p in [*_all_task_files(), *ANSIBLE.glob("roles/*/templates/*"), *ANSIBLE.glob("inventories/*/group_vars/**/*")] if p.is_file())
    assert "ansible.builtin.password" not in text and "/generated/" not in text and "/issued/" not in text and "admin_password" not in text.replace("legacy", "")
    assert "eco_secrets_dir ~ '/issued" not in text
    copies = [t for p in _all_task_files() for t in _tasks(yaml.safe_load(p.read_text())) if "ansible.builtin.copy" in t and "content" in t["ansible.builtin.copy"]]
    assert not copies, [t["name"] for t in copies]                                                          # no `copy: content:` of a value


def test_sandboxes_get_only_the_blob_and_plain_settings():
    agentpg = yaml.safe_load((ANSIBLE / "roles/app/tasks/agentpg.yml").read_text())
    sbx = next(t for t in agentpg if "agentpg_sandbox" in t)["agentpg_sandbox"]
    assert "secret_env" not in sbx and "APGKIT_SEALED" in sbx["env"] and "eco_env " in sbx["env"] and sbx["settings_hash"] == "{{ eco_settings_hash }}"
    split = (ANSIBLE / "roles/app/tasks/split_env.yml").read_text()
    assert "op: seal" in split and "eco_fleet.agentpg.seal" in split and "drop:" in split
    container = (ANSIBLE / "roles/app/tasks/container.yml").read_text()
    assert "app.sealed.env.j2" in container and "src: app.env.j2" not in container and "eco_envfile.checksum" not in container
    site = (ANSIBLE / "site.yml").read_text()
    assert "eco_fleet.agentpg.seal | bool" in site


def test_deploy_unseals_each_app_before_waiting_for_it_and_unseal_replaces_unlock():
    for f in ("agentpg.yml", "container.yml"):
        names = [t.get("name", "") for t in _tasks(yaml.safe_load((ANSIBLE / "roles/app/tasks" / f).read_text()))]
        assert next(i for i, n in enumerate(names) if "unseal it with" in n) < next(i for i, n in enumerate(names) if "wait until it answers" in n), f
    ident = (ANSIBLE / "roles/identity/tasks/main.yml").read_text()
    assert "vault" not in ident.split("Identity provider container and readiness")[1].split("The identity provider answers")[0]
    assert not (ANSIBLE / "roles/vault").exists()
    unseal = yaml.safe_load((ANSIBLE / "unseal.yml").read_text())
    assert "unseal" in unseal[0]["tags"] and any(t.get("ansible.builtin.include_role", {}).get("tasks_from") == "unseal_one.yml" for t in _tasks(unseal))
    assert yaml.safe_load((ANSIBLE / "unlock.yml").read_text())[0]["ansible.builtin.import_playbook"] == "unseal.yml"
    verify = (ANSIBLE / "roles/verify/tasks/main.yml").read_text()
    assert "/api/session" in verify and "eco_vault_failures" in verify and "run unseal.yml" in verify
    defaults = yaml.safe_load((ANSIBLE / "roles/common/defaults/main.yml").read_text())
    assert defaults["eco_state_file"].endswith("/state.sealed") and defaults["eco_seal_passphrase_prompt"] is False and "eco_admin_password" not in defaults
    assert "ECOCTL_SEAL_PASSPHRASE" in (ANSIBLE / "roles/common/defaults/main.yml").read_text() and ".secrets/" in (ANSIBLE / ".gitignore").read_text()
    cfg = (ANSIBLE / "ansible.cfg").read_text()
    assert "action_plugins = plugins/action" in cfg and "[connection]" in cfg.replace("\r", "") and "pipelining = True" in cfg.split("[connection]")[1].split("[")[0]


# ---- real Ansible, local stand-ins: the sealed apps are the kit's own unseal endpoints behind a real HTTP server ----------------------------------------
class _Services:
    """Two apps (identity, llmgw) that start SEALED, served by the kit's real /_seal endpoints on 127.0.0.1."""

    def __init__(self, passphrase=PW):

        from apgkit import sealed as kit
        self.kit, self.apps, self.servers, self.opened = kit, {}, {}, []
        self.passphrase = passphrase
        for name in ("identity", "llmgw"):
            self.start(name)

    def start(self, name):
        import threading

        import uvicorn
        from fastapi import FastAPI
        kit = self.kit
        sealed = kit.Sealed(kit.seal({"DATABASE_URL": f"postgresql://{name}/db"}, self.passphrase))
        sealed.on_unsealed(lambda s, pw, n=name: self.opened.append(n))
        app = FastAPI()
        kit.install(app, sealed, trusted_hops=0, harden=False)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        import time
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        self.apps[name], self.servers[name] = sealed, (server, t)

    def url(self, name):
        return f"http://127.0.0.1:{self.servers[name][0].servers[0].sockets[0].getsockname()[1]}"

    def restart(self):
        self.close()
        self.kit.set_active(None)
        self.opened.clear()
        for name in ("identity", "llmgw"):
            self.start(name)

    def close(self):
        for server, t in self.servers.values():
            server.should_exit = True
            t.join(5)
        self.kit.set_active(None)


@pytest.fixture
def services():
    s = _Services()
    yield s
    s.close()


def _ansible(services, tmp_path, playbook, *extra, verbose=False, env=None):
    import os
    import shutil
    import subprocess
    if not shutil.which("ansible-playbook"):
        pytest.skip("ansible-playbook is not installed")
    secrets_dir = tmp_path / "secrets"
    (secrets_dir / "agentpg").mkdir(parents=True, exist_ok=True)
    for name in ("identity", "llmgw"):
        (secrets_dir / "agentpg" / f"{name}.json").write_text(json.dumps({"url": services.url(name)}))
    evars = {"eco_secrets_dir": str(secrets_dir), "eco_manifest_path": str(MANIFEST), "eco_only": ["llmgw"],
             "eco_secret_overrides": {"identity/IDENTITY_API_KEY": "idk-test-operator-key", "llmgw/LLMGW_API_KEY": "gwk-test-operator-key"}}
    base = {k: v for k, v in os.environ.items() if k != "ECOCTL_SEAL_PASSPHRASE"} | {"ANSIBLE_LOCALHOST_WARNING": "False", "ANSIBLE_INVENTORY_UNPARSED_WARNING": "False"}
    p = subprocess.run(["ansible-playbook", "-i", "inventories/agentpg", playbook, "-e", json.dumps(evars), *extra, *(["-vvv"] if verbose else [])],
                       cwd=ANSIBLE, env={**base, **(env if env is not None else {"ECOCTL_SEAL_PASSPHRASE": PW})}, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr, secrets_dir


def _apgkit_available():
    import shutil
    import subprocess
    exe = shutil.which("ansible-playbook")
    if not exe:
        return False
    try:
        py = open(exe, encoding="utf-8", errors="ignore").readline().strip().removeprefix("#!").split()[0]
        return subprocess.run([py, "-c", "import apgkit.sealed"], capture_output=True, timeout=60).returncode == 0
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return False


needs_ansible_kit = pytest.mark.skipif(not _apgkit_available(), reason="ansible-playbook with agentpg-kit installed in its environment is needed")


def _changed(out: str) -> int:
    return int(re.search(r"localhost\s*:.*changed=(\d+)", out).group(1))


@needs_ansible_kit
def test_a_real_ansible_unseal_opens_every_app_and_a_rerun_changes_nothing(services, tmp_path):
    code, out, secrets_dir = _ansible(services, tmp_path, "unseal.yml", verbose=True)
    assert code == 0, out[-3000:]
    assert services.opened == ["identity", "llmgw"]                                                    # identity first
    assert all(a.unsealed for a in services.apps.values())
    assert PW not in out and "idk-test-operator-key" not in out and "gwk-test-operator-key" not in out    # nothing secret in the log, even at -vvv
    assert sorted(p.name for p in secrets_dir.iterdir()) == ["agentpg", "state.sealed"]                  # one encrypted file; no generated/, issued/ or vault/ folder
    raw = (secrets_dir / "state.sealed").read_text()
    assert raw.startswith("apgkit-seal1$") and "idk-test" not in raw
    code, out2, _ = _ansible(services, tmp_path, "unseal.yml")
    assert code == 0 and _changed(out2) == 0, out2[-2000:]                                              # idempotent: open apps change nothing
    services.restart()                                                                                    # a platform restart: sealed again
    assert not any(a.unsealed for a in services.apps.values())
    _ansible(services, tmp_path, "unseal.yml")
    code, out3, _ = _ansible(services, tmp_path, "unlock.yml")
    assert code == 0 and all(a.unsealed for a in services.apps.values()) and _changed(out3) == 0


@needs_ansible_kit
def test_a_real_ansible_run_with_the_wrong_passphrase_fails_and_opens_nothing(services, tmp_path):
    code, out, secrets_dir = _ansible(services, tmp_path, "unseal.yml")                                  # creates the state under PW
    assert code == 0
    services.restart()
    code, out, _ = _ansible(services, tmp_path, "unseal.yml", env={"ECOCTL_SEAL_PASSPHRASE": "another-long-enough-passphrase-1"})
    assert code != 0 and "could not be decrypted" in out and not any(a.unsealed for a in services.apps.values())
    assert "another-long-enough-passphrase-1" not in out and PW not in out
    code, out, _ = _ansible(services, tmp_path, "unseal.yml", env={})
    assert code != 0 and "ECOCTL_SEAL_PASSPHRASE" in out and not any(a.unsealed for a in services.apps.values())


@needs_ansible_kit
def test_a_real_ansible_run_moves_old_plaintext_secrets_into_the_state(services, tmp_path):
    d = tmp_path / "secrets"
    (d / "generated/trustdesk").mkdir(parents=True)
    (d / "generated/trustdesk/TRUSTDESK_SESSION_SECRET").write_text("old-generated-secret-aaaaaaaaaaaaaaaa")
    (d / "vault").mkdir()
    (d / "vault/admin_password").write_text("old-admin-password-xxxxxxxxxxxxxxxx")
    code, out, _ = _ansible(services, tmp_path, "unseal.yml", verbose=True)
    assert code == 0 and "moved into" in out and "old-generated" not in out and "old-admin" not in out, out[-2000:]
    assert sorted(p.name for p in d.iterdir()) == ["agentpg", "state.sealed"]


@needs_ansible_kit
def test_a_real_ansible_seal_op_builds_the_blob_from_the_kept_key_and_agentpg_describe(tmp_path):
    import os
    import subprocess

    stand_in = tmp_path / "agentpg"
    stand_in.write_text('#!/bin/sh\necho "connection_string: postgresql://admin:x@db.example.test:5432/appdb?sslmode=require"\n')
    stand_in.chmod(0o755)
    play = tmp_path / "p.yml"
    play.write_text(f"""
- hosts: localhost
  gather_facts: false
  tasks:
    - eco_state: {{op: put_db_key, path: "{tmp_path}/s.sealed", app: a, value: "dbkey/odd"}}
      no_log: true
    - eco_state:
        op: seal
        path: "{tmp_path}/s.sealed"
        app: a
        instance_id: inst-1
        secret_env: {{A_TOKEN: "tokentokentoken", A_ENCRYPTION_KEY: "kkkkkkkkkkkk"}}
        drop: A_ENCRYPTION_KEY
        agentpg_bin: "{stand_in}"
      register: r
      no_log: true
    - ansible.builtin.copy: {{content: "{{{{ r.blob }}}}", dest: "{tmp_path}/blob.txt"}}
      no_log: true
""")
    env = {**os.environ, "ECOCTL_SEAL_PASSPHRASE": PW, "ANSIBLE_LOCALHOST_WARNING": "False", "ANSIBLE_INVENTORY_UNPARSED_WARNING": "False"}
    p = subprocess.run(["ansible-playbook", str(play)], cwd=ANSIBLE, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    # opened in a fresh interpreter: Ansible sealed it with the real scrypt cost, the tests lower it in this process
    code = "import json, sys; from apgkit import sealed; print(json.dumps(sealed.open_blob(open(sys.argv[1]).read(), sys.argv[2])))"
    out = subprocess.run([sys.executable, "-c", code, str(tmp_path / "blob.txt"), PW], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == {"A_TOKEN": "tokentokentoken", "DATABASE_URL": "postgresql://admin:dbkey%2Fodd@db.example.test:5432/appdb?sslmode=require"}
