"""Bind reviewed historical industry snapshots to stable research issuer keys."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, hashlib, json
import pandas as pd
from .phase2_protocol import sha256, load_seal
from .security_identity import code_change_maps
from .public_data import is_ashare
from .archived_industry_research import select_archived_research_memberships


def canonical_members(raw, changes):
    if not {'security_code', 'industry_code'}.issubset(raw) or raw.empty:
        raise ValueError('complete literal stock/industry snapshot required')
    f = raw.copy()
    if f[['security_code', 'industry_code']].isna().any().any() or f.security_code.duplicated().any():
        raise ValueError('unique raw security identities required')
    if not f.security_code.map(lambda x: isinstance(x, str)).all() or not f.security_code.astype(str).str.fullmatch('[0-9]{6}').all() or not f.industry_code.astype(str).str.fullmatch('[A-S][0-9]{2}').all():
        raise ValueError('literal six-digit stock and major-industry codes required')
    roots, _, _ = code_change_maps(changes)
    source_symbols = f.security_code.map(lambda x: ('sh.' if x.startswith(('6', '9')) else 'sz.') + x)
    eligible = source_symbols.map(is_ashare)
    result = f[eligible].copy()
    result['source_symbol'] = source_symbols[eligible]
    result['symbol'] = result.source_symbol.map(lambda x: roots.get(x, x))
    result['industry'] = result.industry_code
    if result.symbol.duplicated().any():
        raise ValueError('two source aliases collide in one complete issuer snapshot')
    return result, dict(raw_rows=len(f), a_share_rows=len(result), excluded_b_beijing_or_other_codes=int((~eligible).sum()),
                        same_issuer_aliases=int(result.source_symbol.ne(result.symbol).sum()))


def build_archived_industry_inputs(project, out):
    root = Path(project).resolve()
    def local(value):
        p = Path(value)
        if p.is_absolute() or '..' in p.parts or ':' in str(p):
            raise ValueError('project-relative industry paths required')
        result = (root / p).resolve()
        if not result.is_relative_to(root):
            raise ValueError('industry path escapes project')
        return result
    target = local(out)
    if target.exists():
        raise ValueError('new immutable industry input output required')
    reviews = local('artifacts/phase2/industry_source_review_20261023_v1')
    old_raw = local('artifacts/phase2/raw/csrc_industry_archives_20261022_v1')
    old_tables = local('artifacts/phase2/normalized/csrc_industry_archive_tables_20261023_v2')
    modern_raw = local('artifacts/phase2/raw/capco_industry_archives_20261023_v1')
    modern_tables = local('artifacts/phase2/normalized/capco_industry_tables_20261023_v1')
    dataset = local('artifacts/market_ratios_causal_quality1')
    experiment = local('artifacts/three_horizon_real_causal_quality1')
    sealed = local('artifacts/phase2/protocol_20261020')
    for p in [reviews, old_raw, old_tables, modern_raw, modern_tables, dataset, experiment, sealed]:
        if target.is_relative_to(p) or p.is_relative_to(target):
            raise ValueError('industry output overlaps immutable inputs')
    protocol, seal = load_seal(sealed, root)
    code_names = ['archived_industry_inputs.py', 'archived_industry_research.py', 'security_identity.py', 'public_data.py']
    def current_code_hashes():
        return {n: hashlib.sha256((root/'ashare_mvp'/n).read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode()).hexdigest() for n in code_names}
    code_hashes = current_code_hashes()
    sources = {}
    def bind(path, expected=None):
        digest = sha256(path)
        if expected is not None and digest != expected:
            raise ValueError('reviewed industry source changed: ' + path.name)
        sources[path.relative_to(root).as_posix()] = digest
    for name in ['csrc_review.json', 'capco_review.json']:
        bind(reviews/name)
    old_review = json.loads((reviews/'csrc_review.json').read_text(encoding='utf8'))
    modern_review = json.loads((reviews/'capco_review.json').read_text(encoding='utf8'))
    if old_review.get('schema') != 'primary_csrc_extraction_review_v1' or old_review.get('all_changed_or_added_rows_visually_checked') is not True or old_review.get('all_page_stock_key_coverage_exact') is not True or old_review.get('numeric_major_codes_unchanged') is not True:
        raise ValueError('primary CSRC extraction correction review required')
    if modern_review.get('status') != 'all_five_release_classifications_verified' or len(modern_review.get('releases', [])) != 5 or any(r['issues'] for r in modern_review['releases']):
        raise ValueError('exhaustive independent modern table cross-check required')
    for name, digest in old_review['source_hashes'].items():
        bind(local(name), digest)
    old_meta = json.loads((old_tables/'manifest.json').read_text(encoding='utf8'))
    bind(old_raw/'manifest.json', old_meta['source_capture_sha256'])
    if old_meta['status'] != 'all_releases_extracted_review_pending' or len(old_meta['releases']) != 16 or any(r['page_issues'] or r['rows'] != r['source_text_code_count'] or r['rows'] != r['unique_security_codes'] for r in old_meta['releases']):
        raise ValueError('complete original CSRC table coverage required')
    bind(modern_raw/'manifest.json', modern_review['capture_sha256'])
    bind(modern_tables/'manifest.json', modern_review['extraction_sha256'])
    old_capture = json.loads((old_raw/'manifest.json').read_text(encoding='utf8'))
    modern_capture = json.loads((modern_raw/'manifest.json').read_text(encoding='utf8'))
    modern_meta = json.loads((modern_tables/'manifest.json').read_text(encoding='utf8'))
    if modern_meta['source_manifest_sha256'] != sha256(modern_raw/'manifest.json'):
        raise ValueError('modern extraction is detached from captured originals')
    changes_file = local('config/same_issuer_code_changes.json')
    bind(changes_file)
    changes = pd.read_json(changes_file, convert_dates=False)
    old = pd.read_csv(old_tables/'memberships.csv', dtype={'security_code': str}, low_memory=False)
    catalogs, members, counts = [], [], []
    def append_release(frame, period, published, observed, attachment, standard):
        selected, summary = canonical_members(frame, changes)
        if not len(selected):
            raise ValueError('empty A-share release is not a complete usable snapshot')
        identity = attachment['sha256']
        selected['release_id'] = identity
        selected['published_date'] = published
        selected['first_observed_at_utc'] = observed
        selected['source_sha256'] = identity
        selected['classification_standard'] = standard
        members.append(selected[['symbol', 'industry', 'release_id', 'source_symbol', 'source_page', 'classification_standard', 'published_date', 'first_observed_at_utc', 'source_sha256']])
        catalogs.append(dict(release_id=identity, period_end=period, published_date=published, first_observed_at_utc=observed, source_sha256=identity,
                             snapshot_kind='official_archived_industry_release', members_count=len(selected), source_url=attachment['url'], classification_standard=standard))
        counts.append(dict(published_date=published, **summary))
    for release in old_capture['releases']:
        attachments = [a for a in release['attachments'] if a['file'].endswith('.pdf')]
        if len(attachments) != 1:
            raise ValueError('one complete primary CSRC snapshot per release required')
        a = attachments[0]
        bind(old_raw/release['page']['file'], release['page']['sha256'])
        f = old[old.classification_year.eq(release['year']) & old.classification_quarter.eq(release['quarter'])].copy()
        if f.empty or not f.source_pdf_sha256.eq(a['sha256']).all() or not f.observed_at_utc.eq(a['observed_at_utc']).all() or not f.published_date_text.eq(release['published_date_text']).all():
            raise ValueError('CSRC row provenance or observation changed')
        period = pd.Timestamp(release['year'], release['quarter']*3, 1) + pd.offsets.MonthEnd(0)
        append_release(f, str(period.date()), release['published_date_text'], a['observed_at_utc'], a, 'CSRC_2012')
    for release in modern_capture['releases']:
        a = release['attachment']
        article_file = modern_raw/release['article']['file']
        bind(article_file, release['article']['sha256'])
        literal = article_file.read_text(encoding='utf8').strip()
        prefix = 'export const allData = '
        if not literal.startswith(prefix):
            raise ValueError('literal archived publication metadata required')
        article = json.loads(literal[len(prefix):].rstrip(';'))
        if article['date'] != release['published_date'] or article['title'] != release['title']:
            raise ValueError('original industry publication date/title differs')
        m = next(r for r in modern_meta['releases'] if r['published_date'] == release['published_date'])
        bind(modern_raw/a['file'], a['sha256'])
        bind(modern_tables/m['file'], m['sha256'])
        review = next(r for r in modern_review['releases'] if r['published_date'] == release['published_date'])
        if review['source_sha256'] != a['sha256'] or review['expected_rows'] != m['parsed_rows'] or review['literal_rows'] != m['parsed_rows'] or m['ambiguous_rows']:
            raise ValueError('modern literal table review does not cover the full source')
        f = pd.read_csv(modern_tables/m['file'], dtype={'stock_code': str}).rename(columns={'stock_code': 'security_code', 'industry': 'industry_code'})
        if len(f) != m['parsed_rows'] or not f.source_sha256.eq(a['sha256']).all() or not f.first_observed_at_utc.eq(a['observed_at_utc']).all() or not f.published_date.eq(release['published_date']).all():
            raise ValueError('modern row observation/provenance differs')
        year = int(release['title'][:4])
        period = f'{year}-06-30' if '上半年' in release['title'] else f'{year}-12-31'
        append_release(f, period, release['published_date'], a['observed_at_utc'], a, 'CAPCO_published_table')
    catalog = pd.DataFrame(catalogs).sort_values('published_date').reset_index(drop=True)
    membership = pd.concat(members, ignore_index=True).sort_values(['release_id', 'symbol']).reset_index(drop=True)
    if len(catalog) != 21 or membership.duplicated(['release_id', 'symbol']).any():
        raise ValueError('all twenty-one complete release identities required')
    bind(dataset/'metadata.json')
    meta = json.loads((dataset/'metadata.json').read_text(encoding='utf8'))
    bind(dataset/'calendar.csv', meta['converted_hashes']['calendar.csv'])
    bind(experiment/'models/summary.json')
    summary = json.loads((experiment/'models/summary.json').read_text(encoding='utf8'))
    bind(experiment/'factor_samples.csv', summary['input_frame_sha256']['samples'])
    keys = pd.read_csv(experiment/'factor_samples.csv', usecols=['date', 'symbol'], parse_dates=['date'])
    calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(dataset/'calendar.csv').date))
    if keys.duplicated(['date', 'symbol']).any() or keys.date.gt(pd.Timestamp(protocol['research_end'])).any():
        raise ValueError('original research sample keys required')
    coverage = []
    for date, group in keys.groupby('date', sort=True):
        selected = select_archived_research_memberships(membership, catalog, calendar, date, protocol['research_end'])
        if selected.empty:
            raise ValueError('original eligible signal lacks any published historical release')
        release = catalog[catalog.release_id.eq(selected.release_id.iloc[0])].iloc[0]
        coverage.append(dict(date=str(date.date()), eligible=len(group), known=int(group.symbol.isin(selected.symbol).sum()),
                             release_id=release.release_id, published_date=release.published_date, period_end=release.period_end,
                             publication_age_days=(date-pd.Timestamp(release.published_date)).days))
        if len(coverage) % 100 == 0:
            print(json.dumps(dict(signals_checked=len(coverage))), flush=True)
    target.mkdir(parents=True)
    catalog.to_csv(target/'catalog.csv', index=False)
    membership.to_csv(target/'memberships.csv', index=False)
    pd.DataFrame(coverage).to_csv(target/'signal_coverage.csv', index=False)
    for name, digest in sources.items():
        if sha256(root/name) != digest:
            raise ValueError('industry source changed during construction')
    if current_code_hashes() != code_hashes:
        raise ValueError('industry construction code changed during execution')
    proof = dict(schema='verified_archived_industry_memberships_v1', status='verified_archived_research_memberships_ready',
                 created_at_utc=datetime.now(timezone.utc).isoformat(), protocol_sha256=seal['protocol_sha256'], source_hashes=sources,
                 code_hashes=code_hashes,
                 releases=len(catalog), membership_rows=len(membership), source_counts=counts, security_filter='public_data.is_ashare; identical to original eligible universe', signal_dates=len(coverage), eligible_rows=len(keys),
                 eligible_with_known_industry=sum(r['known'] for r in coverage), maximum_publication_age_days=max(r['publication_age_days'] for r in coverage),
                 output_hashes={p.name: sha256(p) for p in target.glob('*.csv')}, research_training_admission=True, models_trained=0,
                 historical_original_vintages_certified=False, genuine_historical_observations_claimed=False, independent_data_used=False,
                 limitation='Archived primary-release research reconstruction. Observation timestamps remain in 2026. No 2022 release was found; retain only the last published full snapshot until the next release, and absent/new issuers remain unknown. Not a real-time membership source.')
    (target/'manifest.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.')
    parser.add_argument('--out', required=True)
    result = build_archived_industry_inputs(**vars(parser.parse_args()))
    print(json.dumps({k: result[k] for k in ['status', 'releases', 'membership_rows', 'signal_dates', 'eligible_rows', 'eligible_with_known_industry', 'maximum_publication_age_days']}), flush=True)


if __name__ == '__main__':
    main()
