#!/usr/bin/env python3
"""oprn-dots background worker: takes pixel-art jobs from the queue and has YOUR agent CLI draw them, forever.

  python3 dots_worker.py join --name NAME [--contact C]   sign up (no invite needed) and save the key in the config file
  python3 dots_worker.py run                               the service loop (what the background service runs)
  python3 dots_worker.py once                              one cycle: take a job if there is one, draw, submit, finish
  python3 dots_worker.py check                             is the key accepted? which agent command is configured?
  python3 dots_worker.py where                             print the config / log / work paths

Loop: get_next_job -> write the job prompt + reference images into a work folder -> run the agent command headless in
that folder (prompt on stdin) -> zip what it wrote to ./out -> submit_zip (chunked when big) -> mark_job_done.
No job: sleep poll_seconds and ask again. Failure: report_problem (twice failed on the same job: blocked=true).
While the agent works, the job lease is renewed every 10 minutes. Python 3.8+ standard library only.
"""
import argparse, base64, io, json, os, platform, re, shutil, subprocess, sys, threading, time, urllib.error, urllib.request, zipfile

DEFAULT_ORIGIN = 'https://mdc2.tail9e7432.ts.net:8443'
APP = 'oprn-dots-worker'


def base_dir():
    if os.environ.get('DOTS_WORKER_HOME'): return os.environ['DOTS_WORKER_HOME']
    if platform.system() == 'Windows': return os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), APP)
    return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), APP)


HOME = base_dir(); CFG = os.path.join(HOME, 'config.json'); LOG = os.path.join(HOME, 'worker.log'); WORK = os.path.join(HOME, 'jobs')
FAILS = os.path.join(HOME, 'failures.json')

AGENTS = {   # {workdir} is replaced; the prompt goes to stdin
    'codex': ['codex', 'exec', '--skip-git-repo-check', '-s', 'workspace-write', '-C', '{workdir}', '-'],
    'claude': ['claude', '-p', '--permission-mode', 'acceptEdits', '--allowedTools', 'Bash,Write,Edit,Read,Glob,Grep'],
}

TASK = """You are a pixel-art worker for the oprn-dots queue. This folder is your work folder.
- The job prompt (Korean) is below and in ./prompt.md. Reference images it mentions are in ./refs/.
- Draw every sprite BY HAND, pixel by pixel: author the pixel data yourself (for example a character map or explicit
  per-pixel colors turned into a PNG with a small script). NEVER use image-generation models or tools, never copy art.
- Follow the prompt exactly (canvas sizes, file names, palette, outline rules). Compare with the references at 1x and fix
  what is off at least twice.
- Write ALL deliverables (the PNGs, the preview/compare sheets the prompt asks for, manifest.txt if asked) into ./out/.
- ./out/notes.txt line 1 must be the exact model name and reasoning level you are running as, e.g. "model: astra-6.2 / high".
  Then one line per file as the prompt asks.
- Do not upload anything yourself; when ./out/ is complete, just finish. The worker service zips and submits it.

=== JOB {job_id} ===
{prompt}
"""


def log(*a):
    os.makedirs(HOME, exist_ok=True)
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + ' '.join(str(x) for x in a)
    print(line, flush=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 5 * 1024 * 1024: os.replace(LOG, LOG + '.1')
        with open(LOG, 'a', encoding='utf-8') as f: f.write(line + '\n')
    except OSError: pass


def jload(p, d):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except Exception: return d


def jsave(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True); t = p + '.tmp'
    fd = os.open(t, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(o, f, ensure_ascii=False, indent=1)
    os.replace(t, p)
    try: os.chmod(p, 0o600)
    except OSError: pass


def cfg():
    c = jload(CFG, None)
    if not c or not c.get('key'): sys.exit(f'not signed up yet: run  python3 {sys.argv[0]} join --name <name>   (config: {CFG})')
    return c


def http(url, body=None, timeout=120):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data, {'Content-Type': 'application/json'} if data else {}, method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()


class ToolErr(Exception): pass


def tool(c, name, **args):
    st, raw = http(f"{c['origin']}/{c['key']}/mcp", {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}, timeout=600)
    if st == 404: raise SystemExit('the server does not accept this key (disabled?). Ask the operator or sign up again.')
    if st != 200: raise ToolErr(f'{name}: HTTP {st}')
    msg = json.loads(raw)
    if 'error' in msg: raise ToolErr(f"{name}: {msg['error'].get('message')}")
    r = msg['result']
    if r.get('isError'): raise ToolErr(r['content'][0].get('text', 'error'))
    return r.get('structuredContent') or r


def detect_agent():
    for name in ('codex', 'claude'):
        if shutil.which(name): return name
    return None


def cmd_join(a):
    origin = (a.origin or os.environ.get('DOTS_ORIGIN') or DEFAULT_ORIGIN).rstrip('/')
    old = jload(CFG, {})
    if old.get('key') and not a.force:
        print(f"already signed up as {old.get('worker_id')} ({old.get('name')}); use --force for a new key"); return
    st, raw = http(origin + '/join', {'name': a.name, 'contact': a.contact or ''}, timeout=30)
    try: r = json.loads(raw)
    except ValueError: r = {}
    if st != 200 or not r.get('ok'): sys.exit(f"signup failed (HTTP {st}): {r.get('error') or raw[:200]!r}")
    agent = a.agent or old.get('agent_name') or detect_agent()
    c = {**old, 'origin': origin, 'worker_id': r['worker_id'], 'name': r['name'], 'key': r['key'], 'agent_name': agent,
         'agent_command': old.get('agent_command') or AGENTS.get(agent or '', []), 'poll_seconds': old.get('poll_seconds', 300),
         'agent_timeout_minutes': old.get('agent_timeout_minutes', 120)}
    jsave(CFG, c)
    print(f"signed up: worker {r['worker_id']} ({r['name']}). Key saved in {CFG} (keep it private).")
    if not c['agent_command']: print('no agent CLI found (codex or claude). Edit agent_command in the config file.')


def cmd_check(a):
    c = cfg(); st, raw = http(f"{c['origin']}/{c['key']}/health", timeout=30)
    print(f"server: HTTP {st} {raw[:120].decode(errors='replace')}")
    cmd = c.get('agent_command') or []
    print('agent command:', ' '.join(cmd) or '(none)', '| found:', bool(cmd and shutil.which(cmd[0])))
    print('config:', CFG, '| log:', LOG)
    if st != 200 or not cmd or not shutil.which(cmd[0]): sys.exit(1)


def fail_count(jid, inc=False):
    f = jload(FAILS, {})
    if inc: f[jid] = f.get(jid, 0) + 1; jsave(FAILS, f)
    return f.get(jid, 0)


def fetch_refs(c, job, d):
    os.makedirs(os.path.join(d, 'refs'), exist_ok=True)
    for ref in job.get('reference_images', []):
        name = os.path.basename(ref['name']); p = os.path.join(d, 'refs', name)
        if os.path.exists(p): continue
        try:
            st, raw = http(ref['url'], timeout=60)
            if st == 200 and raw[:8] == b'\x89PNG\r\n\x1a\n':
                with open(p, 'wb') as f: f.write(raw)
        except Exception as e: log('ref download failed', name, e)


def run_agent(c, jid, d, prompt):
    cmd = [x.replace('{workdir}', d) for x in c.get('agent_command') or []]
    if not cmd or not shutil.which(cmd[0]): raise ToolErr(f'agent command not found: {cmd[:1]} (edit agent_command in {CFG})')
    stop = threading.Event()
    def heartbeat():   # re-asking get_next_job returns our own job and renews the 30-minute lease
        while not stop.wait(600):
            try: tool(c, 'get_next_job')
            except Exception as e: log('lease renew failed', e)
    threading.Thread(target=heartbeat, daemon=True).start()
    log('agent start', jid, ' '.join(cmd[:3]), '...')
    try:
        with open(os.path.join(d, 'agent.log'), 'ab') as out:
            p = subprocess.run(cmd, input=prompt.encode('utf-8'), cwd=d, stdout=out, stderr=subprocess.STDOUT, timeout=int(c.get('agent_timeout_minutes', 120)) * 60)
        log('agent exit', jid, p.returncode)
    except subprocess.TimeoutExpired: log('agent timeout', jid)
    finally: stop.set()


def zip_out(d):
    od = os.path.join(d, 'out'); buf = io.BytesIO(); n = 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(od):
            for f in sorted(files):
                if re.search(r'\.(png|txt|md|json)$', f, re.I):
                    p = os.path.join(root, f); z.write(p, os.path.relpath(p, od)); n += f.lower().endswith('.png')
    return buf.getvalue(), n


def submit(c, jid, data, zname):
    if len(data) < 4 * 1024 * 1024: return tool(c, 'submit_zip', job_id=jid, zip_base64=base64.b64encode(data).decode(), zip_name=zname)
    up = tool(c, 'begin_upload', job_id=jid, kind='zip', filename=zname)['upload_id']
    step = 3 * 1024 * 1024
    for i in range(0, len(data), step): tool(c, 'append_chunk', upload_id=up, index=i // step, chunk_base64=base64.b64encode(data[i:i + step]).decode())
    import hashlib
    return tool(c, 'finish_upload', upload_id=up, sha256=hashlib.sha256(data).hexdigest())


def cycle(c):
    """one job; returns True when a job was handled (so the loop asks again at once)."""
    r = tool(c, 'get_next_job'); job = r.get('job')
    if not job: return False
    jid = job['job_id']; d = os.path.join(WORK, jid); os.makedirs(os.path.join(d, 'out'), exist_ok=True)
    log('job', jid)
    with open(os.path.join(d, 'prompt.md'), 'w', encoding='utf-8') as f: f.write(job.get('prompt', ''))
    fetch_refs(c, job, d)
    try:
        run_agent(c, jid, d, TASK.format(job_id=jid, prompt=job.get('prompt', '')))
        data, pngs = zip_out(d)
        if not pngs: raise ToolErr('the agent wrote no PNG into ./out')
        res = submit(c, jid, data, job.get('default_zip_name'))
        tool(c, 'mark_job_done', job_id=jid, note=f'oprn-dots-worker service, {pngs} PNG files')
        log('done', jid, res.get('delivered_as'), pngs, 'png'); return True
    except ToolErr as e:
        n = fail_count(jid, inc=True); tail = ''
        try:
            with open(os.path.join(d, 'agent.log'), 'rb') as f: f.seek(max(0, os.path.getsize(f.name) - 1500)); tail = f.read().decode('utf-8', 'replace')
        except OSError: pass
        log('failed', jid, f'(attempt {n})', e)
        try: tool(c, 'report_problem', job_id=jid, text=f'worker service attempt {n} failed: {e}\n--- agent log tail ---\n{tail}', blocked=n >= 2)
        except Exception as e2: log('report_problem failed', e2)
        return n >= 2


def cmd_run(a):
    c = cfg(); log('worker service started', c['worker_id'], 'agent:', ' '.join((c.get('agent_command') or ['?'])[:1]))
    while True:
        try:
            if cycle(c): continue
        except ToolErr as e: log('error', e)
        except (urllib.error.URLError, OSError, ValueError) as e: log('network error', e)
        time.sleep(max(30, int(c.get('poll_seconds', 300))))


def cmd_once(a):
    c = cfg(); print('handled a job' if cycle(c) else 'no job right now')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='oprn-dots background worker'); sp = ap.add_subparsers(dest='cmd', required=True)
    j = sp.add_parser('join'); j.add_argument('--name', required=True); j.add_argument('--contact'); j.add_argument('--origin'); j.add_argument('--agent', choices=sorted(AGENTS)); j.add_argument('--force', action='store_true')
    for n in ('run', 'once', 'check', 'where'): sp.add_parser(n)
    a = ap.parse_args()
    if a.cmd == 'where': print(json.dumps({'config': CFG, 'log': LOG, 'jobs': WORK}, indent=1)); sys.exit(0)
    {'join': cmd_join, 'run': cmd_run, 'once': cmd_once, 'check': cmd_check}[a.cmd](a)
