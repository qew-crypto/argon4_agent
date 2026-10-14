"""Antigravity's official remote OAuth flow, controlled via a short-lived HTTPS form.
Never expose the PTY or accept arbitrary terminal input through the web endpoint.
"""
import asyncio
import fcntl
import os
import pty
import re
import secrets
import signal
import struct
import subprocess
import termios
import time
from urllib.parse import urlsplit
from .common import capture_process
from .diagnostics import known_failure, decode_envelope, diagnosis

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
LINK = re.compile(r'\x1b\]8;[^;]*;(https?://[^\x07\x1b]+)')


def official_link(raw):
    candidates = LINK.findall(raw) + re.findall(r'https://accounts\.google\.com/[^\s\x07\x1b]+', raw)
    for url in candidates:
        parsed = urlsplit(url)
        if parsed.scheme == 'https' and parsed.hostname == 'accounts.google.com' and not parsed.username:
            return url
    return None


class Login:
    def __init__(self, home, root, on_success):
        self.home, self.root, self.on_success = home, root, on_success
        self.token = None
        self.stage = 'idle'
        self.url = None
        self.deadline = 0
        self.proc = None
        self.fd = None
        self.task = None
        self.verify_task = None
        self.buffer = ''
        self.selected = False
        self.submitted = False
        self.diagnostic = ''
        self.oauth_failure = None
        self.lock = asyncio.Lock()

    async def start(self):
        async with self.lock:
            await self.stop()
            self.token = secrets.token_urlsafe(32)
            self.stage = 'starting'
            self.url = None
            self.diagnostic = ''
            self.oauth_failure = None
            self.deadline = time.monotonic() + 600
            self.buffer = ''
            self.selected = self.submitted = False
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 500, 0, 0))
            env = {k: v for k, v in os.environ.items() if not any(
                x in k.upper() for x in ('TOKEN', 'KEY', 'SECRET', 'PASSWORD'))}
            # Force the documented remote manual-code flow, never a localhost callback.
            env.update(HOME=str(self.home), SSH_CONNECTION='127.0.0.1 1 127.0.0.1 22',
                       TERM='xterm-256color', NO_COLOR='1')
            try:
                self.proc = subprocess.Popen(['agy'], stdin=slave, stdout=slave, stderr=slave,
                    cwd=self.root, env=env, start_new_session=True)
            except Exception:
                os.close(master)
                os.close(slave)
                self.stage = 'error'
                raise
            os.close(slave)
            os.set_blocking(master, False)
            self.fd = master
            self.task = asyncio.create_task(self.monitor())
            return self.token

    def valid(self, token):
        return bool(self.token and time.monotonic() < self.deadline and
                    secrets.compare_digest(token, self.token))

    def active(self):
        return self.stage in ('starting', 'waiting_code', 'checking') and self.valid(self.token or '')

    def status(self):
        # No raw CLI output, authorization code, credentials or logs leave this object.
        return {'stage': self.stage, 'google_url': self.url if self.stage == 'waiting_code' else None, 'diagnostic': self.diagnostic}

    async def submit(self, code):
        async with self.lock:
            if self.stage != 'waiting_code' or self.submitted:
                raise ValueError('Нет ожидающего входа')
            if not code or len(code) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in code):
                raise ValueError('Код должен быть одной строкой ASCII')
            self.submitted = True
            self.stage = 'checking'
            os.write(self.fd, code.encode() + b'\r')
            # PTY echoes could contain the code; discard them and never persist the buffer.
            self.buffer = ''
            self.verify_task = asyncio.create_task(self.verify())

    async def monitor(self):
        try:
            while self.proc and self.proc.poll() is None:
                if time.monotonic() >= self.deadline:
                    self.stage = 'expired'
                    break
                try:
                    chunk = os.read(self.fd, 65536)
                except BlockingIOError:
                    await asyncio.sleep(.1)
                    continue
                except OSError:
                    break
                if b'\x1b]11;?' in chunk:
                    os.write(self.fd, b'\x1b]11;rgb:0000/0000/0000\x07')
                if b'\x1b[6n' in chunk:
                    os.write(self.fd, b'\x1b[1;1R')
                if self.submitted:
                    # Inspect only for fixed error categories; do not keep/return echoed code.
                    category = known_failure(ANSI.sub('', chunk.decode(errors='replace')))
                    if category in ('OAUTH_CODE_REJECTED', 'KEYRING_ERROR') and not self.oauth_failure:
                        self.oauth_failure = category
                    continue
                self.buffer = (self.buffer + chunk.decode(errors='replace'))[-120000:]
                plain = ANSI.sub('', self.buffer)
                if not self.selected and 'Google OAuth' in plain and 'Select login method' in plain:
                    os.write(self.fd, b'\r')
                    self.selected = True
                url = official_link(self.buffer)
                if url and ('authorization code' in plain.lower() or 'paste it below' in plain.lower()):
                    self.url = url
                    self.stage = 'waiting_code'
                elif not self.url and ('Welcome back' in plain or 'signed in as' in plain.lower()):
                    self.stage = 'already_signed_in'
                    break
            if self.stage == 'starting':
                self.stage = 'error'
        except asyncio.CancelledError:
            pass
        finally:
            if self.stage in ('expired', 'error', 'already_signed_in'):
                self.terminate()

    async def verify(self, delay=15):
        # Separate stderr is classified internally, never forwarded as raw text.
        env = {k: v for k, v in os.environ.items() if not any(
            x in k.upper() for x in ('TOKEN', 'KEY', 'SECRET', 'PASSWORD'))}
        env['HOME'] = str(self.home)
        self.stage = 'checking'
        self.diagnostic = ''
        try:
            if delay:
                await asyncio.sleep(delay)
            if self.oauth_failure:
                self.stage = 'check_failed'
                self.diagnostic = diagnosis(self.oauth_failure, phase='oauth')
                return
            code, text, stderr = await capture_process(['agy', '-p', 'Reply exactly OK. Do not use tools.',
                '--mode', 'plan', '--disable-slash-commands', '--output-format', 'json',
                '--print-timeout', '75s'],
                cwd=self.root, timeout=90, env=env, limit=30000)
            data = decode_envelope(text)
            if code == 0 and data and data.get('status') == 'SUCCESS' and isinstance(data.get('response'), str) and data['response']:
                self.stage = 'success'
                self.diagnostic = diagnosis('OK')
                await self.on_success()
            else:
                category = known_failure(stderr + '\n' + text)
                if not category:
                    category = 'CLI_OUTPUT_FORMAT' if not data else 'UNKNOWN_CLI_ERROR'
                self.stage = 'check_failed'
                self.diagnostic = diagnosis(category, exit_code=code, phase='model')
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.stage = 'check_failed'
            self.diagnostic = diagnosis('TIMEOUT', phase='model')
        except FileNotFoundError:
            self.stage = 'check_failed'
            self.diagnostic = diagnosis('CLI_NOT_FOUND', phase='startup')
        except Exception:
            self.stage = 'check_failed'
            self.diagnostic = diagnosis('INTERNAL_ERROR')
        finally:
            self.terminate()
            if self.diagnostic:
                print('LOGIN_CHECK: ' + self.diagnostic, flush=True)

    def terminate(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait(timeout=5)
        self.proc = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.buffer = ''

    async def stop(self):
        tasks = [t for t in (self.task, self.verify_task) if t and not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.task = self.verify_task = None
        self.terminate()
        self.token = None
        self.stage = 'idle'
