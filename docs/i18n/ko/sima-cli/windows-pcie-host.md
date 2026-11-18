# Windows에 Neat PCIe Host 설치

Windows PCIe 호스트 릴리스는 `sima-cli` 명령 하나로 설치할 수 있습니다.
릴리스 매니페스트에는 서로 호환되는 Scheduler 및 Core MSI 패키지,
설치 순서, 버전, 체크섬이 기록됩니다. `sima-cli`는 전체 패키지를 다운로드하고
검증한 다음 각 네이티브 패키지를 Windows Installer에 전달합니다.

지원되는 Platform PCIe 패키지와 x64 GStreamer MSVC 런타임을 설치한 뒤
PowerShell에서 명령을 실행하세요.
컴퓨터 전체에 설치되는 Scheduler 및 Core MSI 패키지에 대해 표준 사용자는 Windows
관리자 승인 프롬프트를 한 번만 승인합니다. 권한이 상승된 하나의 단계에서 Scheduler를
설치한 뒤 Core를 설치하며, Scheduler가 실패하면 Core를 시작하지 않습니다. 권한이
상승하는 것은 이 단계뿐입니다. CLI,
관리형 Python 환경 및 고객 스킬은 현재 사용자의 세션과 프로필에 그대로 유지됩니다.
표준 사용자는 Windows가 승인 프롬프트를 표시할 수 있도록 대화형 PowerShell 창에서
명령을 실행해야 합니다.

```powershell
sima-cli neat install core/pciehost/windows/amd64@v<Neat version>
```

MSI를 실행하기 전에 `sima-cli`는 Windows OpenSSH 도구, 실행 중인 Platform 서비스,
GStreamer, 호환되는 Python 버전과 아키텍처, pip를 확인합니다. 필수 조건이
충족되지 않으면 네이티브 설치 상태를 변경하지 않고 중단합니다. 기본적으로
`%USERPROFILE%\pyneatpcie`를 만들거나 재사용하고, 해당 `pyneatpcie` wheel을
설치한 다음 버전과 가져오기를 확인합니다. 다른 애플리케이션 환경을 선택하려면
`--python-executable <path>`, C++만 설치하려면 `--native-only`를 사용하세요.

Windows Installer가 완료되면 CLI는 설치된 호스트 진단 도구를 실행하고,
패키지에 맞는 `neat-pcie-application-builder` 스킬을 현재 사용자의 Codex 및
Claude 스킬 디렉터리에 동기화합니다. 두 MSI 중 하나를 실행하기 전에 CLI는 해당
스킬 ID가 사용되지 않았거나 같은 설치 패키지 경로로 이미 등록되어 있는지도
확인합니다. 다른 소스의 스킬이나 같은 ID를 사용하는 등록되지 않은 스킬 디렉터리는
변경하지 않고 설치를 중단합니다. 패키지를 다시 설치할 때는 같은 패키지 경로로
등록된 스킬만 교체할 수 있습니다.

같은 명령을 다시 실행하면 같은 검사를 수행하고 각 MSI를 같은 순서로
Windows Installer에 다시 전달합니다. Python 패키지는 이 릴리스의 wheel로
설치되었고 여전히 import할 수 있을 때만 그대로 둡니다. 그렇지 않으면 `sima-cli`가
wheel을 다시 설치합니다. 네이티브 패키지나
pip 단계가 실패하면 표시된 문제를 해결하고 같은 명령을 다시 실행하세요.
설치 프로그램은 실패 전에 설치가 완료된 네이티브 패키지를 알려줍니다.
Windows Installer 오류 `1625`는 호스트의 애플리케이션 제어 정책이 패키지를
거부했다는 뜻입니다. 첫 릴리스의 Neat MSI 패키지, DLL, Python 확장 모듈은
코드 서명되지 않았습니다. `sima-cli`는 릴리스 채널에서 HTTPS로 이를 다운로드하고,
각 패키지의 SHA-256 체크섬을 다운로드 후와 Windows Installer 또는 pip를 시작하기 전에
다시 검증하며(표준 사용자의 경우 승인 프롬프트 전과 권한이 상승된 단계 안에서 모두
검증합니다), 더 이상 일치하지 않는 패키지가 있으면 중단합니다. 기업의 애플리케이션 제어 정책이 서명되지
않은 바이너리를 차단할 수 있습니다. Windows 관리자에게 정책 검토를 요청하세요.
`sima-cli`는 Windows 보안 제어를 비활성화하지 않습니다.
Windows Installer를 시작할 수 없으면 `sima-cli`는 시도한 패키지, 권한 안내,
예정된 설치 로그 위치를 표시합니다.

`sima-cli`는 릴리스를 설치할 때 각 MSI의 제품 코드를 레지스트리 기록에 저장합니다.
하나의 명령이 이 코드를 사용해 한 번의 관리자 승인 프롬프트로, 권한이 상승된 하나의
단계에서 Core를 제거한 뒤 Scheduler를 제거하고, 이후 기록을 삭제합니다.

```powershell
sima-cli packages list
sima-cli packages remove gh:sima-neat/core/pciehost/windows/amd64 --version INSTALLED_VERSION --yes
```

`INSTALLED_VERSION`을 `packages list`에 표시된 정확한 값으로 바꾸세요. 이미 없는
패키지는 제거된 것으로 간주합니다. Windows Installer가 실패하면 Core가 남아 있는 동안
Scheduler는 제거하지 않고, 기록은 `remove-failed` 상태로 유지되므로 같은 명령을 다시
실행할 수 있습니다. 종료 코드 `3010`은 두 패키지가 모두 제거되었으며 완료하려면
Windows를 다시 시작해야 한다는 뜻입니다.

이 명령은 현재 사용자의 Python 패키지, 관리형 Python 환경 또는 동기화된 고객 스킬을
삭제하지 않으며, 이를 제거하는 명령을 출력합니다.

```powershell
& "$env:USERPROFILE\pyneatpcie\Scripts\python.exe" -m pip uninstall -y pyneatpcie
sima-cli playbooks remove neat-pcie-application-builder --yes
```
