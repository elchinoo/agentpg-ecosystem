import base64
import json
import os

from apgkit.auth import TokenSource

src = TokenSource(os.environ["DEMO_OIDC_ISSUER"], "caller", lambda: os.environ["CALLER_SECRET"], scope="demo.read", audience="demo")
token = src.token()                              # client_credentials grant, cached until near expiry
open("svc.token", "w").write(token)
part = lambda i: json.loads(base64.urlsafe_b64decode(token.split(".")[i] + "=="))  # noqa: E731
print("header:", {k: part(0)[k] for k in ("alg", "typ")}, "kid present:", "kid" in part(0))
print("claims:", {k: part(1).get(k) for k in ("iss", "aud", "scope", "sub_type")})
