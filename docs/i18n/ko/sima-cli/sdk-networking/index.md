# Neat SDK 네트워크 설정

Neat SDK는 Neat 개발 환경이라고도 하며, Docker 컨테이너 내에서 실행됩니다. `sima-cli sdk setup`은 컨테이너를 생성하고, 호스트 작업 공간 마운트를 준비하며, 활성화된 경우 Insight를 시작하고, 호스트 브라우저와 DevKit가 개발 중에 사용하는 컨테이너 포트를 게시합니다.

이 가이드에서는 Modalix DevKit와 함께 SDK를 사용할 때 가장 중요한 네트워크 구성 요소에 대해 설명합니다.

## 네트워크 모델

일반적인 SDK 설정에는 세 가지 요소가 있습니다.

- 호스트 컴퓨터: Docker와 `sima-cli`, SDK 컨테이너, 그리고 사용자의 브라우저를 실행합니다.
- SDK 컨테이너: Neat 개발 환경과 선택 사항인 Insight 서비스를 실행합니다.
- DevKit: 로컬 네트워크 또는 직접 이더넷/공유 네트워크 연결을 통해 호스트에 연결됩니다.

지원되는 SDK 컨테이너 네트워크는 `simasdkbridge`입니다. DevKit 및 Insight 네트워킹이 필요한 경우 Docker 또는 VS Code Dev Containers 확장 프로그램을 통해 SDK 컨테이너를 직접 시작하지 마십시오. 대신 `sima-cli sdk setup`을 사용하여 컨테이너, 포트 매핑, 작업 공간 공유 및 Insight 구성이 함께 생성되도록 합니다.

## 어떤 설정으로 구성되어 있습니까?

DevKit 통합을 사용하여 설정을 실행하면, `sima-cli`가 다음을 구성합니다.

- Docker 네트워크: `simasdkbridge`를 생성하거나 재사용합니다.
- SDK 컨테이너 포트: 호스트에 Insight UI, 비디오, RTSP, 메타데이터, WebRTC 및 웹 SSH 포트를 게시합니다.
- Insight 포트 매핑: 생성된 포트 매핑을 SDK 작업 공간 구성에 기록합니다.
- 작업 공간 공유: `--devkit`를 사용할 때 호스트에서 DevKit 작업 공간에 대한 접근 권한을 설정합니다.
- DevKit 인터넷 접속: DevKit를 호스트의 공유 네트워크 링크를 통해 연결하여, 필요할 때 DevKit가 패키지 저장소에 접근하고 종속성을 다운로드할 수 있도록 합니다.
- Linux 공유 네트워크 라우팅: Ubuntu의 Linux 공유 네트워크 링크에서 필요에 따라 범위가 지정된 전달/NAT 규칙을 적용합니다.

사용법:

```bash
sima-cli sdk setup --devkit <devkit-ip>
```

## 지속적인 공유 네트워크 복구(Linux)

일부 Ubuntu 호스트에서는 NetworkManager의 네트워크 공유 기능이 케이블이 다시 연결되거나 호스트가 재부팅될 때 방화벽 규칙을 다시 생성할 수 있습니다. 이 경우 설정이 현재 세션을 복구할 수 있지만, 복구가 영구적이지 않다는 경고가 표시될 수 있습니다.

대화형 설정을 위해 `sima-cli`는 영구적인 NetworkManager 디스패처 프로필을 설치하기 전에 사용자에게 확인합니다.

자동화를 위해 명시적으로 옵션을 선택하십시오.

```bash
sima-cli sdk setup --devkit <devkit-ip> --persistent-network-profile -y
```

지속적인 프로필은 감지된 공유 네트워크 경로에만 적용됩니다. 이 프로필은 `-y`만으로는 설치되지 않습니다.

## DevKit 인터넷 접속

DevKit이 권장되는 Linux/macOS 공유 네트워크 링크를 통해 연결되면 인터넷 액세스는 호스트 컴퓨터에 의존합니다. 이는 DevKit이 설정 및 개발 중에 패키지를 다운로드하거나, 종속성을 설치하거나, 외부 서비스에 연결해야 할 때 중요합니다.

종속성 다운로드를 시도하는 동안 DevKit 명령이 실패하면 호스트가 정상적으로 인터넷에 연결되어 있고 공유 네트워크 경로가 여전히 활성화되어 있는지 확인하십시오. Linux에서는 네트워크 진단 도구를 사용하여 공유 네트워크 전달 문제를 식별할 수 있습니다.

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

## Insight 및 공개된 포트

Insight는 생성된 호스트 포트를 사용합니다. 가능한 경우 기본값이 사용되지만, 이미 사용 중인 포트가 있는 경우 `sima-cli`가 기본값이 아닌 포트를 할당할 수 있습니다.

SDK 셸 내에서 활성 매핑을 확인하려면 다음을 수행하세요.

```bash
neat --json
```

`exposedPorts`와 `insight.webUiUrl` 항목을 찾으세요. DevKit 스트림, RTSP 소스, 브라우저 액세스 또는 애플리케이션 출력 대상으로 구성할 때 해당 값을 사용하세요.

## 관련 페이지

- [SDK 네트워킹 문제 해결](troubleshooting.md)
- [SDK 네트워크 변경 사항 롤백](rollback.md)
- [`sima-cli sdk setup`](../commands/sima-cli-sdk-setup.md)
- [`sima-cli sdk doctor network`](../commands/sima-cli-sdk-doctor-network.md)
- [`sima-cli sdk network repair`](../commands/sima-cli-sdk-network-repair.md)
- [`sima-cli sdk network rollback`](../commands/sima-cli-sdk-network-rollback.md)
