from pathlib import Path

from apgkit import migrate, settings_store
from apgkit.db import Database
from pgvector import Vector

db = Database(vector=True)                                   # registers the pgvector adapter once the extension exists
print("applied:", migrate.run(db, Path("migrations"), extensions=("vector",), table="demo_migrations"))
print("again  :", migrate.run(db, Path("migrations"), extensions=("vector",), table="demo_migrations"))
with db.connect() as conn:
    settings_store.put(conn, "theme", "dark")
    conn.execute("INSERT INTO item (name, embedding) VALUES ('kettle', %s), ('teapot', %s), ('lamp', %s)",
                 (Vector([1, 0, 0]), Vector([.9, .1, 0]), Vector([0, 0, 1])))
    conn.commit()
    print("setting:", settings_store.get(conn, "theme"))
    q = conn.execute("SELECT name FROM item ORDER BY embedding <=> %s LIMIT 2", (Vector([1, .05, 0]),)).fetchall()
    print("nearest:", [r["name"] for r in q])
