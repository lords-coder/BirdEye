import webview
import threading
import time

def on_closed():
    print('Window closed')

window = webview.create_window('BirdEye Test', 'about:blank', width=800, height=600)

def close_after():
    time.sleep(2)
    window.destroy()

t = threading.Thread(target=close_after)
t.start()

webview.start()
print('WebView started and closed successfully')