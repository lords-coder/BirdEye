import webview
import sys

print("Creating window...")
window = webview.create_window('Test', 'about:blank', width=400, height=300)
print('Window created')

def close_it():
    import time
    time.sleep(2)
    print('Closing...')
    window.destroy()
    sys.exit(0)

import threading
import time
import sys

t = threading.Thread(target=close_it)
t.daemon = True
t.start()

print('Starting webview...')
try:
    webview.start(debug=True)
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
print('Done')