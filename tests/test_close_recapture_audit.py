from pathlib import Path
import importlib.util
import pandas as pd
import pytest

spec=importlib.util.spec_from_file_location('close_recapture_audit',Path(__file__).resolve().parents[1]/'scripts/audit_close_recapture.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def test_revised_values_and_added_security_keys_remain_visible(tmp_path):
    old=tmp_path/'old.csv';new=tmp_path/'new.csv'
    pd.DataFrame(dict(code=['sh.600000','sz.000001'],name=['A','B'],price=['10','20'])).to_csv(old,index=False)
    pd.DataFrame(dict(code=['sz.000001','sh.600000','sh.000816'],name=['B','changed','index'],price=['20','10','30'])).to_csv(new,index=False)
    result=audit.compare_csv(old,new,['code'])
    assert result['added_keys']==['sh.000816'] and result['changed_cells']=={'name':1}
    assert not result['identical'] and not result['all_common_values_exact']
    assert result['original_rows']==2 and result['new_rows']==3


def test_same_key_values_in_different_row_order_are_identical(tmp_path):
    frame=pd.DataFrame(dict(code=['sh.600000','sz.000001'],value=['','10']))
    old=tmp_path/'old.csv';new=tmp_path/'new.csv';frame.to_csv(old,index=False);frame.iloc[::-1].to_csv(new,index=False)
    result=audit.compare_csv(old,new,['code'])
    assert result['identical'] and result['original_sha256']!=result['new_sha256']


@pytest.mark.parametrize('fault',['duplicate','empty_key','removed_column'])
def test_ambiguous_keys_rejected_and_removed_columns_not_hidden(tmp_path,fault):
    old=tmp_path/'old.csv';new=tmp_path/'new.csv';frame=pd.DataFrame(dict(code=['sh.600000'],value=['10']));frame.to_csv(old,index=False)
    if fault=='duplicate':frame=pd.concat([frame,frame],ignore_index=True)
    elif fault=='empty_key':frame.loc[0,'code']=''
    else:frame=frame.drop(columns='value')
    frame.to_csv(new,index=False)
    if fault=='removed_column':
        result=audit.compare_csv(old,new,['code']);assert not result['identical'] and result['removed_columns']==['value']
    else:
        with pytest.raises(ValueError,match='keys'):audit.compare_csv(old,new,['code'])
