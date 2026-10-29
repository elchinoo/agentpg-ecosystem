from apgkit.db import Database
from apgkit.vault import Vault, VaultLocked

db, vault = Database(), Vault("DEMO")
with db.connect() as conn:
    vault.initialize(conn, "correct horse battery")         # a data key, wrapped under the admin password
    conn.commit()
    sealed = vault.encrypt("provider-api-key-example")      # envelope: Fernet under the data key
    print("stored   :", sealed[:30] + "...")
    print("decrypted:", vault.decrypt(sealed))
    vault.lock()                                            # what a restart does
    try:
        vault.decrypt(sealed)
    except VaultLocked as exc:
        print("locked   :", exc)
    vault.unlock_with_password(conn, "correct horse battery")
    print("unlocked :", vault.decrypt(sealed))
