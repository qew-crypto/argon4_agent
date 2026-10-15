import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from agent.api_protocol import ProtocolError, from_message, from_text
from agent.provider import Provider
from agent.config import Config


def message(name, arguments):
    return SimpleNamespace(content=None, tool_calls=[SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))])


class ProtocolTests(unittest.TestCase):
    def test_plain_and_legacy_reply_are_progress_not_final(self):
        for raw in ('Сейчас открою сайт', '{"reply":"Открываю"}'):
            self.assertEqual(from_text(raw)['status'], 'progress')

    def test_tool_and_explicit_completion(self):
        self.assertEqual(from_message(message('browser', {'action': 'open', 'url': 'https://example.com'})),
                         {'tool': 'browser', 'args': {'action': 'open', 'url': 'https://example.com'}})
        done = from_message(message('finish_task', {'status': 'completed', 'reply': 'готово', 'evidence': 'tool result sent=true'}))
        self.assertEqual(done['status'], 'completed')
        progress = from_message(message('report_progress', {'text': 'Продолжаю'}))
        self.assertEqual(progress['status'], 'progress')

    def test_invalid_calls_and_unverified_completion_not_executed(self):
        for raw in ('{"tool":', '[1]', '{"tool":"unknown","args":{}}', '{"reply":"done","status":"completed"}'):
            with self.assertRaises(ProtocolError):
                from_text(raw)
        with self.assertRaises(ProtocolError):
            from_message(SimpleNamespace(content='', tool_calls=[1, 2]))


class APITests(unittest.IsolatedAsyncioTestCase):
    def response(self, msg, reason='tool_calls'):
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=reason)])

    async def test_native_browser_tools_used(self):
        with tempfile.TemporaryDirectory() as t:
            config = Config(t); config.data.update(api_key='LOCAL_TEST_ONLY', api_model='fixture')
            provider = Provider(Path(t), config)
            create = AsyncMock(return_value=self.response(message('browser', {'action': 'screenshot'})))
            provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            action = await provider.next('api', [])
            self.assertEqual(action['args']['action'], 'screenshot')
            self.assertIn('tools', create.await_args.kwargs)

    async def test_clear_unsupported_native_calls_fall_back_only_once(self):
        class Unsupported(Exception):
            status_code = 400
        with tempfile.TemporaryDirectory() as t:
            config = Config(t); config.data.update(api_key='LOCAL_TEST_ONLY', api_model='fixture')
            provider = Provider(Path(t), config)
            success = self.response(SimpleNamespace(content='{"tool":"list_files","args":{}}', tool_calls=[]), 'stop')
            create = AsyncMock(side_effect=[Unsupported('tools not supported'), success, success])
            provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            await provider.next('api', [])
            await provider.next('api', [])
            self.assertEqual(create.await_count, 3)
            self.assertIn('tools', create.await_args_list[0].kwargs)
            self.assertNotIn('tools', create.await_args_list[1].kwargs)
            self.assertNotIn('tools', create.await_args_list[2].kwargs)

    async def test_truncated_json_not_used(self):
        with tempfile.TemporaryDirectory() as t:
            config = Config(t); config.data.update(api_key='LOCAL_TEST_ONLY', api_model='fixture')
            provider = Provider(Path(t), config)
            create = AsyncMock(return_value=self.response(SimpleNamespace(content='{"tool":', tool_calls=[]), 'length'))
            provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with self.assertRaises(ProtocolError):
                await provider.next('api', [])
