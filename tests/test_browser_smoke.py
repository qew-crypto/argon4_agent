"""Optional real Chromium tests, local HTML only, no Google or Telegram login.
Run: RUN_BROWSER_TESTS=1 CHROMIUM_EXECUTABLE=/usr/bin/chromium python -m unittest discover -s tests -p test_browser_smoke.py -v
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from agent.config import Config
from agent.gemini_web import GeminiWeb, GeminiWebError
from agent.tools import Tools

HTML = '''<!doctype html><html><body>
<button id="model" aria-label="model">Gemini 4 Argon</button>
<div id="modelmenu" hidden><button role="menuitemradio" onclick="document.querySelector('#model').textContent=this.textContent;this.parentElement.hidden=true">Gemini 4 Argon</button><button role="menuitemradio">Other model</button></div>
<button id="strength" aria-label="reasoning">High</button>
<div id="strengthmenu" hidden><button role="menuitemradio" onclick="document.querySelector('#strength').textContent=this.textContent;this.parentElement.hidden=true">High</button></div>
<div role="textbox" contenteditable="true" id="editor"></div>
<script>
model.onclick=()=>modelmenu.hidden=false;
strength.onclick=()=>strengthmenu.hidden=false;
editor.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();let r=document.createElement('model-response');r.innerHTML='<div class="markdown">'+JSON.stringify({reply:'fixture completed'})+'</div>';document.body.append(r);}};
</script></body></html>'''


@unittest.skipUnless(os.getenv('RUN_BROWSER_TESTS') == '1', 'opt-in local Chromium test')
class BrowserSmoke(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'STATE_DIR': str(self.root / 'state'),
            'GEMINI_MODEL_BUTTON_SELECTOR': '#model', 'GEMINI_STRENGTH_BUTTON_SELECTOR': '#strength'})
        self.env.start()
        self.pw = await async_playwright().start()
        options = {'executable_path': os.environ['CHROMIUM_EXECUTABLE']} if os.getenv('CHROMIUM_EXECUTABLE') else {}
        self.browser = await self.pw.chromium.launch(**options)
        self.page = await self.browser.new_page()
        await self.page.route('https://gemini.google.com/**', lambda route: route.fulfill(content_type='text/html', body=HTML))
        self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        self.config = Config(self.root); self.config.data.update(web_model='Gemini 4 Argon', web_strength='High')
        self.web = GeminiWeb(self.workspace, self.config)
        self.web.start = AsyncMock()
        self.web.page = self.page

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()
        self.env.stop()
        self.tmp.cleanup()

    async def test_model_strength_generation_and_no_silent_fallback(self):
        self.assertEqual(await self.web.available(), ['Gemini 4 Argon', 'Other model'])
        self.assertIn('генерация не проверена', await self.web.check())
        text = await self.web.generate('Return JSON', [{'role': 'user', 'content': 'hi'}])
        self.assertEqual(json.loads(text), {'reply': 'fixture completed'})
        self.config.data['web_model'] = 'Unavailable Argon'
        with self.assertRaises(GeminiWebError):
            await self.web.generate('Return JSON', [])
        self.assertEqual(await self.page.locator('model-response').count(), 0)

    async def test_task_browser_snapshot_fill_screenshot_and_persistence(self):
        telegram_bot = type('Bot', (), {'send_photo': AsyncMock(), 'send_document': AsyncMock()})()
        tools = Tools(self.workspace, telegram_bot, 1)
        tools.context = self.page.context; tools.page = self.page
        await self.page.set_content('<input placeholder="visible"><input type="password" value="LOCAL_SECRET"><input hidden id="hidden"><select id="sel"><option value="a">A</option><option value="b">B</option></select>')
        result = await tools.browser_action({'action': 'text'})
        self.assertNotIn('LOCAL_SECRET', json.dumps(result))
        self.assertFalse(any(x['selector'] == '#hidden' for x in result['controls']))
        first = result['controls'][0]['selector']
        await tools.browser_action({'action': 'fill', 'selector': first, 'text': 'hello'})
        await tools.browser_action({'action': 'select', 'selector': '#sel', 'text': 'b'})
        screenshot = await tools.browser_action({'action': 'screenshot', 'path': 'output/screen.png'})
        self.assertTrue(screenshot['sent'])
        telegram_bot.send_photo.assert_awaited_once()
        await tools.execute('send_file', {'path': 'output/screen.png', 'photo': True})
        telegram_bot.send_photo.assert_awaited_once()  # no duplicate screenshot
        self.assertTrue((self.workspace / 'output/screen.png').is_file())
        self.assertEqual(await self.page.locator(first).input_value(), 'hello')
        self.assertEqual(await self.page.locator('#sel').input_value(), 'b')

    async def test_one_fio_secret_fills_three_real_inputs(self):
        from agent.secrets import SecretVault
        vault = SecretVault(); key = vault.add('Иванов Иван Иванович')
        try:
            tools = Tools(self.workspace, None, 1, vault=vault)
            tools.context = self.page.context; tools.page = self.page
            await self.page.set_content('<input id="surname"><input id="name"><input id="patronymic">')
            result = await tools.browser_action({'action': 'fill_secret_form', 'secret_id': key, 'format': 'fio',
                'fields': [{'selector': '#surname', 'part': 'surname'}, {'selector': '#name', 'part': 'given_name'},
                           {'selector': '#patronymic', 'part': 'patronymic'}]})
            for selector, value in (('#surname', 'Иванов'), ('#name', 'Иван'), ('#patronymic', 'Иванович')):
                self.assertEqual(await self.page.locator(selector).input_value(), value)
                self.assertNotIn(value, result)
            with self.assertRaises(ValueError):
                vault.resolve(key)
        finally:
            vault.clear()

    async def test_labeled_fields_custom_editor_and_overlaid_checkbox(self):
        tools = Tools(self.workspace, None, 1)
        tools.context = self.page.context; tools.page = self.page
        await self.page.set_content('''<label for="surname">Фамилия</label><input id="surname" required>
        <div id="editor" contenteditable="true" role="textbox"></div>
        <div style="position:relative;width:200px;height:40px">
        <input type="checkbox" id="agree" style="position:absolute;left:0;top:0">
        <label for="agree" style="position:absolute;left:0;top:0;width:200px;height:40px;background:lightgray">Согласен</label></div>''')
        observation = await tools.browser_action({'action': 'text'})
        field = next(c for c in observation['controls'] if c['selector'] == '#surname')
        self.assertEqual(field['label'], 'Фамилия')
        self.assertTrue(field['editable']); self.assertTrue(field['required'])
        await tools.browser_action({'action': 'fill', 'selector': '#surname', 'text': 'Иванов'})
        self.assertEqual(await self.page.locator('#surname').input_value(), 'Иванов')
        await tools.browser_action({'action': 'focus', 'selector': '#editor'})
        await tools.browser_action({'action': 'type', 'selector': '#editor', 'text': 'hello'})
        self.assertEqual(await self.page.locator('#editor').inner_text(), 'hello')
        await tools.browser_action({'action': 'check', 'selector': '#agree'})
        self.assertTrue(await self.page.locator('#agree').is_checked())
        await tools.browser_action({'action': 'uncheck', 'selector': '#agree'})
        self.assertFalse(await self.page.locator('#agree').is_checked())
