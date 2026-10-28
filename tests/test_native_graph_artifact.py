import json
import copy
from types import SimpleNamespace
import pytest
from icp.graph_schema import profile_digest
from icp.native_graph_artifact import artifact_digest,load_release,apply_release,field_equal,build_publication_graph


def fixture():
    properties={'func_id':'F','name':'功能F','description':'来源行为','viewpoint':'SYSTEM_BLACKBOX',
        'source_ids':['E'],'unknown_fields':[],'unknown_reason':'未使用'}
    node={'id':'F','label':'功能F','type':'TopLevelFunction','properties':properties,'evidence_ids':['E']}
    graph={'id':'function','type':'function','nodes':[node],'edges':[],'corpus_version':'version'}
    record={'id':'F','kind':'function','fields':{'parent_id':None,'description':'来源行为'}}
    kb=SimpleNamespace(version='version',records={'F':record},by_kind={'function':[record],'rbd':[],'fta':[],'fmeca':[]},
        data={'archive_sha256':'archive','graphs':[graph],'evidence':{'E':{'document':'来源文档','section':'功能说明'}}})
    payload={'passed':True,'all_source_units_selected':True,'source_unit_ids':['F'],'corpus_version':'version',
        'archive_sha256':'archive','graph_profile_sha256':profile_digest(),'native_revision':'test',
        'nodes':[copy.deepcopy(node)],'edges':[]}
    payload['artifact_sha256']=artifact_digest(payload)
    return kb,graph,payload


def test_verified_artifact_marks_native_fields_and_keeps_source_extras_separate(tmp_path):
    kb,graph,payload=fixture();path=tmp_path/'native.json';path.write_text(json.dumps(payload))
    release=load_release(kb,path);result=apply_release(graph,release)
    assert result['nodes'][0]['properties']['native_property_fields']
    assert result['native_revision']=='test'


@pytest.mark.parametrize('defect',['version','digest','missing_node','unknown_source'])
def test_invalid_native_artifact_is_rejected_instead_of_using_engineering_backfill(tmp_path,defect):
    kb,graph,payload=fixture()
    if defect=='version':payload['corpus_version']='old'
    if defect=='digest':payload['artifact_sha256']='wrong'
    if defect=='missing_node':payload['nodes']=[]
    if defect=='unknown_source':payload['nodes'][0]['evidence_ids']=['unknown']
    if defect!='digest':payload['artifact_sha256']=artifact_digest(payload)
    path=tmp_path/'native.json';path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):load_release(kb,path)


def test_native_required_node_cannot_fall_back_to_an_existing_projection_node():
    kb,graph,payload=fixture()
    with pytest.raises(ValueError,match='unavailable'):
        apply_release(graph,{'nodes_by_id':{},'edge_keys':set(),'native_revision':'test','artifact_sha256':'test'})


def test_readback_accepts_metadata_order_but_rejects_lost_or_changed_values():
    assert field_equal(['trigger','input'],['input','trigger'],'unknown_fields')
    assert field_equal(['E2','E1'],['E1','E2'],'source_ids')
    assert not field_equal(['input'],['input','trigger'],'unknown_fields')
    assert not field_equal(['input','input'],['input','trigger'],'unknown_fields')
    assert not field_equal(0,False,'is_redundant')
    assert not field_equal(.25,.5,'probability')
    assert not field_equal(float('nan'),.5,'probability')


def test_reference_merge_preserves_all_explicit_native_unknown_properties():
    kb,graph,payload=fixture()
    native=copy.deepcopy(payload['nodes'][0])
    native['properties'].update(optional_unknown=None,unknown_fields=['optional_unknown'],unknown_reason='source does not supply it')
    graph['nodes'][0]['properties'].update(optional_unknown=None,unknown_fields=['optional_unknown'])
    stub={'id':'reference','type':'reference','corpus_version':'version','nodes':[
        {'id':'F','type':'Reference','label':'功能F','properties':{'reference':True},'evidence_ids':['E']}],'edges':[]}
    kb.data['graphs']=[stub,graph]
    release={'nodes_by_id':{'F':native},'edge_keys':set(),'native_revision':'test','artifact_sha256':'test'}
    result=build_publication_graph(kb,release)
    node=result['nodes'][0];properties=node['properties']
    assert node['type']=='TopLevelFunction'
    assert 'optional_unknown' in properties and properties['optional_unknown'] is None
    assert 'optional_unknown' in properties['unknown_fields']
    assert not set(properties['source_projection_extra_fields'])&{'native_property_fields','source_projection_extra_fields','native_revision'}
