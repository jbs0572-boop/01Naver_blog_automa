"""Synthetic temporary inputs test diagnostics, never replace historical logs."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.historical_log_preflight import REQUIRED_LOGS, inspect_logs, main


def populate_temporary_inputs(root: Path) -> None:
    for relative, count in REQUIRED_LOGS.items():
        path = root / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text('{}\n' * count, encoding='utf-8')


def test_missing_inputs_are_all_reported_and_not_created(tmp_path):
    result = inspect_logs(tmp_path)
    assert result['diagnostic_status'] == 'blocked'
    assert [x['status'] for x in result['logs']] == ['missing', 'missing']
    assert not (tmp_path / 'runs').exists()


def test_readable_counts_do_not_claim_authenticity_or_full_suite(tmp_path):
    populate_temporary_inputs(tmp_path)
    result = inspect_logs(tmp_path)
    assert result['diagnostic_status'] == 'inputs_readable'
    assert result['historical_authenticity'] == 'not_established'
    assert result['full_suite'] == 'not_run_by_this_command'
    for item in result['logs']:
        assert item['provenance'] == 'unverified'
        assert item['sha256'] == hashlib.sha256((tmp_path / item['path']).read_bytes()).hexdigest()
        assert item['actual_records'] == item['expected_records']


@pytest.mark.parametrize('payload,status', [
    ('{}\n', 'count_mismatch'), ('invalid\n', 'unreadable_or_invalid'),
    ('{"event_type":"unknown"}\n', 'unreadable_or_invalid'),
])
def test_bad_input_does_not_hide_second_missing_file(tmp_path, payload, status):
    target = tmp_path / next(iter(REQUIRED_LOGS))
    target.parent.mkdir()
    target.write_text(payload)
    result = inspect_logs(tmp_path)
    assert [x['status'] for x in result['logs']] == [status, 'missing']
    assert result['diagnostic_status'] == 'blocked'


def test_directory_is_not_accepted_as_log(tmp_path):
    (tmp_path / next(iter(REQUIRED_LOGS))).mkdir(parents=True)
    assert inspect_logs(tmp_path)['logs'][0]['status'] == 'not_regular_file'


def test_invalid_utf8_is_reported(tmp_path):
    path = tmp_path / next(iter(REQUIRED_LOGS))
    path.parent.mkdir()
    path.write_bytes(b'\xff')
    assert inspect_logs(tmp_path)['logs'][0]['status'] == 'unreadable_or_invalid'


def test_external_runs_symlink_is_not_read(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'runs').symlink_to(outside, target_is_directory=True)
    assert all(x['status'] == 'outside_root' for x in inspect_logs(root)['logs'])


def test_internal_symlink_is_rejected(tmp_path):
    target = tmp_path / 'target'
    target.write_text('{}\n' * 8)
    path = tmp_path / next(iter(REQUIRED_LOGS))
    path.parent.mkdir()
    path.symlink_to(target)
    assert inspect_logs(tmp_path)['logs'][0]['status'] == 'symlink_rejected'


def test_cli_nonzero_for_missing_inputs_and_machine_readable_report(tmp_path, capsys):
    assert main(['--root', str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)['diagnostic_status'] == 'blocked'


def test_cli_zero_only_means_readable_not_authentic(tmp_path, capsys):
    populate_temporary_inputs(tmp_path)
    assert main(['--root', str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)['historical_authenticity'] == 'not_established'


def test_mutation_during_validation_is_not_accepted(tmp_path, monkeypatch):
    populate_temporary_inputs(tmp_path)
    import scripts.historical_log_preflight as subject
    original = subject.validate_log
    def changing_validator(path):
        count = original(path)
        path.write_text('{}\n')
        return count
    monkeypatch.setattr(subject, 'validate_log', changing_validator)
    result = inspect_logs(tmp_path)
    assert all(x['status'] == 'changed_during_check' for x in result['logs'])
    assert result['diagnostic_status'] == 'blocked'


def test_read_failure_is_reported_without_exposing_log_contents(tmp_path, monkeypatch):
    populate_temporary_inputs(tmp_path)
    original = Path.read_bytes
    def fail_read(path):
        if path.suffix == '.jsonl':
            raise PermissionError('sensitive example should not leak')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', fail_read)
    result = inspect_logs(tmp_path)
    assert all(x['error_type'] == 'PermissionError' for x in result['logs'])
    assert 'sensitive example' not in json.dumps(result)
