import json
import os
from pathlib import Path


class Config:
    def __init__(self, state):
        self.path = Path(state) / 'settings.json'
        self.data = {
            'mode': os.getenv('DEFAULT_MODE', 'web'),
            'base_url': os.getenv('API_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai/'),
            'api_key': os.getenv('API_KEY', ''),
            'api_model': os.getenv('API_MODEL', ''),
            'web_model': os.getenv('GEMINI_WEB_MODEL', 'Gemini 4 Argon'),
            'web_strength': os.getenv('GEMINI_WEB_STRENGTH', 'auto'),
            'max_steps': max(1, min(200, int(os.getenv('MAX_TOOL_STEPS', '40')))),
        }
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

        self.data['max_steps'] = max(1, min(200, int(self.data['max_steps'])))
        # Remove obsolete CLI settings, retain API configuration.
        self.data.pop('account_model', None)
        if self.data.get('mode') in ('agy', 'account'):
            self.data['mode'] = 'web'
        if self.data.get('mode') not in ('api', 'web'):
            self.data['mode'] = 'web'

    def save(self):
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(self.data, f)
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)
