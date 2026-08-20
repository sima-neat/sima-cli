# sima-cli 명령어 참조

sima-cli 명령줄 인터페이스에 대한 마크다운 형식의 참고 문서를 생성했습니다.

## 설치 방법

대부분의 사용자는 해당 운영체제용 공개 설치 프로그램 URL에서 최신 공식 버전을 다운로드하여 설치하십시오.

### Linux, macOS, 그리고 DevKit

터미널에서 설치 프로그램을 실행합니다.

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/linux-mac.sh | bash
```

설치가 완료되면 새 터미널을 열거나 셸 프로필을 다시 로드한 다음, 설치가 제대로 되었는지 확인합니다.

```bash
sima-cli --version
```

### Windows PowerShell

PowerShell에서 Windows 설치 파일을 다운로드하여 실행합니다.

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/windows.bat -OutFile windows.bat
.\windows.bat
```

설치가 완료되면 새 명령 프롬프트 또는 PowerShell 창을 열고 설치를 확인합니다.

```powershell
sima-cli --version
```

### 고급: 브랜치 또는 릴리스를 선택하세요.

최신 공식 PyPI 릴리스를 설치하는 대신 특정 테스트 버전의 빌드 또는 릴리스를 선택해야 하는 경우에만 `install.py`를 사용하십시오.

Linux, macOS 또는 DevKit에서:

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/install.py -o sima-cli-install.py
python3 sima-cli-install.py
```

특정 브랜치 또는 버전을 설치합니다.

```bash
python3 sima-cli-install.py feature/my-branch latest
python3 sima-cli-install.py v2.1.6 latest
```

Windows의 PowerShell에서:

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/install.py -OutFile sima-cli-install.py
python .\sima-cli-install.py
```

특정 브랜치 또는 버전을 설치하려면:

```powershell
python .\sima-cli-install.py feature/my-branch latest
python .\sima-cli-install.py v2.1.6 latest
```

`v2.1.6`과 같은 릴리스 태그는 공개 PyPI에서 설치합니다. 브랜치 이름은 `artifacts.neat.sima.ai/sima-cli`에서 테스트된 아티팩트를 설치합니다.

공개 PyPI 릴리스도 직접 설치할 수 있습니다.

```bash
pip install sima-cli
```

## 안내

| 안내 | 설명 |
| --- | --- |
| [ Neat SDK 네트워킹 설정 ](sdk-networking/index.md) | SDK, Docker, Insight 및 DevKit의 네트워킹 구성 방법을 이해합니다. |
| [SDK 네트워킹 문제 해결](sdk-networking/troubleshooting.md) | 네트워크 진단을 실행하고, Linux 공유 네트워크 라우팅을 복구하고, 지원 관련 데이터를 수집합니다. |
| [SDK 네트워크 변경 사항 되돌리기](sdk-networking/rollback.md) | 미리 보기 기능을 사용하여 Linux SDK 네트워크 설정 또는 변경 사항을 적용하기 전에 확인하고, 필요에 따라 변경 사항을 취소할 수 있습니다. |

## 최상위 명령어

| 명령 | 설명 |
| --- | --- |
| [`sima-cli appzoo`](commands/sima-cli-appzoo.md) | App Zoo에서 샘플 앱에 접속하세요. |
| [`sima-cli bootimg`](commands/sima-cli-bootimg.md) | SiMa DevKit를 부팅할 수 있는 이미지를 준비합니다. |
| [`sima-cli device`](commands/sima-cli-device.md) | 로컬 네트워크에서 주변의 SiMa.ai 장치를 찾아보세요. |
| [`sima-cli download`](commands/sima-cli-download.md) | 지정된 URL에서 파일 또는 전체 폴더를 다운로드합니다. |
| [`sima-cli install`](commands/sima-cli-install.md) | SiMa 패키지를 설치합니다. |
| [`sima-cli login`](commands/sima-cli-login.md) | SiMa 개발자 포털을 통해 인증하세요. |
| [`sima-cli logout`](commands/sima-cli-logout.md) | 캐시된 자격 증명과 구성 파일을 삭제하여 로그아웃합니다. |
| [`sima-cli mla`](commands/sima-cli-mla.md) | 머신 러닝 가속기 유틸리티. |
| [`sima-cli modelzoo`](commands/sima-cli-modelzoo.md) | Model Zoo에서 모델에 접근하세요. |
| [`sima-cli neat`](commands/sima-cli-neat.md) | Neat 빌드 아티팩트를 찾아 다운로드하고 설치하세요. |
| [`sima-cli network`](commands/sima-cli-network.md) | DevKit에서 네트워크 IP 주소를 설정합니다. |
| [`sima-cli nvme`](commands/sima-cli-nvme.md) | NVMe 작업을 Modalix DevKit에서 수행합니다. |
| [`sima-cli packages`](commands/sima-cli-packages.md) | sima-cli 패키지 레지스트리를 관리합니다(목록 표시, 검사, 정리 등). |
| [`sima-cli playbooks`](commands/sima-cli-playbooks.md) | 플레이북을 설치하고 관리합니다(Codex/Claude). |
| [`sima-cli sdcard`](commands/sima-cli-sdcard.md) | MLSoc DevKit 또는 Modalix 얼리 액세스 장치의 데이터 저장 장치로 SD 카드를 준비합니다. |
| [`sima-cli sdk`](commands/sima-cli-sdk.md) | SiMa SDK 2.0 컨테이너 환경을 관리하고 배포합니다(베타 버전). |
| [`sima-cli selfupdate`](commands/sima-cli-selfupdate.md) | sima-cli를 PyPI 또는 직접적인 휠 URL에서 수동으로 업데이트하세요. |
| [`sima-cli serial`](commands/sima-cli-serial.md) | DevKit의 UART 직렬 콘솔에 연결합니다. |
| [`sima-cli update`](commands/sima-cli-update.md) | SiMa DevKit 또는 원격 SiMa 장치의 소프트웨어를 업데이트합니다. |

## 전체 명령어 목록

- [`sima-cli`](commands/sima-cli.md)
- [`sima-cli appzoo`](commands/sima-cli-appzoo.md)
- [`sima-cli bootimg`](commands/sima-cli-bootimg.md)
- [`sima-cli device`](commands/sima-cli-device.md)
- [`sima-cli download`](commands/sima-cli-download.md)
- [`sima-cli install`](commands/sima-cli-install.md)
- [`sima-cli login`](commands/sima-cli-login.md)
- [`sima-cli logout`](commands/sima-cli-logout.md)
- [`sima-cli mla`](commands/sima-cli-mla.md)
- [`sima-cli modelzoo`](commands/sima-cli-modelzoo.md)
- [`sima-cli neat`](commands/sima-cli-neat.md)
- [`sima-cli network`](commands/sima-cli-network.md)
- [`sima-cli nvme`](commands/sima-cli-nvme.md)
- [`sima-cli packages`](commands/sima-cli-packages.md)
- [`sima-cli playbooks`](commands/sima-cli-playbooks.md)
- [`sima-cli sdcard`](commands/sima-cli-sdcard.md)
- [`sima-cli sdk`](commands/sima-cli-sdk.md)
- [`sima-cli selfupdate`](commands/sima-cli-selfupdate.md)
- [`sima-cli serial`](commands/sima-cli-serial.md)
- [`sima-cli update`](commands/sima-cli-update.md)
- [`sima-cli appzoo clone`](commands/sima-cli-appzoo-clone.md)
- [`sima-cli appzoo describe`](commands/sima-cli-appzoo-describe.md)
- [`sima-cli appzoo get`](commands/sima-cli-appzoo-get.md)
- [`sima-cli appzoo list`](commands/sima-cli-appzoo-list.md)
- [`sima-cli device discover`](commands/sima-cli-device-discover.md)
- [`sima-cli mla meminfo`](commands/sima-cli-mla-meminfo.md)
- [`sima-cli modelzoo describe`](commands/sima-cli-modelzoo-describe.md)
- [`sima-cli modelzoo get`](commands/sima-cli-modelzoo-get.md)
- [`sima-cli modelzoo list`](commands/sima-cli-modelzoo-list.md)
- [`sima-cli neat artifacts`](commands/sima-cli-neat-artifacts.md)
- [`sima-cli neat download`](commands/sima-cli-neat-download.md)
- [`sima-cli neat install`](commands/sima-cli-neat-install.md)
- [`sima-cli neat sdk`](commands/sima-cli-neat-sdk.md)
- [`sima-cli packages build`](commands/sima-cli-packages-build.md)
- [`sima-cli packages list`](commands/sima-cli-packages-list.md)
- [`sima-cli packages show`](commands/sima-cli-packages-show.md)
- [`sima-cli playbooks apply`](commands/sima-cli-playbooks-apply.md)
- [`sima-cli playbooks delete`](commands/sima-cli-playbooks-delete.md)
- [`sima-cli playbooks describe`](commands/sima-cli-playbooks-describe.md)
- [`sima-cli playbooks install`](commands/sima-cli-playbooks-install.md)
- [`sima-cli playbooks list`](commands/sima-cli-playbooks-list.md)
- [`sima-cli playbooks remove`](commands/sima-cli-playbooks-remove.md)
- [`sima-cli playbooks update`](commands/sima-cli-playbooks-update.md)
- [`sima-cli sdk doctor`](commands/sima-cli-sdk-doctor.md)
- [`sima-cli sdk elxr`](commands/sima-cli-sdk-elxr.md)
- [`sima-cli sdk ls`](commands/sima-cli-sdk-ls.md)
- [`sima-cli sdk model`](commands/sima-cli-sdk-model.md)
- [`sima-cli sdk mpk`](commands/sima-cli-sdk-mpk.md)
- [`sima-cli sdk neat`](commands/sima-cli-sdk-neat.md)
- [`sima-cli sdk network`](commands/sima-cli-sdk-network.md)
- [`sima-cli sdk remove`](commands/sima-cli-sdk-remove.md)
- [`sima-cli sdk ros2`](commands/sima-cli-sdk-ros2.md)
- [`sima-cli sdk run`](commands/sima-cli-sdk-run.md)
- [`sima-cli sdk setup`](commands/sima-cli-sdk-setup.md)
- [`sima-cli sdk start`](commands/sima-cli-sdk-start.md)
- [`sima-cli sdk stop`](commands/sima-cli-sdk-stop.md)
- [`sima-cli sdk yocto`](commands/sima-cli-sdk-yocto.md)
- [`sima-cli sdk doctor network`](commands/sima-cli-sdk-doctor-network.md)
- [`sima-cli sdk network repair`](commands/sima-cli-sdk-network-repair.md)
- [`sima-cli sdk network rollback`](commands/sima-cli-sdk-network-rollback.md)
