"""Optional Secret Service: failures must not prevent Telegram/API startup."""
import atexit
import os
import re
import select
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

STATUS = 'not_started'
ERROR = ''
BUS = None
RUNTIME = None
DIAGNOSTICS = None


def diagnostic(text):
    # Only startup diagnostics, never passwords, credentials or OAuth terminal output.
    text = re.sub(r'https?://\S+', '[URL]', str(text))
    text = re.sub(r'(?:gh[pousr]_|github_pat_|AIza)[A-Za-z0-9_-]+', '[REDACTED]', text)
    return ' '.join(text.split())[:600]


def cleanup():
    global BUS, RUNTIME, DIAGNOSTICS
    if BUS:
        if BUS.poll() is None:
            BUS.terminate()
            try:
                BUS.wait(timeout=3)
            except subprocess.TimeoutExpired:
                BUS.kill()
                BUS.wait()
        if BUS.stdout:
            BUS.stdout.close()
        BUS = None
    if DIAGNOSTICS:
        DIAGNOSTICS.close()
        DIAGNOSTICS = None
    if RUNTIME:
        shutil.rmtree(RUNTIME, ignore_errors=True)
        RUNTIME = None


def spawn_bus():
    global BUS, RUNTIME, DIAGNOSTICS
    cleanup()
    # Do not inherit /run/user/0 or an inaccessible host session bus after dropping UID.
    RUNTIME = tempfile.mkdtemp(prefix=f'agent-dbus-{os.getuid()}-')
    os.chmod(RUNTIME, 0o700)
    os.environ['XDG_RUNTIME_DIR'] = RUNTIME
    address = 'unix:path=' + str(Path(RUNTIME) / 'bus')
    DIAGNOSTICS = tempfile.TemporaryFile()
    BUS = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--nopidfile',
        '--address=' + address, '--print-address=1'],
        stdout=subprocess.PIPE, stderr=DIAGNOSTICS, text=True)
    ready, _, _ = select.select([BUS.stdout], [], [], 10)
    line = BUS.stdout.readline().strip() if ready else ''
    if not line or not line.startswith('unix:') or BUS.poll() is not None:
        DIAGNOSTICS.seek(0)
        detail = DIAGNOSTICS.read(4096).decode(errors='replace')
        raise RuntimeError('D-Bus did not start: ' + (detail or 'timeout/no socket address'))
    os.environ['DBUS_SESSION_BUS_ADDRESS'] = line


def start_keyring():
    global STATUS, ERROR
    if STATUS == 'ready' and BUS and BUS.poll() is None:
        return True
    try:
        for executable in ('dbus-daemon', 'gnome-keyring-daemon'):
            if not shutil.which(executable):
                raise RuntimeError(f'{executable} missing; enable the project custom Dockerfile in Bothost')
        spawn_bus()
        key = Path(os.environ['HOME']) / '.keyring-password'
        if not key.exists():
            fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_urlsafe(48))
        key.chmod(0o600)
        result = subprocess.run(['gnome-keyring-daemon', '--unlock', '--components=secrets'],
            input=key.read_text(), text=True, capture_output=True, timeout=10)
        if result.returncode:
            raise RuntimeError('GNOME keyring: ' + (result.stderr or f'exit={result.returncode}'))
        STATUS, ERROR = 'ready', ''
        return True
    except Exception as e:
        STATUS, ERROR = 'unavailable', diagnostic(e)
        print('KEYRING_UNAVAILABLE: ' + ERROR + '; Telegram/API remain available.', flush=True)
        cleanup()
        os.environ.pop('DBUS_SESSION_BUS_ADDRESS', None)
        return False


atexit.register(cleanup)
