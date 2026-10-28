from icp.knowledge import Knowledge
from icp.graphs import graph_for
from icp.graph_schema import typed_graph,SCHEMAS,merge_node


def test_schema_shape_keeps_missing_fields_unknown_and_source_bound():
    kb=Knowledge();g=typed_graph(kb,graph_for(kb,'function','all'))
    assert not any(n['type']=='SystemRequirement' for n in g['nodes'])
    for n in g['nodes']:
        if n['type'] in SCHEMAS:assert set(SCHEMAS[n['type']])<=set(n['properties'])
        assert set(n['properties']['source_ids'])<=set(kb.data['evidence'])
    leaves=[n for n in g['nodes'] if n['type']=='SubFunction']
    assert len(leaves)==24 and all(n['properties']['trigger'] is None for n in leaves)


def test_redundancy_and_reliability_are_not_fabricated_without_time():
    kb=Knowledge();g=typed_graph(kb,graph_for(kb,'rbd','RBD-SYN-001'));nodes={n['id']:n for n in g['nodes']}
    assert nodes['RBD-SYN-001-BLOCK-PSU-A']['properties']['is_redundant'] is True
    assert nodes['RBD-SYN-001-BLOCK-ICU']['properties']['is_redundant'] is False
    success=nodes['RBD-SYN-001-SUCCESS']['properties']
    assert success['system_reliability'] is None and success['system_mtbf'] is None
    assert success['reliability_samples'][2]=={'hours':10.0,'reliability':'0.99951007'}


def test_reference_views_do_not_erase_fta_event_probabilities():
    kb=Knowledge();merged={}
    for graph in kb.data['graphs']:
        for node in typed_graph(kb,graph)['nodes']:merged[node['id']]=merge_node(merged.get(node['id']),node)
    for model in kb.by_kind['fta']:
        for event in model['fields']['events']:
            properties=merged[event['id']]['properties']
            assert properties['probability']==event['probability']
            assert properties['probability_unit']=='dimensionless'
            assert 'probability' not in properties['unknown_fields']


def test_schema_relations_preserve_source_direction_and_label_derivation():
    kb=Knowledge();g=typed_graph(kb,graph_for(kb,'function','all'))
    parents={r['id']:r['fields']['parent_id'] for r in kb.by_kind['function'] if r['fields']['parent_id']}
    inverses={e['source']:e for e in g['edges'] if e['type']=='is_subfunction_of'}
    assert {k:e['target'] for k,e in inverses.items()}==parents
    assert all(e['derivation'] and e['evidence_ids'] for e in inverses.values())
    fta=typed_graph(kb,graph_for(kb,'fta','FTA-SYN-001'))
    # Every causal shortcut must be witnessed by an event -> gate -> event
    # path. It is never a new independent event used in probability sums.
    inputs=[e for e in fta['edges'] if e['type']=='gate_input']
    outputs=[e for e in fta['edges'] if e['type']=='gate_output']
    causal=[e for e in fta['edges'] if e['type']=='lead_to_event']
    assert causal and all(any(a['source']==e['source'] and a['target']==b['source'] and b['target']==e['target'] for a in inputs for b in outputs) for e in causal)


def test_failure_mode_grade_is_not_a_detector_or_cause_grade():
    kb=Knowledge();g=typed_graph(kb,graph_for(kb,'fmeca','FM-SYN-001'));nodes={n['id']:n for n in g['nodes']}
    assert nodes['FM-SYN-001']['properties']['severity']=='II'
    for suffix in ['cause','local_effect','final_effect','detection','mitigation']:
        p=nodes['FM-SYN-001-'+suffix]['properties']
        assert p['severity'] is None and p['occurrence'] is None
        assert p['related_failure_mode_severity']=='II'
        assert 'severity' in p['unknown_fields']
