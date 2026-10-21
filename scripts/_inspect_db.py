import sqlite3
con = sqlite3.connect('data/attendance.db')
cur = con.cursor()
print('=== TABLES ===')
for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print(' ', row[0])
print()
for t in ['users','students','cameras','attendancesession','recognitionslog','presence_state','cours','period','alert','passwordresetrequest']:
    try:
        n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f'{t}: {n} rows')
    except Exception as e:
        print(f'{t}: (not present: {e})')
con.close()
