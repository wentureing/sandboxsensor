import sqlite3
import os

DBS = ["dist/viadb/via.db", "dist/viadb/downloader.db", "dist/viadb/monkey.db"]

for path in DBS:
    if not os.path.exists(path):
        print("%s : (missing)" % path)
        continue
    print("=" * 60)
    print("DB:", path, os.path.getsize(path), "bytes")
    con = sqlite3.connect(path)
    cur = con.cursor()
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
    except Exception as e:
        print("  ERR", e)
        continue
    if not tables:
        print("  (无表)")
        con.close()
        continue
    for t in tables:
        try:
            cur.execute('SELECT COUNT(*) FROM "%s"' % t)
            n = cur.fetchone()[0]
            cur.execute('PRAGMA table_info("%s")' % t)
            cols = [r[1] for r in cur.fetchall()]
            print("  %-24s %4d 行   列=%s" % (t, n, cols[:8]))
        except Exception as e:
            print("  %-24s ERR %s" % (t, e))
    # 找含 mht / offline / url 的字段
    print("  --- 含 mht/offline 的字段 ---")
    hit = False
    for t in tables:
        try:
            cur.execute('PRAGMA table_info("%s")' % t)
            cols = [r[1] for r in cur.fetchall()]
            for c in cols:
                for kw in ("%mht%", "%offline%", "%.mht%"):
                    cur.execute('SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?' % (t, c), (kw,))
                    n = cur.fetchone()[0]
                    if n:
                        print("      %s.%s LIKE %s -> %d 行" % (t, c, kw, n))
                        hit = True
        except Exception:
            pass
    if not hit:
        print("      (无)")
    con.close()
    print()
