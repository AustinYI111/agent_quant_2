from pathlib import Path
from ashare_mvp.backup import backup, restore
from ashare_mvp.restore_research import restore_artifact_layers


def long_parent(tmp_path, length):
    padding = max(1, length - len(str(tmp_path)) - 1)
    parent = tmp_path / ('d' * padding)
    parent.mkdir()
    return parent


def test_restore_valid_destination_does_not_fail_from_nested_temp_path(tmp_path):
    source = tmp_path / 'source'
    relative = Path('artifacts') / ('a' * 37) / 'data.csv'
    file = source / relative
    file.parent.mkdir(parents=True)
    file.write_bytes(b'original verified research data\n')
    archive = tmp_path / 'snapshot.pqr'
    password = 'unit-test-password-only'
    backup(source, ['artifacts'], archive, password)
    destination = long_parent(tmp_path, 180) / 'restored'
    assert len(str(destination / relative)) < 260
    result = restore(archive, destination, password)
    assert result['verified'] and result['restored_files'] == 1
    assert (destination / relative).read_bytes() == file.read_bytes()


def test_two_layers_restore_into_existing_clone_with_deep_parent(tmp_path):
    password = 'unit-test-password-only'
    archives = []
    expected = []
    for index in range(2):
        source = tmp_path / f'source{index}'
        relative = Path('artifacts') / (str(index) + 'a' * 36) / 'data.csv'
        file = source / relative
        file.parent.mkdir(parents=True)
        file.write_bytes(f'verified layer {index}\n'.encode())
        archive = tmp_path / f'layer{index}.pqr'
        backup(source, ['artifacts'], archive, password)
        archives.append(archive)
        expected.append((relative, file.read_bytes()))
    destination = long_parent(tmp_path, 165) / 'clone'
    destination.mkdir()
    code = destination / 'README.md'
    code.write_text('existing code remains intact', encoding='utf8')
    result = restore_artifact_layers(archives, destination, password)
    assert result['verified'] and result['archives'] == 2 and result['installed_files'] == 2
    for relative, value in expected:
        assert (destination / relative).read_bytes() == value
    assert code.read_text(encoding='utf8') == 'existing code remains intact'
