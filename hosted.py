#!/usr/bin/env python3
"""oprn-dots hosted worker: let the queue server draw with YOUR agent login, so your PC does not have to stay on.

  python3 hosted.py enroll --name NAME [--contact C]     show the consent text, ask for yes, log in, upload, done
  python3 hosted.py status                               is the server drawing with my login? (needs relogin?)
  python3 hosted.py relogin                              upload a new login (after "needs relogin")
  python3 hosted.py revoke                               destroy my login on the server and stop the worker

Login options for enroll / relogin:
  (default)            a NEW, separate Codex login made just for the server (codex login into a temporary folder), so your
                       own PC session is not shared and neither side logs the other out
  --use-existing       upload your current ~/.codex/auth.json instead (using it on your PC at the same time can log you out)
  --auth-file PATH     upload this auth.json
Python 3.8+ standard library only. Nothing is uploaded before you type yes.
"""
import argparse, getpass, json, os, platform, shutil, subprocess, sys, tempfile, urllib.error, urllib.request

DEFAULT_ORIGIN = 'https://mdc2.tail9e7432.ts.net:8443'
APP = 'oprn-dots-hosted'


def cfg_path():
    if platform.system() == 'Windows': d = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), APP)
    else: d = os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), APP)
    return os.path.join(d, 'config.json')


CFG = cfg_path()


def load_cfg():
    try:
        with open(CFG, encoding='utf-8') as f: return json.load(f)
    except Exception: return {}


def save_cfg(c):
    os.makedirs(os.path.dirname(CFG), exist_ok=True); t = CFG + '.tmp'
    fd = os.open(t, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(c, f, ensure_ascii=False, indent=1)
    os.replace(t, CFG)
    try: os.chmod(CFG, 0o600)
    except OSError: pass


def http(url, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data, {'Content-Type': 'application/json'} if data else {}, method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b'{}')
        except ValueError: return e.code, {}


def ask_consent(origin):
    st, c = http(origin + '/hosted/consent')
    if st != 200 or 'text' not in c: sys.exit(f'cannot read the consent text from the server (HTTP {st})')
    print('\n' + c['text'] + '\n', flush=True)
    print('> ', end='', flush=True)
    ans = sys.stdin.readline().strip().lower()
    if ans != 'yes': sys.exit('동의하지 않아 아무것도 올리지 않았습니다. / Not agreed: nothing was uploaded.')
    return {'version': c['version'], 'sha256': c['sha256'], 'answer': 'yes'}


def codex_home(): return os.environ.get('CODEX_HOME') or os.path.join(os.path.expanduser('~'), '.codex')


def fresh_codex_login():
    """a separate login for the server: `codex login` into a temporary CODEX_HOME, read auth.json, then remove the temporary copy."""
    exe = shutil.which('codex')
    if not exe: sys.exit('codex CLI not found. Install it (npm i -g @openai/codex) or pass --auth-file PATH.')
    tmp = tempfile.mkdtemp(prefix='dots-hosted-login-')
    try:
        print('A browser window opens for a NEW Codex (ChatGPT) login used only by the server. 서버 전용 새 로그인 창이 열립니다.', flush=True)
        subprocess.run([exe, 'login'], env={**os.environ, 'CODEX_HOME': tmp}, check=False)
        p = os.path.join(tmp, 'auth.json')
        if not os.path.exists(p): sys.exit('login did not finish (no auth.json). Nothing was uploaded.')
        with open(p, encoding='utf-8') as f: return f.read()
    finally: shutil.rmtree(tmp, ignore_errors=True)


def claude_login(a):
    if a.use_existing:
        p = os.path.join(os.path.expanduser('~'), '.claude', '.credentials.json')
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f: return f.read()
        if platform.system() == 'Darwin':
            r = subprocess.run(['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip(): return r.stdout.strip()
        sys.exit('no Claude Code login found on this computer.')
    if shutil.which('claude'):
        print('Running `claude setup-token`: log in, then copy the token it prints (sk-ant-oat01-...).', flush=True)
        subprocess.run(['claude', 'setup-token'], check=False)
    return getpass.getpass('Paste the token (hidden) / 토큰 붙여 넣기: ').strip()


def get_login(a):
    if a.auth_file:
        with open(a.auth_file, encoding='utf-8') as f: return f.read()
    if a.agent == 'claude': return claude_login(a)
    if a.use_existing:
        p = os.path.join(codex_home(), 'auth.json')
        if not os.path.exists(p): sys.exit(f'{p} not found. Run `codex login` first or drop --use-existing.')
        print('주의: 이 PC 의 로그인을 그대로 올립니다. 이 PC 에서 동시에 쓰면 서로 로그아웃될 수 있습니다.', flush=True)
        with open(p, encoding='utf-8') as f: return f.read()
    return fresh_codex_login()


def cmd_enroll(a):
    c = load_cfg()
    if c.get('key') and not a.force: sys.exit(f"already enrolled as {c.get('worker_id')}; use `status`, `relogin`, `revoke`, or --force for a new worker")
    origin = (a.origin or c.get('origin') or DEFAULT_ORIGIN).rstrip('/')
    consent = ask_consent(origin)
    cred = get_login(a)
    st, r = http(origin + '/hosted/enroll', {'name': a.name, 'contact': a.contact or '', 'provider': a.agent, 'credential': cred, 'consent': consent})
    cred = None
    if st != 200 or not r.get('ok'): sys.exit(f"enroll failed (HTTP {st}): {r.get('error')}")
    save_cfg({'origin': origin, 'worker_id': r['worker_id'], 'name': r['name'], 'key': r['key'], 'provider': a.agent})
    print(f"enrolled: worker {r['worker_id']} ({r['name']}), state {r['state']}. The server now draws with this login; your PC can be off.")
    print(f'Your worker key is saved in {CFG} (keep it private). Revoke any time: python3 hosted.py revoke')


def need():
    c = load_cfg()
    if not c.get('key'): sys.exit(f'not enrolled on this computer (no {CFG}). Run: python3 hosted.py enroll --name <name>')
    return c


STATE_KO = {'active': '서버에서 그리는 중/대기', 'needs_relogin': '다시 로그인 필요 (python3 hosted.py relogin)', 'paused': '오류로 멈춤 (운영자 확인 중)', 'revoked': '회수됨'}


def cmd_status(a):
    c = need(); st, r = http(f"{c['origin']}/{c['key']}/hosted/status", {})
    if st == 404: sys.exit('the server no longer accepts this worker (revoked or disabled).')
    if st != 200: sys.exit(f"status failed (HTTP {st}): {r.get('error')}")
    print(json.dumps({**r, 'state_ko': STATE_KO.get(r.get('state'), r.get('state'))}, ensure_ascii=False, indent=1))


def cmd_relogin(a):
    c = need(); a.agent = a.agent or c.get('provider') or 'codex'
    consent = ask_consent(c['origin']); cred = get_login(a)
    st, r = http(f"{c['origin']}/{c['key']}/hosted/relogin", {'provider': a.agent, 'credential': cred, 'consent': consent})
    if st != 200: sys.exit(f"relogin failed (HTTP {st}): {r.get('error')}")
    c['provider'] = a.agent; save_cfg(c); print('new login uploaded; state', r.get('state'))


def cmd_revoke(a):
    c = need()
    if not a.yes:
        print('서버에 맡긴 로그인을 파기하고 일꾼을 멈춥니다. 계속하려면 yes / Destroy the stored login and stop? type yes')
        print('> ', end='', flush=True)
        if sys.stdin.readline().strip().lower() != 'yes': sys.exit('cancelled')
    st, r = http(f"{c['origin']}/{c['key']}/hosted/revoke", {})
    if st == 404: sys.exit('the server no longer accepts this worker (already revoked or disabled by the operator).')
    if st != 200: sys.exit(f"revoke failed (HTTP {st}): {r.get('error')}")
    c['revoked'] = True; save_cfg(c)
    print(f"revoked: stored login destroyed={r.get('login_destroyed')}, worker stopped, jobs handed back={r.get('jobs_released')}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='oprn-dots hosted worker (the server draws with your login)'); sp = ap.add_subparsers(dest='cmd', required=True)
    for n in ('enroll', 'relogin'):
        p = sp.add_parser(n)
        if n == 'enroll':
            p.add_argument('--name', required=True); p.add_argument('--contact'); p.add_argument('--origin'); p.add_argument('--force', action='store_true')
            p.add_argument('--agent', choices=['codex', 'claude'], default='codex')
        else: p.add_argument('--agent', choices=['codex', 'claude'])
        p.add_argument('--use-existing', action='store_true'); p.add_argument('--auth-file')
    sp.add_parser('status'); r = sp.add_parser('revoke'); r.add_argument('--yes', action='store_true', help='skip the confirmation question')
    a = ap.parse_args()
    {'enroll': cmd_enroll, 'status': cmd_status, 'relogin': cmd_relogin, 'revoke': cmd_revoke}[a.cmd](a)
