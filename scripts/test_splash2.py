import webview
import threading
import time
import sys

print('Creating splash window...')
html = open('scripts/splash.html', 'r', encoding='utf-8').read()
print('Creating splash window...')
w = webview.create_window('BirdEye - Starting', html=html, width=500, height=600, resizable=False, frameless=True, easy_drag=False, on_top=True)
print('Splash window created')

def close_it():
    time.sleep(2)
    print('Closing...')
    w.destroy()
    sys.exit(0)

t = threading.Thread(target=close_it)
t.daemon = True
t.start()
print('Starting webview...')
webview.start()
print('Done')