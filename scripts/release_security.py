"""Collect local credentials in memory for exact-byte scans; never display values."""
import json
from pathlib import Path
from dotenv import dotenv_values


def private_values(root:Path):
    values=set()
    def remember(value):
        if isinstance(value,str) and len(value)>=8 and '${' not in value and value not in {'<REDACTED>','***'}:
            values.add(value.encode('utf-8'))
    def credential_name(key):
        # TOKEN_LIMIT_MODE and MAX_TOKENS are settings, not credentials.
        return key.endswith(('password','token','api_key','secret','secret_key','daemon_key','inner_api_key'))
    def collect(value,key=''):
        if isinstance(value,dict):
            for name,item in value.items():collect(item,name.lower())
        elif isinstance(value,list):
            for item in value:collect(item,key)
        elif credential_name(key):remember(value)
    private=root/'.secrets'
    for path in [root/'.env',*private.glob('*.env'),*private.glob('platforms/*/.env')]:
        if path.is_file():collect(dict(dotenv_values(path)))
    for path in private.glob('*.json'):
        try:value=json.loads(path.read_text(encoding='utf-8-sig'))
        except (ValueError,UnicodeError):continue
        collect(value)
        if any(marker in path.name for marker in ['tool-keys','dify-tools']):
            if isinstance(value,dict):
                for item in value.values():remember(item)
    return values


def scan(content,values,name):
    if any(value in content for value in values):raise RuntimeError('Private credential detected in '+name)
