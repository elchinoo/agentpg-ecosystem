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
        (lambda d: d["apps"]["cpsim"].update({"url_env": {"paybroker": "PAYBROKER_URL"}}), "only apps it calls"),
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
