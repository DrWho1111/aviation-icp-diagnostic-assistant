import yaml
import pytest
from scripts.prepare_isolated_rebuild import isolated_compose
from scripts.release_security import private_values,scan


def test_isolation_supports_upstream_both_default_port_syntaxes(tmp_path):
    path=tmp_path/'compose.yaml'
    path.write_text(yaml.safe_dump({'services':{'example':{'container_name':'live-name','ports':[
        '127.0.0.1:${TEI_PORT-6380}:80','127.0.0.1:${HTTP_PORT:-8081}:9380']}},
        'volumes':{'data':{'name':'live-data'}},'networks':{'default':{'name':'live-network'}}}),encoding='utf-8')
    isolated_compose(path,{},10000)
    value=yaml.safe_load(path.read_text(encoding='utf-8'))
    ports=value['services']['example']['ports']
    assert [(p['published'],p['target']) for p in ports]==[('16380',80),('18081',9380)]
    assert all(p['host_ip']=='127.0.0.1' for p in ports)
    assert 'container_name' not in value['services']['example']
    assert 'name' not in value['volumes']['data'] and 'name' not in value['networks']['default']


def test_isolation_rejects_external_live_volumes(tmp_path):
    path=tmp_path/'compose.yaml';path.write_text('services: {}\nvolumes:\n  data:\n    external: true\n',encoding='utf-8')
    with pytest.raises(ValueError):isolated_compose(path,{},10000)


def test_release_scan_covers_native_keys_without_treating_token_limit_as_secret(tmp_path):
    private=tmp_path/'.secrets';private.mkdir()
    (tmp_path/'.env').write_text('API_KEY=SECRET-KEY-TEST\nRERANK_TOKEN_LIMIT_MODE=truncate\n',encoding='utf-8')
    (private/'dify-native-main.json').write_text('{"api_key":"SECRET-NATIVE-TEST"}',encoding='utf-8')
    values=private_values(tmp_path)
    scan(b'{"truncate":false}',values,'public.py')
    with pytest.raises(RuntimeError):scan(b'SECRET-NATIVE-TEST',values,'unsafe.txt')
