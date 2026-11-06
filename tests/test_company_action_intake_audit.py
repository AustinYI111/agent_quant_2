import json,runpy,sys
from pathlib import Path
import pandas as pd
import pytest
from ashare_mvp.reviewed_cash_extension import extend
from ashare_mvp.phase2_protocol import sha256
from test_reviewed_cash_extension import fixture


@pytest.mark.parametrize('tamper',[None,'provider_symbol','payment_provider_symbol'])
def test_independent_cash_audit_rejects_arithmetically_unchanged_wrong_trading_identity(tmp_path,monkeypatch,tamper):
    args,root,work,_=fixture(tmp_path,provider_code='302132.SZ',canonical_symbol='sz.300114')
    extend(**args,code_changes='changes.json')
    if tamper:
        csv=work/'staged/actions.csv';frame=pd.read_csv(csv);frame.loc[len(frame)-1,tamper]='sz.300114';frame.to_csv(csv,index=False)
        path=work/'staged/summary.json';stage=json.loads(path.read_text());stage['output_hashes']['actions.csv']=sha256(csv);path.write_text(json.dumps(stage))
    archive=root/'archive-fingerprint-fixture';archive.write_bytes(b'unit checksum anchor only; not a SQLite integrity fixture')
    script=Path(__file__).parents[1]/'scripts/audit_company_action_intake.py'
    monkeypatch.setattr(sys,'argv',[str(script),'--project',str(root),'--work-root',str(work),'--source','@work/intake','--staged','@work/staged','--inputs','inputs.json','--review','@work/review.json','--archive',archive.name,'--expected-archive-sha256',sha256(archive),'--out','@work/audit.json'])
    if tamper:
        with pytest.raises(AssertionError):runpy.run_path(str(script),run_name='__main__')
        assert not (work/'audit.json').exists()
    else:
        runpy.run_path(str(script),run_name='__main__');result=json.loads((work/'audit.json').read_text())
        assert result['source_and_payment_trading_codes_independently_verified'] and result['primary_receipt_code_and_observation_verified']
