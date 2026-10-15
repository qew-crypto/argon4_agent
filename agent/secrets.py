"""Owner-only, short-lived process memory; not an E2E Telegram transport."""
import asyncio
import secrets
import time


class SecretVault:
    def __init__(self, ttl=900):
        self.ttl = ttl
        self._items = {}
        self._timers = {}

    def _forget(self, key):
        self._items.pop(key, None)
        timer = self._timers.pop(key, None)
        if timer:
            timer.cancel()

    def purge(self):
        for key, item in list(self._items.items()):
            if item['expires'] <= time.monotonic():
                self._forget(key)

    def add(self, value):
        self.purge()
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError('Нужно непустое значение до 4096 символов.')
        if len(self._items) >= 20:
            raise ValueError('Слишком много секретов; /secret clear очистит память.')
        key = 's_' + secrets.token_hex(8)
        self._items[key] = {'value': value, 'expires': time.monotonic() + self.ttl, 'used': False}
        try:
            self._timers[key] = asyncio.get_running_loop().call_later(self.ttl, self._forget, key)
        except RuntimeError:
            pass  # Synchronous unit tests use purge; bot always runs inside an event loop.
        return key

    def resolve(self, key):
        self.purge()
        if key == 'latest':
            key = next((k for k in reversed(self._items) if not self._items[k]['used']), '')
        item = self._items.get(key)
        if item is None or item['used']:
            raise ValueError('Секрет не найден, истёк или уже использован. Добавь новый через /secret.')
        return key, item['value']

    def consume(self, key):
        # Keep value ONLY for output redaction until expiry; cannot be filled twice.
        if key in self._items:
            self._items[key]['used'] = True

    def redact(self, text):
        self.purge()
        for value in sorted((item['value'] for item in self._items.values()), key=len, reverse=True):
            text = text.replace(value, '[секрет скрыт]')
        return text

    def clear(self):
        for key in list(self._items):
            self._forget(key)
