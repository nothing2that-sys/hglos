#!/usr/bin/env python3
"""doc/33 O fixture: fake codex / claude / agy CLI. Synthetic values only (U13).

Invoked as: python fake_cli.py <codex|claude|agy> <real CLI arguments...>
The runner reaches it through HGLOS_CROSS_HOST_<TOOL>_CMD (a JSON argv prefix).

Environment read by the fake:
  HGLOS_FAKE_CAPTURE   folder; every non --version invocation writes <tool>-<n>.json
  HGLOS_FAKE_SCENARIO  JSON file {"codex": {...}, "claude": {...}, "agy": {...}}
  CODEX_HOME           fake Codex home; exec writes sessions/2026/11/02/rollout-*.jsonl

No network, no real credentials, no real CLI. Output shapes follow the S0
measurements in doc/33 6.2, 6.3 and 6.8 (structure only, values synthetic).
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
VERSIONS = {'codex': 'codex-cli 0.164.0', 'claude': '2.1.316 (Claude Code)', 'agy': '1.4.12'}
ACCOUNT = 'example-acct-1'
INSTALL = 'example-install-1'
AGY_ACCOUNT = 'example-acct-2'
THREAD = '00000000-0000-4000-8000-000000000001'
SESSION = '00000000-0000-4000-8000-000000000011'
CONVERSATION = '00000000-0000-4000-8000-000000000021'
INTEREST = ('HGLOS_', 'CODEX_HOME', 'ANTHROPIC_', 'OPENAI_', 'CODEX_API_KEY', 'GEMINI_', 'GOOGLE_')


def scenario(tool):
    path = os.environ.get('HGLOS_FAKE_SCENARIO')
    if not path or not Path(path).is_file():
        return {}
    return json.loads(Path(path).read_text(encoding='utf-8')).get(tool, {})


def opt(args, *names):
    for i, arg in enumerate(args[:-1]):
        if arg in names:
            return args[i + 1]
    return None


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_text(path):
    try:
        return Path(path).read_text(encoding='utf-8')
    except (OSError, TypeError):
        return None


def capture(tool, record):
    folder = Path(os.environ['HGLOS_FAKE_CAPTURE'])
    folder.mkdir(parents=True, exist_ok=True)
    n = len(list(folder.glob(f'{tool}-*.json'))) + 1
    path = folder / f'{tool}-{n}.json'
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


def base_record(tool, args, stdin):
    env = {k: v for k, v in os.environ.items() if k.startswith(INTEREST)}
    return {
        'tool': tool, 'argv': args, 'cwd': os.getcwd(), 'pid': os.getpid(),
        'env_keys': sorted(os.environ), 'env': env,
        'stdin_len': None if stdin is None else len(stdin),
        'stdin_sha256': None if stdin is None else sha(stdin),
        'stdin_text': None if stdin is None else stdin.decode('utf-8', 'replace'),
    }


def spawn_and_sleep(sc, record, tool):
    if sc.get('spawn_child_sleep'):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        record['child_pids'] = [child.pid]
    capture(tool, record)
    time.sleep(sc.get('sleep', 0))


def finish(sc):
    if sc.get('stderr'):
        sys.stderr.write(sc['stderr'])
    sys.stdout.flush()
    sys.stderr.flush()
    return sc.get('exit', 0)


# ---------------------------------------------------------------- codex

def codex_app_server(sc):
    capture('codex', base_record('codex', sys.argv[2:], None))
    if sc.get('app_server_fail'):
        sys.stderr.write('synthetic app-server failure\n')
        return 1
    limits = {'limitId': 'codex', 'limitName': None, 'normalModelSlug': None,
              'primary': {'usedPercent': 50, 'windowDurationMins': 10080, 'resetsAt': 1793774159},
              'secondary': None, 'credits': {'hasCredits': False, 'unlimited': False, 'balance': '0'},
              'individualLimit': None, 'spendControlReached': False,
              'planType': 'prolite', 'rateLimitReachedType': None}
    limits.update(sc.get('rate_limits', {}))
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if 'id' not in msg:
            continue
        if msg.get('method') == 'initialize':
            out = {'id': msg['id'], 'result': {
                'userAgent': 'fixture/0.164.0', 'codexHome': 'C:\\Users\\example\\.codex',
                'platformFamily': 'windows', 'platformOs': 'windows'}}
            print(json.dumps(out), flush=True)
            note = {'method': 'remoteControl/status/changed', 'params': {
                'status': 'disabled', 'serverName': 'EXAMPLE_HOST', 'installationId': INSTALL}}
            print(json.dumps(note), flush=True)
        elif msg.get('method') == 'account/rateLimits/read':
            out = {'id': msg['id'], 'result': {
                'ordinaryUsageAllowed': True, 'rateLimits': limits,
                'rateLimitsByLimitId': {'codex': limits},
                'rateLimitResetCredits': {'availableCount': 1, 'credits': [
                    {'id': 'example-credit-1', 'status': 'available', 'title': 'Full reset'}]},
                'accountId': ACCOUNT, 'rateLimitUpsell': None}}
            print(json.dumps(out), flush=True)
            return 0
        else:
            print(json.dumps({'id': msg['id'], 'error': {'code': -32601, 'message': 'unknown'}}), flush=True)
    return 0


def rollout_lines(sc, thread, model, effort, request):
    template = json.loads((HERE / 'codex_rollout_r2.json').read_text(encoding='utf-8'))['lines']
    values = {'{{THREAD_ID}}': thread, '{{MODEL}}': sc.get('rollout_model', model),
              '{{EFFORT}}': sc.get('rollout_effort', effort),
              '{{REQUEST}}': sc.get('rollout_request', request)}

    def fill(node):
        if isinstance(node, dict):
            return {k: fill(v) for k, v in node.items()}
        if isinstance(node, list):
            return [fill(v) for v in node]
        if isinstance(node, str) and node in values:
            return values[node]
        return node

    lines = [fill(line) for line in template]

    def user(text):
        return {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': text}]}}

    if sc.get('memory_off'):
        for line in lines:
            content = line.get('payload', {}).get('content') or [{}]
            if content[0].get('text', '').startswith('## Memory'):
                content[0]['text'] = '<skills_instructions>Synthetic skills text.</skills_instructions>'
    request_at = max(i for i, line in enumerate(lines)
                     if line.get('payload', {}).get('role') == 'user'
                     and line['payload']['content'][0]['text'] == values['{{REQUEST}}'])
    inserts = []
    if sc.get('environment_context_before_request'):
        inserts.append(user('<environment_context>\n  <cwd>C:\\Users\\example\\repo</cwd>\n</environment_context>'))
    if sc.get('extra_user_before_request'):
        inserts.append(user(sc['extra_user_before_request']))
    lines[request_at:request_at] = inserts
    for model_value in sc.get('extra_turn_context_models', []):
        lines.append({'type': 'turn_context', 'payload': {'model': model_value, 'effort': effort}})
    return lines


def codex_exec(sc, args):
    stdin = sys.stdin.buffer.read() if args and args[-1] == '-' else None
    record = base_record('codex', args, stdin)
    record['output_schema_text'] = file_text(opt(args, '--output-schema'))
    if sc.get('write_repo_file'):
        Path(opt(args, '--cd', '-C') or os.getcwd(), sc['write_repo_file']).write_text('escaped\n', encoding='utf-8')
    thread = sc.get('thread_id', THREAD)
    model = opt(args, '-m', '--model')
    effort = None
    for i, arg in enumerate(args[:-1]):
        if arg in ('-c', '--config') and args[i + 1].startswith('model_reasoning_effort='):
            effort = args[i + 1].split('=', 1)[1].strip('"')
    print(json.dumps({'type': 'thread.started', 'thread_id': thread}), flush=True)
    home = os.environ.get('CODEX_HOME')
    if home and not sc.get('no_rollout'):
        folder = Path(home, 'sessions', '2026', '11', '02')
        folder.mkdir(parents=True, exist_ok=True)
        name = f"rollout-2026-11-02T13-00-00-{sc.get('rollout_thread', thread)}.jsonl"
        text = (stdin or b'').decode('utf-8')
        lines = rollout_lines(sc, thread, model, effort, text)
        (folder / name).write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in lines),
                                   encoding='utf-8')
    print(json.dumps({'type': 'turn.started'}), flush=True)
    spawn_and_sleep(sc, record, 'codex')
    out = opt(args, '-o', '--output-last-message')
    if out and not sc.get('no_output'):
        Path(out).write_bytes(sc.get('output', '{"findings": []}').encode('utf-8'))
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 2}}), flush=True)
    return finish(sc)


# ---------------------------------------------------------------- claude

def claude(sc, args):
    stdin = sys.stdin.buffer.read()
    record = base_record('claude', args, stdin)
    record['json_schema_arg'] = opt(args, '--json-schema')
    spawn_and_sleep(sc, record, 'claude')
    model = opt(args, '--model')
    session = sc.get('session_id', SESSION)
    emit = lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)
    emit({'type': 'system', 'subtype': 'init', 'model': model, 'tools': ['Glob', 'Grep', 'Read'],
          'mcp_servers': [], 'permissionMode': opt(args, '--permission-mode'), 'session_id': session})
    if sc.get('auth_fail'):
        emit({'type': 'result', 'subtype': 'success', 'is_error': True, 'session_id': session,
              'result': 'Failed to authenticate: OAuth session expired and could not be refreshed',
              'total_cost_usd': 0, 'modelUsage': {}})
        return finish(dict(sc, exit=sc.get('exit', 1)))
    schema = opt(args, '--json-schema') is not None
    structured = sc.get('structured_output', {'findings': []}) if schema else None
    text = json.dumps(structured) if schema else sc.get('result', 'Synthetic context answer.\n')
    final = sc.get('final_model', model)
    models = sc.get('assistant_models', [final])
    for i, m in enumerate(models):
        content = [{'type': 'text', 'text': 'step'}]
        if schema and i == len(models) - 1:
            # S0-20: with --json-schema the final assistant event calls the StructuredOutput tool.
            content = [{'type': 'tool_use', 'name': 'StructuredOutput', 'input': structured}]
        message = {'model': m, 'content': content}
        if i == len(models) - 1 and sc.get('omit_final_model'):
            del message['model']
        emit({'type': 'assistant', 'message': message, 'session_id': session})
    result = {'type': 'result', 'subtype': sc.get('subtype', 'success'),
              'is_error': sc.get('subtype', 'success') != 'success', 'result': sc.get('result', text),
              'session_id': session, 'num_turns': 2 if schema else 1, 'total_cost_usd': 0.01,
              'usage': {'input_tokens': 2, 'output_tokens': 4},
              'modelUsage': {k: {'inputTokens': 2, 'outputTokens': 4, 'costUSD': 0.01}
                             for k in sc.get('model_usage', [model])}}
    if schema and not sc.get('omit_structured'):
        result['structured_output'] = structured
    emit(result)
    return finish(dict(sc, exit=sc.get('exit', 1 if result['is_error'] else 0)))


# ---------------------------------------------------------------- agy

def agy_usage(sc, args):
    capture('agy', base_record('agy', args, None))
    u = sc.get('usage', {})
    groups = u.get('groups', {'Gemini Models': [['weekly', 0.8], ['5h', 0.9]],
                              'Claude and GPT models': [['weekly', 0.8], ['5h', 1.0]],
                              'Gemini 4 Argon': [['weekly', 0.8]]})
    data = {'description': 'synthetic', 'groups': [
        {'name': name, 'description': 'synthetic group',
         'buckets': [{'id': f'{name}-{w}', 'name': f'{w} limit', 'description': 'synthetic bucket',
                      'window': w, 'remaining_fraction': f, 'reset_time': '2026-11-04T00:00:00Z'}
                     for w, f in buckets]}
        for name, buckets in groups.items()]}
    tokens = u.get('total_tokens', 0)
    envelope = {'conversation_id': '', 'status': 'SUCCESS', 'response': 'synthetic usage table\n',
                'duration_seconds': 0, 'num_turns': u.get('num_turns', 0),
                'usage': {'input_tokens': tokens, 'output_tokens': 0, 'thinking_tokens': 0,
                          'cache_read_tokens': 0, 'total_tokens': tokens},
                'command': {'name': u.get('command_name', 'usage'), 'data': data},
                'accountId': AGY_ACCOUNT}
    if u.get('drop_command'):
        del envelope['command']
    print(json.dumps(envelope), flush=True)
    return u.get('exit', 0)


def agy(sc, args):
    if opt(args, '-p') == '/usage':
        return agy_usage(sc, args)
    record = base_record('agy', args, None)
    record['json_schema_text'] = file_text(opt(args, '--json-schema'))
    cwd = Path.cwd()
    record['packet'] = {p.relative_to(cwd).as_posix(): sha(p.read_bytes())
                        for p in sorted(cwd.rglob('*')) if p.is_file()}
    spawn_and_sleep(sc, record, 'agy')
    if sc.get('exit'):
        return finish(sc)
    model = opt(args, '--model')
    prompt = opt(args, '-p') or ''
    log = opt(args, '--log-file')
    if log and not sc.get('no_log'):
        stamp = 'I1102 13:00:00.{:06d}    1001 '
        rows = [f'resolver.go:85] Model ID {model} not in local config, defaulting to CCPA',
                'resolver.go:111] Model resolved via default',
                f'printmode.go:206] Print mode: starting (promptLength={len(prompt)}, model="{model}", conversationID="")',
                f"manager.go:499] Initializing CLI store manager for workspace {sc.get('log_workspace', str(cwd))}"]
        rows += [f'model_resolver.go:107] Resolving model {m}' for m in sc.get('resolving', [model, model])]
        Path(log).write_text(''.join(stamp.format(i) + row + '\n' for i, row in enumerate(rows, 1)),
                             encoding='utf-8')
    schema = opt(args, '--json-schema') is not None
    response = sc.get('response', '{"findings": []}' if schema else 'Synthetic context answer.\n')
    envelope = {'conversation_id': sc.get('conversation_id', CONVERSATION), 'status': 'SUCCESS',
                'response': response, 'duration_seconds': 1.0, 'num_turns': 1,
                'usage': {'input_tokens': 10, 'output_tokens': 2, 'thinking_tokens': 0,
                          'cache_read_tokens': 0, 'total_tokens': 12},
                'accountId': AGY_ACCOUNT}
    print(json.dumps(envelope, ensure_ascii=False), flush=True)
    return finish(sc)


def main():
    tool, args = sys.argv[1], sys.argv[2:]
    if args == ['--version']:
        print(VERSIONS[tool])
        return 0
    sc = scenario(tool)
    if tool == 'codex':
        if args[:1] == ['app-server']:
            return codex_app_server(sc)
        if args[:1] == ['exec']:
            return codex_exec(sc, args)
        capture('codex', base_record('codex', args, None))
        sys.stderr.write('fake codex: unsupported subcommand\n')
        return 64
    if tool == 'claude':
        return claude(sc, args)
    return agy(sc, args)


if __name__ == '__main__':
    raise SystemExit(main())
