from apgkit import masking
from apgkit.db import Database

with Database().connect() as conn:
    conn.execute("CREATE TABLE person (id int PRIMARY KEY, name text, email text, iban text)")
    conn.execute("INSERT INTO person VALUES (1, 'Alice Martin', 'alice@example.com', 'BE68539007547034')")
    conn.commit()
    out = masking.mask_view(conn, "person", "person_masked", {"id": "pass", "name": "initial", "email": "email", "iban": "last4"})
    print("base table denied to the read-only role:", out["base_denied"])
