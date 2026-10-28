import importlib.util
import json
from pathlib import Path
import sys
import pytest

scripts=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(scripts))
from run_extended_eval import resume_report


@pytest.mark.parametrize('bad', ['version','question','order'])
def test_resume_rejects_changed_scope_or_case_identity(tmp_path,bad):
    value={'case_sha256':'frozen','split':'all','case_filter':'','corpus_version':'current',
        'cases':[{'id':'case-1','question':'original','passed':True}]}
    if bad=='version':value['corpus_version']='old'
    if bad=='question':value['cases'][0]['question']='changed'
    if bad=='order':value['cases'][0]['id']='case-2'
    path=tmp_path/'report.json';path.write_text(json.dumps(value))
    with pytest.raises(ValueError):resume_report(path,'frozen','all','',[{'id':'case-1','question':'original'}],'current')
