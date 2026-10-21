import sqlite3
con = sqlite3.connect('data/attendance.db')
cur = con.cursor()
tables = ['users','students','cameras','attendance_sessions','attendance_records',
          'attendance_overrides','recognition_logs','presence_states','presence_events',
          'courses','periods','alerts','password_reset_requests','cameras']
for t in tables:
    try:
        n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f'{t}: {n} rows')
    except Exception as e:
        print(f'{t}: ERROR {e}')
print()
print('=== users ===')
for row in cur.execute('SELECT id,username,role,status,password_source,security_level FROM users'):
    print(' ', row)
print('=== students ===')
for row in cur.execute('SELECT id,name,registration_no,section FROM students'):
    print(' ', row)
print('=== cameras ===')
for row in cur.execute('SELECT id,name,host,port,username,camera_role,type,enabled,has_password FROM cameras'):
    print(' ', row)
print('=== alembic_version ===')
for row in cur.execute('SELECT version_num FROM alembic_version'):
    print(' ', row)
con.close()
