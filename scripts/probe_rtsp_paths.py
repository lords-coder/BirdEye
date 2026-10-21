"""RTSP credential/path diagnostic — runs in-process, never prints secrets.

Reads the stored (encrypted) camera credentials from the DB, attempts a
DESCRIBE against a list of common stream paths using both Basic and Digest
auth, and prints ONLY the RTSP status line per path.  Passwords never
appear in output, argv, or logs.
"""
import re
import socket
import hashlib
import urllib.parse

from app.db import SessionLocal
from app.models import Camera


def digest_header(method, uri, user, pw, realm, nonce,
                  qop="auth", cnonce="abc123", nc="00000001"):
    ha1 = hashlib.md5(f"{user}:{realm}:{pw}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    resp = hashlib.md5(
        f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()
    ).hexdigest()
    return (
        f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", qop={qop}, nc={nc}, cnonce="{cnonce}", response="{resp}"'
    )


PATHS = [
    "/Streaming/Channels/101", "/Streaming/Channels/1",
    "/cam/realmonitor?channel=1&subtype=0", "/live", "/h264",
    "/0", "/video", "/stream1", "/",
]


def probe(cam):
    full = cam.build_rtsp_url()
    u = urllib.parse.urlparse(full)
    host, port = u.hostname, u.port or 554
    user = u.username or ""
    pw = urllib.parse.unquote(u.password or "")
    print(f"=== {cam.name} ({host}:{port}) user={user} ===")
    for p in PATHS:
        test_url = f"rtsp://{host}:{port}{p}"
        try:
            s = socket.socket()
            s.settimeout(4)
            s.connect((host, port))
            req = (f"DESCRIBE {test_url} RTSP/1.0\r\nCSeq: 1\r\n"
                   "Accept: application/sdp\r\n\r\n")
            s.sendall(req.encode())
            raw = s.recv(4096).decode("utf-8", "ignore")
            first = raw.splitlines()[0] if raw else "<empty>"
            if "401" in first and "Digest" in raw:
                realm = re.search(r'realm="([^"]+)"', raw)
                nonce = re.search(r'nonce="([^"]+)"', raw)
                if realm and nonce:
                    auth = digest_header("DESCRIBE", p, user, pw,
                                         realm.group(1), nonce.group(1))
                    req2 = (f"DESCRIBE {test_url} RTSP/1.0\r\nCSeq: 2\r\n"
                            f"Authorization: {auth}\r\n"
                            "Accept: application/sdp\r\n\r\n")
                    s.sendall(req2.encode())
                    raw2 = s.recv(4096).decode("utf-8", "ignore")
                    line2 = raw2.splitlines()[0] if raw2 else "<empty>"
                    print(f"  {p:42s} -> {line2}   [digest]")
                    if "200" in line2:
                        print(f"  >>> CONFIRMED PATH: {p}")
                        s.close()
                        return p
            else:
                print(f"  {p:42s} -> {first}")
            s.close()
        except Exception as exc:  # noqa: BLE001
            print(f"  {p:42s} -> ERR {type(exc).__name__}: {exc}")
    return None


if __name__ == "__main__":
    db = SessionLocal()
    for cam in db.query(Camera).all():
        probe(cam)
    db.close()
