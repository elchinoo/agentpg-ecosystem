import sys

from apgkit.auth import schema
from protect import db, local

with db.connect() as conn:
    conn.execute(schema.SQL)                     # the kit's tables: sessions, API tokens, OIDC state
    conn.commit()
    scope = sys.argv[1]
    _, raw = local.create_token(conn, f"{scope}-token", scope)
open(f"{scope}.token", "w").write(raw)
print(f"API token for scope '{scope}' saved to {scope}.token")
