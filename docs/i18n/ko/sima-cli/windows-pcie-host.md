# Windows에 Neat PCIe Host 설치

Windows PCIe 호스트 릴리스는 `sima-cli` 명령 하나로 설치할 수 있습니다.
릴리스 매니페스트에는 서로 호환되는 Scheduler 및 Core MSI 패키지,
설치 순서, 버전, 체크섬이 기록됩니다. `sima-cli`는 전체 패키지를 다운로드하고
검증한 다음 각 네이티브 패키지를 Windows Installer에 전달합니다.

지원되는 Platform PCIe 패키지와 x64 GStreamer MSVC 런타임을 설치한 뒤
PowerShell에서 명령을 실행하세요. 서명된 드라이버, 호스트 라이브러리,
`SiMaMlaDaemon` 서비스는 Platform 패키지가 관리하며 Neat는 이를 교체하거나 업데이트하지 않습니다.
컴퓨터 전체에 설치되는 Scheduler 및 Core MSI 패키지에 대해 Windows가 관리자 승인을
요청할 수 있습니다. 권한이 상승하는 것은 이 네이티브 설치 프로그램뿐입니다. CLI,
관리형 Python 환경 및 고객 스킬은 현재 사용자의 세션과 프로필에 그대로 유지됩니다.
표준 사용자는 Windows가 승인 프롬프트를 표시할 수 있도록 대화형 PowerShell 창에서
명령을 실행해야 합니다.

```powershell
sima-cli neat install core/pciehost/windows/amd64@3.0
```

MSI를 실행하기 전에 `sima-cli`는 Windows OpenSSH 도구, 실행 중인 Platform 서비스,
GStreamer, 호환되는 Python 버전과 아키텍처, pip를 확인합니다. 필수 조건이
충족되지 않으면 네이티브 설치 상태를 변경하지 않고 중단합니다. 기본적으로
`%USERPROFILE%\pyneatpcie`를 만들거나 재사용하고, 해당 `pyneatpcie` wheel을
설치한 다음 버전과 가져오기를 확인합니다. 다른 애플리케이션 환경을 선택하려면
`--python-executable <path>`, C++만 설치하려면 `--native-only`를 사용하세요.

Windows Installer가 완료되면 CLI는 설치된 호스트 진단 도구를 실행하고,
패키지에 맞는 `neat-pcie-application-builder` 스킬을 현재 사용자의 Codex 및
Claude 스킬 디렉터리에 동기화합니다. 처음 설치할 때 카드에 암호 없이
SSH로 접속할 수 있도록 설정하세요.

```powershell
& "C:\Program Files\SimaPCIeHost\bin\pcie-setup.cmd"
```

설정 명령은 로컬 `10.0.N.1` 관리 링크에서 카드 주소를 검색합니다. 검색할 수 없으면
`-Hosts 10.0.0.2`를 사용하세요. 공개 키가 아직 등록되지 않은 경우에만 OpenSSH가
카드 암호를 요청하며, 명령은 암호를 저장하지 않습니다. 래퍼는 서명되지 않은
첫 릴리스 유틸리티가 Windows PowerShell의 기본 로컬 정책에서 실행되도록
설치된 스크립트 프로세스에만 `ExecutionPolicy Bypass`를 적용합니다.
사용자나 컴퓨터의 실행 정책을 변경하지 않으며, 관리자가 강제한 정책이 우선합니다.

같은 명령을 다시 실행하면 네이티브 패키지 세트를 복구하거나 업그레이드합니다.
일치하는 Python 패키지가 정상 작동하면 검증 후 그대로 둡니다. 네이티브 패키지나
pip 단계가 실패하면 표시된 문제를 해결하고 같은 명령을 다시 실행하세요.
설치 프로그램은 실패 전에 설치가 완료된 네이티브 패키지를 알려줍니다.
Windows Installer 오류 `1625`는 호스트의 애플리케이션 제어 정책이 패키지를
거부했다는 뜻입니다. 승인된 서명 릴리스를 사용하거나 Windows 관리자에게
정책 검토를 요청하세요. Windows 보안 제어를 비활성화하지 마세요.
Windows Installer를 시작할 수 없으면 `sima-cli`는 시도한 패키지, 권한 안내,
예정된 설치 로그 위치를 표시합니다.

Scheduler와 Core는 Windows의 설치된 앱 또는 `msiexec`로 제거합니다.
Python 패키지는 관리 환경에서 별도로 제거하세요.

```powershell
& "$env:USERPROFILE\pyneatpcie\Scripts\python.exe" -m pip uninstall pyneatpcie
```
