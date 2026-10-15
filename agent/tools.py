import os
import zipfile
from pathlib import Path
from .common import safe_path, process


class Tools:
    def __init__(self, root, bot, chat_id, vault=None):
        self.root, self.bot, self.chat_id = root, bot, chat_id
        self.vault = vault
        self.pw = self.browser = self.context = self.page = None

    async def close(self):
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.pw:
                await self.pw.stop()
            self.pw = self.browser = self.context = self.page = None

    async def execute(self, name, a):
        if name == 'shell':
            # Do not inherit provider or Telegram credentials into shell subprocesses.
            env = {k: v for k, v in os.environ.items() if not any(
                s in k.upper() for s in ('TOKEN', 'KEY', 'SECRET', 'PASSWORD', 'COOKIE', 'AUTH'))}
            code, text = await process(['/bin/bash', '-lc', a['command']],
                cwd=self.root, timeout=int(os.getenv('SHELL_TIMEOUT', '90')), env=env)
            return f'exit={code}\n{text}'
        if name == 'list_files':
            p = safe_path(self.root, a.get('path', '.'))
            return '\n'.join(str(x.relative_to(self.root)) + ('/' if x.is_dir() else '')
                             for x in sorted(p.iterdir())[:250])
        if name == 'read_file':
            p = safe_path(self.root, a['path'])
            if p.suffix.lower() == '.pdf':
                from pypdf import PdfReader
                if p.stat().st_size > 20 * 1024**2:
                    raise ValueError('PDF больше 20 МБ')
                return '\n'.join((x.extract_text() or '')[:10000]
                                 for x in PdfReader(p).pages[:20])[:30000]
            with p.open('rb') as f:
                return f.read(30000).decode('utf-8', errors='replace')
        if name == 'write_file':
            p = safe_path(self.root, a['path'])
            text = a['text']
            if len(text.encode()) > 2 * 1024**2:
                raise ValueError('Запись больше 2 МБ; используй shell')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding='utf-8')
            return f'Создан {p.relative_to(self.root)}'
        if name == 'zip_files':
            output = safe_path(self.root, a['output'])
            inputs = [safe_path(self.root, x) for x in a['paths']]
            if not inputs:
                raise ValueError('Нет файлов для архива')
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
                for p in inputs:
                    files = sorted(p.rglob('*')) if p.is_dir() else [p]
                    for f in files:
                        resolved = safe_path(self.root, str(f))
                        if resolved.is_file() and resolved != output:
                            z.write(resolved, resolved.relative_to(self.root))
            return f'Создан {output.relative_to(self.root)}'
        if name == 'send_file':
            p = safe_path(self.root, a['path'])
            if p.stat().st_size > int(os.getenv('MAX_UPLOAD_MB', '49')) * 1024**2:
                raise ValueError('Файл превышает настроенный лимит Telegram')
            with p.open('rb') as f:
                if a.get('photo') and p.stat().st_size <= 9 * 1024**2:
                    await self.bot.send_photo(self.chat_id, photo=f,
                                              read_timeout=120, write_timeout=120)
                else:
                    await self.bot.send_document(self.chat_id, document=f, filename=p.name,
                                                 read_timeout=120, write_timeout=120)
            return 'Файл отправлен'
        if name == 'browser':
            return await self.browser_action(a)
        raise ValueError(f'Неизвестный инструмент: {name}')

    async def browser_action(self, a):
        if self.page is not None and self.page.is_closed():
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        if self.context is None:
            from playwright.async_api import async_playwright
            self.pw = await async_playwright().start()
            profile = Path(os.getenv('STATE_DIR', str(self.root.parent / 'state'))) / 'task-browser-profile'
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            profile.chmod(0o700)
            options = {'executable_path': os.environ['CHROMIUM_EXECUTABLE']} if os.getenv('CHROMIUM_EXECUTABLE') else {}
            self.context = await self.pw.chromium.launch_persistent_context(
                str(profile), headless=True, accept_downloads=True, **options)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.page.set_default_timeout(20000)
        action = a['action']
        if action == 'open':
            if not a['url'].startswith(('https://', 'http://')):
                raise ValueError('Только HTTP/HTTPS')
            await self.page.goto(a['url'], wait_until='domcontentloaded', timeout=45000)
        elif action == 'click':
            await self.page.locator(a['selector']).click()
        elif action == 'select':
            await self.page.locator(a['selector']).select_option(a['text'])
        elif action == 'press':
            await self.page.locator(a['selector']).press(a['text'])
        elif action == 'scroll':
            await self.page.mouse.wheel(0, max(-5000, min(5000, int(a.get('text', '600')))))
        elif action == 'wait':
            await self.page.locator(a['selector']).wait_for(state='visible', timeout=20000)
        elif action == 'fill_secret':
            if self.vault is None:
                raise ValueError('Хранилище секретов недоступно.')
            key, value = self.vault.resolve(a.get('secret_id', 'latest'))
            # Return a fixed message. Do NOT snapshot a page that could echo a secret.
            try:
                await self.page.locator(a['selector']).fill(value)
            except Exception:
                raise ValueError('Не удалось заполнить секрет; значение и диагностика скрыты.') from None
            self.vault.consume(key)
            return 'Одноразовый секрет введён в указанное поле. Значение скрыто.'
        elif action == 'fill':
            await self.page.locator(a['selector']).fill(a['text'])
        elif action == 'download':
            p = safe_path(self.root, a.get('path', 'downloads/file.bin'))
            p.parent.mkdir(parents=True, exist_ok=True)
            async with self.page.expect_download() as info:
                await self.page.locator(a['selector']).click()
            download = await info.value
            await download.save_as(str(p))
            return f'Скачано: {p.relative_to(self.root)}'
        elif action == 'upload':
            p = safe_path(self.root, a['path'])
            await self.page.locator(a['selector']).set_input_files(str(p))
        elif action == 'screenshot':
            p = safe_path(self.root, a.get('path', 'output/browser.png'))
            p.parent.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(p), full_page=True)
            return f'Скриншот: {p.relative_to(self.root)}'
        elif action != 'text':
            raise ValueError('Неизвестное действие браузера')
        # Include control metadata, never password values.
        controls = await self.page.locator('input, button, a, textarea, select, [role="button"], [contenteditable="true"]').evaluate_all(
            r"""els => els.filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden').slice(0,100).map(e => {
              let selector = null;
              const unique = s => { try { return document.querySelectorAll(s).length === 1; } catch { return false; } };
              const esc = x => CSS.escape(x);
              if (e.id && unique('#'+esc(e.id))) selector = '#'+esc(e.id);
              for (const attr of ['data-testid','name','aria-label','placeholder','href']) {
                if (selector) break;
                const v = e.getAttribute(attr);
                if (v) { const s = e.tagName.toLowerCase()+'['+attr+'="'+esc(v)+'"]'; if(unique(s)) selector = s; }
              }
              if (!selector) {
                let node=e, parts=[];
                while(node && node.nodeType===1 && node!==document.documentElement) {
                  let i=1, sib=node; while((sib=sib.previousElementSibling)) if(sib.tagName===node.tagName) i++;
                  parts.unshift(node.tagName.toLowerCase()+':nth-of-type('+i+')');
                  const s=parts.join(' > '); if(unique(s)) {selector=s;break;} node=node.parentElement;
                }
              }
              return {tag:e.tagName, selector, type:e.getAttribute('type'), disabled:!!e.disabled,
                text:(e.innerText || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').slice(0,120),
                checked:['checkbox','radio'].includes(e.type)?e.checked:undefined,
                options:e.tagName==='SELECT'?Array.from(e.options).slice(0,40).map(o=>({text:o.text,value:o.value})):undefined};
            })""")
        return {'url': self.page.url,
                'text': (await self.page.locator('body').inner_text())[:16000],
                'controls': controls}
