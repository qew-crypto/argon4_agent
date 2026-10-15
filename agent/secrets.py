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
        self._items[key] = {'value': value, 'expires': time.monotonic() + self.ttl, 'used': False, 'masks': []}
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

    def mask_parts(self, key, values):
        if key in self._items:
            self._items[key]['masks'] = list(dict.fromkeys(v for v in values if v))

    def consume(self, key):
        # Keep value ONLY for output redaction until expiry; cannot be filled twice.
        if key in self._items:
            self._items[key]['used'] = True

    def redact(self, text):
        self.purge()
        values = [value for item in self._items.values() for value in [item['value'], *item.get('masks', [])]]
        for value in sorted(set(values), key=len, reverse=True):
            text = text.replace(value, '[секрет скрыт]')
        return text

    def clear(self):
        for key in list(self._items):
            self._forget(key)


def field_values(value, fields, format='fio'):
    """Local-only secret transformation. Never returns values to the model.

    No arbitrary code, regex, template evaluation, or guessed compound names.
    """
    import json
    if not isinstance(fields, list) or not 1 <= len(fields) <= 20:
        raise ValueError('Нужен список от 1 до 20 полей.')
    if any(not isinstance(f, dict) or not isinstance(f.get('selector'), str) or not f['selector'] for f in fields):
        raise ValueError('У каждого поля нужен наблюдаемый селектор.')
    selectors = [f['selector'] for f in fields]
    if len(set(selectors)) != len(selectors):
        raise ValueError('Селекторы полей не должны повторяться.')
    if format == 'fio':
        if value.lstrip().startswith('{'):
            try:
                raw = json.loads(value)
            except ValueError:
                raise ValueError('Секрет ФИО содержит некорректный JSON; значение скрыто.') from None
            if not isinstance(raw, dict):
                raise ValueError('Для структурированного ФИО нужен JSON-объект.')
            aliases = {'surname': ('surname', 'last_name', 'фамилия'),
                       'given_name': ('given_name', 'first_name', 'имя'),
                       'patronymic': ('patronymic', 'middle_name', 'отчество')}
            parts = {}
            for name, keys in aliases.items():
                candidates = [raw[k] for k in keys if k in raw]
                if len(candidates) > 1 or candidates and not isinstance(candidates[0], str):
                    raise ValueError('Неоднозначные части ФИО; укажи каждую часть одним строковым ключом.')
                if candidates:
                    parts[name] = candidates[0]
        else:
            tokens = value.split()
            if len(tokens) not in (2, 3):
                raise ValueError('ФИО неоднозначно: ожидаю фамилию, имя и необязательное отчество (2–3 слова). Для составных частей передай JSON с ключами фамилия, имя, отчество. Значение скрыто.')
            parts = dict(zip(('surname', 'given_name', 'patronymic'), tokens))
        parts['full'] = value
        keys = [f.get('part') for f in fields]
    elif format == 'json':
        try:
            parts = json.loads(value)
        except ValueError:
            raise ValueError('Секрет должен быть JSON-объектом со строковыми значениями; содержимое скрыто.') from None
        if not isinstance(parts, dict):
            raise ValueError('Нужен JSON-объект.')
        keys = [f.get('key') for f in fields]
    else:
        raise ValueError('Поддерживаются только форматы fio и json.')
    result = []
    for key in keys:
        if not isinstance(key, str) or key not in parts or not isinstance(parts[key], str):
            raise ValueError('В секрете нет нужной строковой части. Ничего не угадываю; укажи формат/ключи или обнови секрет.')
        result.append(parts[key])
    return result
