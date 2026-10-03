# SDK 설정 및 확장 관리

`sima-cli sdk setup`을 실행할 때 선택적 SDK 서비스, 브라우저 VS Code 확장 및 Model Compiler 설치 원본을 선택합니다.

명령 참조: [`sima-cli sdk setup`](../commands/sima-cli-sdk-setup.md)

## Edgematic Studio 선택 설치

기본 설정에서는 Edgematic Studio 질문을 표시하거나 Studio를 설치하거나 포트를 공개하지 않습니다. `-y`와 `--noninteractive`에도 동일하게 적용됩니다.

`--edgematic-studio`로 Studio를 설치하고 포트를 공개합니다. 나중에 수동으로 설치하기 위해 포트만 공개하려면 `--edgematic-studio-port`를 사용합니다.

## 브라우저 VS Code 확장 선택

대화형 설정은 Neat, Codex 및 Claude 확장을 제공합니다. Space로 확장을 선택하고 Enter로 확인합니다. 초기에는 모두 선택 해제되어 있습니다. 아무것도 선택하지 않으면 설치를 건너뛰며, 선택하지 않은 기존 확장은 설치된 상태로 유지됩니다.

자동화 시에는 체크리스트 없이 세 확장을 모두 설치합니다:

```bash
sima-cli sdk setup --noninteractive --all-extensions
```

`--all-extensions`는 확장 선택만 건너뜁니다. 다른 질문도 건너뛰려면 `--noninteractive`를 사용합니다. 기존 SDK 컨테이너를 재사용할 때도 확장을 설치합니다. 이 옵션이 없으면 이전 `SIMA_CLI_INSTALL_CODEX_EXTENSION` 설정이 설치를 요청하지 않는 한 `-y`와 `--noninteractive`는 선택적 확장을 건너뜁니다. `--all-extensions`는 `--minimal`과 함께 사용할 수 없으며 Edgematic Studio를 활성화하거나 Model Compiler 선택을 변경하지 않습니다.

## 브라우저 VS Code 확장 버전

sima-cli는 정확한 Codex 및 Claude 버전을 설치하고 자동 업데이트를 막도록 고정합니다. 설정을 다시 실행하면 다른 버전을 교체하며, 일치하는 버전은 다시 다운로드하지 않고 유지하여 고정합니다. SDK 이미지는 `/etc/sima-neat/vscode-extensions.json`에 검증된 버전을 선언할 수 있습니다:

```json
{
  "schema_version": 1,
  "extensions": {
    "openai.chatgpt": "26.5825.51511",
    "anthropic.claude-code": "2.1.266"
  }
}
```

매니페스트 자체는 사용자를 설치에 참여시키지 않습니다. 스키마 버전 1은 두 ID 모두에 대해 정확한 버전을 요구합니다. 매니페스트가 없는 이전 이미지는 호환성 대체 버전으로 Codex `26.5825.51511` 및 Claude `2.1.266`을 사용합니다. 읽을 수 없거나 잘못된 매니페스트는 오류를 보고하고 확장 설치를 건너뛰지만 나머지 설정은 계속합니다. Codex 또는 Claude 설치 명령이 실패하면 동일한 정확한 버전과 정상 TLS 검증을 사용하여 최대 세 번 재시도합니다.

환경 변수 재정의가 우선합니다:

| 변수 | 동작 |
| --- | --- |
| `SIMA_CLI_CODEX_EXTENSION_ID` | `publisher.extension@exact-version`으로 Codex를 재정의합니다. 값이 비어 있으면 `--all-extensions`를 사용하지 않는 한 비활성화합니다. |
| `SIMA_CLI_CLAUDE_EXTENSION_ID` | `publisher.extension@exact-version`으로 Claude를 재정의합니다. 값이 비어 있으면 `--all-extensions`를 사용하지 않는 한 비활성화합니다. |
| `SIMA_CLI_INSTALL_CODEX_EXTENSION` | 체크리스트 없이 확장 설치를 자동 선택합니다. |

버전 없는 기본 제공 확장 ID는 SDK/기본 고정 버전을 사용합니다. 다른 ID에는 명시적인 버전이 필요합니다. `--minimal`은 선택적 확장 설치를 건너뜁니다. 설정은 설치된 버전을 검증하고 선택한 각 확장의 `metadata.pinned` 플래그를 기록한 다음 브라우저 VS Code를 다시 시작합니다. 이후 열려 있는 브라우저 탭을 새로 고칩니다.

## Model Compiler 설치 원본

설정은 SDK 컨테이너 아키텍처에 따라 현재 작업 디렉터리에서만 `model-compiler-arm64.zip` 또는 `model-compiler-amd64.zip`을 찾습니다. 압축을 푼 폴더, 상위 디렉터리, 워크스페이스 ZIP 및 다른 아키텍처는 검색하지 않습니다.

유효한 로컬 ZIP이 있으면 로컬, 온라인 또는 건너뛰기를 제공합니다. 유효한 ZIP이 없으면 온라인 또는 건너뛰기를 제공하며, Enter 기본값은 건너뛰기입니다.

| 플래그 | 로컬 ZIP 있음 | 유효한 로컬 ZIP 없음 |
| --- | --- | --- |
| `--noninteractive` | 로컬 설치 | 건너뛰기 |
| `--noninteractive -y` | 로컬 설치 | 온라인 설치 |
| `-y` | 로컬 설치 | 온라인 설치 |

온라인 무인 설치에는 `-y`가 필요합니다. 이전 SDK 원본의 경우 호스트에서 `sima-cli login`으로 인증합니다. `--minimal`, `--no-model-compiler` 및 `--no-model-sdk`는 항상 설치를 건너뜁니다.

공식 ZIP은 루트에 `install_modelsdk_wheels.sh`, `source.json`, `manifest.txt` 및 패키지 페이로드를 포함해야 하며, SDK에 선택된 컴파일러 버전과 일치해야 합니다. 아카이브를 호스트 임시 저장소에 압축 해제한 뒤 Linux 컨테이너 임시 저장소로 복사하므로 두 복사본을 위한 공간을 확보합니다. 성공 또는 실패 후 임시 데이터를 삭제하며 원본 ZIP은 유지합니다. 로컬 설치 실패 시 온라인으로 전환하지 않습니다. 시스템 패키지와 Python 필수 구성 요소에는 여전히 네트워크 접근이 필요할 수 있습니다.
