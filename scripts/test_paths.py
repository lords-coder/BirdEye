import sys
sys.path.insert(0, 'scripts')
from birdeye_launcher import _get_repo_root, _get_python_executable
import sys

print('Testing _get_repo_root() in non-frozen mode:')
print(f'  REPO_ROOT: {_get_repo_root()}')

print()
print('Testing _get_python_executable() in non-frozen mode:')
venv_py = _get_python_executable()
print(f'  VENV_PY: {venv_py}')
print(f'  Exists: {venv_py.exists()}')