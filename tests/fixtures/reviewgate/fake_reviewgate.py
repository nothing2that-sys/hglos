"""Fake ReviewGate CLI for tests/test_reviewgate_bridge.py (doc/34 task O).

Never contacts a model or the network. Every value it prints is synthetic.

Environment (set by the test):
  FAKE_RG_SCENARIO  JSON file {"<command>": [response, ...]} where <command> is the
                    first argument (review, implement, export, discard, policy).
                    The k-th call of a command uses the k-th response.
  FAKE_RG_LOG       folder; one call-<seq>.json per invocation with argv, cwd,
                    prompt-file SHA-256, stdout SHA-256 and the FAKE_RG_MARK value.
  FAKE_RG_WORK      folder for synthetic rollouts, export files and reports.

Response keys:
  exit (int), stdout (JSON value) or stdout_text (raw text), stderr (text),
  sleep (seconds), grandchild (bool: start a sleeping child and record its pid),
  touch (path relative to --source-root/--repo to append a line to),
  rollouts [{session, model, variant, file_session?}], export_file {content}.

String placeholders inside stdout:
  @REQUEST_SHA256@         SHA-256 of the --prompt-file bytes
  @ROLLOUT_PATH:k@         absolute path of rollout k
  @SENT_SHA256:k@          SHA-256 of the request message text of rollout k
  @EXPORT_PATH@ / @EXPORT_SHA256@ / @EXPORT_BYTES@ (integer) for export_file
  @WORK@                   FAKE_RG_WORK

Rollout variants: ok, missing, wrong_name, context_model, bad_injection, memory,
  sent_mismatch, instr_missing, instr_two, instr_diff.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROLLOUT_BODY = 'SYNTHETIC-ROLLOUT-BODY'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def values(args, name):
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]


def message(role, text):
    return {'type': 'response_item',
            'payload': {'type': 'message', 'role': role, 'content': [{'type': 'input_text', 'text': text}]}}


def request_text(prompt, variant):
    if variant == 'instr_missing':
        body = {'task_text': prompt, 'unit': 'unit-1'}
    elif variant == 'instr_two':
        body = {'instruction': prompt, 'unit': {'instruction': prompt}}
    elif variant == 'instr_diff':
        body = {'instruction': prompt + ' altered', 'unit': 'unit-1'}
    else:
        body = {'instruction': prompt, 'unit': 'unit-1'}
    return 'SYNTHETIC SYSTEM TEXT\n\n' + json.dumps(body, ensure_ascii=False)


def write_rollout(work, k, spec, prompt):
    variant = spec.get('variant', 'ok')
    session = spec['session']
    name_session = spec.get('file_session') or ('00000000-0000-4000-8000-000000000099'
                                                if variant == 'wrong_name' else session)
    path = work / 'rollouts' / f'rollout-2026-11-06T00-00-{k:02d}-{name_session}.jsonl'
    text = request_text(prompt, variant)
    sent = sha(b'other') if variant == 'sent_mismatch' else sha(text.encode('utf-8'))
    if variant == 'missing':
        return path, sent
    model = spec['model']
    developer = f'{ROLLOUT_BODY} developer instructions'
    if variant == 'memory':
        developer += '\n## Memory\nsynthetic memory'
    rows = [{'type': 'session_meta', 'payload': {'id': session}},
            {'type': 'turn_context', 'payload': {'model': model, 'effort': 'medium'}},
            message('developer', developer),
            message('user', f'# AGENTS.md instructions for example\n{ROLLOUT_BODY}'),
            message('user', f'<environment_context>{ROLLOUT_BODY}</environment_context>')]
    if variant == 'bad_injection':
        rows.append(message('user', f'{ROLLOUT_BODY} unexpected injected user text'))
    rows += [message('user', text),
             {'type': 'turn_context',
              'payload': {'model': 'gpt-0-other' if variant == 'context_model' else model, 'effort': 'medium'}},
             {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                                                   'content': [{'type': 'output_text',
                                                                'text': f'{ROLLOUT_BODY} answer'}]}}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    return path, sent


def substitute(node, table):
    if isinstance(node, dict):
        return {k: substitute(v, table) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, table) for v in node]
    if isinstance(node, str):
        if node in table and not isinstance(table[node], str):
            return table[node]
        for key, value in table.items():
            if isinstance(value, str):
                node = node.replace(key, value)
        return node
    return node


def main():
    args = sys.argv[1:]
    command = args[0] if args else ''
    log = Path(os.environ['FAKE_RG_LOG'])
    work = Path(os.environ['FAKE_RG_WORK'])
    log.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    scenario = json.loads(Path(os.environ['FAKE_RG_SCENARIO']).read_text(encoding='utf-8'))
    seq = len(list(log.glob('call-*.json'))) + 1
    index = sum(1 for p in log.glob('call-*.json')
                if json.loads(p.read_text(encoding='utf-8'))['command'] == command)
    responses = scenario.get(command) or []
    prompt_files = values(args, '--prompt-file')
    prompt_bytes = Path(prompt_files[0]).read_bytes() if prompt_files else None
    entry = {'seq': seq, 'command': command, 'argv': args, 'cwd': os.getcwd(),
             'prompt_sha256': sha(prompt_bytes) if prompt_bytes is not None else None,
             'mark': os.environ.get('FAKE_RG_MARK'), 'pid': os.getpid()}
    if index >= len(responses):
        entry['error'] = 'no scripted response'
        (log / f'call-{seq}.json').write_text(json.dumps(entry), encoding='utf-8')
        sys.stderr.write(f'fake reviewgate: no scripted response for {command} #{index + 1}\n')
        return 99
    resp = responses[index]
    table = {'@WORK@': str(work)}
    if prompt_bytes is not None:
        table['@REQUEST_SHA256@'] = sha(prompt_bytes)
    prompt = prompt_bytes.decode('utf-8') if prompt_bytes is not None else ''
    for k, spec in enumerate(resp.get('rollouts') or []):
        path, sent = write_rollout(work, k, spec, prompt)
        table[f'@ROLLOUT_PATH:{k}@'] = str(path)
        table[f'@SENT_SHA256:{k}@'] = sent
    if resp.get('export_file') is not None:
        content = resp['export_file']['content']
        data = content.encode('utf-8') if isinstance(content, str) else \
            (json.dumps(content, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        target = work / 'exports' / f'export-{seq}' / 'hglos.findings.live.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        table.update({'@EXPORT_PATH@': str(target), '@EXPORT_SHA256@': sha(data), '@EXPORT_BYTES@': len(data)})
    if resp.get('touch'):
        root = (values(args, '--source-root') or values(args, '--repo'))[0]
        with open(Path(root) / resp['touch'], 'a', encoding='utf-8') as handle:
            handle.write('TOUCHED_BY_FAKE_REVIEWER = 1\n')
    if resp.get('grandchild'):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        entry['grandchild_pid'] = child.pid
    if 'stdout_text' in resp:
        out = resp['stdout_text'].encode('utf-8')
    else:
        out = json.dumps(substitute(resp.get('stdout'), table), ensure_ascii=False, indent=2).encode('utf-8')
    entry['stdout_sha256'] = sha(out)
    (log / f'call-{seq}.json').write_text(json.dumps(entry), encoding='utf-8')
    if resp.get('sleep'):
        time.sleep(resp['sleep'])
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write((resp.get('stderr') or '').encode('utf-8'))
    sys.stderr.buffer.flush()
    return int(resp.get('exit', 0))


if __name__ == '__main__':
    sys.exit(main())
