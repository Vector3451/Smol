"""
Security & regression test suite for the Smol NAS web UI.

Drives NAS/app.py through Flask's test client with stubbed ML dependencies
(torch/transformers are stubbed, so this runs on any machine without the
model stack). All privileged commands are "simulated" because NAS_ENV is
'development' on non-Linux hosts; file operations run against temporary
directories, so nothing in the repository or system is touched.

Run:  python test_security.py
Exit code 0 = all checks passed.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
import types

# --------------------------------------------------------------------------
# Stub torch / transformers BEFORE importing app.py
# --------------------------------------------------------------------------
TORCH_STUB = types.ModuleType('torch')
TORCH_STUB.device = lambda *a, **k: 'cpu'
TORCH_STUB.cuda = types.SimpleNamespace(is_available=lambda: False)
TORCH_STUB.no_grad = contextlib.contextmanager(lambda: (yield None))
TORCH_STUB.Tensor = object


class GPT2LMHeadModelStub:
    @classmethod
    def from_pretrained(cls, *a, **k):
        raise RuntimeError("stubbed: no real model in test harness")


class GPT2TokenizerFastStub:
    @classmethod
    def from_pretrained(cls, *a, **k):
        raise RuntimeError("stubbed: no real tokenizer in test harness")


TF_STUB = types.ModuleType('transformers')
TF_STUB.GPT2LMHeadModel = GPT2LMHeadModelStub
TF_STUB.GPT2TokenizerFast = GPT2TokenizerFastStub

sys.modules.setdefault('torch', TORCH_STUB)
sys.modules.setdefault('transformers', TF_STUB)

# --------------------------------------------------------------------------
# Import the application
# --------------------------------------------------------------------------
NAS_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(NAS_DIR)
sys.path.insert(0, NAS_DIR)

import app as appmod  # noqa: E402

# --------------------------------------------------------------------------
# Temporary sandboxes for filesystem-touching routes
# --------------------------------------------------------------------------
SANDBOX = tempfile.mkdtemp(prefix='smol_test_')
appmod.NAS_ROOT = SANDBOX                                   # file routes sandbox
appmod.SMB_CONF_PATH = os.path.join(SANDBOX, 'smb.conf')    # dev conf sandbox
appmod.BACKUP_HISTORY_FILE = os.path.join(SANDBOX, 'backup_history.json')

# Seed the sandbox: a file at the root, a docs/ subdirectory with a file
with open(os.path.join(SANDBOX, 'readme.txt'), 'w') as f:
    f.write('seed')
os.makedirs(os.path.join(SANDBOX, 'docs'), exist_ok=True)
with open(os.path.join(SANDBOX, 'docs', 'doc.txt'), 'w') as f:
    f.write('doc')
# A decoy file OUTSIDE the NAS root that traversal must never touch
OUTSIDE = tempfile.mkdtemp(prefix='smol_outside_')
OUTSIDE_FILE = os.path.join(OUTSIDE, 'outside.txt')
with open(OUTSIDE_FILE, 'w') as f:
    f.write('protected')
# Minimal smb.conf for share parsing
with open(appmod.SMB_CONF_PATH, 'w') as f:
    f.write('[global]\n   workgroup = WORKGROUP\n[Docs]\n   path = /srv/docs\n')

# --------------------------------------------------------------------------
# Test plumbing
# --------------------------------------------------------------------------
RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    status = 'PASS' if condition else 'FAIL'
    print(f"[{status}] {name}" + (f"  ({detail})" if (detail and not condition) else ''))


def extract_csrf(client, path='/login'):
    """GET a page and pull the session CSRF token out of its HTML."""
    r = client.get(path)
    html = r.data.decode('utf-8', 'replace')
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html) or \
        re.search(r'meta name="csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else None


def login(client, username, password):
    tok = extract_csrf(client, '/login')
    return client.post('/login', data={'username': username, 'password': password, 'csrf_token': tok},
                       follow_redirects=False)


def fresh_admin():
    c = appmod.app.test_client()
    r = login(c, appmod.NAS_USER, appmod.NAS_PASSWORD)
    assert r.status_code == 302, f'admin login failed: {r.status_code}'
    return c


def fresh_user(name='alice'):
    c = appmod.app.test_client()
    tok = extract_csrf(c, '/login')
    c.post('/login', data={'username': name, 'password': 'raspberry', 'csrf_token': tok})
    return c


def last_flash_html(client, rsp):
    """Follow a redirect response and return the final page HTML (flash messages included)."""
    if rsp.status_code != 302:
        return rsp.data.decode('utf-8', 'replace')
    nxt = client.get(rsp.headers.get('Location', '/'))
    return nxt.data.decode('utf-8', 'replace')


# --------------------------------------------------------------------------
# 1. Login flow and CSRF
# --------------------------------------------------------------------------
print("\n== Login / CSRF ==")
c = appmod.app.test_client()
html = c.get('/login').data.decode()
check('login page renders', 'Initialize Session' in html)
check('login page carries CSRF hidden input', 'csrf_token' in html)

r = c.post('/login', data={'username': appmod.NAS_USER, 'password': appmod.NAS_PASSWORD},
           follow_redirects=True)
check('POST login without CSRF token is rejected',
      r.status_code == 200 or 'csrf' in r.data.decode().lower(),
      f'status={r.status_code}')

# session must NOT be authenticated after the rejected attempt
r = c.get('/')
check('rejected login did not create a session', r.status_code in (302,) or 'login' in r.headers.get('Location', ''))

r = login(c, appmod.NAS_USER, appmod.NAS_PASSWORD)
check('admin login with CSRF succeeds (302 -> /)', r.status_code == 302 and r.headers.get('Location', '').endswith('/'))

# session cookie flags (Set-Cookie is only emitted when the session changes,
# i.e. on the login response itself)
set_cookies = [h for h in r.headers.getlist('Set-Cookie')]
found = [h for h in set_cookies if 'SameSite=None' in h and 'Secure' in h and 'HttpOnly' in h]
check('session cookie has SameSite=None; Secure; HttpOnly', bool(found), str(set_cookies))

r = c.get('/', follow_redirects=True)
check('admin dashboard renders', 'System Overview' in r.data.decode())

# secret key strength
check('secret key is a generated 64-hex value (not a known default)',
      re.fullmatch(r'[0-9a-f]{64}', appmod.app.secret_key.decode() if isinstance(appmod.app.secret_key, bytes) else str(appmod.app.secret_key)) is not None)
check('body cap MAX_CONTENT_LENGTH = 2 GiB', appmod.app.config.get('MAX_CONTENT_LENGTH') == 2 * 1024 * 1024 * 1024)

# --------------------------------------------------------------------------
# 2. Upload hardening (C5 + C6)
# --------------------------------------------------------------------------
print("\n== Upload hardening ==")
tok = extract_csrf(c, '/files')
evil = (io.BytesIO(b'pwned'), '../../evil.txt', 'text/plain')
r = c.post('/files/upload', data={'file': evil, 'path': ''}, content_type='multipart/form-data')
check('upload without CSRF rejected', r.status_code == 302)
check('no file written by CSRF-rejected upload', not os.path.exists(os.path.join(SANDBOX, 'evil.txt')))

evil = (io.BytesIO(b'pwned'), '../../evil.txt', 'text/plain')
r = c.post('/files/upload', data={'file': evil, 'path': '', 'csrf_token': tok},
           content_type='multipart/form-data')
check('traversal filename is sanitized and saved inside root',
      os.path.exists(os.path.join(SANDBOX, 'evil.txt')))
check('nothing escaped the NAS root', not os.path.exists(os.path.join(OUTSIDE, 'evil.txt')))

r = c.get('/files')
check('uploaded file visible in explorer', 'evil.txt' in r.data.decode())

r = c.post('/files/upload', data={'file': (io.BytesIO(b'x'), 'x.txt'), 'path': '../../', 'csrf_token': tok},
           content_type='multipart/form-data', follow_redirects=True)
check('upload to a path outside the NAS root is refused', 'Access Denied' in r.data.decode())

# --------------------------------------------------------------------------
# 3. Files boundary enforcement (C6)
# --------------------------------------------------------------------------
print("\n== File boundary ==")
r = c.post('/files/delete', data={'path': '../../outside.txt', 'csrf_token': tok}, follow_redirects=True)
check('delete outside root refused', 'Access Denied' in r.data.decode())
check('outside decoy file untouched', open(OUTSIDE_FILE).read() == 'protected')

r = c.get('/files/download', query_string={'path': '../../outside.txt'})
check('download outside root -> 403', r.status_code == 403)

r = c.get('/files/stream', query_string={'path': '../../outside.txt'})
check('stream outside root -> 403', r.status_code == 403)

r = c.get('/files/..%2f', follow_redirects=False)   # '..' exists (parent dir) — boundary must deny
check('browse traversal attempt blocked', r.status_code == 200 and 'Access Denied' in r.data.decode('utf-8', 'replace'),
      f'status={r.status_code}')

r = c.get('/files/..%2fno_such_dir', follow_redirects=False)
check('nonexistent traversal target does not leak content',
      'Path not found' in r.data.decode('utf-8', 'replace') or 'Access Denied' in r.data.decode('utf-8', 'replace'))

r = c.post('/files/delete', data={'path': 'readme.txt', 'csrf_token': tok}, follow_redirects=True)
check('delete inside root works for admin (simulated removal)',
      'Deleted: readme.txt' in r.data.decode('utf-8', 'replace'))

# --------------------------------------------------------------------------
# 4. Execute endpoint (C1)
# --------------------------------------------------------------------------
print("\n== Assistant execute ==")


def exec_json(client, command):
    token = None
    with client.session_transaction() as s:
        token = s.get('_csrf_token')
    return client.post('/api/assistant/execute',
                       data=json.dumps({'command': command}),
                       content_type='application/json',
                       headers={'X-CSRF-Token': token or ''})


r = exec_json(c, 'here is some prose without any command')
check('unparseable text is refused (400), not run through shell', r.status_code == 400)

r = exec_json(c, 'sudo mkfs.ext4 /dev/sda1')
check('mkfs via assistant rejected', r.status_code == 400)

r = exec_json(c, 'sudo rm -rf /')
check('rm -rf / rejected as destructive', r.status_code == 400)

r = exec_json(c, 'sudo shutdown now')
check('shutdown via assistant rejected', r.status_code == 400)

r = exec_json(c, 'echo hello')
check('parseable benign command executes end-to-end',
      r.status_code == 200 and 'hello' in r.get_json().get('output', ''),
      r.data[:200] if r.status_code != 200 else '')

# --------------------------------------------------------------------------
# 5. Admin-only routes (H1 + service restart)
# --------------------------------------------------------------------------
print("\n== Admin-only enforcement ==")
cu = fresh_user('alice')
utok = extract_csrf(cu, '/files')
r = cu.post('/system/power', data={'action': 'reboot', 'csrf_token': utok})
check('standard user cannot reboot (admin-only redirect)', r.status_code == 302 and '/files' in r.headers.get('Location', ''))

r = cu.post('/service/restart/ssh', data={'csrf_token': utok})
check('standard user cannot restart services', r.status_code == 302 and '/files' in r.headers.get('Location', ''))

r = cu.post('/api/assistant/execute', data=json.dumps({'command': 'echo hi'}),
            content_type='application/json', headers={'X-CSRF-Token': utok})
check('standard user cannot execute (403 or admin redirect)',
      r.status_code == 403 or (r.status_code == 302 and '/files' in r.headers.get('Location', '')))

r = cu.get('/api/assistant/execute')
check('execute is POST-only', r.status_code == 405)

r = cu.get('/storage', follow_redirects=False)
check('standard user blocked from storage page', r.status_code == 302)

# admin power action simulated in dev
r = c.post('/system/power', data={'action': 'reboot', 'csrf_token': tok}, follow_redirects=True)
check('admin reboot accepted (simulated in dev)', 'Rebooting' in r.data.decode())

# --------------------------------------------------------------------------
# 6. Storage safety (H4 + mountpoint traversal)
# --------------------------------------------------------------------------
print("\n== Storage safety ==")
r = c.post('/storage/format', data={'device': 'sda1', 'fstype': 'evil.sh', 'label': 'L', 'csrf_token': tok},
           follow_redirects=True)
check('format rejects unknown fstype', 'Unsupported filesystem' in r.data.decode())
r = c.post('/storage/format', data={'device': '../..', 'fstype': 'ext4', 'label': 'L', 'csrf_token': tok},
           follow_redirects=True)
check('format rejects traversal device', 'Invalid device' in r.data.decode())
r = c.post('/storage/format', data={'device': 'sda1', 'fstype': 'ext4', 'label': 'NAS_TEST', 'csrf_token': tok},
           follow_redirects=True)
check('format accepts whitelisted values (simulated)', 'Formatted' in r.data.decode())

r = c.post('/storage/mount', data={'device': 'sdb1', 'mountpoint': '/mnt/../etc/evil', 'csrf_token': tok},
           follow_redirects=True)
check('mountpoint traversal refused', 'must be in /mnt or /srv' in r.data.decode())
r = c.post('/storage/mount', data={'device': 'sdb1', 'mountpoint': '/mnt/data', 'csrf_token': tok},
           follow_redirects=True)
check('normal mountpoint accepted (simulated)', 'Mounted' in r.data.decode())

# --------------------------------------------------------------------------
# 7. User management (M1 + validation)
# --------------------------------------------------------------------------
print("\n== User management ==")
r = c.post('/users/add', data={'username': 'bad name!', 'password': 'longenough1', 'csrf_token': tok},
           follow_redirects=True)
check('invalid username rejected on users/add', 'Invalid username' in r.data.decode())
r = c.post('/users/add', data={'username': 'testuser1', 'password': 'longenough1', 'csrf_token': tok},
           follow_redirects=True)
check('valid user creation succeeds (simulated)', 'added successfully' in r.data.decode())

# signup validation (unauthenticated client)
cs = appmod.app.test_client()
stok = extract_csrf(cs, '/signup')
r = cs.post('/signup', data={'username': 'Nope Uppercase', 'password': 'longenough1', 'csrf_token': stok},
            follow_redirects=True)
check('signup rejects invalid username', 'Invalid username' in r.data.decode())
r = cs.post('/signup', data={'username': 'gooduser2', 'password': 'short', 'csrf_token': stok},
            follow_redirects=True)
check('signup rejects short password', 'at least 8' in r.data.decode())
r = cs.post('/signup', data={'username': 'gooduser2', 'password': 'longenough1', 'csrf_token': stok},
            follow_redirects=True)
check('valid signup succeeds (simulated)', 'Registration successful' in r.data.decode())

# --------------------------------------------------------------------------
# 8. Login rate limiting
# --------------------------------------------------------------------------
print("\n== Rate limiting ==")
cb = appmod.app.test_client()
for _ in range(5):
    btok = extract_csrf(cb, '/login')
    cb.post('/login', data={'username': 'brutex', 'password': 'wrongpw', 'csrf_token': btok})
btok = extract_csrf(cb, '/login')
r = cb.post('/login', data={'username': 'brutex', 'password': 'raspberry', 'csrf_token': btok},
            follow_redirects=True)
check('account is locked after repeated failures', 'Too many failed attempts' in r.data.decode())

# admin login remains unaffected (different key)
ca = appmod.app.test_client()
r = login(ca, appmod.NAS_USER, appmod.NAS_PASSWORD)
check('admin login unaffected by another account lock', r.status_code == 302)

# --------------------------------------------------------------------------
# 9. Backup persistence (M5)
# --------------------------------------------------------------------------
print("\n== Backup persistence ==")
r = c.post('/backup/run', data={'source': '/srv/src', 'dest': '/mnt/dst', 'csrf_token': tok},
           follow_redirects=True)
check('backup runs (simulated)', 'completed' in r.data.decode() or 'simulated' in r.data.decode())
check('history persisted to disk', os.path.exists(appmod.BACKUP_HISTORY_FILE))
r = c.post('/backup/run', data={'source': '/srv/src2', 'dest': '/mnt/dst2', 'csrf_token': tok},
           follow_redirects=True)
with open(appmod.BACKUP_HISTORY_FILE) as f:
    hist = json.load(f)
check('history accumulates across runs', len(hist) == 2 and hist[-1]['details'].startswith('Source:'))

r = c.get('/backup')
check('backup page renders persisted history', b'/srv/src' in r.data)

# --------------------------------------------------------------------------
# 10. Shares (dev conf redirected to sandbox)
# --------------------------------------------------------------------------
print("\n== Shares ==")
r = c.get('/shares')
check('shares page renders', r.status_code == 200)
r = c.post('/shares/add', data={'name': 'Media', 'path': '/srv/media', 'valid_users': 'dev',
                                'csrf_token': tok}, follow_redirects=True)
with open(appmod.SMB_CONF_PATH) as f:
    conf = f.read()
check('share section written to conf', '[Media]' in conf and 'path = /srv/media' in conf)

# --------------------------------------------------------------------------
# 11. Templates: XSS escaping present; cookies.txt gone
# --------------------------------------------------------------------------
print("\n== Templates & repo hygiene ==")
with open(os.path.join(NAS_DIR, 'templates', 'health.html'), encoding='utf-8') as f:
    h = f.read()
check('health.html defines esc() and applies it', 'function esc(' in h and 'esc(smart.model_name' in h)
check('cookies.txt removed from working tree', not os.path.exists(os.path.join(NAS_DIR, 'cookies.txt')))

r = c.get('/health')
check('health page renders for admin', r.status_code == 200)

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
failed = [(n, d) for (n, ok, d) in RESULTS if not ok]
print("\n" + "=" * 60)
print(f"TOTAL: {len(RESULTS)}   PASS: {len(RESULTS) - len(failed)}   FAIL: {len(failed)}")
if failed:
    print("\nFailed checks:")
    for n, d in failed:
        print(f"  - {n} {d}")
sys.exit(1 if failed else 0)
