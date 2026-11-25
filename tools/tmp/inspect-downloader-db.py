import os
import sqlite3

DB = "dist/viadb/downloader.db"
if not os.path.exists(DB):
    raise SystemExit("先 pull downloader db")

c = sqlite3.connect(DB)
cur = c.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("=== 表 ===")
print("  ", tables)
print()

for t in tables:
    try:
        cur.execute('SELECT COUNT(*) FROM "%s"' % t)
        n = cur.fetchone()[0]
        cur.execute('PRAGMA table_info("%s")' % t)
        cols = [r[1] for r in cur.fetchall()]
        print("  %-24s %4d 行   列=%s" % (t, n, cols[:10]))
        if n and n <= 8:
            cur.execute('SELECT * FROM "%s" LIMIT 8' % t)
            for r in cur.fetchall():
                print("      ", r)
    except Exception as e:
        print("  %-24s ERR %s" % (t, e))
    print()

c.close()
