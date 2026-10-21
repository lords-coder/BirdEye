import sqlite3

con = sqlite3.connect('data/attendance.db')
cur = con.cursor()
print('=== TABLES ===')
for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print(' ', row[0])
print()
for t in ['users','students','cameras','attendance_sessions','recognition_logs','presence_state','courses','periods','alerts','password_reset_requests','attendance_records','attendance_overrides','attendance_sessions']:
    try:
        n = cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'{t}: {n} rows')
    except Exception as e:
        print(f'{t}: ERROR - {e}')
con.close()