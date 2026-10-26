from copy import deepcopy
import pytest
from ashare_mvp.industry_portfolios import matched_family


@pytest.fixture
def family():
    protocol=dict(horizons=['5d','20d','12m'],validation=dict(candidate_estimators=[30,60],research_validation_windows=[(f'{y}-07-01',f'{y+1}-06-30') for y in range(2021,2026)]))
    common=dict(independent_data_used=False,model_selection_performed=False,formal_model_promoted=False,protocol_sha256='sealed')
    controls=dict(common,status='candidate_fits_complete_selection_pending',models=[])
    fitting=dict(common,status='candidate_fits_complete_selection_pending',source_hashes={'controls/manifest.json':'bound'},models=[])
    accounts=dict(common,status='ranking_accounts_evaluated_selection_pending',source_hashes={'controls/manifest.json':'bound'},accounts=[])
    for h in protocol['horizons']:
        for t in [30,60]:
            accounts['accounts'].append(dict(horizon=h,n_estimators=t,group='baseline',status='complete_calendar_mature_ranking_account'))
            for s,e in protocol['validation']['research_validation_windows']:
                r=dict(horizon=h,n_estimators=t,validation_start=s,validation_end=e,group='baseline',model_sha256=f'{h}-{t}-{s}',train_keys_sha256='ordered train',prediction_keys_sha256='ordered test',max_label_end='before embargo',cutoff='before test',train_rows=100,prediction_rows=50)
                controls['models'].append(r)
                for group in ['industry','combined']:
                    fitting['models'].append(dict(r,group=group,matched_control_model_sha256=r['model_sha256']))
    return fitting,accounts,controls,protocol,'controls/manifest.json','bound'


def test_complete_family_reuses_six_baselines_without_refitting(family):
    assert len(matched_family(*family))==6


@pytest.mark.parametrize('changed',['lineage','same_count_wrong_fold','duplicate_fold','control_model','ordered_training_keys','partial_baseline','duplicate_baseline','selected','independent'])
def test_changed_inputs_cannot_be_compared_as_the_same_matched_experiment(family,changed):
    args=deepcopy(family);f,a,c,*_=args
    if changed=='lineage': a['source_hashes']['controls/manifest.json']='other training'
    elif changed=='same_count_wrong_fold': f['models'][0]['validation_start']='2020-07-01'
    elif changed=='duplicate_fold': f['models'][0]=deepcopy(f['models'][1])
    elif changed=='control_model': f['models'][0]['matched_control_model_sha256']='different model'
    elif changed=='ordered_training_keys': f['models'][0]['train_keys_sha256']='same count different order'
    elif changed=='partial_baseline': a['accounts'][0]['status']='incomplete_held_valuation'
    elif changed=='duplicate_baseline': a['accounts'][0]=deepcopy(a['accounts'][1])
    elif changed=='selected': a['model_selection_performed']=True
    elif changed=='independent': c['independent_data_used']=True
    with pytest.raises(ValueError): matched_family(*args)
