import sqlite3

c = sqlite3.connect("dist/viadb/via.db")
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
        print("  %-24s %4d 行   列=%s" % (t, n, cols[:8]))
    except Exception as e:
        print("  %-24s ERR %s" % (t, e))

# 找任何与 offline / download 相关的表并打印内容
print()
print("=== 与 offline/download 相关的表内容 ===")
for t in tables:
    if any(k in t.lower() for k in ("offline", "download", "file", "page", "history")):
        try:
            cur.execute('SELECT * FROM "%s" LIMIT 5' % t)
            rows = cur.fetchall()
            cur.execute('PRAGMA table_info("%s")' % t)
            cols = [r[1] for r in cur.fetchall()]
            print("  --- %s (%d 行抽样) ---" % (t, len(rows)))
            print("      列:", cols)
            for r in rows:
                print("      ", r)
        except Exception as e:
            print("  %s ERR %s" % (t, e))

# 全库搜 'offline' 字样出现的位置
print()
print("=== 全库搜 .mht / offline 字样 ===")
found = False
for t in tables:
    try:
        cur.execute('PRAGMA table_info("%s")' % t)
        cols = [r[1] for r in cur.fetchall()]
        for col in cols:
            q = 'SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?' % (t, col)
            cur.execute(q, ("%mht%",))
            n = cur.fetchone()[0]
            if n:
                print("  %s.%s 含 mht: %d 行" % (t, col, n))
                found = True
    except Exception:
        pass
if not found:
    print("  没有任何表字段包含 'mht'")

c.close()
