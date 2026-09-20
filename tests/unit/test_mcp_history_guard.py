"""A failed history checkpoint must prevent an MCP geometry write."""
from types import SimpleNamespace
import pytest
from klink.client import KLinkClient
from klink.mcp.history_guard import attach


def guarded_client(tmp_path, monkeypatch, result):
    agent = pytest.importorskip('vestigraph.agent_client')
    control = tmp_path / 'control.json'
    control.write_text('{}')
    monkeypatch.setattr(agent, 'control_path', lambda root: control)
    calls = []
    def prepare(name, arguments, root):
        calls.append(('checkpoint', name, arguments))
        return result
    monkeypatch.setattr(agent, 'call', prepare)
    client = KLinkClient(port=12345)
    client._running = True
    ctx = SimpleNamespace(_port=12345, _sessions=SimpleNamespace(root=tmp_path),
                          _method_specs={'shape.insert_box': {'mutates': True}})
    attach(ctx, client)
    def send(payload):
        calls.append(('write', payload['method']))
        client._pending[payload['id']].put({'ok': True, 'result': {'inserted': 1}})
    monkeypatch.setattr(client._transport, 'send', send)
    return client, calls


def test_checkpoint_failure_prevents_sending_edit(tmp_path, monkeypatch):
    client, calls = guarded_client(tmp_path, monkeypatch, {'ok': False})
    with pytest.raises(RuntimeError, match='write was not sent'):
        client.call('shape.insert_box', {'cell': 'TOP'})
    assert [item[0] for item in calls] == ['checkpoint']
    assert not client._pending


def test_checkpoint_completes_before_edit_is_sent(tmp_path, monkeypatch):
    client, calls = guarded_client(tmp_path, monkeypatch, {'ok': True, 'prepared': True})
    assert client.call('shape.insert_box', {'cell': 'TOP'}) == {'inserted': 1}
    assert [item[0] for item in calls] == ['checkpoint', 'write']
    assert calls[0][2] == {'session_id': 'klayout-12345'}


def test_read_calls_do_not_create_checkpoints(tmp_path, monkeypatch):
    client, calls = guarded_client(tmp_path, monkeypatch, {'ok': True})
    client.call('shape.query', {'cell': 'TOP'})
    assert [item[0] for item in calls] == ['write']
