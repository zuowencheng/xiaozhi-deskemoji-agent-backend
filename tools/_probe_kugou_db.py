# -*- coding: utf-8 -*-
"""只读探查酷狗 SQLite 数据库（歌单/历史/本地曲库）。"""
import os
import shutil
import sqlite3
import tempfile

BASE = r"C:\Users\zuowe\AppData\Roaming\KuGou8"
DBS = ["KGMusicV3.db", "playlistV3.db", "RecentlyMusicV3.db"]

tmp = tempfile.mkdtemp(prefix="kgdb_")
for name in DBS:
    src = os.path.join(BASE, name)
    if not os.path.exists(src):
        print("=== %s : 不存在" % name)
        continue
    print("=== %s  (%.1f MB)" % (name, os.path.getsize(src) / 1048576.0))
    dst = os.path.join(tmp, name)
    try:
        shutil.copy2(src, dst)
    except Exception as e:
        print("    复制失败:", e)
        continue
    try:
        con = sqlite3.connect("file:%s?mode=ro" % dst.replace("\\", "/"), uri=True)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print("    表:", tables)
        for t in tables:
            try:
                cur.execute("SELECT COUNT(*) FROM [%s]" % t)
                n = cur.fetchone()[0]
            except Exception as e:
                print("      - %s: count err %s" % (t, e))
                continue
            try:
                cur.execute("PRAGMA table_info([%s])" % t)
                cols = [c[1] for c in cur.fetchall()]
            except Exception:
                cols = []
            print("      - %-28s rows=%-7d cols=%s" % (t, n, ",".join(cols[:14])))
            if n > 0 and n < 5000:
                try:
                    cur.execute("SELECT * FROM [%s] LIMIT 5" % t)
                    for row in cur.fetchall():
                        s = str(row)
                        print("          >", s[:220])
                except Exception:
                    pass
        con.close()
    except Exception as e:
        print("    打开失败:", e)
    print()

print("临时目录:", tmp)
