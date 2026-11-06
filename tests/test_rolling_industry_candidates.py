import pandas as pd
import pytest
from ashare_mvp.rolling_candidates import key_hash
from ashare_mvp.rolling_industry_candidates import check_matched_fold


@pytest.fixture
def fold():
    train=pd.DataFrame(dict(date=pd.to_datetime(['2021-06-01','2021-06-01','2021-07-01']),symbol=['sh.600000','sh.600001','sh.600000'],label_end=pd.to_datetime(['2021-06-08','2021-06-08','2021-07-08'])))
    test=pd.DataFrame(dict(date=pd.to_datetime(['2021-07-30','2021-07-30']),symbol=['sh.600000','sh.600001']))
    cutoff=pd.Timestamp('2021-07-28')
    control=dict(train_rows=3,train_dates=2,prediction_rows=2,prediction_dates=1,train_keys_sha256=key_hash(train),prediction_keys_sha256=key_hash(test),max_label_end='2021-07-08',cutoff='2021-07-28')
    return train,test,cutoff,control


def test_fixed_fold_matches_original_control_without_retraining_it(fold):
    train,test,cutoff,control=fold
    assert check_matched_fold(control,train,test,cutoff)==control


def test_same_size_but_different_predicted_issuer_rejected(fold):
    train,test,cutoff,control=fold;test=test.copy();test.loc[0,'symbol']='sh.600999'
    with pytest.raises(ValueError,match='original matched control'):check_matched_fold(control,train,test,cutoff)


def test_training_order_is_bound_to_original_control(fold):
    train,test,cutoff,control=fold
    with pytest.raises(ValueError,match='original matched control'):check_matched_fold(control,train.iloc[::-1],test,cutoff)


def test_missing_control_key_does_not_silently_skip_comparison(fold):
    train,test,cutoff,control=fold;control=control.copy();control.pop('prediction_keys_sha256')
    with pytest.raises(ValueError,match='original matched control'):check_matched_fold(control,train,test,cutoff)


def test_same_stock_keys_with_different_label_end_rejected(fold):
    train,test,cutoff,control=fold;train=train.copy();train.loc[2,'label_end']=pd.Timestamp('2021-07-09')
    with pytest.raises(ValueError,match='original matched control'):check_matched_fold(control,train,test,cutoff)


def test_matching_metadata_cannot_bypass_strict_embargo(fold):
    train,test,cutoff,control=fold;train=train.copy();train.loc[2,'label_end']=cutoff;control=control.copy();control['max_label_end']=str(cutoff.date())
    with pytest.raises(ValueError,match='strict mature-label'):check_matched_fold(control,train,test,cutoff)
