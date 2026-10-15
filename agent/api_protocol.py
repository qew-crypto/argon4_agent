"""OpenAI-compatible native calls, with an explicit JSON protocol fallback."""
import json


class ProtocolError(ValueError):
    pass


def function(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}}}


STRING = {'type': 'string'}
TOOLS = [
    function('browser', 'Operate the existing task browser. screenshot automatically sends its image to the owner; do not send twice.', {
        'action': {'type': 'string', 'enum': ['open', 'text', 'click', 'click_at', 'focus', 'type', 'check', 'uncheck', 'hover', 'fill', 'fill_secret', 'fill_secret_form', 'select', 'press', 'scroll', 'wait', 'screenshot', 'download', 'upload']},
        **{key: STRING for key in ('url', 'selector', 'text', 'path', 'secret_id')},
        'x': {'type': 'number'}, 'y': {'type': 'number'},
        'format': {'type': 'string', 'enum': ['fio', 'json']},
        'fields': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'selector': STRING, 'part': {'type': 'string', 'enum': ['surname', 'given_name', 'patronymic', 'full']},
            'key': STRING}, 'required': ['selector'], 'additionalProperties': False}}}, ['action']),
    function('shell', 'Run a shell command; permission policy enforced by host.', {'command': STRING}, ['command']),
    function('read_file', 'Read workspace text/PDF.', {'path': STRING}, ['path']),
    function('write_file', 'Write UTF-8 workspace file.', {'path': STRING, 'text': STRING}, ['path', 'text']),
    function('list_files', 'List workspace files.', {'path': STRING}),
    function('zip_files', 'Create workspace archive.', {'paths': {'type': 'array', 'items': STRING}, 'output': STRING}, ['paths', 'output']),
    function('send_file', 'Send existing workspace file to owner. Screenshots already auto-send; no duplicate needed.', {'path': STRING, 'photo': {'type': 'boolean'}}, ['path']),
    function('report_progress', 'Optional short progress update. This does NOT end the task; continue with tools.', {'text': STRING}, ['text']),
    function('finish_task', 'End ONLY when completed and verified, genuinely blocked, or missing necessary user input. A plan/promise is not completion.', {
        'status': {'type': 'string', 'enum': ['completed', 'needs_input', 'blocked']},
        'reply': STRING, 'evidence': STRING}, ['status', 'reply', 'evidence']),
]
TOOL_NAMES = {t['function']['name'] for t in TOOLS}


def validate_action(obj):
    if not isinstance(obj, dict):
        raise ProtocolError('Expected action object')
    if isinstance(obj.get('tool'), str) and isinstance(obj.get('args'), dict):
        if obj['tool'] not in TOOL_NAMES - {'finish_task', 'report_progress'}:
            raise ProtocolError('Unknown tool')
        return {'tool': obj['tool'], 'args': obj['args']}
    if isinstance(obj.get('reply'), str):
        status = obj.get('status', 'progress')
        if status not in ('progress', 'completed', 'needs_input', 'blocked'):
            raise ProtocolError('Invalid task status')
        if status == 'completed' and not str(obj.get('evidence', '')).strip():
            raise ProtocolError('Completion requires observed evidence or explanation for non-tool tasks')
        # Do not silently stop on an old {reply: "I will open..."} response.
        return {'reply': obj['reply'], 'status': status, 'evidence': str(obj.get('evidence', ''))[:2000]}
    raise ProtocolError('Invalid action')


def from_text(raw):
    text = raw.strip()
    if text.startswith('```') and '\n' in text:
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        return validate_action(json.loads(text))
    except json.JSONDecodeError:
        # Plain-language planning is a progress update, NEVER implicit completion.
        if not text or text.startswith(('{', '[')):
            raise ProtocolError('Incomplete or invalid JSON') from None
        return {'reply': text, 'status': 'progress'}


def from_message(message):
    calls = getattr(message, 'tool_calls', None) or []
    if not calls:
        return from_text(message.content or '')
    if len(calls) != 1:
        raise ProtocolError('Exactly one action per step required')
    call = calls[0].function
    try:
        args = json.loads(call.arguments)
    except (TypeError, ValueError):
        raise ProtocolError('Invalid tool JSON') from None
    if not isinstance(args, dict):
        raise ProtocolError('Invalid tool arguments')
    if call.name == 'finish_task':
        if args.get('status') not in ('completed', 'needs_input', 'blocked') or not isinstance(args.get('evidence'), str):
            raise ProtocolError('Invalid completion')
        return validate_action(args)
    if call.name == 'report_progress':
        if not isinstance(args.get('text'), str):
            raise ProtocolError('Invalid progress')
        return {'reply': args['text'], 'status': 'progress'}
    return validate_action({'tool': call.name, 'args': args})
