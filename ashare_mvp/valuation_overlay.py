"""Reviewed primary-notice reference overlays; holder entitlements stay fixed."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlparse
import json
import pandas as pd
from .phase2_protocol import sha256,utc,day
from .reference_overrides import load_reference_evidence,apply_reference_overrides


def reviewed_valuation_references(actions,evidence,project,calendar,research_end):
    root=Path(project).resolve()
    def local(value):
        p=Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):raise ValueError('project-relative valuation evidence required')
        path=(root/p).resolve()
        if not path.is_relative_to(root):raise ValueError('valuation evidence escapes project')
        return path
    file=local(evidence);records=load_reference_evidence(file)
    required={'source_file','source_capture_file','source_capture_sha256','observed_at_utc','expected_share_multiplier','reference_share_multiplier'}
    if records.empty or not required.issubset(records):raise ValueError('complete captured reference evidence required')
    end=day(research_end)
    if pd.to_datetime(records.ex_date).gt(end).any():raise ValueError('independent reference event cannot enter research evaluation')
    hashes={file.relative_to(root).as_posix():sha256(file)}
    for row in records.itertuples(index=False):
        source=local(row.source_file);capture=local(row.source_capture_file)
        if sha256(source)!=row.source_sha256 or sha256(capture)!=row.source_capture_sha256:raise ValueError('primary valuation notice or capture changed')
        with source.open('rb') as stream:
            if stream.read(5)!=b'%PDF-':raise ValueError('primary notice must be PDF')
        if urlparse(row.source_url).scheme!='https' or urlparse(row.source_url).hostname not in {'static.cninfo.com.cn','dataclouds.cninfo.com.cn','static.sse.com.cn','www.sse.com.cn','disc.static.szse.cn'}:
            raise ValueError('primary official notice host required')
        observation=utc(row.observed_at_utc)
        if observation>pd.Timestamp(datetime.now(timezone.utc)):raise ValueError('future notice observation rejected')
        raw=json.loads(capture.read_text(encoding='utf8'))
        if raw.get('schema')!='original_candidate_notice_capture_v1' or raw.get('code')!=str(row.symbol).split('.')[-1] or raw.get('ex_date')!=str(day(row.ex_date).date()):
            raise ValueError('notice capture identity differs from reviewed reference')
        matches=[item for item in raw.get('notices',[]) if item['sha256']==row.source_sha256 and item['url']==row.source_url]
        if len(matches)!=1 or (capture.parent/matches[0]['file']).resolve()!=source or utc(matches[0]['observed_at_utc'])!=observation:
            raise ValueError('reviewed reference observation is not the original captured notice')
        hashes[source.relative_to(root).as_posix()]=row.source_sha256
        hashes[capture.relative_to(root).as_posix()]=row.source_capture_sha256
    economics=['symbol','ex_date','pay_date','cash_per_share','share_multiplier']
    before=actions[economics].reset_index(drop=True)
    result=apply_reference_overrides(actions,records,calendar)
    if not before.equals(result[economics].reset_index(drop=True)):
        raise ValueError('reference-only evidence changed actual holder economics')
    return result,dict(schema='reviewed_candidate_valuation_reference_overlay_v1',events=len(records),source_hashes=hashes,
        actual_holder_economics_changed=False,training_features_or_labels_changed=False,
        first_observed_clocks_preserved=True,historical_vintages_certified=False)
