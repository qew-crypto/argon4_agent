import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from agent.completion import active_workflow_reason
from agent.api_protocol import ProtocolError
from agent.provider import Provider
from agent.config import Config


class GateTests(unittest.TestCase):
    def test_question_one_is_not_the_whole_test(self):
        snapshot = {'text': 'Вопрос 1: 3x − 7 = 8', 'controls': [{'text': 'Сохранить ответ', 'disabled': False}]}
        self.assertIsNotNone(active_workflow_reason('Пройди весь тест и отправь ответы', snapshot))
        self.assertIsNone(active_workflow_reason('Реши только первый вопрос', snapshot))
        self.assertIsNone(active_workflow_reason('Пройди тест', {'text': 'Работа отправлена на проверку', 'controls': []}))


class ReviewerTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_completion_review_and_forced_action(self):
        with tempfile.TemporaryDirectory() as t:
            config = Config(t); config.data.update(api_key='LOCAL_TEST_ONLY', api_model='fixture')
            provider = Provider(Path(t), config)
            review = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
                content=json.dumps({'approved': False, 'reason': 'Only question 1 solved'})))])
            call = SimpleNamespace(function=SimpleNamespace(name='browser', arguments='{"action":"text"}'))
            step = SimpleNamespace(choices=[SimpleNamespace(finish_reason='tool_calls', message=SimpleNamespace(tool_calls=[call], content=None))])
            create = AsyncMock(side_effect=[review, step])
            provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            result = await provider.review_completion('api', 'Complete all questions', 'Question 1 done', [], None)
            self.assertFalse(result['approved'])
            await provider.next('api', [], require_action=True)
            kwargs = create.await_args.kwargs
            self.assertEqual(kwargs['tool_choice'], 'required')
            names = {tool['function']['name'] for tool in kwargs['tools']}
            self.assertNotIn('finish_task', names)
            self.assertNotIn('report_progress', names)

    async def test_invalid_reviewer_does_not_approve(self):
        with tempfile.TemporaryDirectory() as t:
            config = Config(t); config.data.update(api_model='fixture')
            provider = Provider(Path(t), config)
            reply = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='{"approved":"false","reason":"x"}'))])
            create = AsyncMock(return_value=reply)
            provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with self.assertRaises(ProtocolError):
                await provider.review_completion('api', 'goal', 'done', [], None)
