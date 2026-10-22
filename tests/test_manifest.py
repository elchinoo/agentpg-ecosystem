import copy
from pathlib import Path

import pytest
import yaml

from ecoctl.manifest import ManifestError, load, parse

REPO_MANIFEST = Path(__file__).resolve().parents[1] / "ecosystem.yaml"


def doc():
    return copy.deepcopy(yaml.safe_load(REPO_MANIFEST.read_text()))


def test_the_shipped_manifest_is_valid_and_callees_come_first():
    m = load(REPO_MANIFEST)
    order = [a.key for a in m.order()]
    assert order.index("pagerlens") < order.index("citypulse") < order.index("cpsim")
    assert order.index("trustdesk") < order.index("cpsim")
    assert m.identity.key == "identity" and m.sandbox_name("trustdesk") == "eco-trustdesk" and m.instance_name("trustdesk") == "eco-trustdesk-db"
    assert m.image(m.apps["paybroker"]) == "ghcr.io/elchinoo/paybroker:latest"
    assert m.apps["cpsim"].service_client_id == "cpsim-service" and m.apps["cpsim"].service and not m.apps["pagerlens"].service
    assert not m.apps["pagerlens-agent"].deploy and not m.apps["pagerlens-agent"].web


def test_unknown_keys_are_errors_everywhere():
    for path in ((), ("apps", "trustdesk"), ("agentpg",), ("apps", "trustdesk", "roles", 0)):
        d = doc()
        node = d
        for p in path:
            node = node[p]
        node["surprise"] = 1
        with pytest.raises(ManifestError, match="unknown key"):
            parse(d)


@pytest.mark.parametrize("name", ["TRUSTDESK_ENCRYPTION_KEY", "DB_PASSWORD", "API_TOKEN", "CLIENT_SECRET", "DATABASE_DSN"])
def test_secrets_cannot_be_written_in_the_manifest(name):
    d = doc()
    d["apps"]["trustdesk"]["env"][name] = "hunter2"
    with pytest.raises(ManifestError, match="looks like a secret"):
        parse(d)


def test_credentials_inside_urls_are_refused():
    d = doc()
    d["apps"]["trustdesk"]["env"]["LLM_BASE_URL"] = "https://user:pw@api.example.com/v1"
    with pytest.raises(ManifestError, match="embeds a password"):
        parse(d)


def test_a_call_must_name_an_app_a_scope_with_the_callee_prefix_and_an_exposed_scope():
    for mutate, message in (
        (lambda d: d["apps"]["cpsim"]["calls"].update({"ghost": ["ghost.x"]}), "not an app"),
        (lambda d: d["apps"]["cpsim"]["calls"].update({"trustdesk": ["citypulse.ingest"]}), "must start with 'trustdesk.'"),
        (lambda d: d["apps"]["cpsim"]["calls"].update({"trustdesk": ["trustdesk.typo"]}), "not listed under apps.trustdesk.exposes"),
        (lambda d: d["apps"]["cpsim"]["calls"].update({"trustdesk": []}), "lists no scopes"),
        (lambda d: d["apps"]["cpsim"].update({"url_env": {"pagerlens-agent": "AGENT_URL"}}), "only apps it calls or uses"),
    ):
        d = doc()
        mutate(d)
        with pytest.raises(ManifestError, match=message):
            parse(d)


def test_cycles_are_refused():
    d = doc()
    d["apps"]["pagerlens"]["calls"] = {"citypulse": ["citypulse.read"]}
    with pytest.raises(ManifestError, match="cycle"):
        parse(d)


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d.update(version=2), "version must be 1"),
    (lambda d: d["apps"].update({"Bad Name": {}}), "lowercase"),
    (lambda d: d["apps"].update({"identity": {}}), "reserved"),
    (lambda d: d["apps"]["trustdesk"].update(port=0), "port number"),
    (lambda d: d["apps"]["trustdesk"].update(extensions=["rm -rf"]), "unsupported"),
    (lambda d: d["apps"]["trustdesk"].update(default_roles=["ghost"]), "roles this app defines"),
    (lambda d: d["apps"]["trustdesk"].update(exposes=["other.thing"]), "must look like trustdesk."),
    (lambda d: d["apps"]["trustdesk"]["roles"].append({"name": "Bad Role"}), "valid role name"),
    (lambda d: d["access"]["groups"]["support"]["grants"].update({"ghost": "viewer"}), "not an app"),
    (lambda d: d["access"]["groups"]["support"]["grants"].update({"trustdesk": "ghost"}), "not a role of trustdesk"),
    (lambda d: d["agentpg"].update(registry="not a registry!"), "registry"),
    (lambda d: d["identity"].update(calls={"trustdesk": ["trustdesk.kb.read"]}), "does not call"),
    (lambda d: d.update(apps={}), "at least one app"),
])
def test_validation_messages(mutate, message):
    d = doc()
    mutate(d)
    with pytest.raises(ManifestError, match=message):
        parse(d)


def test_unreadable_files_are_manifest_errors(tmp_path):
    with pytest.raises(ManifestError):
        load(tmp_path / "missing.yaml")
    (tmp_path / "bad.yaml").write_text("a: [unclosed")
    with pytest.raises(ManifestError):
        load(tmp_path / "bad.yaml")


# ---- the LLM gateway wiring -------------------------------------------------------------------------------------------
def test_llm_defaults_and_the_apps_that_use_it():
    m = load(REPO_MANIFEST)
    assert m.consumers() == ["pagerlens", "paybroker", "trustdesk"]
    assert (m.llm.gateway, m.llm.chat_alias, m.llm.embed_alias, m.llm.embed_dimensions) == ("llmgw", "chat-default", "embed-default", 1536)
    assert not m.apps["cpsim"].llm and not m.apps["llmgw"].llm
    d = doc()
    del d["llm"]
    assert parse(d).llm == parse(d).llm.__class__() and parse(d).consumers() == ["pagerlens", "paybroker", "trustdesk"]      # every key is optional


def test_consumers_come_after_the_gateway_and_the_cascade_follows_them():
    m = load(REPO_MANIFEST)
    order = [a.key for a in m.order()]
    assert all(order.index("llmgw") < order.index(k) for k in m.consumers())
    assert m.dependents({"llmgw"}) == {"pagerlens", "trustdesk", "paybroker", "citypulse", "cpsim", "pagerlens-agent"}      # incl. what calls a consumer
    assert m.closure({"paybroker"}) == {"paybroker", "llmgw"}


def test_budget_can_be_set_or_removed_and_limits_are_checked():
    d = doc()
    d["llm"].update({"budget": None, "rpm": 5, "budget_mode": "soft"})
    llm = parse(d).llm
    assert llm.budget is None and llm.rpm == 5 and llm.budget_mode == "soft"
    d["llm"]["budget"] = 1234
    assert parse(d).llm.budget == 1234
    for key, bad in (("budget", 0), ("budget", "lots"), ("rpm", True), ("rpm", 0), ("embed_dimensions", 99999), ("budget_mode", "maybe"),
                     ("chat_alias", "no spaces"), ("embed_alias", "chat-default"), ("surprise", 1)):
        d = doc()
        d["llm"][key] = bad
        with pytest.raises(ManifestError):
            parse(d)


def test_the_gateway_must_exist_be_deployed_and_not_use_itself():
    d = doc()
    d["llm"]["gateway"] = "ghost"
    with pytest.raises(ManifestError, match="'ghost' is not an app"):
        parse(d)
    d = doc()
    d["apps"]["llmgw"]["deploy"] = False
    with pytest.raises(ManifestError, match="must be deployed"):
        parse(d)
    d = doc()
    d["apps"]["llmgw"]["llm"] = True
    with pytest.raises(ManifestError, match="cannot be an LLM consumer"):
        parse(d)
    d = doc()
    del d["apps"]["llmgw"]
    d["apps"]["cpsim"]["calls"].pop("llmgw")
    with pytest.raises(ManifestError, match="not an app"):
        parse(d)
    for app in d["apps"].values():                       # nobody uses an LLM: the gateway is not needed at all
        app.pop("llm", None)
    assert parse(d).consumers() == []


def test_an_llm_app_cannot_set_the_names_ecoctl_owns_and_must_be_deployed():
    for name in ("LLM_BASE_URL", "LLM_CHAT_MODEL", "EMBEDDING_MODEL", "EMBEDDING_DIM"):
        d = doc()
        d["apps"]["trustdesk"]["env"][name] = "x"
        with pytest.raises(ManifestError, match="set by ecoctl"):
            parse(d)
    d = doc()
    d["apps"]["trustdesk"]["generate"].append("LLM_API_KEY")
    with pytest.raises(ManifestError, match="set by ecoctl"):
        parse(d)
    d = doc()
    d["apps"]["pagerlens-agent"]["llm"] = True
    with pytest.raises(ManifestError, match="needs a deployed app"):
        parse(d)
    d = doc()
    d["apps"]["trustdesk"]["llm"] = "yes"
    with pytest.raises(ManifestError, match="llm must be true or false"):
        parse(d)


def test_a_consumer_that_the_gateway_calls_is_a_cycle():
    d = doc()
    d["apps"]["llmgw"]["calls"] = {"trustdesk": ["trustdesk.metrics"]}
    with pytest.raises(ManifestError, match="cycle"):
        parse(d)
