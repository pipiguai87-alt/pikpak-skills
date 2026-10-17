"""Offline maturity regression tests; never use an account or live API."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path.home() / '.hermes' / 'cache' / 'scratch'


def load(skill, script):
    spec = importlib.util.spec_from_file_location(script, ROOT / 'skills' / skill / 'scripts' / (script + '.py'))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backup = load('pikpak-batch-backup', 'batch_backup')
media = load('pikpak-media-index', 'media_index')
search = load('pikpak-search', 'search_files')
share = load('pikpak-share-manager', 'share_audit')


class BackupSafety(unittest.TestCase):
    def run_backup(self, dest, files, downloader):
        def walk(folder, fixture, rel, out, counters, limit):
            out.extend(files)
            counters['files'] = len(files)
        with mock.patch.object(sys, 'argv', ['backup', '--dest', str(dest)]), \
             mock.patch.object(backup, 'walk', side_effect=walk), \
             mock.patch.object(backup, 'pikpak', side_effect=downloader), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                backup.main()
                return 0
            except SystemExit as exc:
                return exc.code

    def test_existing_file_is_not_overwritten_or_trusted_by_size(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            dest = Path(tmp)
            target = dest / 'neutral.txt'
            target.write_bytes(b'original')
            called = []
            def download(*args, **kwargs):
                called.append(args)
                Path(args[-1]).write_bytes(b'bad')
                return 0, '', ''
            rc = self.run_backup(dest, [([], {'id':'F1','name':'neutral.txt','size':'3'})], download)
            self.assertNotEqual(rc, 0)
            self.assertEqual(target.read_bytes(), b'original')
            self.assertEqual(called, [])

    def test_download_success_has_local_digest_and_no_overwrite(self):
        import hashlib
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            def download(*args, **kwargs):
                self.assertNotEqual(Path(args[-1]), Path(tmp) / 'neutral.txt')
                Path(args[-1]).write_bytes(b'abc')
                return 0, '', ''
            rc = self.run_backup(Path(tmp), [([], {'id':'F1','name':'neutral.txt','size':'3'})], download)
            self.assertEqual(rc, 0)
            self.assertEqual((Path(tmp) / 'neutral.txt').read_bytes(), b'abc')
            report = json.loads((Path(tmp) / 'backup_manifest.json').read_text())
            self.assertEqual(report['files'][0]['sha256'], hashlib.sha256(b'abc').hexdigest())
            self.assertEqual(report['remote_integrity'], 'unverified')

    def test_existing_same_size_file_is_not_trusted(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            dest = Path(tmp)
            (dest / 'neutral.txt').write_bytes(b'bad')
            calls = []
            rc = self.run_backup(dest, [([], {'id':'F1','name':'neutral.txt','size':'3'})],
                                 lambda *a, **kw: calls.append(a))
            self.assertNotEqual(rc, 0)
            self.assertEqual(calls, [])
            self.assertEqual((dest / 'neutral.txt').read_bytes(), b'bad')

    def test_symlink_ancestor_never_writes_outside_dest(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            root = Path(tmp)
            dest, outside = root / 'dest', root / 'outside'
            dest.mkdir(); outside.mkdir()
            (dest / 'link').symlink_to(outside, target_is_directory=True)
            calls = []
            def download(*args, **kwargs):
                calls.append(args)
                Path(args[-1]).write_bytes(b'abc')
                return 0, '', ''
            rc = self.run_backup(dest, [(['link'], {'id':'F1','name':'neutral.txt','size':'3'})], download)
            self.assertNotEqual(rc, 0)
            self.assertEqual(calls, [])
            self.assertFalse((outside / 'neutral.txt').exists())

    def test_colliding_names_are_rejected_before_download(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            calls = []
            def download(*args, **kwargs):
                calls.append(args)
                Path(args[-1]).write_bytes(b'abc')
                return 0, '', ''
            files = [([], {'id':'F1','name':'a/b','size':'3'}),
                     ([], {'id':'F2','name':'a_b','size':'3'})]
            rc = self.run_backup(Path(tmp), files, download)
            self.assertNotEqual(rc, 0)
            self.assertEqual(calls, [])

    def test_short_download_is_failure_and_not_published(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            def download(*args, **kwargs):
                Path(args[-1]).write_bytes(b'ab')
                return 0, '', ''
            rc = self.run_backup(Path(tmp), [([], {'id':'F1','name':'neutral.txt','size':'3'})], download)
            self.assertNotEqual(rc, 0)
            self.assertFalse((Path(tmp) / 'neutral.txt').exists())

    def test_reserved_manifest_name_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            calls = []
            def download(*args, **kwargs):
                calls.append(args)
                Path(args[-1]).write_bytes(b'abc')
                return 0, '', ''
            rc = self.run_backup(Path(tmp), [([], {'id':'F1','name':'backup_manifest.json','size':'3'})], download)
            self.assertNotEqual(rc, 0)
            self.assertEqual(calls, [])


class SearchMaturity(unittest.TestCase):
    def run_main(self, args, rows):
        def walk(folder, fixture, rel, out, counters, limit):
            out.extend(rows)
        with mock.patch.object(sys, 'argv', ['search', *args, '--json', '-']), \
             mock.patch.object(search, 'walk', side_effect=walk), \
             contextlib.redirect_stdout(io.StringIO()) as buf, contextlib.redirect_stderr(io.StringIO()):
            search.main()
            return json.loads(buf.getvalue())

    def test_regex_with_spaces_is_one_expression(self):
        result = self.run_main(['--query', '^Neutral Movie$', '--regex'],
                               [([], {'id':'F','kind':'drive#file','name':'Neutral Other'})])
        self.assertEqual(result['total_found'], 0)

    def test_invalid_regex_rejected_before_scanning_empty_drive(self):
        with mock.patch.object(sys, 'argv', ['search','--query','[','--regex']), \
             mock.patch.object(search, 'walk') as walker, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                search.main()
            walker.assert_not_called()

    def test_folder_search_returns_folder_not_descendant(self):
        fixture = {'': {'': {'files':[{'id':'D','kind':'drive#folder','name':'Neutral'}], 'next_page_token':''}},
                   'D': {'': {'files':[{'id':'F','kind':'drive#file','name':'child.txt'}], 'next_page_token':''}}}
        rows = []
        search.walk('', fixture, [], rows, {'files':0,'folders':0}, 50)
        report = self.run_main(['--query','Neutral','--type','folder'], rows)
        self.assertEqual([r['id'] for r in report['results']], ['D'])


class PaginationMaturity(unittest.TestCase):
    def test_repeated_page_token_fails_closed(self):
        for mod in (search, media, backup):
            with self.subTest(module=mod.__name__), \
                 mock.patch.object(mod, 'ls_page', side_effect=[
                     {'files':[], 'next_page_token':'again'},
                     {'files':[], 'next_page_token':'again'},
                     AssertionError('unbounded pagination')]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    mod.walk('', None, [], [], {'files':0,'folders':0}, 50)


class MediaMaturity(unittest.TestCase):
    def test_bad_metadata_is_unknown_not_inferred_processing(self):
        row = media.video_record([], {'name':'neutral.2160p.hevc.mp4',
                                     'params':{'duration':'12garbage','width':'-3840','height':'2160'}})
        self.assertEqual(row['duration'], 0)
        self.assertFalse(row['is_4k'])
        self.assertEqual(row['codec'], '')
        self.assertFalse(row['pending'])

    def test_transcode_codec_is_not_reported_as_original(self):
        row = media.video_record([], {'name':'neutral.mp4', 'medias':[
            {'is_origin':False, 'video':{'video_codec':'h264'}}]})
        self.assertEqual(row['codec'], '')

    def test_source_column_and_mime_video_without_extension(self):
        rows = [([], {'kind':'drive#file', 'name':'neutral', 'size':'1',
                       'mime_type':'video/mp4', 'params':{'duration':'5','url':'https://example.invalid/source'}})]
        def walk(folder, fixture, rel, out, counters, limit):
            out.extend(rows)
        with mock.patch.object(sys, 'argv', ['media','--format','csv']), \
             mock.patch.object(media, 'walk', side_effect=walk), \
             contextlib.redirect_stdout(io.StringIO()) as buf:
            media.main()
        import csv
        rows = list(csv.reader(io.StringIO(buf.getvalue())))
        self.assertIn('source', rows[0])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][rows[0].index('source')], 'https://example.invalid/source')

    def test_explicit_audio_metadata_is_not_a_video(self):
        rows = [([], {'kind':'drive#file','name':'neutral.mp3','mime_type':'audio/mpeg',
                      'params':{'duration':'120'},'size':'1'})]
        def walk(folder, fixture, rel, out, counters, limit):
            out.extend(rows)
        with mock.patch.object(sys, 'argv', ['media','--format','csv']), \
             mock.patch.object(media, 'walk', side_effect=walk), \
             contextlib.redirect_stdout(io.StringIO()) as buf:
            media.main()
        import csv
        self.assertEqual(len(list(csv.reader(io.StringIO(buf.getvalue())))), 1)

    def test_source_query_and_password_are_not_exported(self):
        row = media.video_record([], {'name':'neutral.mp4', 'params':{
            'url':'https://user:secret@example.invalid/video?token=private#secret'}})
        self.assertEqual(row['source'], 'https://example.invalid/video')

    def test_csv_formula_cells_are_neutralized(self):
        rows = [(['@folder'], {'kind':'drive#file', 'name':'=1+1.mp4','size':'1'})]
        def walk(folder, fixture, rel, out, counters, limit):
            out.extend(rows)
        with mock.patch.object(sys, 'argv', ['media','--format','csv']), \
             mock.patch.object(media, 'walk', side_effect=walk), \
             contextlib.redirect_stdout(io.StringIO()) as buf:
            media.main()
        import csv
        data = list(csv.reader(io.StringIO(buf.getvalue())))
        self.assertEqual(data[1][0], "'=1+1.mp4")
        self.assertEqual(data[1][6], "'@folder")


class ShareMaturity(unittest.TestCase):
    def test_missing_expiry_is_unknown_not_forever(self):
        flags = share.audit([{'share_id':'S'}])[0]['flags']
        self.assertFalse(any('长期有效' in f for f in flags))
        self.assertTrue(any('未知' in f for f in flags))

    def test_exact_seven_day_boundary_and_numeric_string_time(self):
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        dt = share.parse_time('1759276800000')
        self.assertEqual(dt, datetime.fromtimestamp(1759276800, timezone.utc))
        rows = share.audit([{'share_id':'A','expiration':(now+timedelta(days=7, seconds=1)).isoformat()},
                            {'share_id':'B','expiration':now.isoformat()}], now=now)
        self.assertNotIn('7 天内将过期', rows[0]['flags'])
        self.assertIn('已过期', rows[1]['flags'])

    def test_twenty_page_budget_fails_on_unique_tokens(self):
        calls = []
        def response(*args, **kwargs):
            calls.append(args)
            i = len(calls)
            payload = json.dumps({'shares':[{'share_id':str(i), 'title':'neutral'}], 'next_page_token':str(i)})
            return type('Response', (), {'returncode':0,'stdout':payload,'stderr':''})()
        with mock.patch.object(share.subprocess, 'run', side_effect=response), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                share.fetch_shares()
        self.assertEqual(len(calls), 20)

    def test_audit_cli_uses_fixed_now_and_does_not_emit_shell(self):
        rows = [{'share_id':'S; invalid', 'title':'neutral\ncommand',
                 'expiration':'2026-01-01T00:00:00Z'}]
        with mock.patch.object(sys, 'argv', ['share','--now','2026-01-01T00:00:00Z','--json','-']), \
             mock.patch.object(share, 'fetch_shares', return_value=rows), \
             contextlib.redirect_stdout(io.StringIO()) as buf, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            share.main()
        report = json.loads(buf.getvalue())
        self.assertEqual(report['audited_at'], '2026-01-01T00:00:00+00:00')
        self.assertIn('已过期', report['shares'][0]['flags'])
        self.assertNotIn('pikpak share rm', err.getvalue())
        self.assertNotIn('neutral\ncommand', err.getvalue())

    def test_explicit_null_share_fixture_never_enters_live_cli(self):
        response = cli_response({'shares': [], 'next_page_token': ''})
        with mock.patch.object(share.subprocess, 'run', return_value=response) as process, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                share.fetch_shares(None)
        process.assert_not_called()

    def test_share_pagination_limit_never_silently_returns_partial(self):
        payload = json.dumps({'shares':[], 'next_page_token':'loop'})
        response = type('Response', (), {'returncode':0,'stdout':payload,'stderr':''})()
        with mock.patch.object(share.subprocess, 'run', return_value=response), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                share.fetch_shares()


def invoke_cli(mod, args):
    """Observe public CLI return code and both output streams."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, 'argv', [mod.__name__, *args]), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            mod.main()
            rc = 0
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def forbid_external_calls():
    """Fail closed if a regression test reaches a process or network boundary."""
    import socket
    import subprocess
    import urllib.request

    def denied(*args, **kwargs):
        raise AssertionError("data regression tests forbid external side effects")

    owners = (
        (subprocess, ("run", "Popen", "call", "check_call", "check_output")),
        (socket, ("socket", "create_connection", "getaddrinfo", "gethostbyname")),
        (urllib.request, ("urlopen",)),
        (os, ("system", "popen", "posix_spawn", "posix_spawnp")),
    )
    with contextlib.ExitStack() as stack:
        for owner, names in owners:
            for name in names:
                if hasattr(owner, name):
                    stack.enter_context(mock.patch.object(owner, name, side_effect=denied))
        yield


def cli_response(payload, rc=0, stderr=''):
    import subprocess
    return subprocess.CompletedProcess(['offline-mock'], rc, json.dumps(payload), stderr)


class FixtureEntryReview(unittest.TestCase):
    def run_blocked(self, mod, args):
        with forbid_external_calls():
            try:
                return invoke_cli(mod, args)
            except Exception as exc:
                self.fail(f'{mod.__name__} escaped controlled fixture rejection: {exc!r}')

    def test_explicit_empty_fixture_never_falls_back_to_live(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            specs = {
                search: ['--query', 'neutral', '--json', '-'],
                media: [],
                share: ['--json', '-'],
                backup: ['--dest', folder, '--dry-run'],
            }
            for mod, args in specs.items():
                with self.subTest(module=mod.__name__):
                    rc, out, err = self.run_blocked(mod, [*args, '--from-fixture', ''])
                    self.assertNotEqual(rc, 0)
                    self.assertEqual(out, '')
                    self.assertIn('fixture', err.lower())

    def test_invalid_fixture_path_and_structure_fail_before_cli(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            root = Path(folder)
            bad_json = root / 'bad.json'
            bad_json.write_text('{', encoding='utf-8')
            null_root = root / 'null.json'
            null_root.write_text('null', encoding='utf-8')
            missing = root / 'missing.json'
            specs = {
                search: ['--query', 'neutral', '--json', '-'],
                media: [],
                share: ['--json', '-'],
                backup: ['--dest', folder, '--dry-run'],
            }
            for mod, args in specs.items():
                for fixture in ('', str(missing), str(bad_json), str(null_root)):
                    with self.subTest(module=mod.__name__, fixture=fixture):
                        rc, out, err = self.run_blocked(
                            mod, [*args, '--from-fixture', fixture])
                        self.assertNotEqual(rc, 0)
                        self.assertEqual(out, '')
                        self.assertTrue('fixture' in err.lower() or 'schema_error' in err)

    def test_batch_empty_fixture_cannot_bypass_dry_run_requirement(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            for extra in ([], ['--dry-run']):
                with self.subTest(extra=extra):
                    rc, out, err = self.run_blocked(
                        backup, ['--dest', folder, *extra, '--from-fixture', ''])
                    self.assertNotEqual(rc, 0)
                    self.assertEqual(out, '')
                    self.assertIn('fixture', err.lower())

    def test_only_unspecified_fixture_uses_mocked_live_listing(self):
        page = {'files': [], 'next_page_token': ''}
        specs = {
            search: ['--query', 'neutral', '--json', '-'],
            media: [],
            share: ['--json', '-'],
            backup: None,
        }
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            specs[backup] = ['--dest', folder, '--dry-run']
            for mod, args in specs.items():
                payload = {'shares': [], 'next_page_token': ''} if mod is share else page
                with self.subTest(module=mod.__name__), \
                     mock.patch('subprocess.run', return_value=cli_response(payload)) as process:
                    rc, out, err = invoke_cli(mod, args)
                self.assertEqual(rc, 0, err)
                self.assertGreater(process.call_count, 0)


class DocumentationReview(unittest.TestCase):
    def test_public_contracts_and_upstream_release_boundaries_are_explicit(self):
        release_urls = {
            'pikpak-search': ['rclone/rclone/releases/tag/v1.75.2', '52funny/pikpakcli/releases/tag/v0.23'],
            'pikpak-media-index': ['rclone/rclone/releases/tag/v1.75.2'],
            'pikpak-share-manager': ['rclone/rclone/releases/tag/v1.75.2', '52funny/pikpakcli/releases/tag/v0.23', 'Quan666/PikPakAPI/releases/tag/0.1.11'],
            'pikpak-batch-backup': ['rclone/rclone/releases/tag/v1.75.2', '52funny/pikpakcli/releases/tag/v0.23', 'restic/restic/releases/tag/v0.19.1'],
        }
        for name, urls in release_urls.items():
            root = ROOT / 'skills' / name
            skill = (root / 'SKILL.md').read_text()
            refs = (root / 'references' / 'upstream-design.md').read_text()
            with self.subTest(skill=name):
                self.assertIn('schema', skill)
                self.assertIn('exit_code', skill)
                self.assertIn('研读快照', refs)
                self.assertIn('祖先关系未核验', refs)
                self.assertNotIn('/Users/', refs)
                for url in urls:
                    self.assertIn('https://github.com/' + url, refs)
                if name != 'pikpak-share-manager':
                    self.assertIn('--max-pages', skill)
                    self.assertIn('--scan-timeout', skill)
                if name != 'pikpak-batch-backup':
                    self.assertIn('--overwrite', skill)
                    self.assertIn('0600', skill)
        media_refs = (ROOT / 'skills/pikpak-media-index/references/upstream-design.md').read_text()
        self.assertIn('FFmpeg 发行未核验', media_refs)
        self.assertNotIn('不能将源码拷进 MIT 项目', media_refs)
        self.assertIn('不能仅以 MIT 分发', media_refs)
        self.assertIn('合规评估', media_refs)
        self.assertIn('纯文本', (ROOT / 'skills/pikpak-media-index/SKILL.md').read_text())
        self.assertIn('--epoch-unit', (ROOT / 'skills/pikpak-share-manager/SKILL.md').read_text())
        self.assertIn('O_NONBLOCK', (ROOT / 'skills/pikpak-batch-backup/SKILL.md').read_text())


class PaginationReview(unittest.TestCase):
    def test_unique_empty_pages_and_slow_pages_fail_incomplete(self):
        import time
        for mod in (search, media, backup):
            args = {search:['--query','neutral','--json','-'], media:[],
                    backup:['--dest','neutral-unused','--dry-run']}[mod]
            calls = []
            def response(cmd, **kwargs):
                calls.append(kwargs)
                self.assertLessEqual(len(calls), 3, 'page budget must bound the test')
                return cli_response({'files':[], 'next_page_token':str(len(calls))})
            with self.subTest(module=mod.__name__, budget='pages'), \
                 mock.patch('subprocess.run', side_effect=response):
                rc, out, err = invoke_cli(mod, [*args,'--max-pages','2'])
                self.assertNotEqual(rc, 0)
                self.assertEqual(out, '')
                self.assertIn('incomplete', err)
                self.assertEqual(len(calls), 2)
            calls.clear()
            def slow(cmd, **kwargs):
                calls.append(kwargs)
                time.sleep(0.03)  # bounded neutral delay, no network or subprocess
                return cli_response({'files':[], 'next_page_token':''})
            with self.subTest(module=mod.__name__, budget='time'), \
                 mock.patch('subprocess.run', side_effect=slow):
                rc, out, err = invoke_cli(mod, [*args,'--scan-timeout','0.01'])
                self.assertNotEqual(rc, 0)
                self.assertEqual(out, '')
                self.assertIn('incomplete', err)
                self.assertEqual(len(calls), 1)
                self.assertLessEqual(calls[0]['timeout'], 0.01)


class ReportReview(unittest.TestCase):
    def test_reports_are_exclusive_private_and_never_follow_links(self):
        for mod in (search, media, share):
            collection = 'shares' if mod is share else 'files'
            page = {collection:[], 'next_page_token':''}
            with self.subTest(module=mod.__name__), tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
                dest = Path(folder) / 'neutral-report'
                arg = '--output' if mod is media else '--json'
                prefix = ['--query','neutral'] if mod is search else []
                def run(extra=()):
                    with mock.patch('subprocess.run', return_value=cli_response(page)):
                        return invoke_cli(mod, [*prefix,arg,str(dest),*extra])
                dest.write_text('original')
                rc, out, err = run()
                self.assertNotEqual(rc, 0)
                self.assertEqual(dest.read_text(), 'original')
                dest.unlink()
                target = Path(folder) / 'neutral-original'
                target.write_text('outside-original')
                dest.symlink_to(target)
                rc, out, err = run()
                self.assertNotEqual(rc, 0)
                self.assertEqual(target.read_text(), 'outside-original')
                rc, out, err = run(['--overwrite'])
                self.assertNotEqual(rc, 0)
                self.assertEqual(target.read_text(), 'outside-original')
                dest.unlink()
                rc, out, err = run()
                self.assertEqual(rc, 0, err)
                self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
                dest.write_text('replace-authorized')
                rc, out, err = run(['--overwrite'])
                self.assertEqual(rc, 0, err)
                self.assertNotEqual(dest.read_text(), 'replace-authorized')
                self.assertEqual(dest.stat().st_mode & 0o777, 0o600)


class MediaReview(unittest.TestCase):
    def test_nonstring_sources_are_omitted_before_sanitized_fallback(self):
        import csv
        import html

        marker = 'NEUTRAL_SOURCE_TYPE_SENTINEL'
        bad_values = ({'access_token': marker}, [marker], 12, True)
        fallback_url = ('https://user:' + marker + '@neutral.invalid:8443/source'
                        '?access_token=' + marker + '#' + marker)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            fixture = Path(folder) / 'neutral-source.json'
            for field in ('url', 'original_url'):
                for bad in bad_values:
                    for fallback in ((False, True) if field == 'url' else (False,)):
                        row: dict[str, object] = {
                            'id': 'F', 'name': 'neutral.mp4', 'kind': 'drive#file', 'size': '3'}
                        if field == 'url':
                            row['params'] = {'url': bad}
                            if fallback:
                                row['original_url'] = fallback_url
                        else:
                            row['original_url'] = bad
                        fixture.write_text(json.dumps({'': {'': {
                            'files': [row], 'next_page_token': ''}}}), encoding='utf-8')
                        expected = 'https://neutral.invalid:8443/source' if fallback else ''
                        for fmt in ('csv', 'md'):
                            with self.subTest(field=field, value_type=type(bad).__name__,
                                              fallback=fallback, format=fmt), \
                                 forbid_external_calls(), \
                                 mock.patch('subprocess.run', side_effect=AssertionError(
                                     'explicit source fixture must never launch CLI')) as process:
                                rc, out, err = invoke_cli(media, [
                                    '--from-fixture', str(fixture), '--format', fmt])
                                process.assert_not_called()
                                self.assertEqual(rc, 0, err)
                                self.assertEqual(err, '')
                                self.assertNotIn(marker, html.unescape(out + err))
                                self.assertNotIn('access_token', html.unescape(out + err))
                                if fmt == 'csv':
                                    records = list(csv.DictReader(io.StringIO(out)))
                                    self.assertEqual(len(records), 1)
                                    self.assertEqual(records[0]['name'], 'neutral.mp4')
                                    self.assertEqual(records[0]['source'], expected)
                                else:
                                    records = [line for line in out.splitlines()
                                               if line.startswith('| ')][1:]
                                    self.assertEqual(len(records), 1)
                                    cells = [html.unescape(cell.strip())
                                             for cell in records[0].split('|')[1:-1]]
                                    self.assertEqual(cells[1], 'neutral.mp4')
                                    self.assertEqual(cells[-1], expected or '-')

    def test_invalid_primary_source_falls_back_to_sanitized_original(self):
        row = {'id':'F', 'name':'neutral.mp4', 'kind':'drive#file', 'size':'3',
               'params':{'url':'not-a-url'},
               'original_url':'https://user:SYNTHETIC_ONLY@neutral.invalid/source?token=SYNTHETIC_ONLY#private'}
        with mock.patch('subprocess.run', return_value=cli_response({'files':[row], 'next_page_token':''})):
            rc, out, err = invoke_cli(media, ['--format','csv'])
        self.assertEqual(rc, 0, err)
        import csv
        rows = list(csv.DictReader(io.StringIO(out)))
        self.assertEqual(rows[0]['source'], 'https://neutral.invalid/source')
        self.assertNotIn('SYNTHETIC_ONLY', out + err)
        direct = media.video_record([], {**row, 'params':{'url':'https://neutral.invalid/preferred'}})
        self.assertEqual(direct['source'], 'https://neutral.invalid/preferred')


class NumericBoundaryReview(unittest.TestCase):
    def test_media_400_digit_size_and_total_are_safe(self):
        size = 10 ** 400
        self.assertEqual(media.human_size(size), str(size) + ' B')
        row = {'id': 'F', 'name': 'neutral.mp4', 'kind': 'drive#file', 'size': str(size)}
        page = {'files': [row], 'next_page_token': ''}
        with mock.patch('subprocess.run', return_value=cli_response(page)):
            rc, csv_text, err = invoke_cli(media, ['--format', 'csv'])
        self.assertEqual(rc, 0, err)
        self.assertIn(str(size) + ' B', csv_text)
        self.assertNotIn('OverflowError', csv_text + err)

        with mock.patch('subprocess.run', return_value=cli_response(page)):
            rc, markdown_text, err = invoke_cli(media, [])
        self.assertEqual(rc, 0, err)
        self.assertIn(str(size) + ' B', markdown_text)
        self.assertNotIn('OverflowError', markdown_text + err)

    def test_backup_400_digit_total_is_safe_in_dry_run(self):
        size = 10 ** 400
        page = {'files': [{'id': 'F', 'name': 'neutral.bin', 'kind': 'drive#file',
                           'size': str(size)}], 'next_page_token': ''}
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder, \
                mock.patch('subprocess.run', return_value=cli_response(page)):
            rc, out, err = invoke_cli(backup, ['--dest', folder, '--dry-run'])
        self.assertEqual(rc, 0, err)
        self.assertIn(str(size) + ' B', out)
        self.assertNotIn('OverflowError', out + err)
    def test_extreme_digit_strings_are_controlled_and_never_echoed(self):
        digits = '9' * 4301
        page = {'files': [{'id': 'F', 'name': 'neutral.mp4', 'kind': 'drive#file',
                           'size': digits}], 'next_page_token': ''}
        with mock.patch('subprocess.run', return_value=cli_response(page)):
            rc, out, err = invoke_cli(media, ['--format', 'csv'])
        self.assertEqual(rc, 0, err)
        self.assertNotIn(digits, out + err)
        self.assertNotIn('ValueError', out + err)

        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder, \
                mock.patch('subprocess.run', return_value=cli_response({
                    'files': [{'id': 'F', 'name': 'neutral.bin', 'kind': 'drive#file',
                               'size': digits}], 'next_page_token': ''})):
            rc, out, err = invoke_cli(backup, ['--dest', folder, '--dry-run'])
        self.assertNotEqual(rc, 0)
        self.assertNotIn(digits, out + err)
        self.assertNotIn('ValueError', out + err)
        self.assertIn('schema_error', err)

    def test_native_json_4301_digit_integer_is_controlled_in_both_int_modes(self):
        digits = '9' * 4301
        raw_files = ('{"files":[{"id":"F","name":"neutral.bin",'
                     '"kind":"drive#file","size":' + digits + '}],'
                     '"next_page_token":""}')
        raw_shares = ('{"shares":[{"share_id":"S","title":"neutral",'
                      '"files":' + digits + '}],"next_page_token":""}')
        if hasattr(sys, 'get_int_max_str_digits'):
            old_limit = sys.get_int_max_str_digits()
            limits = [old_limit] if old_limit == 0 else [old_limit, 0]
        else:
            old_limit = None
            limits = [None]
        specs = {
            media: (['--format', 'csv'], raw_files),
            backup: (None, raw_files),
            search: (['--query', 'neutral', '--json', '-'], raw_files),
            share: (['--json', '-'], raw_shares),
        }
        try:
            for limit in limits:
                if limit is not None:
                    sys.set_int_max_str_digits(limit)
                with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
                    specs[backup] = (['--dest', folder, '--dry-run'], raw_files)
                    for mod, (args, raw) in specs.items():
                        with self.subTest(module=mod.__name__, int_limit=limit), \
                             mock.patch('subprocess.run', return_value=type(
                                 'Response', (), {'returncode': 0, 'stdout': raw, 'stderr': ''})()):
                            try:
                                rc, out, err = invoke_cli(mod, args)
                            except Exception as exc:
                                self.fail(f'uncontrolled JSON integer exception: {type(exc).__name__}: {exc}')
                            self.assertNotEqual(rc, 0)
                            self.assertEqual(out, '')
                            self.assertNotIn(digits, out + err)
                            self.assertNotIn('ValueError', out + err)
                            self.assertNotIn('OverflowError', out + err)
                            self.assertIn('schema_error', err)
                    if limit is None or limit == 0:
                        for mod in (media, backup):
                            try:
                                display = mod.human_size(10 ** 4301)
                            except Exception as exc:
                                self.fail(f'uncontrolled huge-size exception: {type(exc).__name__}: {exc}')
                            self.assertEqual(display, '未知')
        finally:
            if old_limit is not None:
                sys.set_int_max_str_digits(old_limit)

    def test_nested_backup_size_is_rejected_without_echoing_nested_fields(self):
        secret = 'SYNTHETIC_NESTED_SIZE_SECRET'
        page = {'files': [{'id': 'F', 'name': 'neutral.bin', 'kind': 'drive#file',
                           'size': {'nested': secret}}], 'next_page_token': ''}
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder, \
                mock.patch('subprocess.run', return_value=cli_response(page)):
            rc, out, err = invoke_cli(backup, ['--dest', folder, '--dry-run'])
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, '')
        self.assertNotIn(secret, out + err)
        self.assertNotIn('nested', err)
        self.assertIn('schema_error', err)


class SearchReview(unittest.TestCase):
    def test_help_does_not_claim_unverified_server_search_shutdown(self):
        assert search.__doc__ is not None
        self.assertNotIn('因服务端 API 下线', search.__doc__)

    def test_large_valid_integer_size_is_preserved_without_float_overflow(self):
        size = 10 ** 400
        payload = {'files':[{'id':'F', 'name':'neutral', 'kind':'drive#file', 'size':str(size)}], 'next_page_token':''}
        with mock.patch('subprocess.run', return_value=cli_response(payload)):
            rc, out, err = invoke_cli(search, ['--query','neutral','--sort','size','--json','-'])
        self.assertEqual(rc, 0, err)
        row = json.loads(out)['results'][0]
        self.assertEqual(row['size'], size)
        self.assertEqual(row['size_h'], str(size) + ' B')

    def test_nested_search_size_is_schema_error_without_success_output(self):
        secret = 'SYNTHETIC_SEARCH_SIZE_SECRET'
        payload = {'files': [{'id': 'F', 'name': 'neutral', 'kind': 'drive#file',
                             'size': {'nested': secret}}], 'next_page_token': ''}
        with mock.patch('subprocess.run', return_value=cli_response(payload)):
            rc, out, err = invoke_cli(search, ['--query', 'neutral', '--json', '-'])
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, '')
        self.assertNotIn(secret, out + err)
        self.assertNotIn('nested', err)
        self.assertIn('schema_error', err)

    def test_bad_size_is_unknown_and_never_fabricates_sort_bytes(self):
        for value in (None, True, False, -300, '-300', '1.5 GB', 'broken123', 1.5, [], {}, '', ' 3 '):
            with self.subTest(invalid=value):
                self.assertIsNone(search.to_int(value))
        for raw, expected in ((0,0), ('0',0), (3,3), ('003',3)):
            self.assertEqual(search.to_int(raw), expected)
        rows = [{'id':'bad-neg','name':'neutral negative','size':'-300'},
                {'id':'known-5','name':'neutral five','size':5},
                {'id':'bad-unit','name':'neutral unit','size':'1.5 GB'},
                {'id':'known-0','name':'neutral empty','size':'0'},
                {'id':'bad-bool','name':'neutral bool','size':False},
                {'id':'known-7','name':'neutral seven','size':'7'}]
        payload = {'files':[{**row, 'kind':'drive#file'} for row in rows], 'next_page_token':''}
        for descending in (False, True):
            with self.subTest(descending=descending), \
                 mock.patch('subprocess.run', return_value=cli_response(payload)):
                rc, out, err = invoke_cli(search, ['--query','neutral','--sort','size','--json','-', *(['--desc'] if descending else [])])
                self.assertEqual(rc, 0, err)
                report = json.loads(out)
                known = ['known-7','known-5','known-0'] if descending else ['known-0','known-5','known-7']
                self.assertEqual([r['id'] for r in report['results']], known + ['bad-neg','bad-unit','bad-bool'])
                for row in report['results']:
                    if row['id'].startswith('bad-'):
                        self.assertIsNone(row['size'])
                        self.assertEqual(row['size_h'], '未知')
                self.assertEqual(report['total_found'], 6)


class BackupReview(unittest.TestCase):
    def test_nonregular_staging_is_rejected_without_blocking(self):
        import signal
        import stat
        real_fstat = os.fstat
        def device_metadata(fd):
            result = real_fstat(fd)
            if stat.S_ISREG(result.st_mode) and result.st_size == 3:
                fields = list(result)
                fields[0] = stat.S_IFCHR | 0o600
                return os.stat_result(fields)
            return result
        page = {'files':[{'id':'F', 'name':'neutral.txt', 'kind':'drive#file', 'size':'3'}], 'next_page_token':''}
        for kind in ('fifo', 'directory', 'symlink', 'device_metadata'):
            with self.subTest(staging=kind), tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
                dest = Path(folder)
                def download(cmd, **kwargs):
                    if cmd[1] == 'ls':
                        return cli_response(page)
                    self.assertEqual(cmd[1], 'download')
                    stage = Path(cmd[-1])
                    if kind == 'fifo':
                        os.mkfifo(stage, 0o600)
                    elif kind == 'directory':
                        stage.mkdir()
                    elif kind == 'symlink':
                        neutral = dest / 'neutral-source'
                        neutral.write_bytes(b'abc')
                        stage.symlink_to(neutral)
                    else:
                        stage.write_bytes(b'abc')
                    return cli_response({})
                def deadline(sig, frame):
                    raise AssertionError('staging validation blocked beyond 1 second')
                previous = signal.signal(signal.SIGALRM, deadline)
                old_timer = signal.getitimer(signal.ITIMER_REAL)
                try:
                    signal.setitimer(signal.ITIMER_REAL, 1)
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(mock.patch('subprocess.run', side_effect=download))
                        if kind == 'device_metadata':
                            stack.enter_context(mock.patch('os.fstat', side_effect=device_metadata))
                        rc, out, err = invoke_cli(backup, ['--dest',folder])
                    self.assertNotEqual(rc, 0)
                    self.assertFalse((dest / 'neutral.txt').exists())
                    report = json.loads((dest / 'backup_manifest.json').read_text())
                    self.assertEqual(report['files'][0]['status'], 'failed')
                    self.assertNotIn('sha256', report['files'][0])
                finally:
                    signal.setitimer(signal.ITIMER_REAL, *old_timer)
                    signal.signal(signal.SIGALRM, previous)

    def test_download_and_manifest_force_private_mode_under_umask_022(self):
        previous_umask = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
                def download(*args, **kwargs):
                    Path(args[-1]).write_bytes(b'abc')
                    return 0, '', ''

                rc = BackupSafety().run_backup(
                    Path(folder), [([], {'id': 'F1', 'name': 'neutral.txt', 'size': '3'})], download)
                self.assertEqual(rc, 0)
                target = Path(folder) / 'neutral.txt'
                manifest = Path(folder) / 'backup_manifest.json'
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
        finally:
            os.umask(previous_umask)


class DataContract(unittest.TestCase):
    def test_cli_schema_fail_closed_before_any_success_output(self):
        args = {search:['--query','neutral','--json','-'], media:[],
                share:['--json','-'], backup:['--dest','neutral-unused','--dry-run']}
        for mod, argv in args.items():
            collection = 'shares' if mod is share else 'files'
            row = {'share_id':'S', 'title':'neutral'} if mod is share else {'id':'F', 'name':'neutral.mp4', 'kind':'drive#file', 'size':'3'}
            valid = {collection:[row], 'next_page_token':''}
            payloads = [None, [], 0, True, 'text', {}, {'data':valid},
                        {'error':'invalid_request', 'error_code':400},
                        {**valid, 'error':{'credential':'SYNTHETIC_SCHEMA_ONLY'}},
                        {**valid, 'error_code':0}, {**valid, 'success':False}, {**valid, 'code':400},
                        {**valid, 'error_message':'SYNTHETIC_SCHEMA_ONLY'},
                        {**valid, 'ok':False}, {**valid, 'status':'error'},
                        {'next_page_token':''}, {collection:[]}]
            payloads += [{**valid, collection:value} for value in (None, {}, '', True, 0)]
            payloads += [{**valid, 'next_page_token':value} for value in (None, True, 0, [], {})]
            payloads += [{**valid, collection:[value]} for value in (None, True, 'row', [], {})]
            required = ('share_id', 'title') if mod is share else ('id', 'name', 'kind')
            for field in required:
                payloads.append({**valid, collection:[{k:v for k,v in row.items() if k != field}]})
                for value in (None, True, 3, [], {}):
                    payloads.append({**valid, collection:[{**row, field:value}]})
                if field != 'title':
                    payloads.append({**valid, collection:[{**row, field:''}]})
            if mod is share:
                payloads.append({**valid, collection:[row, row]})
            else:
                payloads.append({**valid, collection:[{**row, 'kind':'drive#unexpected'}]})
                for field in ('params', 'medias', 'modified_time'):
                    for value in (True, 4, 'broken' if field != 'modified_time' else {}, [] if field != 'medias' else {}):
                        payloads.append({**valid, collection:[{**row, field:value}]})
                payloads += [{**valid, collection:[{**row, 'medias':[value]}]} for value in (True, [], 'broken')]
                payloads.append({**valid, collection:[{**row, 'medias':[{'video':'broken'}]}]})
            for index, payload in enumerate(payloads):
                with self.subTest(module=mod.__name__, shape=index), \
                     mock.patch('subprocess.run', return_value=cli_response(payload)):
                    rc, out, err = invoke_cli(mod, argv)
                    self.assertNotEqual(rc, 0)
                    self.assertEqual(out, '')
                    self.assertNotIn('SYNTHETIC_SCHEMA_ONLY', err)

    def test_fixture_contract_does_not_invent_missing_pages(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as folder:
            fixture = Path(folder) / 'neutral.json'
            for mod in (search, media, backup, share):
                args = {search:['--query','neutral','--json','-'], media:[],
                        backup:['--dest',folder,'--dry-run'], share:['--json','-']}[mod]
                invalid = [None, [], {}, {'error':'invalid_request'}]
                if mod is not share:
                    invalid += [{'':{}}, {'':{'':{}}}, {'':{'':{'files':[]}}}]
                else:
                    invalid += [{'shares':[],'next_page_token':'more'}, {'shares':[{'share_id':'S'}]}]
                for index, payload in enumerate(invalid):
                    fixture.write_text(json.dumps(payload))
                    with self.subTest(module=mod.__name__, shape=index), \
                         mock.patch('subprocess.run', side_effect=AssertionError('fixture must not launch CLI')):
                        rc, out, err = invoke_cli(mod, [*args,'--from-fixture',str(fixture)])
                        self.assertNotEqual(rc, 0)
                        self.assertEqual(out, '')

    def test_valid_empty_and_paged_contracts_still_complete(self):
        for mod in (search, media, backup):
            payloads = [{'files':[], 'next_page_token':'second'},
                        {'files':[{'id':'F', 'name':'neutral.mp4', 'kind':'drive#file','size':'3'}], 'next_page_token':''}]
            rows, counts = [], {'files':0,'folders':0}
            with self.subTest(module=mod.__name__), \
                 mock.patch('subprocess.run', side_effect=[cli_response(p) for p in payloads]) as process:
                mod.walk('', None, [], rows, counts, 50)
                self.assertEqual(counts['files'], 1)
                self.assertEqual(len(rows), 1)
                self.assertEqual(process.call_count, 2)
            with mock.patch('subprocess.run', return_value=cli_response({'files':[], 'next_page_token':''})):
                rows, counts = [], {'files':0,'folders':0}
                mod.walk('', None, [], rows, counts, 50)
                self.assertEqual(rows, [])
        payloads = [{'shares':[], 'next_page_token':'second'},
                    {'shares':[{'share_id':'S', 'title':''}], 'next_page_token':''}]
        with mock.patch('subprocess.run', side_effect=[cli_response(p) for p in payloads]) as process:
            self.assertEqual(len(share.fetch_shares()), 1)
            self.assertEqual(process.call_count, 2)
        self.assertEqual(share.fetch_shares({'shares':[]}), [])  # explicit complete fixture snapshot


class ShareReview(unittest.TestCase):
    def test_epoch_overflow_is_unknown_not_an_uncaught_decimal_exception(self):
        for raw in ('1e1000000', '-1e1000000', 'ms:1e1000000', '1e999999999'):
            for unit in ('auto', 'seconds', 'milliseconds'):
                with self.subTest(raw=raw, unit=unit):
                    self.assertIsNone(share.parse_time(raw, epoch_unit=unit))

    def test_epoch_units_include_zero_early_and_thresholds(self):
        from datetime import datetime, timezone
        expected = [(0, '1970-01-01T00:00:00+00:00'),
                    (1, '1970-01-01T00:00:01+00:00'),
                    (946684800, '2000-01-01T00:00:00+00:00'),
                    (999999999, '2001-09-09T01:46:39+00:00'),
                    (1000000000, '2001-09-09T01:46:40+00:00'),
                    (1000000001, '2001-09-09T01:46:41+00:00'),
                    (999999999999, '2001-09-09T01:46:39.999000+00:00'),
                    (1000000000000, '2001-09-09T01:46:40+00:00'),
                    (1000000000001, '2001-09-09T01:46:40.001000+00:00')]
        for value, iso in expected:
            for raw in (value, str(value)):
                with self.subTest(value=raw):
                    self.assertEqual(share.parse_time(raw), datetime.fromisoformat(iso))
        explicit = [(0, '1970-01-01T00:00:00+00:00'),
                    (1, '1970-01-01T00:00:00.001000+00:00'),
                    (-1, '1969-12-31T23:59:59.999000+00:00'),
                    (946684800000, '2000-01-01T00:00:00+00:00'),
                    (1000000000, '1970-01-12T13:46:40+00:00')]
        for value, iso in explicit:
            with self.subTest(milliseconds=value):
                self.assertEqual(share.parse_time(value, epoch_unit='milliseconds'), datetime.fromisoformat(iso))
                self.assertEqual(share.parse_time('ms:' + str(value)), datetime.fromisoformat(iso))
        self.assertEqual(share.parse_time('s:1000000000'), datetime(2001,9,9,1,46,40,tzinfo=timezone.utc))
        self.assertIsNone(share.parse_time(1000000000000, epoch_unit='seconds'))
        for value in (True, False, None, '', [], {}, float('nan'), float('inf'), 'NaN', 'Infinity', 'ms:NaN', '1e100'):
            with self.subTest(invalid=repr(value)):
                self.assertIsNone(share.parse_time(value))

    def test_fractional_epoch_report_preserves_the_actual_audit_instant(self):
        rows = [{'share_id':'S', 'title':'neutral', 'expiration':1002, 'created':0, 'views':0}]
        with mock.patch('subprocess.run', return_value=cli_response({'shares':rows, 'next_page_token':''})):
            rc, out, err = invoke_cli(share, ['--now','1001','--epoch-unit','milliseconds','--json','-'])
        self.assertEqual(rc, 0, err)
        report = json.loads(out)
        self.assertEqual(report['audited_at'], '1970-01-01T00:00:01.001000+00:00')
        self.assertEqual(report['shares'][0]['expiration_parsed'], '1970-01-01T00:00:01.002000+00:00')
        self.assertNotIn('已过期', report['shares'][0]['flags'])

    def test_cli_epoch_unit_applies_to_now_and_share_fields(self):
        rows = [{'share_id':'S', 'title':'neutral', 'expiration':1000, 'created':0, 'views':0}]
        with mock.patch('subprocess.run', return_value=cli_response({'shares':rows, 'next_page_token':''})):
            rc, out, err = invoke_cli(share, ['--now','2000','--epoch-unit','milliseconds','--json','-'])
        self.assertEqual(rc, 0, err)
        report = json.loads(out)
        self.assertEqual(report['audited_at'], '1970-01-01T00:00:02+00:00')
        self.assertEqual(report['shares'][0]['expiration_parsed'], '1970-01-01T00:00:01+00:00')
        self.assertIn('已过期', report['shares'][0]['flags'])

    def test_views_bad_types_are_unknown_not_zero(self):
        from datetime import datetime, timezone
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        base = {'share_id':'S', 'created':'2025-01-01T00:00:00Z', 'expiration':-1}
        for value in (False, True, '0', 0.0, -1, None, [], {'count':0}):
            with self.subTest(value=value):
                row = share.audit([{**base, 'views':value}], now=now)[0]
                self.assertNotIn('30 天以上零访问', row['flags'])
                self.assertIsNone(row['views'])
        self.assertIsNone(share.audit([base], now=now)[0]['views'])
        zero = share.audit([{**base, 'views':0}], now=now)[0]
        self.assertIn('30 天以上零访问', zero['flags'])
        self.assertEqual(zero['views'], 0)
        positive = share.audit([{**base, 'views':2}], now=now)[0]
        self.assertEqual(positive['views'], 2)
        self.assertNotIn('30 天以上零访问', positive['flags'])


class DataSecurity(unittest.TestCase):
    def test_share_files_are_counts_or_unknown_never_nested_json(self):
        secret = 'SYNTHETIC_NESTED_ONLY'
        bad = [{'pass_code':secret, 'web_content_link':'https://neutral.invalid/v?access_token=' + secret}]
        for value in (bad, {'nested':bad}, True, False, -1, '3', 3.0, None):
            with self.subTest(files_type=type(value).__name__):
                data = {'shares':[{'share_id':'S', 'title':'neutral', 'files':value}], 'next_page_token':''}
                with mock.patch('subprocess.run', return_value=cli_response(data)):
                    rc, out, err = invoke_cli(share, ['--now','2026-01-01T00:00:00Z','--json','-'])
                self.assertEqual(rc, 0, err)
                self.assertIsNone(json.loads(out)['shares'][0]['files'])
                self.assertNotIn(secret, out + err)
                self.assertNotIn('web_content_link', out + err)
        for value in (0, 3):
            row = share.audit([{'share_id':'S', 'files':value}])[0]
            self.assertEqual(row['files'], value)
            self.assertIs(type(row['files']), int)

    def test_share_optional_fields_cannot_transmit_nested_objects(self):
        secret = 'SYNTHETIC_NESTED_ONLY'
        nested = {'pass_code':secret}
        row = share.audit([{'share_id':nested, 'title':nested, 'status':nested,
                            'views':nested, 'created':nested, 'expiration':nested,
                            'files':nested, 'pass_code':secret, 'share_url':secret}])[0]
        self.assertNotIn(secret, json.dumps(row))
        self.assertEqual(row['share_id'], '')
        self.assertEqual(row['title'], '')
        self.assertEqual(row['status'], '')
        for field in ('views', 'created', 'expiration_raw', 'files'):
            self.assertIsNone(row[field])
        self.assertTrue(any('无法解析' in f for f in row['flags']))

    def test_cli_failure_never_repeats_stdout_or_stderr(self):
        import subprocess
        secret = 'SYNTHETIC_SENTINEL_ONLY'
        raw = ('Authorization: Bearer ' + secret + '; Cookie: ' + secret
               + '; password=' + secret + '; pass_code=' + secret
               + '; https://user:' + secret + '@neutral.invalid/v?access_token=' + secret)
        args = {search:['--query','neutral','--json','-'], media:[],
                share:['--json','-'], backup:['--dest','neutral-unused','--dry-run']}
        scenarios = [('stderr', subprocess.CompletedProcess(['offline-mock'], 7, '', raw), 7),
                     ('stdout', subprocess.CompletedProcess(['offline-mock'], 7, raw, ''), 7),
                     ('missing', FileNotFoundError(raw), 127),
                     ('timeout', subprocess.TimeoutExpired('offline-mock', 1, output=raw, stderr=raw), 124),
                     ('permission', PermissionError(raw), 126)]
        for mod, argv in args.items():
            for label, result, expected in scenarios:
                with self.subTest(module=mod.__name__, source=label), \
                     mock.patch('subprocess.run', side_effect=result if isinstance(result, Exception) else None,
                                return_value=result), \
                     mock.patch.dict(os.environ, {'PIKPAK_BIN':secret}):
                    rc, out, err = invoke_cli(mod, argv)
                    self.assertNotEqual(rc, 0)
                    self.assertEqual(out, '')
                    self.assertNotIn(secret, err)
                    self.assertNotIn('Authorization', err)
                    self.assertIn('exit_code=' + str(expected), err)

    def test_media_nested_codec_is_dropped_from_csv_and_markdown_exports(self):
        import csv

        secret = 'NEUTRAL_REVIEW2_CODEC_SECRET'
        row = {'id': 'F', 'name': 'neutral.mp4', 'kind': 'drive#file', 'size': '3',
               'medias': [{'is_origin': True, 'video': {'video_codec': {
                   'pass_code': secret,
                   'web_content_link': 'https://neutral.invalid/v?access_token=' + secret,
               }}}]}
        page = {'files': [row], 'next_page_token': ''}
        record = media.video_record([], row)
        self.assertEqual(record['codec'], '')
        for value in record.values():
            self.assertIn(type(value), (str, int, bool))
        self.assertEqual(media.md_cell({'pass_code': secret}), '')
        self.assertEqual(media.csv_cell({'pass_code': secret}), '')

        with mock.patch('subprocess.run', return_value=cli_response(page)):
            rc, markdown_text, err = invoke_cli(media, ['--format', 'md'])
        self.assertEqual(rc, 0, err)
        self.assertNotIn(secret, markdown_text + err)
        self.assertNotIn('pass_code', markdown_text + err)
        self.assertNotIn('web_content_link', markdown_text + err)

        with mock.patch('subprocess.run', return_value=cli_response(page)):
            rc, csv_text, err = invoke_cli(media, ['--format', 'csv'])
        self.assertEqual(rc, 0, err)
        exported = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(exported[0]['codec'], '')
        self.assertNotIn(secret, csv_text + err)
        self.assertNotIn('pass_code', csv_text + err)
        self.assertNotIn('web_content_link', csv_text + err)

        try:
            import markdown
            rendered = markdown.markdown(markdown_text, extensions=['tables'])
        except ImportError:
            rendered = None
        if rendered is not None:
            self.assertNotIn(secret, rendered)
            self.assertNotIn('pass_code', rendered)
            self.assertNotIn('web_content_link', rendered)

    def test_media_codec_credential_like_scalar_is_dropped_without_container_leak(self):
        secret = 'NEUTRAL_CODEC_CREDENTIAL_SECRET'
        row = {'id': 'F', 'name': 'neutral.mp4', 'kind': 'drive#file', 'size': '3',
               'medias': [
                   {'is_origin': True, 'video': {'video_codec': {'pass_code': secret}}},
                   {'is_origin': True, 'video': {'video_codec':
                       'https://user:' + secret + '@neutral.invalid/v?access_token=' + secret}},
               ]}
        record = media.video_record([], row)
        self.assertEqual(record['codec'], '')
        self.assertNotIn(secret, json.dumps(record))
        with mock.patch('subprocess.run', return_value=cli_response({'files': [row], 'next_page_token': ''})):
            rc, out, err = invoke_cli(media, ['--format', 'csv'])
        self.assertEqual(rc, 0, err)
        self.assertNotIn(secret, out + err)
        self.assertNotIn('access_token', out + err)

    def test_markdown_cells_render_as_plain_text(self):
        from html.parser import HTMLParser
        try:
            import markdown
        except ImportError:
            self.skipTest('no installed Markdown parser; run with Hermes Python for real rendering')

        class Cells(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags, self.cells, self.cell = [], [], None
            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)
                if tag == 'td':
                    self.cell = ''
            def handle_data(self, data):
                if self.cell is not None:
                    self.cell += data
            def handle_endtag(self, tag):
                if tag == 'td':
                    self.cells.append(self.cell)
                    self.cell = None

        values = ['![tracking](https://neutral.invalid/pixel).mp4',
                  '[forged](https://neutral.invalid/link)', '*em* **strong** `code` ~~strike~~',
                  r'\![escape](https://neutral.invalid/pixel)', '<img src="https://neutral.invalid/pixel">',
                  '&lt;img&gt; | # + - _ {x} ! [x] (x)', 'neutral\r\nsecond line',
                  'https://neutral.invalid/path', '<https://neutral.invalid/path>']
        renderers = [lambda text: markdown.markdown(text, extensions=['tables'])]
        try:
            from markdown_it import MarkdownIt
            parser = MarkdownIt('commonmark').enable('table')
            renderers.append(lambda text: parser.render(text))
        except ImportError:
            pass
        for render in renderers:
            text = '| value |\n|---|\n' + '\n'.join('| ' + media.md_cell(v) + ' |' for v in values)
            parsed = Cells()
            parsed.feed(render(text))
            self.assertFalse(set(parsed.tags) & {'img', 'a', 'em', 'strong', 'code', 's', 'script'})
            self.assertEqual(parsed.cells, [v.replace('\r', ' ').replace('\n', ' ') for v in values])

        payload = {'files':[{'id':'D', 'kind':'drive#folder', 'name':values[1]}], 'next_page_token':''}
        # Formatting accepts arbitrary text, but the end-to-end codec boundary
        # deliberately drops unsupported codec syntax before rendering.
        for codec, expected_codec in (('h264', 'h264'), (values[2], '-')):
            with self.subTest(codec=codec):
                child = {'files':[{'id':'F', 'kind':'drive#file', 'name':values[0], 'size':'3',
                                  'medias':[{'is_origin':True, 'video':{'video_codec':codec}}],
                                  'params':{'url':'https://neutral.invalid/source'}}], 'next_page_token':''}
                with mock.patch('subprocess.run', side_effect=[cli_response(payload), cli_response(child)]):
                    rc, text, err = invoke_cli(media, [])
                self.assertEqual(rc, 0, err)
                if codec == values[2]:
                    self.assertNotIn(codec, text + err)
                for render in renderers:
                    parsed = Cells()
                    parsed.feed(render(text))
                    self.assertFalse(set(parsed.tags) & {'img', 'a', 'em', 'strong', 'code', 's', 'script'})
                    self.assertEqual(parsed.cells, [
                        '1', values[0], '-', '-', '3 B', expected_codec,
                        values[1], 'https://neutral.invalid/source'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
