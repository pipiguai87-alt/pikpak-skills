#!/usr/bin/env python3
"""Entry skills: neutral offline tests; never invoke a real PikPak CLI."""
import argparse
import importlib.util
import io
import json
import os
from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
from pathlib import Path
import runpy
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / 'skills'
TEST_SCRATCH = None
NEUTRAL_PROGRAMS = {}


def setUpModule():
    # Enter the shared guard FIRST, then close its child-process gap locally.
    global TEST_SCRATCH
    from dev.run_tests import offline_environment
    try:
        from offline_guard import RUNTIME_SCRATCH
    except ImportError:
        TEST_SCRATCH = tempfile.gettempdir()
    else:
        TEST_SCRATCH = str(RUNTIME_SCRATCH)
    stack = ExitStack()
    unittest.addModuleCleanup(stack.close)
    stack.enter_context(offline_environment())
    real_popen = subprocess.Popen

    def forbidden(*args, **kwargs):
        raise AssertionError('entry tests forbid real processes, network and notifications')

    def guarded_popen(argv, *args, **kwargs):
        if not isinstance(argv, (list, tuple)) or not argv or kwargs.get('shell') or kwargs.get('executable'):
            return forbidden()
        path = Path(argv[0])
        expected = NEUTRAL_PROGRAMS.get(str(path))
        if (not path.is_absolute() or expected is None or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            return forbidden()
        return real_popen(argv, *args, **kwargs)

    stack.enter_context(patch.object(subprocess, 'Popen', side_effect=guarded_popen))
    for target in ('socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
                   'urllib.request.urlopen', 'os.system', 'os.popen', 'os.posix_spawn', 'os.posix_spawnp'):
        stack.enter_context(patch(target, side_effect=forbidden))


def run_entry_cli(script, argv=(), *, input='', env=None):
    """Exercise the real argparse/main and exit code without an unguarded child."""
    stdout, stderr = io.StringIO(), io.StringIO()
    command = [str(script), *argv]
    code = 0
    with patch.object(sys, 'argv', command), patch.object(sys, 'stdin', io.StringIO(input)), \
            patch.dict(os.environ, env or {}, clear=env is not None), redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            runpy.run_path(str(script), run_name='__main__')
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
    return subprocess.CompletedProcess(command, code, stdout.getvalue(), stderr.getvalue())


@contextmanager
def allow_neutral_program(path):
    path = path.resolve()
    NEUTRAL_PROGRAMS[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        yield
    finally:
        NEUTRAL_PROGRAMS.pop(str(path), None)


def load(relative):
    path = SKILLS / relative
    if not path.is_file():
        raise AssertionError(f'Missing implementation: {relative}')
    spec = importlib.util.spec_from_file_location('entry_' + path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRIVATE_ERROR_BODY = 'neutral-private-error-body-do-not-echo'


def failure_task_responses():
    """Supported flat/outer/task envelopes with otherwise valid success fields."""
    failures = (
        ('error_code_403', {'error_code': 403}),
        ('error_code_text', {'error_code': '403'}),
        ('error_code_zero', {'error_code': 0}),
        ('error_code_null', {'error_code': None}),
        ('error_code_unauthenticated', {'error_code': 'UNAUTHENTICATED'}),
        ('code_unauthenticated', {'code': 'UNAUTHENTICATED'}),
        ('error', {'error': {'message': PRIVATE_ERROR_BODY}}),
        ('error_null', {'error': None}),
        ('ok_false', {'ok': False}),
        ('success_false', {'success': False}),
    )
    for name, failure in failures:
        for position in ('flat', 'outer', 'nested'):
            response = {'task_id': 'T1', 'status': 'complete', 'file_id': 'F1',
                        'parent_id': 'FOLDER_A', 'ok': True, 'success': True}
            if position != 'flat':
                response['task'] = {'id': 'T1', 'status': 'complete', 'file_id': 'F1',
                                    'parent_id': 'FOLDER_A', 'ok': True, 'success': True}
            container = response['task'] if position == 'nested' else response
            container.update(failure, message=PRIVATE_ERROR_BODY)
            yield name, position, response


class TestOnboarding(unittest.TestCase):
    def test_blank_code_also_explicitly_disables_installer_referral(self):
        module = load('pikpak-onboarding/scripts/registration_plan.py')
        self.assertEqual(module.registration_argv('', True)[-1], '--affiliate=')
        self.assertEqual(module.registration_argv('22874035', True)[-1], '--affiliate=22874035')

    def test_plan_requires_choice_and_never_executes_cli(self):
        script = SKILLS / 'pikpak-onboarding/scripts/registration_plan.py'
        env = {'PATH': os.defpath, 'HOME': '/nonexistent-neutral-home'}
        proc = run_entry_cli(script, env=env)
        self.assertNotEqual(proc.returncode, 0)
        proc = run_entry_cli(script, ['--choice', 'decline'], env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result['action'], 'plan_only')
        self.assertEqual(result['argv'][-1], '--affiliate=')
        self.assertIn('奖励', result['disclosure'])

    def test_decline_never_inherits_installer_affiliate(self):
        module = load('pikpak-onboarding/scripts/registration_plan.py')
        self.assertEqual(module.registration_argv('22874035', False),
                         ['pikpak', 'auth', 'register', '--affiliate='])


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.module = load('pikpak-magnet-pipeline/scripts/magnet_pipeline.py')
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'run.json'

    def ns(self, **kwargs):
        kwargs.setdefault('account_scope', 'acct-test')
        kwargs.setdefault('_account_identity_hash', self.module.identity_digest('fixture-account-id'))
        return argparse.Namespace(state=str(self.state), **kwargs)

    def bound_state(self, **updates):
        fingerprint = hashlib.sha256(json.dumps(
            ['https://example.invalid/approved', ''], ensure_ascii=False).encode()).hexdigest()
        record = {'version': 1, 'fingerprint': fingerprint, 'folder': '',
                  'account_scope': 'acct-test',
                  'account_scope_binding': 'operator_declared_unverified',
                  'account_identity_sha256': self.module.identity_digest('fixture-account-id'),
                  'task_id': 'T1', 'stage': 'submitted'}
        record.update(updates)
        if record['stage'] in ('ready', 'downloaded'):
            record.setdefault('latest_task_status', 'complete')
        return record

    def invoke(self, fn, args):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            fn(args)
        return out.getvalue()

    def test_repeated_add_uses_same_task_without_new_creation(self):
        args = self.ns(magnet='https://example.invalid/demo.txt', folder='', name='')
        with patch.object(self.module, 'pikpak_json', return_value=({'task_id': 'T1'}, None)) as run:
            self.invoke(self.module.step1_add, args)
            self.invoke(self.module.step1_add, args)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(self.state.read_text())['task_id'], 'T1')

    def test_add_rejects_conflicting_ids_and_status_fields(self):
        responses = (
            {'task_id': 'T1', 'task': {'id': 'OTHER'}},
            {'task_id': 'T1', 'status': 'complete', 'phase': 'running'},
        )
        for response in responses:
            with self.subTest(response=response):
                if self.state.exists():
                    self.state.unlink()
                args = self.ns(magnet='https://example.invalid/demo.txt', folder='')
                with patch.object(self.module, 'pikpak_json', return_value=(response, None)):
                    with self.assertRaises(SystemExit):
                        self.invoke(self.module.step1_add, args)
                record = json.loads(self.state.read_text())
                self.assertEqual(record['stage'], 'submitting')
                self.assertNotIn('task_id', record)

    def test_add_rejects_response_folder_mismatch(self):
        args = self.ns(magnet='https://example.invalid/demo.txt', folder='FOLDER_A')
        with patch.object(self.module, 'pikpak_json', return_value=(
                {'task_id': 'T1', 'parent_id': 'FOLDER_B'}, None)):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step1_add, args)
        record = json.loads(self.state.read_text())
        self.assertEqual(record['stage'], 'submitting')
        self.assertNotIn('task_id', record)

    def test_add_error_status_does_not_advance_submitted(self):
        args = self.ns(magnet='https://example.invalid/demo.txt', folder='')
        with patch.object(self.module, 'pikpak_json', return_value=(
                {'task_id': 'T1', 'status': 'error'}, None)):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step1_add, args)
        record = json.loads(self.state.read_text())
        self.assertEqual(record['stage'], 'submitting')
        self.assertNotIn('task_id', record)

    def test_missing_task_id_leaves_uncertain_state_and_blocks_retry(self):
        args = self.ns(magnet='https://example.invalid/demo.txt', folder='', name='')
        with patch.object(self.module, 'pikpak_json', return_value=({}, None)) as run:
            for _ in range(2):
                with self.assertRaises(SystemExit):
                    self.invoke(self.module.step1_add, args)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(self.state.read_text())['stage'], 'submitting')

    def test_uncertain_submission_cannot_claim_unrelated_task_via_status(self):
        args = self.ns(magnet='https://example.invalid/approved', folder='')
        with patch.object(self.module.subprocess, 'run', return_value=
                          subprocess.CompletedProcess(['neutral'], 0, '{}', '')) as run:
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step1_add, args)
            self.assertEqual(run.call_count, 1)
        original = self.state.read_bytes()
        unrelated = {'task_id': 'UNRELATED', 'status': 'complete', 'file_id': 'OTHERFILE'}
        with patch.object(self.module.subprocess, 'run', return_value=
                          subprocess.CompletedProcess(['neutral'], 0, json.dumps(unrelated), '')) as run:
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status, self.ns(task_id='UNRELATED'))
            self.assertEqual(run.call_count, 0, 'must refuse before task get')
            self.assertEqual(self.state.read_bytes(), original)
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step3_download, self.ns(file_id='OTHERFILE', dest=self.tmp.name))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.module.step4_cleanup(self.ns(file_ids=['OTHERFILE'], verified_local=True))
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(run.call_count, 0)
            self.assertEqual(self.state.read_bytes(), original)

    def test_status_response_must_match_exact_task_id(self):
        self.state.write_text(json.dumps(self.bound_state()))
        with patch.object(self.module, 'pikpak_json', return_value=(
                {'task_id': 'OTHER', 'status': 'complete', 'file_id': 'F1'}, None)):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status, self.ns(task_id='T1'))

    def test_status_rejects_conflicting_outer_and_nested_ids(self):
        for response in (
                {'task_id': 'OTHER', 'file_id': 'F1',
                 'task': {'id': 'T1', 'file_id': 'F1', 'status': 'complete'}},
                {'task_id': 'T1', 'file_id': 'OTHERFILE',
                 'task': {'id': 'T1', 'file_id': 'F1', 'status': 'complete'}},
        ):
            with self.subTest(response=response):
                self.state.write_text(json.dumps(self.bound_state()))
                original = self.state.read_bytes()
                with patch.object(self.module, 'pikpak_json', return_value=(response, None)):
                    with self.assertRaises(SystemExit):
                        self.invoke(self.module.step2_status, self.ns(task_id='T1'))
                self.assertEqual(self.state.read_bytes(), original)

    def test_status_rejects_conflicting_status_and_phase(self):
        self.state.write_text(json.dumps(self.bound_state()))
        original = self.state.read_bytes()
        response = {'task_id': 'T1', 'status': 'complete', 'file_id': 'F1',
                    'task': {'id': 'T1', 'phase': 'running', 'file_id': 'F1'}}
        with patch.object(self.module, 'pikpak_json', return_value=(response, None)):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status, self.ns(task_id='T1'))
        self.assertEqual(self.state.read_bytes(), original)

    def test_status_rejects_response_folder_mismatch(self):
        self.state.write_text(json.dumps(self.bound_state(folder='FOLDER_A')))
        original = self.state.read_bytes()
        response = {'task_id': 'T1', 'status': 'complete', 'file_id': 'F1',
                    'parent_id': 'FOLDER_B'}
        with patch.object(self.module, 'pikpak_json', return_value=(response, None)):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status, self.ns(task_id='T1'))
        self.assertEqual(self.state.read_bytes(), original)

    def test_status_rejects_conflicting_response_folder_fields(self):
        self.state.write_text(json.dumps(self.bound_state(folder='FOLDER_A')))
        response = {'task_id': 'T1', 'status': 'complete', 'file_id': 'F1',
                    'parent_id': 'FOLDER_A', 'folder_id': 'FOLDER_B'}
        with patch.object(self.module, 'pikpak_json', return_value=(response, None)):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status, self.ns(task_id='T1'))
        self.assertNotEqual(json.loads(self.state.read_text()).get('stage'), 'ready')

    def test_requested_folder_without_response_field_is_unknown(self):
        self.state.write_text(json.dumps(self.bound_state(folder='FOLDER_A')))
        response = {'task_id': 'T1', 'status': 'complete', 'file_id': 'F1'}
        with patch.object(self.module, 'pikpak_json', return_value=(response, None)):
            output = self.invoke(self.module.step2_status, self.ns(task_id='T1'))
        observed = json.loads(self.state.read_text())
        self.assertEqual(observed['folder_verification'], 'unknown')
        self.assertEqual(json.loads(output)['folder_verification'], 'unknown')

    def test_status_progress_rejects_nested_and_credential_like_values(self):
        secret = 'SYNTHETIC_PROGRESS_SECRET'
        response = {'task_id': 'T1', 'status': 'complete', 'file_id': 'F1',
                    'task': {'id': 'T1', 'status': 'complete', 'file_id': 'F1',
                             'progress': 'https://neutral.invalid/?access_token=' + secret}}
        self.state.write_text(json.dumps(self.bound_state()))
        with patch.object(self.module, 'pikpak_json', return_value=(response, None)):
            output = self.invoke(self.module.step2_status, self.ns(task_id='T1'))
        result = json.loads(output)
        self.assertEqual(result['progress'], 'unknown')
        self.assertNotIn(secret, output)

    def test_unknown_folder_binding_blocks_download_before_cli(self):
        record = self.bound_state(folder='FOLDER_A', file_id='F1', stage='ready',
                                  folder_verification='unknown', latest_task_status='complete')
        self.state.write_text(json.dumps(record))
        destination = Path(self.tmp.name) / 'unknown-folder-download'
        with patch.object(self.module.subprocess, 'run',
                          side_effect=AssertionError('unknown folder must not download')) as run:
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step3_download,
                            self.ns(file_id='F1', dest=str(destination)))
        self.assertEqual(run.call_count, 0)
        self.assertFalse(destination.exists())
        self.assertEqual(json.loads(self.state.read_text()), record)

    def test_failure_envelopes_never_submit_or_promote_ready(self):
        for name, position, response in failure_task_responses():
            for step in ('add', 'status'):
                with self.subTest(envelope=name, position=position, step=step):
                    if self.state.exists():
                        self.state.unlink()
                    if step == 'status':
                        self.state.write_text(json.dumps(self.bound_state(folder='FOLDER_A')))
                        original = self.state.read_bytes()
                    out, err = io.StringIO(), io.StringIO()
                    with patch.object(self.module, 'pikpak_json', return_value=(response, None)) as run, \
                            redirect_stdout(out), redirect_stderr(err):
                        with self.assertRaises(SystemExit) as exc:
                            if step == 'add':
                                self.module.step1_add(self.ns(
                                    magnet='https://example.invalid/demo.txt', folder='FOLDER_A'))
                            else:
                                self.module.step2_status(self.ns(task_id='T1'))
                        self.assertNotEqual(exc.exception.code, 0)
                        if step == 'add':
                            with self.assertRaises(SystemExit):
                                self.module.step1_add(self.ns(
                                    magnet='https://example.invalid/demo.txt', folder='FOLDER_A'))
                        self.assertEqual(run.call_count, 1, 'failed/uncertain creation must not retry')
                    if step == 'add':
                        record = json.loads(self.state.read_text())
                        self.assertEqual(record['stage'], 'submitting')
                        self.assertNotIn('task_id', record)
                        self.assertNotIn('file_id', record)
                    else:
                        self.assertEqual(self.state.read_bytes(), original)
                    with patch.object(self.module, 'pikpak', side_effect=AssertionError('no download')) as run, \
                            redirect_stdout(out), redirect_stderr(err):
                        with self.assertRaises(SystemExit):
                            self.module.step3_download(self.ns(file_id='F1', dest=self.tmp.name))
                        with self.assertRaises(SystemExit):
                            self.module.step4_cleanup(self.ns(file_ids=['F1'], verified_local=True))
                        self.assertEqual(run.call_count, 0)
                    self.assertEqual(out.getvalue(), '')
                    self.assertNotIn(PRIVATE_ERROR_BODY, err.getvalue() + self.state.read_text())

    def test_json_wrapper_rejects_failure_envelopes_without_returning_body(self):
        for name, position, response in failure_task_responses():
            with self.subTest(envelope=name, position=position):
                with patch.object(self.module, 'pikpak', return_value=(0, json.dumps(response), '')):
                    data, error = self.module.pikpak_json('task', 'get', 'T1')
                self.assertIsNone(data)
                self.assertIsInstance(error, str)
                self.assertTrue(error)
                self.assertNotIn(PRIVATE_ERROR_BODY, error)

    def test_auth_failure_envelopes_block_submission_before_task_add(self):
        command = [str(SKILLS / 'pikpak-magnet-pipeline/scripts/magnet_pipeline.py'),
                   'step1-add', 'https://example.invalid/demo', '--state', str(self.state),
                   '--account-scope', 'acct-test']
        for name, position, response in failure_task_responses():
            with self.subTest(envelope=name, position=position):
                out, err = io.StringIO(), io.StringIO()
                auth_response = {**response, 'logged_in': True}
                with patch.object(sys, 'argv', command), \
                        patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess(
                            ['neutral'], 0, json.dumps(auth_response), PRIVATE_ERROR_BODY)) as run, \
                        redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as exc:
                    self.module.main()
                self.assertNotEqual(exc.exception.code, 0)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][1:], ['auth', 'status', '-F', 'json'])
                self.assertFalse(self.state.exists(), 'auth failure must not create a submission intent')
                self.assertEqual(out.getvalue(), '')
                self.assertNotIn(PRIVATE_ERROR_BODY, err.getvalue())

    def test_auth_status_with_valid_identity_and_failure_code_is_rejected(self):
        response = {'logged_in': True, 'profile': {'id': 'fixture-account-id'},
                    'ok': True, 'success': True, 'code': 'UNAUTHENTICATED'}
        with patch.object(self.module, 'pikpak_json', return_value=(response, None)):
            with self.assertRaises(SystemExit) as exc:
                self.module.check_login()
        self.assertNotEqual(exc.exception.code, 0)

    def test_failure_observation_revokes_existing_cleanup_qualification(self):
        local = Path(self.tmp.name) / 'neutral.data'
        local.write_bytes(b'good')
        for name, position, response in failure_task_responses():
            for stage in ('ready', 'downloaded'):
                with self.subTest(envelope=name, position=position, stage=stage):
                    record = self.bound_state(folder='FOLDER_A', folder_verification='matched',
                                              file_id='F1', stage=stage, local_path=str(local),
                                              local_size=4, local_sha256=hashlib.sha256(b'good').hexdigest())
                    self.state.write_text(json.dumps(record))
                    out, err = io.StringIO(), io.StringIO()
                    with patch.object(self.module, 'pikpak_json', return_value=(response, None)), \
                            redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as exc:
                        self.module.step2_status(self.ns(task_id='T1'))
                    self.assertNotEqual(exc.exception.code, 0)
                    observed = json.loads(self.state.read_text())
                    self.assertEqual(observed['stage'], 'blocked')
                    self.assertNotIn('latest_task_status', observed)
                    for field in ('fingerprint', 'task_id', 'file_id', 'folder', 'account_scope',
                                  'account_scope_binding', 'local_path', 'local_size', 'local_sha256'):
                        self.assertEqual(observed[field], record[field], field)
                    self.assertEqual(local.read_bytes(), b'good')
                    with patch.object(self.module, 'pikpak', side_effect=AssertionError('no download')) as run, \
                            redirect_stdout(out), redirect_stderr(err):
                        with self.assertRaises(SystemExit):
                            self.module.step3_download(self.ns(file_id='F1', dest=self.tmp.name))
                        with self.assertRaises(SystemExit):
                            self.module.step4_cleanup(self.ns(file_ids=['F1'], verified_local=True))
                        self.assertEqual(run.call_count, 0)
                    self.assertEqual(out.getvalue(), '')
                    self.assertNotIn(PRIVATE_ERROR_BODY, err.getvalue() + self.state.read_text())

    def test_status_requires_explicit_account_scope_confirmation(self):
        record = self.bound_state(account_scope='acct-A')
        self.state.write_text(json.dumps(record))
        original = self.state.read_bytes()
        with patch.object(self.module, 'pikpak_json', return_value=(
                {'task_id': 'T1', 'status': 'complete', 'file_id': 'F1'}, None)) as run:
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status,
                            argparse.Namespace(state=str(self.state), task_id='T1'))
        self.assertEqual(run.call_count, 0)
        self.assertEqual(self.state.read_bytes(), original)

    def test_scope_receipt_must_be_marked_operator_declared(self):
        record = self.bound_state()
        record.pop('account_scope_binding', None)
        self.state.write_text(json.dumps(record))
        with patch.object(self.module, 'pikpak_json', side_effect=AssertionError('cloud access forbidden')):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status, self.ns(task_id='T1'))

    def test_account_identity_hash_mismatch_blocks_bound_receipt(self):
        digest = lambda value: hashlib.sha256(
            ('pikpak-account-v1:' + value).encode()).hexdigest()
        record = self.bound_state(account_identity_sha256=digest('account-A'))
        self.state.write_text(json.dumps(record))
        original = self.state.read_bytes()
        args = self.ns(task_id='T1', _account_identity_hash=digest('account-B'))
        with patch.object(self.module, 'pikpak_json', side_effect=AssertionError('cloud access forbidden')):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status, args)
        self.assertEqual(self.state.read_bytes(), original)

    def test_cli_rejects_missing_scope_before_auth_status(self):
        command = [str(SKILLS / 'pikpak-magnet-pipeline/scripts/magnet_pipeline.py'),
                   'step1-add', 'https://example.invalid/demo', '--state', str(self.state)]
        with patch.object(sys, 'argv', command), \
                patch.object(self.module, 'check_login', side_effect=AssertionError('auth must not run')):
            with self.assertRaises(SystemExit):
                self.module.main()

    def test_unknown_status_is_error_not_infinite_running(self):
        self.state.write_text(json.dumps(self.bound_state()))
        with patch.object(self.module, 'pikpak_json', return_value=(
                {'task_id': 'T1', 'status': 'brand_new'}, None)):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status, self.ns(task_id='T1'))

    def test_status_recheck_does_not_erase_download_receipt(self):
        local = Path(self.tmp.name) / 'neutral.data'
        local.write_bytes(b'good')
        record = self.bound_state(file_id='F1', stage='downloaded',
                                  local_path=str(local), local_size=4,
                                  local_sha256=hashlib.sha256(b'good').hexdigest())
        self.state.write_text(json.dumps(record))
        with patch.object(self.module, 'pikpak_json', return_value=(
                {'task_id': 'T1', 'status': 'complete', 'file_id': 'F1'}, None)):
            self.invoke(self.module.step2_status, self.ns(task_id='T1'))
        self.assertEqual(json.loads(self.state.read_text())['stage'], 'downloaded')

    def test_cleanup_without_receipt_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.invoke(self.module.step4_cleanup, argparse.Namespace(file_ids=['F1']))

    def test_cleanup_rejects_injected_id_without_printing_command(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.module.step4_cleanup(argparse.Namespace(file_ids=['F1; touch nope']))
        self.assertNotIn('pikpak rm', out.getvalue())

    def test_failed_download_never_retries_outside_destination(self):
        self.state.write_text(json.dumps(self.bound_state(file_id='F1', stage='ready')))
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(self.module, 'pikpak', return_value=(1, '', 'neutral failure')) as run:
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.module.step3_download(self.ns(file_id='F1', dest=tmp))
                self.assertEqual(run.call_count, 1, 'failure must not fallback into the process cwd')


import subprocess
import sys
import hashlib


class TestSave(unittest.TestCase):
    def test_inspection_cli_error_is_nonzero_and_pure_json(self):
        script = SKILLS / 'pikpak-save/scripts/inspect_task.py'
        proc = run_entry_cli(script, ['--task-id', 'T1'],
                             input=json.dumps({'task_id':'T1','status':'error'}))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)['status'], 'error')
        proc = run_entry_cli(script, ['--task-id', 'T1'], input='[]')
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, '')
        secret = 'sensitive-error-body'
        proc = run_entry_cli(script, ['--task-id', 'T1'], input=json.dumps({
            'error': {'message': secret}, 'task_id': 'T1',
            'status': 'complete', 'file_id': 'F1'}))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, '')
        self.assertNotIn(secret, proc.stderr)

    def test_inspection_rejects_wrong_task_even_with_same_name(self):
        module = load('pikpak-save/scripts/inspect_task.py')
        with self.assertRaises(ValueError):
            module.inspect_task({'task_id': 'OTHER', 'status': 'complete', 'file_id': 'F1'}, 'T1')

    def test_inspection_rejects_outer_and_nested_task_id_conflict(self):
        module = load('pikpak-save/scripts/inspect_task.py')
        with self.assertRaises(ValueError):
            module.inspect_task({
                'task_id': 'OTHER',
                'task': {'id': 'T1', 'status': 'complete', 'file_id': 'F1'},
            }, 'T1')

    def test_inspection_rejects_outer_and_nested_file_id_conflict(self):
        module = load('pikpak-save/scripts/inspect_task.py')
        with self.assertRaises(ValueError):
            module.inspect_task({
                'task_id': 'T1',
                'file_id': 'OTHERFILE',
                'task': {'id': 'T1', 'status': 'complete', 'file_id': 'F1'},
            }, 'T1')

    def test_inspection_rejects_conflicting_status_and_phase(self):
        module = load('pikpak-save/scripts/inspect_task.py')
        with self.assertRaises(ValueError):
            module.inspect_task({
                'task_id': 'T1',
                'status': 'complete',
                'task': {'id': 'T1', 'phase': 'running', 'file_id': 'F1'},
            }, 'T1')

    def test_inspection_rejects_explicit_failure_envelopes(self):
        module = load('pikpak-save/scripts/inspect_task.py')
        for name, position, response in failure_task_responses():
            with self.subTest(envelope=name, position=position), self.assertRaises(ValueError) as exc:
                module.inspect_task(response, 'T1')
            self.assertNotIn(PRIVATE_ERROR_BODY, str(exc.exception))

    def test_inspection_failure_cli_is_nonzero_without_success_or_error_body(self):
        script = SKILLS / 'pikpak-save/scripts/inspect_task.py'
        for name, position, response in failure_task_responses():
            with self.subTest(envelope=name, position=position):
                proc = run_entry_cli(script, ['--task-id', 'T1'], input=json.dumps(response))
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(proc.stdout, '', 'failure must not emit complete/download/cleanup actions')
                self.assertNotIn(PRIVATE_ERROR_BODY, proc.stderr)

    def test_complete_without_file_id_is_not_a_success(self):
        module = load('pikpak-save/scripts/inspect_task.py')
        with self.assertRaises(ValueError):
            module.inspect_task({'task_id': 'T1', 'status': 'complete'}, 'T1')

    def test_known_task_states_have_explicit_next_action(self):
        module = load('pikpak-save/scripts/inspect_task.py')
        for status in ('pending', 'running', 'complete', 'error'):
            result = module.inspect_task({'task_id': 'T1', 'status': status, 'file_id': 'F1'}, 'T1')
            self.assertEqual(result['status'], status)
            self.assertIn('next', result)


from types import ModuleType


class TestPipelineBinding(unittest.TestCase):
    module: ModuleType
    tmp: tempfile.TemporaryDirectory
    state: Path
    setUp = TestPipeline.setUp
    ns = TestPipeline.ns
    bound_state = TestPipeline.bound_state
    invoke = TestPipeline.invoke

    def test_orphan_receipts_cannot_authorize_status_download_or_cleanup(self):
        local = Path(self.tmp.name) / 'neutral.data'
        local.write_bytes(b'good')
        base = self.bound_state(file_id='F1', local_path=str(local), local_size=4,
                                local_sha256=hashlib.sha256(b'good').hexdigest())
        for stage in ('ready', 'downloaded'):
            for field, value in (('fingerprint', None), ('fingerprint', 'not-a-sha256'),
                                 ('task_id', None), ('task_id', 'T1;unsafe'),
                                 ('folder', None), ('account_scope', None),
                                 ('account_scope', 'acct-A'),
                                 ('account_scope_binding', None),
                                 ('account_scope_binding', 'verified')):
                record = {**base, 'stage': stage}
                if value is None:
                    record.pop(field)
                else:
                    record[field] = value
                for step in ('status', 'download', 'cleanup'):
                    with self.subTest(stage=stage, field=field, value=value, step=step):
                        self.state.write_text(json.dumps(record))
                        original = self.state.read_bytes()
                        response = {'task_id': 'T1', 'status': 'complete', 'file_id': 'F1'}
                        with patch.object(self.module.subprocess, 'run', return_value=
                                          subprocess.CompletedProcess(['neutral'], 0, json.dumps(response), '')) as run:
                            out = io.StringIO()
                            with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                                if step == 'status':
                                    self.module.step2_status(self.ns(task_id='T1'))
                                elif step == 'download':
                                    self.module.step3_download(self.ns(file_id='F1', dest=str(Path(self.tmp.name) / 'out')))
                                else:
                                    self.module.step4_cleanup(self.ns(file_ids=['F1'], verified_local=True))
                            self.assertEqual(out.getvalue(), '')
                            self.assertEqual(run.call_count, 0)
                            self.assertEqual(self.state.read_bytes(), original)

    def test_ready_or_downloaded_requires_latest_complete_observation(self):
        local = Path(self.tmp.name) / 'neutral.data'
        local.write_bytes(b'good')
        for stage in ('ready', 'downloaded'):
            for status in (None, 'pending', 'running', 'error'):
                for step in ('download', 'cleanup'):
                    with self.subTest(stage=stage, latest=status, step=step):
                        record = self.bound_state(file_id='F1', stage=stage, local_path=str(local),
                                                  local_size=4, local_sha256=hashlib.sha256(b'good').hexdigest())
                        if status is None:
                            record.pop('latest_task_status')
                        else:
                            record['latest_task_status'] = status
                        self.state.write_text(json.dumps(record))
                        original = self.state.read_bytes()
                        dest = Path(self.tmp.name) / f'out-{stage}-{status}-{step}'
                        def download(argv, **kwargs):
                            Path(argv[argv.index('-o') + 1]).write_bytes(b'good')
                            return subprocess.CompletedProcess(argv, 0, '', '')
                        with patch.object(self.module.subprocess, 'run', side_effect=download) as run:
                            out = io.StringIO()
                            with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                                if step == 'download':
                                    self.module.step3_download(self.ns(file_id='F1', dest=str(dest)))
                                else:
                                    self.module.step4_cleanup(self.ns(file_ids=['F1'], verified_local=True))
                            self.assertEqual(run.call_count, 0)
                            self.assertEqual(out.getvalue(), '')
                            self.assertEqual(self.state.read_bytes(), original)
                            self.assertFalse(dest.exists())

    def test_manual_reconciliation_retains_origin_and_allows_bound_steps(self):
        args = self.ns(magnet='https://example.invalid/approved', folder='')
        with patch.object(self.module.subprocess, 'run', return_value=
                          subprocess.CompletedProcess(['neutral'], 0, '{}', '')):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step1_add, args)
        record = json.loads(self.state.read_text())
        fingerprint = record['fingerprint']
        # Explicit human reconciliation, not an automatic claim by step2.
        record.update(task_id='T1', stage='submitted')
        self.state.write_text(json.dumps(record))
        def cli(argv, **kwargs):
            if argv[1:] == ['task', 'get', 'T1', '-F', 'json']:
                return subprocess.CompletedProcess(argv, 0, json.dumps(
                    {'task': {'id': 'T1', 'phase': 'PHASE_TYPE_COMPLETE', 'file_id': 'F1'}}), '')
            self.assertEqual(argv[1:3], ['download', 'F1'])
            self.assertEqual(argv[3], '-o')
            Path(argv[4]).write_bytes(b'good')
            return subprocess.CompletedProcess(argv, 0, '', '')
        with patch.object(self.module.subprocess, 'run', side_effect=cli) as run:
            self.invoke(self.module.step2_status, self.ns(task_id='T1'))
            self.invoke(self.module.step3_download,
                        self.ns(file_id='F1', dest=str(Path(self.tmp.name) / 'out')))
            out = self.invoke(self.module.step4_cleanup, self.ns(file_ids=['F1'], verified_local=True))
            self.assertIn('pikpak rm F1', out)
            self.assertEqual(run.call_count, 2)
        observed = json.loads(self.state.read_text())
        self.assertEqual(observed['fingerprint'], fingerprint)
        self.assertEqual((observed['task_id'], observed['file_id'], observed['latest_task_status']),
                         ('T1', 'F1', 'complete'))

    def test_missing_receipt_or_wrong_file_id_never_starts_download(self):
        self.state.write_text(json.dumps(self.bound_state(file_id='F1', stage='ready')))
        for state, fid in ((None, 'F1'), (str(self.state) + '.absent', 'F1'), (str(self.state), 'F2')):
            with self.subTest(state=state, fid=fid):
                dest = Path(self.tmp.name) / 'no-download'
                with patch.object(self.module.subprocess, 'run', side_effect=AssertionError('must not launch')) as run:
                    with self.assertRaises(SystemExit):
                        self.invoke(self.module.step3_download,
                                    argparse.Namespace(state=state, file_id=fid, dest=str(dest)))
                    self.assertEqual(run.call_count, 0)
                self.assertFalse(dest.exists())


class TestPipelineReceipt(unittest.TestCase):
    module: ModuleType
    tmp: tempfile.TemporaryDirectory
    state: Path
    setUp = TestPipeline.setUp
    ns = TestPipeline.ns
    bound_state = TestPipeline.bound_state
    invoke = TestPipeline.invoke

    def assert_noncomplete_revokes_cleanup(self, status):
        local = Path(self.tmp.name) / 'neutral.data'
        local.write_bytes(b'good')
        record = self.bound_state(file_id='F1', stage='downloaded', latest_task_status='complete',
                                  local_path=str(local), local_size=4,
                                  local_sha256=hashlib.sha256(b'good').hexdigest())
        self.state.write_text(json.dumps(record))
        response = {'task_id': 'T1', 'status': status, 'file_id': 'F1'}
        with patch.object(self.module.subprocess, 'run', return_value=
                          subprocess.CompletedProcess(['neutral'], 0, json.dumps(response), '')) as run:
            self.invoke(self.module.step2_status, self.ns(task_id='T1'))
            self.assertEqual(run.call_count, 1)
            observed = json.loads(self.state.read_text())
            self.assertEqual(observed['stage'], 'waiting')
            self.assertEqual(observed['latest_task_status'], status)
            for field in ('fingerprint', 'task_id', 'file_id', 'local_path', 'local_size', 'local_sha256'):
                self.assertEqual(observed[field], record[field], field)
            self.assertEqual(local.read_bytes(), b'good')
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.module.step4_cleanup(self.ns(file_ids=['F1'], verified_local=True))
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(run.call_count, 1, 'cleanup must not call any CLI')

    def test_downloaded_then_pending_preserves_bytes_but_revokes_cleanup(self):
        self.assert_noncomplete_revokes_cleanup('pending')

    def test_downloaded_then_running_preserves_bytes_but_revokes_cleanup(self):
        self.assert_noncomplete_revokes_cleanup('running')

    def test_conflicting_observation_revokes_previous_cleanup_qualification(self):
        local = Path(self.tmp.name) / 'neutral.data'
        local.write_bytes(b'good')
        record = self.bound_state(file_id='F1', stage='downloaded', latest_task_status='complete',
                                  local_path=str(local), local_size=4,
                                  local_sha256=hashlib.sha256(b'good').hexdigest())
        self.state.write_text(json.dumps(record))
        response = {'task_id': 'T1', 'status': 'complete', 'phase': 'running', 'file_id': 'F1'}
        with patch.object(self.module, 'pikpak_json', return_value=(response, None)):
            with self.assertRaises(SystemExit):
                self.invoke(self.module.step2_status, self.ns(task_id='T1'))
        observed = json.loads(self.state.read_text())
        self.assertNotIn(observed['stage'], ('ready', 'downloaded'))
        self.assertNotEqual(observed.get('latest_task_status'), 'complete')
        self.assertEqual(local.read_bytes(), b'good')
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.module.step4_cleanup(self.ns(file_ids=['F1'], verified_local=True))
        self.assertEqual(out.getvalue(), '')

    def test_unknown_folder_binding_blocks_cleanup(self):
        local = Path(self.tmp.name) / 'neutral.data'
        local.write_bytes(b'good')
        record = self.bound_state(folder='FOLDER_A', file_id='F1', stage='downloaded',
                                  latest_task_status='complete', folder_verification='unknown',
                                  local_path=str(local), local_size=4,
                                  local_sha256=hashlib.sha256(b'good').hexdigest())
        self.state.write_text(json.dumps(record))
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.module.step4_cleanup(self.ns(file_ids=['F1'], verified_local=True))
        self.assertEqual(out.getvalue(), '')

    def test_error_remains_exit_2_and_keeps_local_receipt_without_cleanup(self):
        local = Path(self.tmp.name) / 'neutral.data'
        local.write_bytes(b'good')
        record = self.bound_state(file_id='F1', stage='downloaded', local_path=str(local),
                                  local_size=4, local_sha256=hashlib.sha256(b'good').hexdigest())
        self.state.write_text(json.dumps(record))
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess(
                ['neutral'], 0, json.dumps({'task_id': 'T1', 'status': 'error', 'file_id': 'F1'}), '')) as run:
            with self.assertRaises(SystemExit) as exc:
                self.invoke(self.module.step2_status, self.ns(task_id='T1'))
            self.assertEqual(exc.exception.code, 2)
            observed = json.loads(self.state.read_text())
            self.assertEqual((observed['stage'], observed['latest_task_status']), ('failed', 'error'))
            self.assertEqual(observed['local_sha256'], record['local_sha256'])
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.module.step4_cleanup(self.ns(file_ids=['F1'], verified_local=True))
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(run.call_count, 1)

    def test_same_complete_revalidates_receipt_before_restoring_cleanup(self):
        for waiting in ('pending', 'running'):
            for change in ('none', 'bytes', 'missing', 'file_id'):
                with self.subTest(waiting=waiting, change=change):
                    local = Path(self.tmp.name) / 'neutral.data'
                    local.write_bytes(b'good')
                    record = self.bound_state(file_id='F1', stage='downloaded', latest_task_status='complete',
                                              local_path=str(local), local_size=4,
                                              local_sha256=hashlib.sha256(b'good').hexdigest())
                    self.state.write_text(json.dumps(record))
                    completed_id = 'F2' if change == 'file_id' else 'F1'
                    responses = [{'task_id': 'T1', 'status': waiting, 'file_id': 'F1'},
                                 {'task_id': 'T1', 'status': 'complete', 'file_id': completed_id}]
                    with patch.object(self.module.subprocess, 'run', side_effect=[
                            subprocess.CompletedProcess(['neutral'], 0, json.dumps(r), '') for r in responses]) as run:
                        self.invoke(self.module.step2_status, self.ns(task_id='T1'))
                        if change == 'bytes':
                            local.write_bytes(b'evil')
                        elif change == 'missing':
                            local.unlink()
                        self.invoke(self.module.step2_status, self.ns(task_id='T1'))
                        observed = json.loads(self.state.read_text())
                        self.assertEqual(observed['latest_task_status'], 'complete')
                        if change == 'none':
                            self.assertEqual(observed['stage'], 'downloaded')
                            self.assertEqual(observed['local_sha256'], record['local_sha256'])
                            out = self.invoke(self.module.step4_cleanup,
                                              self.ns(file_ids=['F1'], verified_local=True))
                            self.assertIn('pikpak rm F1', out)
                        else:
                            self.assertEqual(observed['stage'], 'ready')
                            out = io.StringIO()
                            with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                                self.module.step4_cleanup(self.ns(file_ids=[completed_id], verified_local=True))
                            self.assertEqual(out.getvalue(), '')
                            if change == 'file_id':
                                self.assertNotIn('local_sha256', observed, 'never rebind old local bytes to a new file ID')
                        self.assertEqual(run.call_count, 2, 'receipt recovery must not re-download')
                        self.assertTrue(all(c.args[0][1:3] == ['task', 'get'] for c in run.call_args_list))

    def test_local_same_length_change_blocks_cleanup(self):
        self.state.write_text(json.dumps(self.bound_state(file_id='F1', stage='ready')))
        dest = Path(self.tmp.name) / 'out'
        def download(*args, **kwargs):
            Path(args[args.index('-o') + 1]).write_bytes(b'good')
            return 0, '', ''
        with patch.object(self.module, 'pikpak', side_effect=download):
            self.invoke(self.module.step3_download, self.ns(file_id='F1', dest=str(dest)))
        receipt = json.loads(self.state.read_text())
        self.assertEqual(receipt.get('local_sha256'), hashlib.sha256(b'good').hexdigest())
        Path(receipt['local_path']).write_bytes(b'evil')
        with self.assertRaises(SystemExit):
            self.invoke(self.module.step4_cleanup, self.ns(file_ids=['F1'], verified_local=True))

    def test_end_to_end_fake_cli_no_real_credentials_or_network(self):
        fake = Path(self.tmp.name) / 'neutral-cli'
        log = Path(self.tmp.name) / 'calls.jsonl'
        fake.write_text('#!' + sys.executable + '\n' + '''import sys,json,os
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['NEUTRAL_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')
if args[:2]==['auth','status']: print(json.dumps({'logged_in':True,'profile':{'id':'fixture-account-id'}}))
elif args[:2]==['task','add']: print(json.dumps({'task_id':'T1'}))
elif args[:3]==['task','get','T1']: print(json.dumps({'task_id':'T1','status':'complete','file_id':'F1'}))
elif args[:2]==['download','F1'] and len(args)==4 and args[2]=='-o': Path(args[3]).write_bytes(b'neutral')
else: sys.exit(77)
''')
        fake.chmod(0o700)
        env = {'PATH': os.defpath, 'PIKPAK_BIN': str(fake), 'NEUTRAL_LOG': str(log),
               'HOME': self.tmp.name, 'TMPDIR': self.tmp.name}
        script = SKILLS / 'pikpak-magnet-pipeline/scripts/magnet_pipeline.py'
        for cmd in [('step1-add','https://example.invalid/demo', '--account-scope', 'acct-test'),
                    ('step1-add','https://example.invalid/demo', '--account-scope', 'acct-test'),
                    ('step2-status','T1', '--account-scope', 'acct-test'),
                    ('step3-download','F1','--dest',str(Path(self.tmp.name)/'downloads'),
                     '--account-scope', 'acct-test'),
                    ('step4-cleanup','F1','--verified-local', '--account-scope', 'acct-test')]:
            with allow_neutral_program(fake):
                proc = run_entry_cli(script, [*cmd, '--state', str(self.state)], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(sum(c[:2]==['task','add'] for c in calls), 1)
        self.assertFalse(any('rm' in c for c in calls))
        self.assertIn('pikpak rm F1', proc.stdout)


class TestPipelineTimeout(unittest.TestCase):
    module: ModuleType
    tmp: tempfile.TemporaryDirectory
    state: Path
    setUp = TestPipeline.setUp

    def invoke_main(self, *argv):
        return run_entry_cli(SKILLS / 'pikpak-magnet-pipeline/scripts/magnet_pipeline.py',
                             [*argv, '--state', str(self.state), '--account-scope', 'acct-test'])

    def cli(self, argv, **kwargs):
        args = argv[1:]
        self.calls.append(args)
        if args == ['auth', 'status', '-F', 'json']:
            data: dict = {'logged_in': True}
            if self.account is not None:
                data['profile'] = {'id': self.account}
        elif args == ['task', 'add', 'https://example.invalid/approved', '-p', 'FOLDER_A', '-F', 'json']:
            if self.timeout_add:
                raise subprocess.TimeoutExpired(['neutral-cli', *args], 180,
                                                output=PRIVATE_ERROR_BODY, stderr=PRIVATE_ERROR_BODY)
            data = {'task_id': 'T1', 'parent_id': 'FOLDER_A'}
        elif args == ['task', 'get', 'T1', '-F', 'json']:
            if self.timeout_query:
                raise subprocess.TimeoutExpired(['neutral-cli', *args], 120,
                                                output=PRIVATE_ERROR_BODY, stderr=PRIVATE_ERROR_BODY)
            data = {'task_id': 'T1', 'status': 'complete', 'file_id': self.completed_id, 'parent_id': 'FOLDER_A'}
        elif len(args) == 4 and args[:3] == ['download', 'F1', '-o']:
            Path(args[3]).write_bytes(b'neutral-good-content')
            return subprocess.CompletedProcess(argv, 0, '', '')
        else:
            raise AssertionError('Unapproved command in timeout fixture')
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), '')

    def prepare(self, stage):
        self.calls, self.timeout_query = [], False
        self.timeout_add, self.account, self.completed_id = False, 'fixture-account-id', 'F1'
        for argv in [('step1-add', 'https://example.invalid/approved', '--folder', 'FOLDER_A'),
                     ('step2-status', 'T1')]:
            result = self.invoke_main(*argv)
            self.assertEqual(result.returncode, 0, result.stderr)
        if stage == 'downloaded':
            destination = tempfile.mkdtemp(prefix='output-', dir=self.tmp.name)
            result = self.invoke_main('step3-download', 'F1', '--dest', destination)
            self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.state.read_text())
        self.assertEqual(record['stage'], stage)
        return record

    def test_task_query_timeout_revokes_bound_qualification(self):
        for stage in ('ready', 'downloaded'):
            with self.subTest(stage=stage):
                if self.state.exists():
                    self.state.unlink()
                with patch.object(subprocess, 'run', side_effect=self.cli):
                    original = self.prepare(stage)
                    self.timeout_query = True
                    result = self.invoke_main('step2-status', 'T1')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, '')
                    observed = json.loads(self.state.read_text())
                    self.assertEqual(observed['stage'], 'blocked')
                    self.assertNotIn('latest_task_status', observed)
                    self.assertEqual(observed['last_rejected_observation'], 'query_failure')
                    for field, value in original.items():
                        if field not in ('stage', 'latest_task_status'):
                            self.assertEqual(observed[field], value, field)
                    if stage == 'downloaded':
                        self.assertEqual(Path(original['local_path']).read_bytes(), b'neutral-good-content')
                    before = len(self.calls)
                    destination = Path(self.tmp.name) / 'blocked-output'
                    for argv in [('step3-download', 'F1', '--dest', str(destination)),
                                 ('step4-cleanup', 'F1', '--verified-local')]:
                        blocked = self.invoke_main(*argv)
                        self.assertNotEqual(blocked.returncode, 0)
                        self.assertEqual(blocked.stdout, '')
                    self.assertEqual(self.calls[before:], [['auth', 'status', '-F', 'json']] * 2)
                    self.assertFalse(destination.exists())
                    self.assertNotIn(PRIVATE_ERROR_BODY, result.stderr + self.state.read_text())

    def test_transport_timeout_returns_redacted_failure_for_json_caller(self):
        timeout = subprocess.TimeoutExpired(['neutral-cli', PRIVATE_ERROR_BODY], 120,
                                            output=PRIVATE_ERROR_BODY, stderr=PRIVATE_ERROR_BODY)
        with patch.object(subprocess, 'run', side_effect=timeout):
            self.assertEqual(self.module.pikpak('task', 'get', 'T1', fmt_json=True), (124, '', ''))
            data, error = self.module.pikpak_json('task', 'get', 'T1')
        self.assertIsNone(data)
        self.assertIn('124', error)
        self.assertNotIn(PRIVATE_ERROR_BODY, error)

    def test_creation_timeout_retains_submitting_and_never_resubmits(self):
        self.calls, self.timeout_add, self.account = [], True, 'fixture-account-id'
        with patch.object(subprocess, 'run', side_effect=self.cli):
            for attempt in range(2):
                result = self.invoke_main('step1-add', 'https://example.invalid/approved', '--folder', 'FOLDER_A')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                record = json.loads(self.state.read_text())
                self.assertEqual(record['stage'], 'submitting')
                self.assertNotIn('task_id', record)
                self.assertNotIn('file_id', record)
                self.assertNotIn(PRIVATE_ERROR_BODY, result.stderr + self.state.read_text())
                if attempt == 0:
                    self.assertIn('response_unavailable', result.stderr)
        self.assertEqual(sum(c[:2] == ['task', 'add'] for c in self.calls), 1)
        self.assertEqual(sum(c[:2] == ['auth', 'status'] for c in self.calls), 2)

    def test_same_complete_after_timeout_revalidates_local_receipt(self):
        cases = [('ready', 'none'), *(('downloaded', change) for change in ('none', 'bytes', 'missing', 'file_id'))]
        for stage, change in cases:
            with self.subTest(stage=stage, change=change):
                if self.state.exists():
                    self.state.unlink()
                with patch.object(subprocess, 'run', side_effect=self.cli):
                    original = self.prepare(stage)
                    self.timeout_query = True
                    self.assertNotEqual(self.invoke_main('step2-status', 'T1').returncode, 0)
                    self.assertEqual(json.loads(self.state.read_text())['stage'], 'blocked')
                    self.timeout_query = False
                    if change == 'bytes':
                        changed = b'changed-good-content'
                        self.assertEqual(len(changed), original['local_size'], 'must exercise same-size hash mismatch')
                        Path(original['local_path']).write_bytes(changed)
                    elif change == 'missing':
                        Path(original['local_path']).unlink()
                    elif change == 'file_id':
                        self.completed_id = 'F2'
                    before_downloads = sum(c[:1] == ['download'] for c in self.calls)
                    result = self.invoke_main('step2-status', 'T1')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    observed = json.loads(self.state.read_text())
                    self.assertEqual(observed['latest_task_status'], 'complete')
                    self.assertEqual(observed['file_id'], self.completed_id)
                    if stage == 'downloaded' and change == 'none':
                        self.assertEqual(observed['stage'], 'downloaded')
                        self.assertEqual(observed['local_sha256'], original['local_sha256'])
                        cleanup = self.invoke_main('step4-cleanup', 'F1', '--verified-local')
                        self.assertEqual(cleanup.returncode, 0, cleanup.stderr)
                        self.assertIn('pikpak rm F1', cleanup.stdout)
                    else:
                        self.assertEqual(observed['stage'], 'ready')
                        cleanup = self.invoke_main('step4-cleanup', self.completed_id, '--verified-local')
                        self.assertNotEqual(cleanup.returncode, 0)
                        self.assertEqual(cleanup.stdout, '')
                        if change == 'file_id':
                            self.assertNotIn('local_sha256', observed)
                    self.assertEqual(sum(c[:1] == ['download'] for c in self.calls), before_downloads,
                                     'receipt recovery must not re-download')
                    if stage == 'ready':
                        result = self.invoke_main('step3-download', 'F1', '--dest',
                                                  str(Path(self.tmp.name) / 'ready-recovered'))
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(self.invoke_main('step4-cleanup', 'F1', '--verified-local').returncode, 0)

    def test_cleanup_cli_only_reads_identity_and_retains_identity_gate(self):
        with patch.object(subprocess, 'run', side_effect=self.cli):
            self.prepare('downloaded')
            for account in ('fixture-account-id', 'different-fixture-account', None):
                with self.subTest(account=account):
                    self.account = account
                    before = len(self.calls)
                    result = self.invoke_main('step4-cleanup', 'F1', '--verified-local')
                    if account == 'fixture-account-id':
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn('pikpak rm F1', result.stdout)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, '')
                    self.assertEqual(self.calls[before:], [['auth', 'status', '-F', 'json']])

    def test_unrelated_system_exit_is_not_swallowed_or_classified_as_timeout(self):
        with patch.object(subprocess, 'run', side_effect=self.cli):
            self.prepare('downloaded')
        original = self.state.read_bytes()
        def cli(argv, **kwargs):
            if argv[1:] == ['task', 'get', 'T1', '-F', 'json']:
                raise SystemExit(37)
            return self.cli(argv, **kwargs)
        with patch.object(subprocess, 'run', side_effect=cli):
            result = self.invoke_main('step2-status', 'T1')
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.state.read_bytes(), original)


class TestEntryIsolation(unittest.TestCase):
    def test_direct_process_network_and_notification_paths_are_hard_denied(self):
        attempts = (lambda: subprocess.run([sys.executable, '-c', 'raise SystemExit(0)']),
                    lambda: subprocess.Popen(['/unapproved-neutral-program']),
                    lambda: subprocess.run(['osascript', '-e', 'display notification "neutral"']),
                    lambda: socket.socket(),
                    lambda: socket.create_connection(('example.invalid', 443)),
                    lambda: urllib.request.urlopen('https://example.invalid'),
                    lambda: os.system('true'))
        for attempt in attempts:
            with self.subTest(attempt=attempt), self.assertRaises(AssertionError):
                attempt()


class TestPublicEntryEvidence(unittest.TestCase):
    def test_cleanup_help_and_documents_disclose_identity_only_cli_boundary(self):
        script = SKILLS / 'pikpak-magnet-pipeline/scripts/magnet_pipeline.py'
        for argv in (['--help'], ['step4-cleanup', '--help']):
            with self.subTest(help=argv), patch.object(
                    subprocess, 'run', side_effect=AssertionError('help must not access any CLI')) as run:
                result = run_entry_cli(script, argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                text = ' '.join(result.stdout.split())
                for phrase in ('只读 auth status', '身份摘要', '不查询 task/file', '不删除'):
                    self.assertIn(phrase, text)
                self.assertNotIn('不调用 CLI', text)
                self.assertEqual(run.call_count, 0)
        for relative in ('SKILL.md', 'references/upstream-design.md'):
            with self.subTest(document=relative):
                text = (SKILLS / 'pikpak-magnet-pipeline' / relative).read_text()
                for phrase in ('只读', 'auth status', '身份摘要', '不查询 task/file', '不删除',
                               '中性 fixture 合同', '真实 schema 尚未验收'):
                    self.assertIn(phrase, text)
                self.assertNotIn('不会调用 CLI', text)
                self.assertNotIn('账号身份变更检测、', text)

    def test_each_candidate_has_source_license_and_release_boundary(self):
        snapshots = {'cli/cli': '3d7967df5a94052b0554898ffa02526f0f90b8c9',
                     'rclone/rclone': 'd369079a1448c487d09d85bfe11382e16ea4e345',
                     'CJReinforce/PikPakAutoOfflineDownloadBot': '473735baa88ec74aeb03b2702449f3fe8db0741d'}
        candidates = {'pikpak-onboarding': ('cli/cli', 'rclone/rclone'),
                      'pikpak-save': ('rclone/rclone', 'cli/cli'),
                      'pikpak-magnet-pipeline': ('rclone/rclone', 'CJReinforce/PikPakAutoOfflineDownloadBot')}
        for skill, expected in candidates.items():
            with self.subTest(skill=skill):
                text = (SKILLS / skill / 'references/upstream-design.md').read_text()
                self.assertNotIn('/Users/', text, 'public evidence must not depend on private absolute paths')
                rows = [line.strip().strip('|').split('|') for line in text.splitlines() if line.startswith('|')]
                rows = [[cell.strip() for cell in row] for row in rows]
                self.assertIn(['candidate', 'source', 'license', 'release_or_explicit_unverified'], rows)
                by_candidate = {row[0]: row for row in rows if len(row) == 4 and row[0] in expected}
                self.assertEqual(set(by_candidate), set(expected))
                for candidate in expected:
                    _, source, license_info, release = by_candidate[candidate]
                    self.assertIn(snapshots[candidate], source)
                    if candidate.endswith('PikPakAutoOfflineDownloadBot'):
                        self.assertIn('未确认', license_info)
                        self.assertIn('未核验', release)
                        self.assertIn('/releases/latest', release)
                        self.assertIn('不复制代码', text)
                        self.assertIn('不安装运行', text)
                    else:
                        self.assertIn('MIT', license_info)
                        self.assertIn(snapshots[candidate], license_info)
                        self.assertIn('/LICENSE' if candidate == 'cli/cli' else '/COPYING', license_info)
                        self.assertIn('/releases/tag/', release)
                        self.assertIn('未核验', release)
                        self.assertIn('2026-10-16', text)


class TestReferralCoverage(unittest.TestCase):
    def test_every_catalog_skill_publishes_optional_referral_disclosure(self):
        catalog = json.loads((ROOT / 'skills.sh.json').read_text(encoding='utf-8'))
        names = [skill for group in catalog['groupings'] for skill in group['skills']]
        self.assertEqual(len(names), 14)
        self.assertEqual(len(set(names)), 14)
        for name in names:
            with self.subTest(skill=name):
                text = (SKILLS / name / 'SKILL.md').read_text(encoding='utf-8')
                self.assertIn('22874035', text)
                self.assertIn('--affiliate=', text)
                self.assertIn('Referral disclosure', text)
                self.assertIn('拒绝', text)
                if name != 'pikpak-onboarding':
                    self.assertIn('不自动注册', text)
                    self.assertIn('pikpak-onboarding', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
