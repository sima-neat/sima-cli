from click.testing import CliRunner

from sima_cli.cli import modelzoo


def test_modelzoo_help_distinguishes_describe_from_get():
    result = CliRunner().invoke(modelzoo, ["--help"])

    assert result.exit_code == 0, result.output
    assert "describe  Provide information about a specific model." in result.output
    assert "get       Download a specific model." in result.output
