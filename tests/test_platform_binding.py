import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.enable_platform import configure


def test_wrong_knowledge_checkpoint_does_not_replace_private_configuration(tmp_path):
    (tmp_path/'data/processed').mkdir(parents=True)
    (tmp_path/'.secrets').mkdir()
    (tmp_path/'data/processed/knowledge.json').write_text(json.dumps({'version':'current'}))
    (tmp_path/'.secrets/ragflow-import.json').write_text(json.dumps({'version':'old'}))
    env=tmp_path/'.env';env.write_text('TOOL_BACKEND=local\n')
    with pytest.raises(ValueError,match='current knowledge version'):configure(tmp_path,native=True)
    assert env.read_text()=='TOOL_BACKEND=local\n'


def test_incomplete_index_does_not_enable_platform(tmp_path):
    (tmp_path/'data/processed').mkdir(parents=True)
    (tmp_path/'.secrets').mkdir()
    (tmp_path/'data/processed/knowledge.json').write_text(json.dumps({'version':'current','chunks':[{}],'entity_chunks':[]}))
    (tmp_path/'.secrets/ragflow-import.json').write_text(json.dumps({'version':'current','chunks':{}}))
    env=tmp_path/'.env';env.write_text('TOOL_BACKEND=local\n')
    with pytest.raises(ValueError,match='Complete curated'):configure(tmp_path)
    assert env.read_text()=='TOOL_BACKEND=local\n'
