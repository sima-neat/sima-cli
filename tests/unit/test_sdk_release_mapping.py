import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import click
import pytest
import requests

from sima_cli.sdk.utils import (
    SDK_COMPONENTS_URL,
    _extract_sdk_base_version,
    _load_sdk_release_mapping,
    _model_sdk_extension_install_args,
    ensure_model_sdk_extension_installed,
)

CATALOG = json.loads((Path(__file__).parent / 'pkg-metadata/sdk-components.json').read_text())


@pytest.fixture
def catalog_response():
    with patch('sima_cli.sdk.utils.requests.get') as get:
        get.return_value.json.return_value = copy.deepcopy(CATALOG)
        yield get


@pytest.mark.parametrize('version', ['2.0.0', '2.1.3', '3.0.0', '3.0.0.0', '3.0.0.1', '3.0.0.2'])
@pytest.mark.parametrize('suffix', ['', '_Palette_SDK_neat_main_780365a'])
def test_sdk_release_retains_complete_version(version, suffix):
    assert _extract_sdk_base_version('SDK Version = {}{}\n'.format(version, suffix)) == version


@pytest.mark.parametrize('release', ['', 'SDK Version = unknown\n', 'Build Version = 3.0.0.2\n'])
def test_sdk_release_without_version(release):
    assert _extract_sdk_base_version(release) == ''


@pytest.mark.parametrize('machine,arch', [('x86_64', 'amd64'), ('aarch64', 'arm64')])
@pytest.mark.parametrize('sdk,version', [('2.1.3', '2.1.3.1'), ('3.0.0', '3.0.0'),
                                      ('3.0.0.0', '3.0.0'), ('3.0.0.1', '3.0.0.1'), ('3.0.0.2', '3.0.0.2')])
def test_exact_published_compiler_mapping(catalog_response, machine, arch, sdk, version):
    with patch('sima_cli.sdk.utils.platform.machine', return_value=machine):
        assert _model_sdk_extension_install_args(sdk) == ['neat', 'install', 'model-compiler/{}@v{}'.format(arch, version)]
    catalog_response.assert_called_once_with(SDK_COMPONENTS_URL, timeout=8, headers={'Cache-Control': 'no-cache'})
    catalog_response.return_value.raise_for_status.assert_called_once()


@pytest.mark.parametrize('version', ['2.0.0', '2.1.0', '2.1.1', '2.1.2'])
def test_explicit_legacy_mapping(catalog_response, version):
    with patch('sima_cli.sdk.utils.platform.machine', return_value='x86_64'):
        assert _model_sdk_extension_install_args(version) == ['install', '-v', version, 'tools/model-compiler/amd64']


@pytest.mark.parametrize('sdk', ['2.2.0', '3.0.0.3', '3.0.1', '9.0.0'])
def test_unknown_sdk_never_falls_back(catalog_response, sdk):
    with pytest.raises(click.ClickException, match='No Model Compiler compatibility mapping for SDK ' + sdk):
        _model_sdk_extension_install_args(sdk)


@pytest.mark.parametrize('sdk,machine', [('2.0.0', 'arm64'), ('3.0.0.2', 'riscv64')])
def test_unsupported_architecture_fails(catalog_response, sdk, machine):
    with patch('sima_cli.sdk.utils.platform.machine', return_value=machine), pytest.raises(click.ClickException, match='architecture'):
        _model_sdk_extension_install_args(sdk)


@pytest.mark.parametrize('bad', [None, {}, [], {'3.0.0.2': {'version': '3.0.0.2', 'installer': 'shell', 'architectures': ['amd64']}},
    {'3.0.0.2': {'version': '3.0.0.2; echo bad', 'installer': 'neat', 'architectures': ['amd64']}},
    {'3.0.0.2': {'version': '3.0.0.2', 'installer': 'neat', 'architectures': [['amd64']]}}])
def test_invalid_or_missing_compiler_catalog_fails(catalog_response, bad):
    catalog_response.return_value.json.return_value['model_compiler'] = bad
    with pytest.raises(click.ClickException, match='Cannot resolve Model Compiler'):
        _load_sdk_release_mapping()


@pytest.mark.parametrize('error', [requests.Timeout('timed out'), requests.HTTPError('HTTP 503'), ValueError('invalid JSON')])
def test_unavailable_catalog_fails_without_fallback(catalog_response, error):
    catalog_response.side_effect = error
    with pytest.raises(click.ClickException, match='Cannot resolve Model Compiler'):
        _model_sdk_extension_install_args('3.0.0.2')


def test_new_sdk_mapping_needs_only_catalog_change(catalog_response):
    catalog_response.return_value.json.return_value['model_compiler']['3.1.0.1'] = {
        'version': '3.1.0.2', 'installer': 'neat', 'architectures': ['amd64']}
    with patch('sima_cli.sdk.utils.platform.machine', return_value='x86_64'):
        assert _model_sdk_extension_install_args('3.1.0.1')[-1] == 'model-compiler/amd64@v3.1.0.2'


@pytest.mark.parametrize('sdk,expected', [('3.0.0.0', '3.0.0'), ('3.0.0.1', '3.0.0.1'), ('3.0.0.2', '3.0.0.2')])
@pytest.mark.parametrize('machine,arch', [('x86_64', 'amd64'), ('aarch64', 'arm64')])
def test_setup_installs_resolved_compiler(catalog_response, sdk, expected, machine, arch):
    release = Mock(returncode=0, stdout='SDK Version = {}_Palette_SDK_neat_main_abc\n'.format(sdk))
    with patch('sima_cli.sdk.utils._get_container_image_ref', return_value='ghcr.io/sima-neat/sdk:' + sdk), \
         patch('sima_cli.sdk.utils.platform.machine', return_value=machine), \
         patch('sima_cli.sdk.utils.subprocess.run', side_effect=[release, Mock(returncode=0, stdout=machine)]), \
         patch('sima_cli.sdk.utils.discover_archive', return_value=None), \
         patch('sima_cli.sdk.utils.run_command', return_value=True) as run:
        ensure_model_sdk_extension_installed('container', 'docker', auto_install=True)
    assert run.call_count == 2
    script = run.call_args.args[0][-1]
    assert '"$SIMA_CLI_BIN" neat install model-compiler/{}@v{}'.format(arch, expected) in script
    assert '@v2.1.3' not in script


def test_setup_unmapped_sdk_stops_before_prompt_or_install(catalog_response):
    release = Mock(returncode=0, stdout='SDK Version = 3.0.0.3_Palette_SDK_neat_main_abc\n')
    with patch('sima_cli.sdk.utils._get_container_image_ref', return_value='ghcr.io/sima-neat/sdk:3.0.0.3'), \
         patch('sima_cli.sdk.utils.subprocess.run', return_value=release), \
         patch('sima_cli.sdk.utils.yes_no_prompt') as prompt, \
         patch('sima_cli.sdk.utils.run_command') as run, \
         pytest.raises(click.ClickException, match='No Model Compiler compatibility mapping'):
        ensure_model_sdk_extension_installed('container', 'docker', auto_install=True)
    prompt.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize('failed_step', [0, 1])
def test_compiler_setup_stops_on_failure_without_ownership_cleanup(catalog_response, capsys, failed_step):
    release = Mock(returncode=0, stdout='SDK Version = 3.0.0.2\n')
    with patch('sima_cli.sdk.utils._get_container_image_ref', return_value='ghcr.io/sima-neat/sdk:v3.0.0.2'), \
         patch('sima_cli.sdk.utils.discover_archive', return_value=None), \
         patch('sima_cli.sdk.utils.subprocess.run', side_effect=[release, Mock(returncode=0, stdout='aarch64\n')]), \
         patch('sima_cli.sdk.utils.run_command', side_effect=[None] * failed_step + [SystemExit(1)]) as run:
        with pytest.raises(SystemExit) as error:
            ensure_model_sdk_extension_installed(
                'container', 'ashok.sudarsanam', auto_install=True, uid=502, gid=20,
            )

    assert error.value.code == 1
    assert run.call_count == failed_step + 1
    prepare = run.call_args_list[0].args[0]
    assert prepare[:7] == ['docker', 'exec', '-u', 'root', 'container', 'bash', '-lc']
    assert '/etc/sudoers.d/sima-cli-user' in prepare[-1]
    for call in run.call_args_list:
        assert 'chown' not in call.args[0][-1]
        assert 'trap ' not in call.args[0][-1]
    if failed_step == 1:
        install = run.call_args_list[1].args[0]
        assert install[:7] == ['docker', 'exec', '-u', 'ashok.sudarsanam', 'container', 'bash', '-lc']
        assert 'model-compiler/arm64@v3.0.0.2' in install[-1]
    assert '✅ Model Compiler extension installed' not in capsys.readouterr().out


@pytest.mark.parametrize('catalog', [[], {}, {'schema_version': 2, 'revision': 6},
    {'schema_version': 1, 'revision': True}, {'schema_version': 1, 'revision': 0}])
def test_invalid_catalog_schema_is_reported(catalog_response, catalog):
    catalog_response.return_value.json.return_value = catalog
    with pytest.raises(click.ClickException, match='catalog schema'):
        _load_sdk_release_mapping()


@pytest.mark.parametrize('returncode,text', [(1, ''), (0, 'SDK Version = unknown\n')])
def test_setup_without_sdk_identity_fails_before_install(returncode, text):
    with patch('sima_cli.sdk.utils._get_container_image_ref', return_value='ghcr.io/sima-neat/sdk:latest'), \
         patch('sima_cli.sdk.utils.subprocess.run', return_value=Mock(returncode=returncode, stdout=text)), \
         patch('sima_cli.sdk.utils.requests.get') as get, \
         patch('sima_cli.sdk.utils.run_command') as run, \
         pytest.raises(click.ClickException, match='cannot resolve Model Compiler'):
        ensure_model_sdk_extension_installed('container', 'docker')
    get.assert_not_called()
    run.assert_not_called()
