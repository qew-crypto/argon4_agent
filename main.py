"""Bothost entrypoint. Persistent files belong only in /app/data."""
import os
from pathlib import Path


def prepare():
    data = Path(os.getenv('DATA_DIR', '/app/data')).resolve()
    data.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('WORKSPACE_DIR', str(data / 'workspace'))
    os.environ.setdefault('STATE_DIR', str(data / 'state'))
    os.environ['HOME'] = str(data / 'home')
    os.environ['TELEGRAM_BOT_TOKEN'] = (os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN') or '')
    paths = [data, Path(os.environ['HOME']), Path(os.environ['STATE_DIR']), Path(os.environ['WORKSPACE_DIR'])]
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)
        p.chmod(0o700)
        if os.getuid() == 0:
            os.chown(p, 10001, 10001)
    if os.getuid() == 0:
        # Container startup only; never execute agent tools as root.
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    start_keyring()


def start_keyring():
    # Native Linux Secret Service for official CLI account profiles.
    import subprocess
    import secrets
    if not os.getenv('DBUS_SESSION_BUS_ADDRESS'):
        bus = subprocess.run(['dbus-daemon', '--session', '--fork', '--print-address=1'],
            check=True, capture_output=True, text=True, timeout=10)
        os.environ['DBUS_SESSION_BUS_ADDRESS'] = bus.stdout.strip()
    key = Path(os.environ['HOME']) / '.keyring-password'
    if not key.exists():
        fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(48))
    subprocess.run(['gnome-keyring-daemon', '--unlock', '--components=secrets'],
        input=key.read_text(), text=True, capture_output=True, check=True, timeout=10)


if __name__ == '__main__':
    prepare()
    from agent.bot import main
    main()
