# model_zoo/models.py

import requests
import click
import os
import yaml
from InquirerPy import inquirer
from urllib.parse import urlparse
from rich import print
from rich.table import Table
from rich.panel import Panel
from sima_cli.utils.config import get_auth_token
from sima_cli.utils.config_loader import artifactory_url
from sima_cli.download import download_file_from_url
from collections import defaultdict
from urllib.parse import urljoin
import tempfile
import json

ARTIFACTORY_BASE_URL = artifactory_url() + '/artifactory'

MODELZOO_BASE_URL = "https://docs.sima.ai/pkg_downloads"
SDK_PATH_TEMPLATE = "SDK{version}/model_zoo/"

def _model_category_from_yaml_path(yaml_path: str) -> str:
    parts = yaml_path.split("/")
    return parts[1] if len(parts) > 2 else "unknown"


def _build_model_display_map(models):
    raw_labels = []
    label_counts = defaultdict(int)

    for model in models:
        category = _model_category_from_yaml_path(model.get("yaml_file", ""))
        label = f"{category}/{model['name']}"
        raw_labels.append((label, model))
        label_counts[label] += 1

    display_map = {}
    for label, model in raw_labels:
        display_label = label
        if label_counts[label] > 1:
            display_label = f"{label} [{model.get('yaml_file', '(missing yaml path)')}]"
        display_map[display_label] = model

    return display_map


def _canonicalize_external_model_entries(metadata):
    deduped = {}

    for model in metadata:
        category = _model_category_from_yaml_path(model.get("yaml_file", ""))
        key = (model.get("name"), category, tuple(model.get("assets", [])))

        current = deduped.get(key)
        if current is None:
            deduped[key] = model
            continue

        current_yaml = current.get("yaml_file", "")
        candidate_yaml = model.get("yaml_file", "")
        current_base = os.path.splitext(os.path.basename(current_yaml))[0]
        candidate_base = os.path.splitext(os.path.basename(candidate_yaml))[0]

        current_rank = (current_base.endswith("_1"), len(current_yaml), current_yaml)
        candidate_rank = (candidate_base.endswith("_1"), len(candidate_yaml), candidate_yaml)

        if candidate_rank < current_rank:
            deduped[key] = model

    return list(deduped.values())

def _is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

def describe_model_from_yaml_data(yaml_text: str):
    """
    Render model information from raw YAML text.
    Works for both internal and external model sources.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        click.echo(f"❌ Failed to parse YAML: {e}")
        return

    model = data.get("model", {})
    pipeline = data.get("pipeline", {})

    print(
        Panel.fit(
            f"[bold green]{model.get('name', 'Unknown')}[/bold green] - "
            f"{model.get('task', 'Unknown Task')}",
            subtitle=f"Status: [yellow]{model.get('status', 'n/a')}[/yellow]",
        )
    )

    # ------------------------------------------------------------
    # Description
    # ------------------------------------------------------------
    desc_table = Table(title="Description", show_header=False)
    for k, v in (model.get("description") or {}).items():
        desc_table.add_row(k.capitalize(), v or "-")
    print(desc_table)

    # ------------------------------------------------------------
    # Dataset
    # ------------------------------------------------------------
    dataset = model.get("dataset", {})
    dataset_table = Table(title="Dataset", header_style="bold magenta")
    dataset_table.add_column("Key")
    dataset_table.add_column("Value")

    dataset_table.add_row("Name", dataset.get("name", "-"))
    for k, v in (dataset.get("params") or {}).items():
        dataset_table.add_row(k, str(v))
    dataset_table.add_row("Accuracy", dataset.get("accuracy", "-"))
    dataset_table.add_row("Calibration", dataset.get("calibration", "-"))
    print(dataset_table)

    # ------------------------------------------------------------
    # Quality metric
    # ------------------------------------------------------------
    if qm := model.get("quality_metric"):
        print(Panel.fit(f"Quality Metric: [cyan]{qm.get('name')}[/cyan]"))

    # ------------------------------------------------------------
    # Quantization
    # ------------------------------------------------------------
    q = model.get("quantization_settings", {})
    q_table = Table(title="Quantization Settings", header_style="bold blue")
    q_table.add_column("Setting")
    q_table.add_column("Value")

    q_table.add_row("Calibration Samples", str(q.get("calibration_num_samples", "-")))
    q_table.add_row("Calibration Method", q.get("calibration_method", "-"))
    q_table.add_row("Requantization Mode", q.get("requantization_mode", "-"))
    q_table.add_row("Bias Correction", str(q.get("bias_correction", "-")))

    aq = q.get("activation_quantization_scheme", {})
    wq = q.get("weight_quantization_scheme", {})

    q_table.add_row(
        "Activation Quant",
        f"Asym={aq.get('asymmetric')} | PerCh={aq.get('per_channel')} | Bits={aq.get('bits')}",
    )
    q_table.add_row(
        "Weight Quant",
        f"Asym={wq.get('asymmetric')} | PerCh={wq.get('per_channel')} | Bits={wq.get('bits')}",
    )
    print(q_table)

    # ------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------
    transforms = pipeline.get("transforms", [])
    t_table = Table(title="Pipeline Transforms", header_style="bold green")
    t_table.add_column("Name")
    t_table.add_column("Params")

    for step in transforms:
        name = step.get("name", "-")
        params = step.get("params", {})
        param_str = ", ".join(f"{k}={v}" for k, v in params.items()) if params else "-"
        t_table.add_row(name, param_str)

    print(t_table)

def _describe_model_internal(ver: str, boardtype: str, model_name: str):
    repo = "sima-qa-releases"

    # ------------------------------------------------------------
    # 0. Resolve target prefix and base root
    # ------------------------------------------------------------
    target_prefix = "gen2_target" if boardtype == "modalix" else "gen1_target"
    base_root = (
        f"SiMaCLI-SDK-Releases/{ver}-Release/"
        f"modelzoo_edgematic/{target_prefix}/{model_name}"
    )

    click.echo(f"Model Zoo Source : SiMa Artifactory {base_root}: {model_name}...")

    # ------------------------------------------------------------
    # 1. Recursive search for matching YAML files
    # ------------------------------------------------------------
    aql_query = f"""
                items.find({{
                    "repo": "{repo}",
                    "path": "{base_root}",
                    "$or": [
                        {{ "name": {{ "$match": "*.yaml" }} }},
                        {{ "name": {{ "$match": "*.yml" }} }}
                    ],
                    "type": "file"
                }}).include("name", "path", "repo")
                """.strip()

    headers = {
        "Content-Type": "text/plain",
        "Authorization": f"Bearer {get_auth_token(internal=True)}",
    }

    session = requests.Session()
    session.trust_env = False
    response = session.post(
        f"{ARTIFACTORY_BASE_URL}/api/search/aql",
        data=aql_query,
        headers=headers,
    )

    if response.status_code != 200:
        click.echo(f"❌ Failed to list model YAML files: {response.status_code}")
        click.echo(response.text)
        return

    results = response.json().get("results", [])
    if not results:
        click.echo(
            f"❌ No YAML files found for model '{model_name}' "
            f"under target '{target_prefix}'."
        )
        return

    # ------------------------------------------------------------
    # 2. Build real model list: <category>/<model_name>
    # ------------------------------------------------------------
    model_map = {}
    base_prefix = base_root + "/"

    for item in results:
        path = item["path"]

        # Strip genX_target/
        rel = path.replace(base_prefix, "", 1)

        # Expected: <category>/<model_name>/...
        parts = rel.split("/")

        if len(parts) < 2:
            continue

        category = parts[0]
        model_dir = parts[1]
        model_key = f"{category}/{model_dir}"

        model_map.setdefault(model_key, []).append(item)

    model_keys = sorted(model_map.keys())

    # ------------------------------------------------------------
    # 3. Resolve model selection
    # ------------------------------------------------------------
    if len(model_keys) == 1:
        selected_model = model_keys[0]
    else:
        selected_model = inquirer.fuzzy(
            message=f"Multiple models found for '{model_name}', select one:",
            choices=model_keys + ["Exit"],
            max_height="70%",
            instruction="(Type to search, Enter to select)",
        ).execute()

        if selected_model == "Exit":
            click.echo("👋 Exiting.")
            return

    click.echo(f"✅ Selected model: {selected_model}")

    # ------------------------------------------------------------
    # 4. Select YAML file and render
    # ------------------------------------------------------------
    yaml_item = next(
        (f for f in model_map[selected_model]
         if f["name"].endswith((".yaml", ".yml"))),
        None,
    )

    if not yaml_item:
        click.echo("⚠️ No YAML file found for selected model.")
        return

    yaml_url = (
        f"{ARTIFACTORY_BASE_URL}/{repo}/"
        f"{yaml_item['path']}/{yaml_item['name']}"
    )

    session = requests.Session()
    session.trust_env = False  # Ignore .netrc and other env-based config
    r = session.get(
        yaml_url,
        headers={"Authorization": f"Bearer {get_auth_token(internal=True)}"},
    )

    if r.status_code != 200:
        click.echo(f"❌ Failed to fetch YAML: {r.status_code}")
        return

    describe_model_from_yaml_data(r.text)


def _download_model_internal(ver: str, boardtype: str, model_name: str):
    repo = "sima-qa-releases"

    # ------------------------------------------------------------
    # 0. Resolve target prefix and base root
    # ------------------------------------------------------------
    target_prefix = "gen2_target" if boardtype == "modalix" else "gen1_target"
    base_root = (
        f"SiMaCLI-SDK-Releases/{ver}-Release/"
        f"modelzoo_edgematic/{target_prefix}/{model_name}"
    )

    click.echo("Model Zoo Source : SiMa Artifactory...")

    # ------------------------------------------------------------
    # 1. Recursive search for matching files
    # ------------------------------------------------------------
    aql_query = f"""
                items.find({{
                "repo": "{repo}",
                "path": {{
                    "$match": "{base_root}*"
                }},
                "type": "file"
                }}).include("repo", "path", "name")
                """.strip()

    headers = {
        "Content-Type": "text/plain",
        "Authorization": f"Bearer {get_auth_token(internal=True)}",
    }

    session = requests.Session()
    session.trust_env = False
    response = session.post(
        f"{ARTIFACTORY_BASE_URL}/api/search/aql",
        data=aql_query,
        headers=headers,
    )

    if response.status_code != 200:
        click.echo(f"❌ Failed to list model files. Status: {response.status_code}")
        click.echo(response.text)
        return

    results = response.json().get("results", [])
    if not results:
        click.echo(
            f"❌ No files found for model '{model_name}' "
            f"under target '{target_prefix}'."
        )
        return

    # ------------------------------------------------------------
    # 2. Build real model list: <category>/<model_name>
    # ------------------------------------------------------------
    model_map = {}
    base_prefix = base_root + "/"

    for item in results:
        path = item["path"]

        # Strip genX_target prefix
        rel = path.replace(base_prefix, "", 1)

        # Expected: <category>/<model_name>/...
        parts = rel.split("/")

        if len(parts) < 2:
            continue  # safety guard

        category = parts[0]
        model_dir = parts[1]
        model_key = f"{category}/{model_dir}"

        model_map.setdefault(model_key, []).append(item)

    model_keys = sorted(model_map.keys())

    # ------------------------------------------------------------
    # 3. Resolve model selection
    # ------------------------------------------------------------
    if len(model_keys) == 1:
        selected_model = model_keys[0]
    else:
        selected_model = inquirer.fuzzy(
            message=f"Multiple models found for '{model_name}', select one:",
            choices=model_keys + ["Exit"],
            max_height="70%",
            instruction="(Type to search, Enter to select)",
        ).execute()

        if selected_model == "Exit":
            click.echo("👋 Exiting.")
            return

    click.echo(f"✅ Selected model: {selected_model}")

    # ------------------------------------------------------------
    # 4. Download files for selected model
    # ------------------------------------------------------------
    dest_dir = os.path.join(os.getcwd(), selected_model.split("/")[-1])
    os.makedirs(dest_dir, exist_ok=True)

    click.echo(
        f"⬇️  Downloading files for model '{selected_model}' "
        f"to '{dest_dir}'..."
    )

    for item in model_map[selected_model]:
        download_url = (
            f"{ARTIFACTORY_BASE_URL}/{repo}/"
            f"{item['path']}/{item['name']}"
        )

        try:
            local_path = download_file_from_url(
                download_url,
                dest_folder=dest_dir,
                internal=True,
            )
            click.echo(f"✅ {item['name']} -> {local_path}")
        except Exception as e:
            click.echo(f"❌ Failed to download {item['name']}: {e}")

    # ------------------------------------------------------------
    # 5. Handle model_path.txt (unchanged behavior)
    # ------------------------------------------------------------
    model_path_file = os.path.join(dest_dir, "model_path.txt")
    if os.path.exists(model_path_file):
        with open(model_path_file, "r") as f:
            first_line = f.readline().strip()

        if _is_valid_url(first_line):
            click.echo(
                f"\n🔍 model_path.txt contains external model link:\n{first_line}"
            )
            if click.confirm(
                "Do you want to download the FP32 ONNX model from this link?",
                default=True,
            ):
                try:
                    external_model_path = download_file_from_url(
                        first_line,
                        dest_folder=dest_dir,
                        internal=True,
                    )
                    click.echo(
                        f"✅ External model downloaded to: {external_model_path}"
                    )
                except Exception as e:
                    click.echo(f"❌ Failed to download external model: {e}")
        else:
            click.echo(
                "⚠️ model_path.txt exists but does not contain a valid URL."
            )


def _list_available_models_internal(version: str, boardtype: str):
    """
    Query Artifactory for available models for the given SDK version.
    Display them in an interactive menu with an 'Exit' option.
    Apply boardtype filtering:
      - gen1_target* → only shown for mlsoc
      - gen2_target* → only shown for modalix
      - others → always shown
    """
    repo_path = f"SiMaCLI-SDK-Releases/{version}-Release/modelzoo_edgematic"
    aql_query = f"""
        items.find({{
            "repo": "sima-qa-releases",
            "path": {{"$match": "{repo_path}/*"}},
            "type": "folder"
        }}).include("repo","path","name")
    """.strip()

    aql_url = f"{ARTIFACTORY_BASE_URL}/api/search/aql"
    headers = {
        "Content-Type": "text/plain",
        "Authorization": f"Bearer {get_auth_token(internal=True)}"
    }

    session = requests.Session()
    session.trust_env = False 
    response = session.post(aql_url, data=aql_query, headers=headers)
    if response.status_code != 200:
        click.echo(f"❌ Failed to retrieve model list (status {response.status_code})")
        click.echo(response.text)
        return None

    results = response.json().get("results", [])
    base_prefix = f"{repo_path}/"
    model_paths = []

    for item in results:
        # Full relative path like: gen2_target/anomaly_detection/fastflow_demo
        rel = item["path"].replace(base_prefix, "").strip("/")
        name = item["name"]

        full = f"{rel}/{name}" if rel else name
        parts = full.split("/")

        # Require: genX_target/<category>/<model>
        if len(parts) != 3:
            continue

        model_paths.append(full)

    model_paths = sorted(set(model_paths))

    if not model_paths:
        click.echo("No models found.")
        return None

    # Apply boardtype filtering
    filtered_models = []
    for model in model_paths:
        if model.startswith("gen1_target") and boardtype != "mlsoc":
            continue
        if model.startswith("gen2_target") and boardtype != "modalix":
            continue

        filtered_models.append(model.replace('gen1_target/', '').replace('gen2_target/', ''))

    if not filtered_models:
        click.echo(f"No models found for board type '{boardtype}'.")
        return None

    while True:
        # Add Exit option
        choices = filtered_models + ["Exit"]

        # Interactive selection with InquirerPy
        selected_model = inquirer.fuzzy(
            message=f"Select a model from version {version}, boardtype {boardtype}:",
            choices=choices,
            max_height="70%",
            instruction="(Use ↑↓ to navigate, / to search, Enter to select)"
        ).execute()

        if selected_model == "Exit":
            click.echo("👋 Exiting without selecting a model.")
            return None

        click.echo(f"✅ Selected model: {selected_model}")

        # Auto-describe
        _describe_model_internal(version, boardtype, selected_model)

        # Action menu loop
        while True:
            action = inquirer.select(
                message=f"What do you want to do with {selected_model}?",
                choices=["Download model", "Back", "Exit"],
                default="Download model",
                qmark="👉",
            ).execute()

            if action == "Download model":
                _download_model_internal(version, boardtype, selected_model)
            elif action == "Back":
                break  # back to model list
            else:  # Exit
                click.echo("👋 Exiting.")
                return None


def _list_available_models_external(version: str, boardtype: str):
    """
    List available models from the public HTTP model zoo.

    UX:
      - Single upfront fuzzy model selection
      - Category/path preserved in menu label (no alignment padding)
      - YAML downloaded and rendered immediately
      - Action menu: Download / Back / Exit
    """

    # ------------------------------------------------------------
    # 1. Determine gen & metadata file
    # ------------------------------------------------------------
    gen = "gen2" if boardtype == "modalix" else "gen1"
    metadata_name = f"metadata_{gen}.json"

    base_url = "https://docs.sima.ai/pkg_downloads"
    metadata_url = f"{base_url}/SDK{version}/model_zoo/{metadata_name}"

    # ------------------------------------------------------------
    # 2. Download and parse metadata
    # ------------------------------------------------------------
    try:
        with tempfile.TemporaryDirectory() as tmp:
            metadata_path = download_file_from_url(metadata_url, dest_folder=tmp)
            with open(metadata_path, "r") as f:
                metadata = _canonicalize_external_model_entries(json.load(f))
    except Exception as e:
        click.echo(f"❌ Failed to download metadata: {e}")
        return

    if not metadata:
        click.echo("No models found.")
        return

    # ------------------------------------------------------------
    # 3. Build display labels with category preserved (no padding)
    # ------------------------------------------------------------
    model_display_map = _build_model_display_map(metadata)

    choices = sorted(model_display_map.keys())

    # ------------------------------------------------------------
    # 4. Model selection loop
    # ------------------------------------------------------------
    while True:
        selected_label = inquirer.fuzzy(
            message=f"Select model (SDK {version}, {gen}):",
            choices=choices + ["Exit"],
            max_height="70%",
            instruction="(Type to search, Enter to select)",
        ).execute()

        if selected_label == "Exit":
            click.echo("👋 Exiting.")
            return

        model = model_display_map[selected_label]
        model_name = model.get("name")

        click.echo(f"✅ Selected model: {selected_label}")

        # --------------------------------------------------------
        # 5. Download YAML and describe model
        # --------------------------------------------------------
        yaml_url = f"{base_url}/SDK{version}/model_zoo/{model['yaml_file']}"

        try:
            with tempfile.TemporaryDirectory() as tmp:
                yaml_path = download_file_from_url(yaml_url, dest_folder=tmp)
                with open(yaml_path, "r") as f:
                    yaml_text = f.read()
        except Exception as e:
            click.echo(f"❌ Failed to load YAML: {e}")
            continue

        describe_model_from_yaml_data(yaml_text)

        # --------------------------------------------------------
        # 6. Action menu
        # --------------------------------------------------------
        action = inquirer.select(
            message=f"What do you want to do with {model_name}?",
            choices=["Download model assets", "Back", "Exit"],
            default="Download model assets",
            qmark="👉",
        ).execute()

        if action == "Exit":
            click.echo("👋 Exiting.")
            return

        if action == "Back":
            continue

        # --------------------------------------------------------
        # 7. Download model assets
        # --------------------------------------------------------
        for asset in model.get("assets", []):
            asset_url = f"{base_url}/SDK{version}/model_zoo/{asset}"
            download_file_from_url(asset_url)

        click.echo("✅ Download complete.")

def _download_model_external(version: str, boardtype: str, model_name: str):
    """
    Download model from public HTTP model zoo.

    Logic:
      1. Download metadata JSON and search by model name
      2. If multiple matches found, prompt user to select
      3. If single match:
         - download YAML
         - render YAML content
         - download assets immediately
    """

    # ------------------------------------------------------------
    # 1. Determine gen & metadata file
    # ------------------------------------------------------------
    gen = "gen2" if boardtype == "modalix" else "gen1"
    metadata_name = f"metadata_{gen}.json"

    base_url = "https://docs.sima.ai/pkg_downloads"
    metadata_url = f"{base_url}/SDK{version}/model_zoo/{metadata_name}"

    # ------------------------------------------------------------
    # 2. Download and parse metadata
    # ------------------------------------------------------------
    try:
        with tempfile.TemporaryDirectory() as tmp:
            metadata_path = download_file_from_url(metadata_url, dest_folder=tmp)
            with open(metadata_path, "r") as f:
                metadata = _canonicalize_external_model_entries(json.load(f))
    except Exception as e:
        click.echo(f"❌ Failed to download metadata: {e}")
        return

    if not metadata:
        click.echo("❌ No models found in metadata.")
        return

    # ------------------------------------------------------------
    # 3. Search by model name
    # ------------------------------------------------------------
    exact_matches = [m for m in metadata if m.get("name") == model_name]

    if exact_matches:
        matches = exact_matches
    else:
        # fallback: substring / fuzzy-friendly search
        matches = [
            m for m in metadata
            if model_name.lower() in m.get("name", "").lower()
        ]

    if not matches:
        click.echo(f"❌ No model found matching '{model_name}'.")
        return

    # ------------------------------------------------------------
    # 4. If multiple matches, prompt user
    # ------------------------------------------------------------
    if len(matches) > 1:
        display_map = _build_model_display_map(matches)

        selected_label = inquirer.fuzzy(
            message=f"Multiple models found for '{model_name}', select one:",
            choices=list(display_map.keys()) + ["Exit"],
            max_height="70%",
        ).execute()

        if selected_label == "Exit":
            click.echo("👋 Exiting.")
            return

        model = display_map[selected_label]

    else:
        model = matches[0]

    resolved_name = model.get("name")
    click.echo(f"✅ Resolved model: {resolved_name}")

    # ------------------------------------------------------------
    # 5. Download YAML and render content
    # ------------------------------------------------------------
    yaml_url = f"{base_url}/SDK{version}/model_zoo/{model['yaml_file']}"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = download_file_from_url(yaml_url, dest_folder=tmp)
            with open(yaml_path, "r") as f:
                yaml_text = f.read()
    except Exception as e:
        click.echo(f"❌ Failed to download YAML: {e}")
        return

    describe_model_from_yaml_data(yaml_text)

    # ------------------------------------------------------------
    # 6. Download model assets immediately
    # ------------------------------------------------------------
    assets = model.get("assets", [])
    if not assets:
        click.echo("⚠️ No assets listed for this model.")
        return

    for asset in assets:
        asset_url = f"{base_url}/SDK{version}/model_zoo/{asset}"
        download_file_from_url(asset_url)

    click.echo("✅ Model download complete.")

def _describe_model_external(version: str, boardtype: str, model_name: str):
    """
    Describe a model from the public HTTP model zoo using the shared
    describe_model_from_yaml_data renderer.

    Logic:
      1. Download metadata JSON and search by model name
      2. If multiple matches found, prompt user to select
      3. Download YAML and render content
    """

    # ------------------------------------------------------------
    # 1. Determine gen & metadata file
    # ------------------------------------------------------------
    gen = "gen2" if boardtype == "modalix" else "gen1"
    metadata_name = f"metadata_{gen}.json"

    base_url = "https://docs.sima.ai/pkg_downloads"
    metadata_url = f"{base_url}/SDK{version}/model_zoo/{metadata_name}"

    # ------------------------------------------------------------
    # 2. Download and parse metadata
    # ------------------------------------------------------------
    try:
        with tempfile.TemporaryDirectory() as tmp:
            metadata_path = download_file_from_url(metadata_url, dest_folder=tmp)
            with open(metadata_path, "r") as f:
                metadata = _canonicalize_external_model_entries(json.load(f))
    except Exception as e:
        click.echo(f"❌ Failed to download metadata: {e}")
        return

    if not metadata:
        click.echo("❌ No models found in metadata.")
        return

    # ------------------------------------------------------------
    # 3. Search by model name
    # ------------------------------------------------------------
    exact_matches = [m for m in metadata if m.get("name") == model_name]

    if exact_matches:
        matches = exact_matches
    else:
        matches = [
            m for m in metadata
            if model_name.lower() in m.get("name", "").lower()
        ]

    if not matches:
        click.echo(f"❌ No model found matching '{model_name}'.")
        return

    # ------------------------------------------------------------
    # 4. Resolve ambiguity if needed
    # ------------------------------------------------------------
    if len(matches) > 1:
        display_map = _build_model_display_map(matches)

        selected_label = inquirer.fuzzy(
            message=f"Multiple models found for '{model_name}', select one:",
            choices=list(display_map.keys()) + ["Exit"],
            max_height="70%",
        ).execute()

        if selected_label == "Exit":
            click.echo("👋 Exiting.")
            return

        model = display_map[selected_label]
    else:
        model = matches[0]

    resolved_name = model.get("name")
    click.echo(f"✅ Resolved model: {resolved_name}")

    # ------------------------------------------------------------
    # 5. Download YAML and render using shared renderer
    # ------------------------------------------------------------
    yaml_url = f"{base_url}/SDK{version}/model_zoo/{model['yaml_file']}"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = download_file_from_url(yaml_url, dest_folder=tmp)
            with open(yaml_path, "r") as f:
                yaml_text = f.read()
    except Exception as e:
        click.echo(f"❌ Failed to download YAML: {e}")
        return

    describe_model_from_yaml_data(yaml_text)

def list_models(internal, ver, boardtype):
    if internal:
        click.echo("Model Zoo Source : SiMa Artifactory...")
        return _list_available_models_internal(ver, boardtype)
    else:
        return _list_available_models_external(ver, boardtype)

def download_model(internal, ver, boardtype, model_name):
    if internal:
        click.echo("Model Zoo Source : SiMa Artifactory...")
        return _download_model_internal(ver, boardtype, model_name)
    else:
        return _download_model_external(ver, boardtype, model_name)

def describe_model(internal, ver, boardtype, model_name):
    if internal:
        click.echo("Model Zoo Source : SiMa Artifactory...")
        return _describe_model_internal(ver, boardtype, model_name)
    else:
        return _describe_model_external(ver, boardtype, model_name)

# Module CLI tests
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python models.py <version>")
    else:
        version_arg = sys.argv[1]
        boardtype = sys.argv[2]
        _list_available_models_internal(version_arg, boardtype)
