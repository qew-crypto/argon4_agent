import json
import os
from pathlib import Path


class Config:
    def __init__(self, state):
        self.path = Path(state) / 'settings.json'
        self.data = {
            'mode': os.getenv('DEFAULT_MODE', 'api'),
            'base_url': os.getenv('API_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai/'),
            'api_key': os.getenv('API_KEY', ''),
            'api_model': os.getenv('API_MODEL', ''),
            'account_model': os.getenv('ACCOUNT_MODEL', ''),
        }
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    def save(self):
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(self.data, f)
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)
