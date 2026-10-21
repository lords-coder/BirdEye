#!/usr/bin/env python3
"""Scan for potential secrets in source files."""
import os
import re

patterns = [
    re.compile(r'(?i)(password|secret|api[_-]?key|token|credential)\s*[:=]\s*[^\s"\'{}]+'),
    re.compile(r'rtsp://[^:]+:[^@]+@'),  # RTSP URLs with credentials
]

skip_dirs = {'.venv', '__pycache__', '.pytest_cache', '.git', 'build', 'dist', 'archive', 'data', '.mypy_cache', '.ruff_cache', 'node_modules'}
skip_ext = {'.pyc', '.pyo', '.pyd', '.so', '.dll', '.exe', '.bin', '.db', '.sqlite', '.sqlite3', '.rdb', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.mov', '.avi', '.mkv', '.wav', '.mp3', '.onnx', '.npy', '.npz'}

found = 0
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in skip_dirs]
    for f in files:
        if any(f.endswith(ext) for ext in skip_ext):
            continue
        path = os.path.join(root, f)
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as fp:
                content = fp.read()
            for p in patterns:
                for m in p.finditer(content):
                    ctx = content[max(0,m.start()-60):m.end()+60].replace('\n',' ').replace('\r','')
                    print(f'POTENTIAL: {path} | {ctx}')
                    found += 1
        except Exception as e:
            pass

print(f'\nTotal potential findings: {found}')
if found == 0:
    print('No potential secrets found in scanned files.')