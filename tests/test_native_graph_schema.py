import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.native_schema_graphs import audit,windows,properties
import pytest


def fixture():
    nodes=[{'id':n,'type':'SubFunction','properties':{'id':n,'name':n,'source_ids':['source'],'input':None}} for n in ['A','B','C']]
    return {'nodes':nodes,'edges':[{'source':'A','target':'B','type':'has_subfunction'},{'source':'B','target':'C','type':'has_subfunction'}]}


def exported(graph):
    return {'templates':[{'entities':[{'name':n['id'],'type':n['type'],'description':json.dumps(n['properties'])} for n in graph['nodes']],
        'relations':[{'from':e['source'],'to':e['target'],'type':e['type']} for e in graph['edges']]}]}


def test_windows_preserve_all_nodes_and_directed_edges_under_limit():
    graph=fixture();parts=windows(graph,2)
    assert all(len(p['nodes'])<=2 for p in parts)
    assert {n['id'] for p in parts for n in p['nodes']}=={'A','B','C'}
    assert [e for p in parts for e in p['edges']]==graph['edges']


def test_native_fields_can_be_parsed_without_engineering_backfill():
    graph=fixture();result=audit(graph,[exported(graph)])
    assert result['passed'] and result['verified_native_entities']==3
    assert result['normalized']['nodes'][0]['properties']['input'] is None


def test_missing_native_candidate_is_not_filled_from_source_gold():
    graph=fixture();raw=exported(graph);raw['templates'][0]['entities'].pop()
    result=audit(graph,[raw])
    assert not result['passed'] and result['missing_entities']==['C']
    assert {n['id'] for n in result['normalized']['nodes']}=={'A','B'}


def test_native_wrong_values_extra_fields_and_reversed_edges_are_rejected():
    graph=fixture();raw=exported(graph)
    props=graph['nodes'][0]['properties']|{'input':'guessed','probability':.99}
    raw['templates'][0]['entities'][0]['description']=json.dumps(props)
    raw['templates'][0]['relations'][0]['from']='B';raw['templates'][0]['relations'][0]['to']='A'
    result=audit(graph,[raw])
    assert not result['passed'] and result['errors'] and 'A' in result['missing_entities']


def test_native_display_names_resolve_only_through_explicit_extracted_ids():
    graph=fixture()
    names={'A':'甲功能','B':'乙功能','C':'丙功能'}
    for node in graph['nodes']:node['properties']['name']=names[node['id']]
    raw=exported(graph)
    for entity in raw['templates'][0]['entities']:entity['name']=names[entity['name']]
    for edge in raw['templates'][0]['relations']:
        edge['from']=names[edge['from']];edge['to']=names[edge['to']]
    result=audit(graph,[raw])
    assert result['passed'] and result['explicit_native_id_aliases_normalized']==3
    raw['templates'][0]['entities'].pop()
    assert not audit(graph,[raw])['passed']


def test_native_dictionary_description_is_parsed_as_data_without_execution():
    assert properties({'description':"{'id': 'A', 'probability': None, 'flag': False}"})=={'id':'A','probability':None,'flag':False}
    with pytest.raises((ValueError,SyntaxError)):
        properties({'description':"__import__('os').system('must-not-run')"})


def test_mixed_literal_spellings_normalize_data_without_changing_source_strings():
    value=properties({'description':'{"probability":null,"detection_level":None,"flag":true,"description":"None null true"}'})
    assert value=={'probability':None,'detection_level':None,'flag':True,'description':'None null true'}
    with pytest.raises(ValueError):properties({'description':'{"value":dangerous_name,"other":None}'})
    with pytest.raises(ValueError):properties({'description':'{None: 1}'})


def test_native_relation_display_names_require_unambiguous_native_properties():
    graph=fixture()
    for node in graph['nodes']:node['properties']['name']='display-'+node['id']
    raw=exported(graph)
    for edge in raw['templates'][0]['relations']:
        edge['from']='display-'+edge['from'];edge['to']='display-'+edge['to']
    assert audit(graph,[raw])['passed']
    graph['nodes'][1]['properties']['name']='display-A'
    raw=exported(graph)
    raw['templates'][0]['relations'][0]['from']='display-A'
    assert not audit(graph,[raw])['passed']


def test_invalid_duplicate_cannot_overwrite_a_separately_valid_native_node():
    graph=fixture();good=exported(graph);bad=exported(graph)
    props=graph['nodes'][0]['properties']|{'input':'invented'}
    bad['templates'][0]['entities'][0]['description']=json.dumps(props)
    checked=audit(graph,[bad,good])
    assert checked['passed'] and not checked['raw_strict_passed']
    assert checked['quarantined_invalid_duplicates']
    accepted=next(n for n in checked['normalized']['nodes'] if n['id']=='A')
    assert accepted['properties']['input'] is None
    # No separately valid A exists: quarantine cannot manufacture one.
    good['templates'][0]['entities'].pop(0)
    assert not audit(graph,[bad,good])['passed']


def test_rejected_extra_candidates_cannot_enter_the_published_native_graph():
    graph=fixture();raw=exported(graph)
    raw['templates'][0]['entities'].append({'name':'INVENTED','type':'SubFunction','description':'{}'})
    raw['templates'][0]['relations'].append({'from':'C','to':'A','type':'has_subfunction'})
    result=audit(graph,[raw])
    assert result['accepted_graph_passed'] and not result['raw_strict_passed']
    assert len(result['rejected_native_candidates'])==2
    assert {n['id'] for n in result['normalized']['nodes']}=={'A','B','C'}
    assert len(result['normalized']['edges'])==2


def test_incomplete_native_nodes_cannot_be_assembled_field_by_field():
    graph=fixture();one=exported(graph);two=exported(graph)
    p=graph['nodes'][0]['properties']
    one['templates'][0]['entities'][0]['description']=json.dumps({k:v for k,v in p.items() if k!='input'})
    two['templates'][0]['entities'][0]['description']=json.dumps({k:v for k,v in p.items() if k!='source_ids'})
    result=audit(graph,[one,two])
    assert not result['accepted_graph_passed'] and 'A' in result['missing_entities']
