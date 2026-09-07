"""Adversarial acceptance cases identified during the 1.0 audit."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from naviscoord.model import ElementRef, Clash, ClashExport, Issue
from naviscoord.analysis.noise import NoiseFilter
from naviscoord.analysis.cluster import ClashCluster, build_clusters
from naviscoord.analysis.rootcause import RootCauseDetector, _separation, _has_sleeve_near
from naviscoord.analysis.pipeline import AnalysisResult, analyze
from naviscoord.interop import revit_worklist
from naviscoord.state import SessionState, StateError
from naviscoord.history import snapshot, compare
from naviscoord import samples


def element(path, category, discipline, lo=(0.,0.,0.), hi=(1.,1.,1.), **props):
    return ElementRef(path_id=path, category=category, discipline=discipline,
                      bbox_min=lo, bbox_max=hi, props=props)


def hit(a,b,point=(0,0,0),guid='c',level=''):
    return Clash(guid,'t',guid,a,b,distance_m=-.4,point=point,level=level)


@pytest.mark.parametrize('a,b,da,db', [
    ('Generic Models','Walls','HVAC','EST'),
    ('Floors','Floors','EST','EST'),
    ('Doors','Floors','ARQ','ARQ'),
    ('Walls','Ceilings','ARQ','ARQ'),
])
def test_categories_never_prove_designed_contact(profile,a,b,da,db):
    clash=hit(element('a',a,da),element('b',b,db))
    assert NoiseFilter(profile).run([clash]).kept == [clash]


def test_explicit_host_matches_only_the_actual_host(profile):
    wall=element('w','Walls','ARQ')
    door=element('d','Doors','ARQ',**{'NC:HostPath':'w'})
    assert NoiseFilter(profile).run([hit(door,wall)]).reasons['architectural_hosting']==1
    assert NoiseFilter(profile).run([hit(door,replace(wall,path_id='other'))]).kept_count==1


def test_separation_handles_containment_and_wrong_direction():
    slab=element('s','Floors','EST',hi=(10,10,1))
    pipe=element('p','Pipes','HID',lo=(0,0,.4),hi=(5,.1,.5))
    offsets=_separation(hit(pipe,slab))
    assert pipe.bbox_max[2]-offsets['down_m'] < slab.bbox_min[2]
    assert pipe.bbox_min[2]+offsets['up_m'] > slab.bbox_max[2]
    assert offsets['down_m'] > .5 and offsets['up_m'] > .6


def test_close_opening_is_not_enough_to_resolve_a_crossing():
    host=element('w','Walls','ARQ')
    sleeve=element('s','Sleeves','EST',lo=(-.1,-.1,-.1),hi=(.1,.1,.1))
    assert not _has_sleeve_near((0,0,0),[sleeve],1,host=host)
    sleeve.props['NC:HostPath']='w'
    assert _has_sleeve_near((0,0,0),[sleeve],1,host=host)
    assert not _has_sleeve_near((.2,0,0),[sleeve],1,host=host)


def test_repeated_patterns_cross_grid_boundaries(profile):
    clusters=[ClashCluster([hit(element(f'a{i}','Pipes','HID'),element(f'b{i}','Beams','EST'),
        (x,0,3*i),f'c{i}',f'L{i}')]) for i,x in enumerate([.499,.501,.499])]
    assert len(RootCauseDetector(profile)._repeated_typology(clusters))==1


def test_pair_span_is_bounded_and_members_conserved(profile):
    a=element('a','Pipes','HID'); b=element('b','Beams','EST')
    cs=[hit(a,b,(0,0,z),str(z)) for z in [0,30]]
    groups=build_clusters(cs,profile)
    assert len(groups)==2
    assert all(g.span<=6 for g in groups)
    assert {c.guid for g in groups for c in g.clashes}=={'0','30'}


def two_issues():
    base=Issue('ISS-a','pair',('EST','HID'),['clash1'],(0,0,0),(0,0,0),(1,1,1),responsible='HID')
    return base,replace(base,issue_id='ISS-b',clash_ids=['clash2'],folded_into='ISS-a')


def test_folding_conserves_group_and_authoring_destinations(monkeypatch):
    from naviscoord import mcp_server as m
    issues=two_issues(); result=AnalysisResult(issues=list(issues))
    bridge=SimpleNamespace(apply_groups=lambda groups,*a,**kw:{'groups':groups})
    monkeypatch.setattr(m,'STATE',SimpleNamespace(require_mutable=lambda x:x,require_fresh_result=lambda:result,bridge=bridge))
    response=m.navis_apply_groups(limit=1,expected_document_fingerprint='test')
    assert {c for g in response['groups'] for c in g['clash_guids']}=={'clash1','clash2'}
    rows=[]
    for i,issue in enumerate(issues):
        row=issue.to_json(); row['targets']=[dict(actionable_in_revit=True,discipline='HID',source_file='HID.rvt',revit_element_id=str(i),name='pipe')]
        rows.append(row)
    worklist=revit_worklist({'source':{},'issues':rows})
    assert {i['revit_element_id'] for m in worklist['models'] for i in m['items']}=={'0','1'}


def test_revision_change_invalidates_analysis():
    bridge=SimpleNamespace(analysis_state=lambda:{'analysis_revision':'new'})
    state=SessionState(bridge=bridge,analysis_revision='old',result=AnalysisResult())
    with pytest.raises(StateError,match='changed'):
        state.require_fresh_result()
    assert state.result is None and not state.analysis_revision


def test_issue_identity_survives_ranking_and_input_reversal(profile):
    raw=samples.full_project_case()
    first=analyze(ClashExport.from_json(raw),profile)
    raw['clashes'].reverse()
    second=analyze(ClashExport.from_json(raw),profile)
    assert {tuple(sorted(i.clash_ids)):i.issue_id for i in first.issues}=={tuple(sorted(i.clash_ids)):i.issue_id for i in second.issues}


def test_history_preserves_changes_without_claiming_resolution():
    a,b=two_issues(); provenance={'document_fingerprint':'same','profile_checksum':'same'}
    before=snapshot(AnalysisResult(issues=[a,b]),provenance)
    after=snapshot(AnalysisResult(issues=[replace(a,severity=90)]),provenance)
    changes=compare(before,after)
    assert changes['changed']==['ISS-a']
    assert changes['no_longer_reported']==['ISS-b']
    assert changes['resolution_verified'] is False


def test_readonly_blocks_write_before_connecting(monkeypatch):
    from naviscoord.bridge import Bridge,BridgeError
    monkeypatch.setenv('NAVISCOORD_READ_ONLY','1')
    with pytest.raises(BridgeError,match='Read-only'):
        Bridge().call('clash/group',{'expected_document_fingerprint':'x','dry_run':False})


def test_full_inventory_preserves_opening_not_in_clash_export():
    raw={'penetration_inventory':{'complete':True,'scope':'all_loaded_models','elements':[{'path_id':'s','category':'Sleeves'}]}}
    export=ClashExport.from_json(raw)
    assert export.penetration_inventory['elements'][0]['path_id']=='s'


def test_empty_paths_do_not_prove_contact():
    from naviscoord.analysis.relations import designed_contact
    assert not designed_contact(element('', 'Walls', 'ARQ'), element('', 'Ceilings', 'ARQ'))


def test_identical_filenames_in_different_models_do_not_prove_host():
    from naviscoord.analysis.relations import hosted_by
    a=replace(element('a','Doors','ARQ', **{'Host Id':'42'}), source_file='model.nwc', model_index=0)
    b=replace(element('b','Walls','ARQ', **{'Element Id':'42'}), source_file='model.nwc', model_index=1)
    assert not hosted_by(a,b)


def test_coincident_density_uses_multiplicity_and_preserves_every_result():
    from naviscoord.analysis.cluster import _dbscan
    a=element('a','Walls','EST'); b=element('b','Ducts','HVAC')
    hits=[hit(a,b,guid=str(i)) for i in range(5000)]
    assert _dbscan(hits,.4,5000)==[hits]
    assert len(_dbscan(hits,.4,5001))==5000


def test_cad_material_layer_is_not_a_storey():
    from naviscoord.model import _level_from_sides
    a=element('a','PolyFace Mesh','', **{'Layer':'S_BRICK_FACING_F10'})
    b=element('b','PolyFace Mesh','', **{'Layer':'DOORS_WINDOWS'})
    assert _level_from_sides(a,b)==''
    assert _level_from_sides(replace(a,props={'Layer':'ORG_NIVEL_01'}),b)=='ORG_NIVEL_01'
    assert _level_from_sides(replace(a,props={'Layer':'STEEL','Level':'Mezzanine'}),b)=='Mezzanine'


def test_different_clash_elevations_do_not_establish_floors(profile):
    clusters = [ClashCluster([hit(element(f'a{i}', 'Pipes', 'HID'), element(f'b{i}', 'Beams', 'EST'),
                                  (0, 0, i * .1), str(i))]) for i in range(5)]
    assert RootCauseDetector(profile)._repeated_typology(clusters) == []


def test_unknown_disciplines_do_not_produce_movement_instructions(profile):
    from naviscoord.analysis.severity import SeverityScorer, suggest_action
    cluster = ClashCluster([hit(element('a', 'PolyFace Mesh', 'OTRO'), element('b', 'PolyFace Mesh', 'OTRO'))])
    verdict = SeverityScorer(profile).score(cluster)
    assert 'provisional' in ' '.join(verdict.why)
    assert 'repite el análisis' in suggest_action(cluster, verdict, profile)


def test_snapshot_records_real_matrix_coverage():
    from naviscoord.analysis.pipeline import MatrixCoverage
    result = AnalysisResult(coverage=MatrixCoverage(total=2, ran=1, never_run=['pending']))
    data = snapshot(result, {'document_fingerprint': 'same'})
    assert data['coverage']['matrix']['tests_never_run'] == ['pending']
    assert data['coverage']['matrix']['complete'] is False


@pytest.mark.parametrize('invalid', [[], {'schema': 'naviscoord.analysis-snapshot/1', 'issues': [{}]},
    {'schema': 'naviscoord.analysis-snapshot/1', 'issues': [], 'provenance': {}}])
def test_comparison_rejects_malformed_snapshots(invalid):
    with pytest.raises(ValueError):
        compare(invalid, invalid)
