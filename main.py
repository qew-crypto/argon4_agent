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
    if os.getuid() == 0:
        # Keep container tools unprivileged with a registered Unix identity.
        import pwd
        import subprocess
        try:
            pwd.getpwuid(10001)
        except KeyError:
            subprocess.run(['useradd', '-u', '10001', '-M', '-d', os.environ['HOME'],
                            '-s', '/usr/sbin/nologin', 'agent'], check=True, capture_output=True)
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


if __name__ == '__main__':
    prepare()
    from agent.bot import main
    main()
