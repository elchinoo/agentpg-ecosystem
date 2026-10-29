from apgkit.auth import AppAuth, Authenticator, LocalAuth, from_env
from apgkit.db import Database
from fastapi import Depends, FastAPI

db = Database()                                  # DATABASE_URL, or the PG* variables
app_auth = AppAuth("demo", "demo",
                   role_permissions={"admin": frozenset({"*"})},
                   scope_permissions={"read": frozenset({"things:read"}),
                                      "demo.read": frozenset({"things:read"})})
local = LocalAuth(app_auth, env_prefix="DEMO", token_prefix="dm_", token_scopes={"read", "other"})
fed = from_env("DEMO", "demo")                   # on only when DEMO_OIDC_ISSUER is set
auth = Authenticator(app_auth, local, db, cookie_name="dm_session", verifier=fed.verifier)
app = FastAPI()


@app.get("/things", dependencies=[Depends(auth.require("things:read"))])
def things():
    return ["kettle", "teapot"]
