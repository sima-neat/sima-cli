from unittest.mock import Mock, patch

from sima_cli.model_zoo.model import _list_available_models_internal


def _prompt_result(value):
    prompt = Mock()
    prompt.execute.return_value = value
    return prompt


def test_internal_model_list_returns_to_model_selection_after_download():
    response = Mock(status_code=200)
    response.json.return_value = {
        "results": [
            {
                "path": (
                    "SiMaCLI-SDK-Releases/2.1.2-Release/modelzoo_edgematic/"
                    "gen2_target/pose_estimation"
                ),
                "name": "open_pose",
            }
        ]
    }
    session = Mock()
    session.post.return_value = response

    with patch("sima_cli.model_zoo.model.requests.Session", return_value=session), \
         patch("sima_cli.model_zoo.model.get_auth_token", return_value="token"), \
         patch(
             "sima_cli.model_zoo.model.inquirer.fuzzy",
             side_effect=[
                 _prompt_result("pose_estimation/open_pose"),
                 _prompt_result("Exit"),
             ],
         ) as fuzzy, \
         patch(
             "sima_cli.model_zoo.model.inquirer.select",
             return_value=_prompt_result("Download model"),
         ) as select, \
         patch("sima_cli.model_zoo.model._describe_model_internal") as describe, \
         patch("sima_cli.model_zoo.model._download_model_internal") as download:
        _list_available_models_internal("2.1.2", "modalix")

    assert fuzzy.call_count == 2
    select.assert_called_once()
    describe.assert_called_once_with("2.1.2", "modalix", "pose_estimation/open_pose")
    download.assert_called_once_with("2.1.2", "modalix", "pose_estimation/open_pose")
