from apgkit.db import Database

with Database().connect(retries=5, delay=1.0) as conn:       # backs off 1 s, 2 s, 4 s ... while the database wakes
    print("connected:", conn.execute("SELECT 'the database is up' AS msg").fetchone()["msg"])
