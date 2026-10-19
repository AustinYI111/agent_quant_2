"""Bind portable feature-generation contracts to saved research models."""
from pathlib import Path
import hashlib,json


def bind_dataset_policy(model_directory,dataset_metadata,features,oneil=False):
    root=Path(model_directory)
    policy=dict(schema='horizon_feature_input_policy_v1',features=list(features),oneil=bool(oneil),
        price_basis=dataset_metadata.get('price_basis'),quality_policy=dataset_metadata.get('quality_policy'),
        source_dataset_metadata_sha256=hashlib.sha256(json.dumps(dataset_metadata,sort_keys=True).encode()).hexdigest(),
        transforms=dict(cross_section='same-signal-date 1%/99% winsorization, mean and population standard deviation; native NaN',
            momentum='63/126 sessions, 252-session return excluding latest21',risk='63-session volatility',
            technical_missingness='causal mode keeps eligible candidates with native missing technical features; no future-outcome filter',
            optional_oneil='252-session high,50-session volume mean,growth spread',
            minimum_listing_days=365,maximum_report_age_days=550,
            financials='as-of normalized publication/update/dependency visibility clocks; original provider vintages unverified'),
        code_sha256={name:hashlib.sha256(Path(__file__).with_name(name).read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()).hexdigest()
            for name in ['factors.py','public_conversion.py','causal_quality.py','horizon_labels.py','horizon_research.py','pipeline.py']})
    raw=json.dumps(policy,ensure_ascii=False,indent=2,allow_nan=False).encode('utf8');digest=hashlib.sha256(raw).hexdigest()
    for file in root.rglob('metadata.json'):
        meta=json.loads(file.read_text(encoding='utf8'))
        if meta.get('schema')!='horizon_ranker_v1':continue
        if meta.get('features')!=list(features):raise ValueError('saved research feature contract differs')
        (file.parent/'feature_input_policy.json').write_bytes(raw)
        meta['feature_input_policy_sha256']=digest
        meta['source_data_kind']='unverified_public_causal_quality' if dataset_metadata.get('quality_policy') else 'unverified_public'
        file.write_text(json.dumps(meta,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    (root/'feature_input_policy.json').write_bytes(raw)
    return digest


def validate_model_policy(directory,metadata):
    digest=metadata.get('feature_input_policy_sha256')
    if digest is None:return
    raw=(Path(directory)/'feature_input_policy.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('model feature input policy hash mismatch')
    policy=json.loads(raw)
    if policy.get('schema')!='horizon_feature_input_policy_v1' or policy.get('features')!=metadata['features']:
        raise ValueError('model feature input policy contract differs')
