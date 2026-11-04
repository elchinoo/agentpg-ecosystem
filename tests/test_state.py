"""The state file is ciphertext at rest: nothing secret is on the operator's disk in the clear."""
import json
import os
import stat

import pytest
from apgkit import sealed as kit

from ecoctl import cli
from ecoctl.state import State, StateError
from tests.conftest import ADMIN_PASSWORD, PASSPHRASE, read_state
from tests.unsealed import UNSEALED

SECRETS = {"db": "dbkey-Zk3pQ9sT7vXw2LmN8aBc", "client": "cs_client-secret-4Hn2Vb7Qx9", "admin": "admin-key-Ra5Tg8Yu1Ie3Oo", "jwt": "jwt-secret-Mn6Bv3Cx1Zq9"}


def filled(directory) -> State:
    st = State(directory)
    st.app("trustdesk")["db_key"] = SECRETS["db"]
    st.app("trustdesk")["client_secret"] = SECRETS["client"]
    st.data["identity_admin_key"] = SECRETS["admin"]
    st.data["mcp"] = {"jwt_secret": SECRETS["jwt"]}
    st.save()
    return st


def everything_in(directory) -> bytes:
    return b"".join(p.read_bytes() for p in directory.rglob("*") if p.is_file())


def test_round_trip_is_encrypted_at_rest_with_a_kit_blob(tmp_path):
    st = filled(tmp_path / "eco")
    raw = st.path.read_text()
    assert raw.startswith(kit.SEAL_PREFIX + "$")
    again = State(st.dir)
    assert again.app("trustdesk")["db_key"] == SECRETS["db"] and again.admin_key() == SECRETS["admin"] and again.mcp_jwt_secret() == SECRETS["jwt"]
    assert read_state(st.path)["apps"]["trustdesk"]["client_secret"] == SECRETS["client"]
    assert stat.S_IMODE(os.stat(st.path).st_mode) == 0o600 and stat.S_IMODE(os.stat(st.dir).st_mode) == 0o700


def test_no_secret_substring_in_the_file_or_any_file_of_the_state_directory(tmp_path):
    st = filled(tmp_path / "eco")
    st.app("trustdesk")["db_key"] = SECRETS["db"] + "-rotated"                # a second save: another temporary file came and went
    st.save()
    blob = everything_in(st.dir)
    assert blob
    for value in [*SECRETS.values(), SECRETS["db"] + "-rotated", PASSPHRASE]:
        assert value.encode() not in blob
    assert sorted(p.name for p in st.dir.iterdir()) == ["state.json"]          # no temporary file is left behind


def test_the_temporary_file_only_ever_holds_ciphertext(tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(open(src, "rb").read())
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    filled(tmp_path / "eco")
    assert seen and all(s.startswith(b"apgkit-seal1$") and not any(v.encode() in s for v in SECRETS.values()) for s in seen)


def test_an_unchanged_state_is_not_written_again(tmp_path):
    st = filled(tmp_path / "eco")
    before = st.path.read_bytes()
    st.save()
    State(st.dir).save()
    assert st.path.read_bytes() == before


def test_wrong_passphrase_is_a_clear_error_that_shows_no_data(tmp_path, monkeypatch):
    st = filled(tmp_path / "eco")
    monkeypatch.setenv("ECOCTL_SEAL_PASSPHRASE", "a-different-passphrase-entirely-1")
    with pytest.raises(StateError, match="wrong passphrase") as e:
        State(st.dir)
    text = str(e.value)
    assert not any(v in text for v in [*SECRETS.values(), PASSPHRASE, "a-different-passphrase-entirely-1"])
    assert e.value.__cause__ is None


def test_the_cli_reports_a_wrong_passphrase_without_a_traceback(tmp_path, monkeypatch, capsys):
    st = filled(tmp_path / "eco")
    monkeypatch.setenv("ECOCTL_SEAL_PASSPHRASE", "a-different-passphrase-entirely-1")
    assert cli.main(["--state", str(st.dir), "-f", str(UNSEALED), "plan"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("state error: ") and "wrong passphrase" in err and "Traceback" not in err
    assert not any(v in err for v in SECRETS.values())


def test_a_damaged_file_is_reported_not_traced(tmp_path):
    st = filled(tmp_path / "eco")
    st.path.write_text(st.path.read_text()[:-12] + "AAAAAAAAAAAA")
    with pytest.raises(StateError, match="could not be decrypted"):
        State(st.dir)
    st.path.write_text("not json and not a blob " + SECRETS["db"])
    with pytest.raises(StateError, match="not a valid state file") as e:
        State(st.dir)
    assert SECRETS["db"] not in str(e.value)


def test_a_short_passphrase_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ECOCTL_SEAL_PASSPHRASE", "short")
    st = State(tmp_path / "eco")
    st.admin_key()
    with pytest.raises(StateError, match="at least 20"):
        st.save()
    assert not (tmp_path / "eco" / "state.json").exists()


def test_the_passphrase_is_asked_once_per_run_and_twice_when_creating(tmp_path, monkeypatch):
    monkeypatch.delenv("ECOCTL_SEAL_PASSPHRASE")
    asked = []

    def ask(q):
        asked.append(q)
        return PASSPHRASE

    st = State(tmp_path / "eco", ask=ask)
    st.admin_key()
    st.save()
    st.app("x")["k"] = "v"
    st.save()
    assert len(asked) == 2                                                     # the passphrase and its confirmation, then it is remembered in memory only
    asked.clear()
    st2 = State(st.dir, ask=ask)
    assert len(asked) == 1 and st2.app("x")["k"] == "v"
    answers = iter([PASSPHRASE, PASSPHRASE + "x"])
    with pytest.raises(StateError, match="differ"):
        State(tmp_path / "other", ask=lambda q: next(answers)).require_passphrase()


# ---- legacy plaintext ----------------------------------------------------------------------------------------------------
def legacy(directory, extra=None):
    directory.mkdir(mode=0o700)
    data = {"apps": {"trustdesk": {"db_key": SECRETS["db"], "client_secret": SECRETS["client"]}}, "identity_admin_key": SECRETS["admin"], **(extra or {})}
    path = directory / "state.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def test_a_plaintext_state_is_read_and_rewritten_encrypted_on_the_next_save(tmp_path):
    path = legacy(tmp_path / "eco")
    said = []
    st = State(path.parent, announce=said.append)
    assert st.app("trustdesk")["db_key"] == SECRETS["db"] and st.admin_key() == SECRETS["admin"]
    assert path.read_text().startswith("{") and "plaintext state file" in said[0]            # read as it was; nothing rewritten until a save
    st.save()
    assert path.read_text().startswith("apgkit-seal1$") and State(path.parent).app("trustdesk")["client_secret"] == SECRETS["client"]
    assert not any(v.encode() in everything_in(path.parent) for v in SECRETS.values())
    assert any("now encrypted" in s and "overwritten" in s for s in said)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_the_old_plaintext_bytes_are_overwritten_before_the_replace(tmp_path, monkeypatch):
    path = legacy(tmp_path / "eco")
    old_inode_content = []
    real_replace = os.replace

    def spy(src, dst):
        old_inode_content.append(open(dst, "rb").read())                     # what the old file holds at the moment it is replaced
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    size = path.stat().st_size
    State(path.parent, announce=lambda t: None).save()
    assert old_inode_content == [b"\0" * size]


def test_a_legacy_vault_admin_password_is_deleted_on_migration(tmp_path):
    path = legacy(tmp_path / "eco", {"vault": {"admin_password": "old-kept-vault-admin-pw-1234"}, "mcp": {"jwt_secret": SECRETS["jwt"]}})
    said = []
    st = State(path.parent, announce=said.append)
    assert "vault" not in st.data and any("vault admin password" in s for s in said)
    st.save()
    assert b"old-kept-vault-admin-pw-1234" not in everything_in(path.parent)
    assert "vault" not in read_state(path) and read_state(path)["mcp"]["jwt_secret"] == SECRETS["jwt"]


def test_an_encrypted_state_with_a_leftover_vault_password_is_cleaned_too(tmp_path):
    st = State(tmp_path / "eco")
    st.data["vault"] = {"admin_password": "old-kept-vault-admin-pw-1234", "other": 1}
    st.save()
    st2 = State(st.dir, announce=lambda t: None)
    assert st2.data["vault"] == {"other": 1}
    st2.save()
    assert read_state(st.path)["vault"] == {"other": 1}


# ---- commands that need no passphrase ------------------------------------------------------------------------------------
def test_validate_and_a_first_plan_work_without_a_passphrase(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ECOCTL_SEAL_PASSPHRASE")
    monkeypatch.setattr("getpass.getpass", lambda *_: pytest.fail("asked for a passphrase"))
    assert cli.main(["-f", str(UNSEALED), "validate"]) == 0
    assert cli.main(["--state", str(tmp_path / "none"), "-f", str(UNSEALED), "plan"]) == 0          # no state file yet: nothing to decrypt
    assert not (tmp_path / "none").exists()


def test_a_plan_over_an_existing_encrypted_state_needs_the_passphrase(tmp_path, monkeypatch):
    st = filled(tmp_path / "eco")
    monkeypatch.delenv("ECOCTL_SEAL_PASSPHRASE")
    monkeypatch.setattr("getpass.getpass", lambda *_: PASSPHRASE)
    assert cli.main(["--state", str(st.dir), "-f", str(UNSEALED), "plan"]) == 0


# ---- the vault password is never persisted ------------------------------------------------------------------------------------
def test_the_state_has_no_way_to_keep_the_vault_password():
    assert not hasattr(State, "keep_vault_admin_password") and not hasattr(State, "vault_admin_password")


def test_the_admin_password_from_the_environment_never_reaches_the_state_directory(tmp_path):
    st = filled(tmp_path / "eco")
    assert ADMIN_PASSWORD.encode() not in everything_in(st.dir) and "admin_password" not in json.dumps(read_state(st.path))


def test_the_real_scrypt_cost_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(kit, "_KDF", (17, 8, 1))
    st = filled(tmp_path / "eco")
    assert State(st.dir).app("trustdesk")["db_key"] == SECRETS["db"]
