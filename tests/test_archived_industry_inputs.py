import pandas as pd
import pytest
from ashare_mvp.archived_industry_inputs import canonical_members


@pytest.fixture
def changes():
    return pd.DataFrame([dict(old_symbol='sz.000022',new_symbol='sz.001872',announcement_date='2018-12-21',effective_date='2018-12-26',share_ratio=1,source_url='https://static.cninfo.com.cn/finalpage/2018-12-21/1205681867.PDF')])


def frame(codes):
    return pd.DataFrame(dict(security_code=codes,industry_code=['C39']*len(codes),source_page=list(range(1,len(codes)+1))))


def test_verified_alias_uses_stable_issuer_and_preserves_source(changes):
    result,counts=canonical_members(frame(['001872','600000']),changes)
    assert result.symbol.tolist()==['sz.000022','sh.600000']
    assert result.source_symbol.tolist()==['sz.001872','sh.600000']
    assert counts['same_issuer_aliases']==1
    assert result.source_page.tolist()==[1,2]


def test_b_share_and_beijing_rows_are_explicitly_excluded(changes):
    result,counts=canonical_members(frame(['000001','688001','200001','900001','430001','920001']),changes)
    assert result.symbol.tolist()==['sz.000001','sh.688001']
    assert counts['raw_rows']==6 and counts['a_share_rows']==2
    assert counts['excluded_b_beijing_or_other_codes']==4


def test_two_aliases_never_silently_collapse_one_release(changes):
    with pytest.raises(ValueError,match='collide'):
        canonical_members(frame(['000022','001872']),changes)


def test_numeric_security_code_is_rejected_instead_of_losing_leading_zero(changes):
    with pytest.raises(ValueError,match='literal six-digit'):
        canonical_members(frame([600000]),changes)


def test_duplicate_raw_security_is_not_arbitrarily_deduplicated(changes):
    with pytest.raises(ValueError,match='unique raw'):
        canonical_members(frame(['000001','000001']),changes)


def test_share_exchange_is_not_treated_as_simple_identity_change(changes):
    changes=changes.assign(share_ratio=changes.share_ratio.astype(float));changes.loc[0,'share_ratio']=.5
    with pytest.raises(ValueError,match='share exchange'):
        canonical_members(frame(['001872']),changes)


def test_malformed_industry_or_empty_input_rejected(changes):
    f=frame(['000001']);f.loc[0,'industry_code']='unknown'
    with pytest.raises(ValueError,match='industry codes'):canonical_members(f,changes)
    with pytest.raises(ValueError,match='complete literal'):canonical_members(f.iloc[:0],changes)
