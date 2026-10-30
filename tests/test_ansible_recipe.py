"""The Ansible recipe (ansible/) must stay in step with ecosystem.yaml and with ecoctl's contract. Runs without Ansible, the agentpg CLI or any
infrastructure: it loads the recipe's filter plugin and the shared code of its agentpg_* modules, renders its env template with Jinja2 and compares
everything with ecoctl.manifest, ecoctl.plan and ecoctl.agentpg (the modules also run against ecoctl's in-memory fake CLI)."""
import importlib.util
import re
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


def test_modules_create_then_converge_and_keep_the_database_key_private(tmp_path):
    fake = FakeAgentPG()
    fake.url_with_id = True
    fake.creating_polls = 3                                   # the wait loops really loop
    sleeps = []
    cli = ap.Cli(run=fake, interval=1, sleep=sleeps.append)
    st = ap.State(str(tmp_path / "s" / "x.json"))
    inst, sbx = _deploy(cli, st, {"A": "1"})
    assert inst["created"] and sbx["created"] and sbx["changed"] and sbx["url"] == "https://eco-x-sbx-1.sandbox.example" and sleeps
    assert "a1p_sk" not in str(inst) + str(sbx) and inst["db_key_saved"]                 # the database key goes to the state file only
    assert (tmp_path / "s" / "x.json").stat().st_mode & 0o777 == 0o600 and (tmp_path / "s").stat().st_mode & 0o777 == 0o700
    assert ap.State(st.path).data["db_key"].startswith("a1p_sk_") and ap.State(st.path).data["extensions"] == ["vector", "pg_trgm"]
    n = len(fake.calls)
    fake.calls.clear()
    st2 = ap.State(st.path)                                   # a new run reads the state again
    inst2, sbx2 = _deploy(cli, st2, {"A": "1"})
    assert not inst2["changed"] and not sbx2["changed"] and not sbx2["env_differs"]
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


SECRET_WORDS = re.compile(r"\beco_admin_pw\b|\beco_admin_password\b|eco_admin_prompted|eco_vault_operator_key")


def test_every_task_that_handles_the_admin_password_or_the_operator_key_has_no_log():
    seen = 0
    for path in [*ANSIBLE.glob("*.yml"), *ANSIBLE.glob("roles/*/tasks/*.yml")]:
        for task in _tasks(yaml.safe_load(path.read_text())):
            body = {k: v for k, v in task.items() if k not in {"name", "when", "register", "changed_when", "failed_when", "until", "loop_control", "tags", "retries", "delay"}}
            if SECRET_WORDS.search(yaml.safe_dump(body)):
                seen += 1
                assert task.get("no_log") is True, f"{path.relative_to(ANSIBLE)}: {task.get('name')} handles the admin password or operator key without no_log: true"
    assert seen >= 5


def test_the_vault_tasks_are_ordered_and_idempotent_by_construction():
    one = yaml.safe_load((ANSIBLE / "roles/vault/tasks/one.yml").read_text())
    by = {t["name"].split(": ", 1)[-1]: t for t in one}
    state = by["is its vault created, is it open"]
    assert state["check_mode"] is False and state["changed_when"] is False                                    # a read: never a change
    create, unlock = by["create its vault with the admin password"], by["open its vault with the admin password"]
    assert "not eco_vault_now.json.vault.initialized" in create["when"] and "== 200" in create["changed_when"]
    assert "initialized and not eco_vault_now.json.vault.unlocked" in unlock["when"] and "== 200" in unlock["changed_when"]       # an open vault changes nothing
    names = [t["name"].split(": ", 1)[-1] for t in one]
    guard = "a vault that does not exist yet is created only where that is asked for (site.yml), not by unlock.yml"
    assert names.index(guard) < names.index("create its vault with the admin password")
    assert names.index("open its vault with the admin password") < names.index("the vault is open now") < names.index("wait until it answers /readyz")
    assert one[0]["when"] == "eco_admin_pw is not defined"
    pw = (ANSIBLE / "roles/vault/tasks/password.yml").read_text()
    assert "mode: \"0600\"" in pw and "eco_admin_password_file" in pw and "echo: false" in pw


def test_the_vault_is_opened_before_identity_or_the_gateway_is_waited_for_and_before_dependents():
    ident = [t.get("name", "") for t in _tasks(yaml.safe_load((ANSIBLE / "roles/identity/tasks/main.yml").read_text()))]
    vault_at = next(i for i, n in enumerate(ident) if "create or open its vault" in n)
    assert ident.index("Identity provider container and readiness") < vault_at < ident.index("Read the issuer (the first request fixes it when IDENTITY_PUBLIC_URL is unset)")
    app = [t.get("name", "") for t in _tasks(yaml.safe_load((ANSIBLE / "roles/app/tasks/one_app.yml").read_text()))]
    probes = (("container", ": container"), ("vault", "create or open its vault"), ("signin", "where its sign-in points now"))
    at = {k: next(i for i, n in enumerate(app) if probe in n) for k, probe in probes}
    assert at["container"] < at["vault"] < at["signin"]
    for f in ("agentpg.yml", "container.yml"):  # a locked app answers 503 on /readyz: deploy waits on /healthz, the unlock task on /readyz
        assert "eco_item.vault != 'password'" in (ANSIBLE / "roles/app/tasks" / f).read_text()


def test_unlock_and_verify_playbooks_exist_and_say_what_they_do():
    unlock = yaml.safe_load((ANSIBLE / "unlock.yml").read_text())
    assert unlock[0]["vars"]["eco_vault_create"] is False and "unlock" in unlock[0]["tags"]
    assert any(t.get("ansible.builtin.include_role", {}).get("name") == "vault" for t in _tasks(unlock))
    verify = (ANSIBLE / "roles/verify/tasks/main.yml").read_text()
    assert "/api/session" in verify and "eco_vault_failures" in verify and "eco_vault_locked | length == 0" in verify
    assert "Vaults" in (ANSIBLE / "roles/verify/templates/report.j2").read_text()
    defaults = (ANSIBLE / "roles/common/defaults/main.yml").read_text()
    assert "ECO_ADMIN_PASSWORD" in defaults and "{{ eco_secrets_dir }}/vault/admin_password" in defaults and "eco_admin_password_prompt: false" in defaults
    assert ".secrets/" in (ANSIBLE / ".gitignore").read_text()                                                  # where a generated password lives


# A real Ansible run of the vault tasks against a local stand-in for the two services (no network beyond 127.0.0.1, nothing on AgentPG).
class _Services:
    def __init__(self):
        import http.server
        import json as _json
        import threading

        from tests.fakes import FakeVault
        self.vaults = {"identity": FakeVault(), "llmgw": FakeVault()}
        self.keys = {"identity": "idk-test-operator-key", "llmgw": "gwk-test-operator-key"}
        self.log: list[tuple[str, str, str]] = []
        outer = self

        def make(name):
            class H(http.server.BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def _send(self, code, body):
                    data = _json.dumps(body).encode()
                    self.send_response(code)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                def do_GET(self):  # noqa: N802
                    outer.log.append((name, "GET", self.path))
                    v = outer.vaults[name]
                    if self.path == "/api/session":
                        return self._send(200, {"state": "logged_out", "vault": v.session()})
                    if self.path == "/readyz":
                        return self._send(503 if name == "identity" and not v.unlocked else 200, {"status": "x"})
                    return self._send(200, {"status": "ok"})

                def do_POST(self):  # noqa: N802
                    outer.log.append((name, "POST", self.path))
                    body = _json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                    if self.headers.get("authorization") != "Bearer " + outer.keys[name]:
                        return self._send(401, {"detail": "authentication required"})
                    r = outer.vaults[name].handle(self.path, body)
                    return self._send(r.status_code, r.json()) if r else self._send(404, {})
            return H

        self.servers = {n: http.server.ThreadingHTTPServer(("127.0.0.1", 0), make(n)) for n in self.vaults}
        for s in self.servers.values():
            threading.Thread(target=s.serve_forever, daemon=True).start()

    def url(self, name):
        return f"http://127.0.0.1:{self.servers[name].server_address[1]}"

    def close(self):
        for s in self.servers.values():
            s.shutdown()
            s.server_close()

    def restart(self):
        for v in self.vaults.values():
            v.restart()


@pytest.fixture
def services():
    s = _Services()
    yield s
    s.close()


def _ansible(services, tmp_path, playbook, *extra, verbose=False):
    import json
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
             "eco_secret_overrides": {"identity/IDENTITY_API_KEY": services.keys["identity"], "llmgw/LLMGW_API_KEY": services.keys["llmgw"]}}
    env = {k: v for k, v in os.environ.items() if k != "ECO_ADMIN_PASSWORD"} | {"ANSIBLE_LOCALHOST_WARNING": "False", "ANSIBLE_INVENTORY_UNPARSED_WARNING": "False"}
    p = subprocess.run(["ansible-playbook", "-i", "inventories/agentpg", playbook, "-e", json.dumps(evars), *extra, *(["-vvv"] if verbose else [])],
                       cwd=ANSIBLE, env=env, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr, secrets_dir


def _changed(out: str) -> int:
    return int(re.search(r"localhost\s*:.*changed=(\d+)", out).group(1))


def test_a_real_ansible_run_creates_then_opens_the_vaults_and_a_rerun_changes_nothing(services, tmp_path):
    import stat as _stat
    code, out, secrets_dir = _ansible(services, tmp_path, "unlock.yml", "-e", "eco_vault_create=true", verbose=True)
    assert code == 0, out[-3000:]
    pw = (secrets_dir / "vault/admin_password").read_text().strip()
    assert len(pw) == 32 and _stat.S_IMODE((secrets_dir / "vault/admin_password").stat().st_mode) == 0o600
    assert all(v.initialized and v.unlocked and v.password == pw for v in services.vaults.values())
    assert pw not in out and services.keys["identity"] not in out and services.keys["llmgw"] not in out                  # nothing secret in the log, even at -vvv
    assert services.vaults["identity"].initializations == 1 == services.vaults["llmgw"].initializations
    order = [(n, m, p) for n, m, p in services.log if m == "POST"]
    assert order[0] == ("identity", "POST", "/api/admin-password")                                                       # identity first
    code, out2, _ = _ansible(services, tmp_path, "unlock.yml")
    assert code == 0 and _changed(out2) == 0, out2[-2000:]                                                               # idempotent
    services.restart()                                                                                                   # a platform restart
    assert not any(v.unlocked for v in services.vaults.values())
    code, out3, _ = _ansible(services, tmp_path, "unlock.yml")
    assert code == 0 and _changed(out3) == 2 and all(v.unlocked for v in services.vaults.values()), out3[-2000:]
    assert all(v.initializations == 1 and v.unlocks == 1 for v in services.vaults.values())
    code, out4, _ = _ansible(services, tmp_path, "unlock.yml")
    assert code == 0 and _changed(out4) == 0


def test_a_real_ansible_run_gets_the_password_from_the_environment_and_never_writes_it_down(services, tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("ECO_ADMIN_PASSWORD", "operator-held-passphrase-one")
    import os
    import subprocess
    secrets_dir = tmp_path / "secrets"
    (secrets_dir / "agentpg").mkdir(parents=True)
    for name in ("identity", "llmgw"):
        (secrets_dir / "agentpg" / f"{name}.json").write_text(json.dumps({"url": services.url(name)}))
    evars = {"eco_secrets_dir": str(secrets_dir), "eco_manifest_path": str(MANIFEST), "eco_only": ["llmgw"], "eco_vault_create": True,
             "eco_secret_overrides": {"identity/IDENTITY_API_KEY": services.keys["identity"], "llmgw/LLMGW_API_KEY": services.keys["llmgw"]}}
    import shutil
    if not shutil.which("ansible-playbook"):
        pytest.skip("ansible-playbook is not installed")
    p = subprocess.run(["ansible-playbook", "-i", "inventories/agentpg", "unlock.yml", "-e", json.dumps(evars), "-vvv"], cwd=ANSIBLE, env=dict(os.environ), capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    assert all(v.password == "operator-held-passphrase-one" for v in services.vaults.values())
    assert "operator-held-passphrase-one" not in p.stdout + p.stderr and not (secrets_dir / "vault").exists()


def test_a_real_ansible_unlock_never_creates_a_vault_and_a_vault_that_exists_never_gets_a_made_up_password(services, tmp_path):
    code, out, _ = _ansible(services, tmp_path, "unlock.yml")
    assert code != 0 and "does not exist yet" in out and not any(v.initialized for v in services.vaults.values())
    code, out, secrets_dir = _ansible(services, tmp_path, "unlock.yml", "-e", "eco_vault_create=true")
    assert code == 0
    services.restart()
    (secrets_dir / "vault/admin_password").unlink()                                                                       # the generated file was lost
    code, out, _ = _ansible(services, tmp_path, "unlock.yml")
    assert code != 0 and "made up" in out and not any(v.unlocked for v in services.vaults.values())
