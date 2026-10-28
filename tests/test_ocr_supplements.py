import json
import pytest
from icp.ocr_supplements import load_pages,supplement_digest


def test_page_source_review_and_duplicates_are_required(tmp_path):
    p=tmp_path/'pages.json';doc={'id':'DOC-001','corpus':'research','sha256':'source'}
    row={'document_id':'DOC-001','page':1,'source_sha256':'source','reviewed':True,'text':'source text'}
    def write(rows):p.write_text(json.dumps({'pages':rows}),encoding='utf-8')
    write([row]);assert load_pages(p,[doc])[('DOC-001',1)]['text']=='source text'
    initial=supplement_digest(p)
    for bad in [{**row,'reviewed':False},{**row,'source_sha256':'different'},{**row,'text':''},{**row,'page':0}]:
        write([bad])
        with pytest.raises(ValueError):load_pages(p,[doc])
    write([row,row])
    with pytest.raises(ValueError):load_pages(p,[doc])
    assert initial!=supplement_digest(p)
