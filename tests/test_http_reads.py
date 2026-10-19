import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.services.http_files import HttpFileReader, read_origin, read_path
from test_robot_bridge import MemoryRobot


@pytest.fixture
def web():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get('Authorization')))
            status, headers, payload = replies.get(self.path, (404, {}, b''))
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    seen, replies = [], {}
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', replies, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def policy(tmp_path, origin, **access):
    document = {'state_dir': 'state', 'source_dir': 'edits', 'allow_writes': False,
        'robots': {'r1': {'profile': {'host': '127.0.0.1', 'roots': ['md:']},
                         'http_read_url': origin, 'http_read_files': ['md:NUMREG.VA'], **access}}}
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path, document


def test_http_preserves_exact_bytes_and_uses_separate_credentials_without_proxy(tmp_path, web, monkeypatch):
    origin, replies, seen = web
    payload = b"[1] = 42 'Count'\r\n"
    replies['/MD/NUMREG.VA?_TEMPLATE='] = (200, {'Content-Type': 'text/plain', 'Content-Length': str(len(payload))}, payload)
    path, _ = policy(tmp_path, origin, http_read_username='reader', http_read_password_env='FANUC_HTTP_TEST')
    monkeypatch.setenv('FANUC_HTTP_TEST', 'test-only-password')
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('no_proxy', '')
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        result = bridge.read_file('r1', 'md:NUMREG.VA')
        assert Path(result['local_path']).read_bytes() == payload
        assert result['sha256'] == hashlib.sha256(payload).hexdigest() and result['transport'] == 'http'
        retained = bridge.reference_capture('r1', 'md:NUMREG.VA', result['sha256'])
        assert Path(retained['local_path']).read_bytes() == payload
        assert bridge.list_robots()['robots'][0]['http_read_files'] == ['md:NUMREG.VA']
        assert 'test-only-password' not in json.dumps(result)
        sample = bridge.operations.sample('r1', ['R[1]'])
        assert sample['values']['R[1]'] == '42' and sample['file_transports'] == {'NUMREG.VA': 'http'}
    expected = 'Basic ' + base64.b64encode(b'reader:test-only-password').decode('ascii')
    assert seen == [('/MD/NUMREG.VA?_TEMPLATE=', expected)] * 2
    assert robot.connects == robot.uploads == 0


def test_nonallowlisted_files_keep_ftp_and_http_failure_has_no_ftp_fallback(tmp_path, web):
    origin, _, seen = web
    path, _ = policy(tmp_path, origin)
    robot = MemoryRobot()
    with RobotBridge.from_config(path, lambda _: robot) as bridge:
        assert bridge.read_file('r1', 'md:MAIN.LS')['transport'] == 'ftp'
        connects = robot.connects
        with pytest.raises(RuntimeError, match='Controller read failed'):
            bridge.read_file('r1', 'md:NUMREG.VA')
        assert robot.connects == connects
        with pytest.raises(ValueError, match='disabled'):
            bridge.prepare_upload('r1', 'md:MAIN.LS', 'MAIN.LS', '0' * 64)
    assert len(seen) == 1 and not robot.uploads


@pytest.mark.parametrize('status,headers,payload', [
    (302, {'Location': '/karel/ComSet'}, b''),
    (200, {'Content-Type': 'text/html'}, b'<html>Login</html>'),
    (200, {'Content-Type': 'text/plain'}, b'\xef\xbb\xbf  <html>Login</html>'),
    (200, {'Content-Length': '9999'}, b'small'),
    (200, {'Content-Length': '64'}, b'small'),
    (200, {'Content-Encoding': 'gzip'}, b'encoded'),
    (200, {}, b'a' * 65),
    (401, {}, b'Login required')])
def test_http_failures_never_leave_a_captured_file(tmp_path, web, status, headers, payload):
    origin, replies, seen = web
    replies['/MD/NUMREG.VA?_TEMPLATE='] = (status, headers, payload)
    target = tmp_path / 'result.va'
    with pytest.raises((ValueError, RuntimeError)):
        HttpFileReader(origin, '127.0.0.1').download_file('md:NUMREG.VA', target, maximum=64)
    assert not target.exists() and len(seen) == 1


@pytest.mark.parametrize('remote', ['md:../X.VA', 'karel:ComSet', '/karel/ComSet',
    'http://127.0.0.1/MD/X.VA', 'md:X.VA?x=1', 'md:a%2fX.VA', 'md:folder/X.VA', 'md:X.TP'])
def test_http_paths_never_select_commands_or_arbitrary_urls(remote):
    with pytest.raises(ValueError):
        read_path(remote)


@pytest.mark.parametrize('url', ['http://192.0.2.2', 'http://user:pass@192.0.2.1',
    'http://192.0.2.1/karel', 'http://192.0.2.1?x=1', 'http://192.0.2.1?', 'http://192.0.2.1#',
    'http://@192.0.2.1', 'ftp://192.0.2.1', 'http://192.0.2.1\r\n'])
def test_http_origins_stay_on_explicit_configured_host(url):
    with pytest.raises(ValueError):
        read_origin(url, '192.0.2.1')


def test_http_credentials_missing_and_invalid_policy_make_no_network_request(tmp_path, web):
    origin, _, seen = web
    path, document = policy(tmp_path, origin, http_read_username='reader', http_read_password_env='FANUC_UNSET_TEST')
    with RobotBridge.from_config(path) as bridge:
        with pytest.raises(RuntimeError):
            bridge.read_file('r1', 'md:NUMREG.VA')
    assert not seen
    for changes in [{'http_read_files': ['md:NUMREG.VA', 'MD:numreg.va']},
                    {'http_read_files': ['mc:X.VA']}, {'http_read_url': None},
                    {'http_read_username': 'user:bad'}, {'http_read_password_env': 'bad-name'}]:
        candidate = json.loads(json.dumps(document))
        candidate['robots']['r1'].update(changes)
        path.write_text(json.dumps(candidate), encoding='utf-8')
        with pytest.raises(ValueError):
            BridgeConfig.load(path)


def test_endpoint_reload_invalidates_http_receipts_and_refuses_enablement_or_allowlist_change(tmp_path, web):
    origin, replies, _ = web
    replies['/MD/NUMREG.VA?_TEMPLATE='] = (200, {'Content-Type': 'text/plain'}, b"[1] = 1 'Count'\n")
    path, document = policy(tmp_path, origin)
    with RobotBridge.from_config(path) as bridge:
        capture = bridge.read_file('r1', 'md:NUMREG.VA')
        updated = json.loads(json.dumps(document))
        updated['robots']['r1']['http_read_url'] = 'http://127.0.0.1:1'
        path.write_text(json.dumps(updated), encoding='utf-8')
        assert bridge.reload_connections()['status'] == 'reloaded'
        with pytest.raises(ValueError):
            bridge.reference_capture('r1', 'md:NUMREG.VA', capture['sha256'])
        updated['robots']['r1']['http_read_files'].append('md:POSREG.VA')
        path.write_text(json.dumps(updated), encoding='utf-8')
        with pytest.raises(ValueError, match='policy'):
            bridge.reload_connections()
