import asyncio
import os
import signal
from pathlib import Path


def safe_path(root: Path, value: str) -> Path:
    root = root.resolve()
    p = (root / value).resolve()
    if not p.is_relative_to(root):
        raise ValueError('Путь должен находиться внутри рабочей папки')
    return p


async def process(argv, *, cwd, timeout, env=None, limit=18000, merge_stderr=True):
    # File-backed output prevents unbounded subprocess output from consuming RAM.
    import tempfile
    with tempfile.TemporaryFile() as stream, tempfile.TemporaryFile() as diagnostics:
        p = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env, stdout=stream,
            stderr=asyncio.subprocess.STDOUT if merge_stderr else diagnostics, start_new_session=True)
        try:
            await asyncio.wait_for(p.wait(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await p.wait()
            raise
        stream.seek(0)
        result = stream.read(limit).decode('utf-8', errors='replace')
        if stream.read(1):
            result += '\n[вывод обрезан]'
        return p.returncode, result
