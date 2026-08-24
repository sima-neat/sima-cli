# SDK 네트워크 변경 사항 되돌리기

SDK 설정 또는 네트워크 복구를 통해 이루어진 Linux 호스트 네트워킹 변경 사항을 검사하거나 되돌리려면 롤백을 사용하세요.

롤백은 최선을 다하는 방식입니다. 롤백은 `sima-cli`가 감지된 DevKit/shared-network 경로에 대해 식별할 수 있는 범위 지정 규칙을 제거합니다. 관련 없는 호스트 네트워킹, VPN 구성, Docker 설치 상태 또는 사용자 관리 방화벽 규칙은 재설정하지 않습니다.

## 롤백 작업을 미리 보기

`--apply`가 제공되지 않는 한 롤백은 테스트 실행 모드에서 실행됩니다.

```bash
sima-cli sdk network rollback --devkit <devkit-ip>
```

변경 사항을 적용하기 전에 표를 검토하십시오.

## 변경 사항 되돌리기

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply
```

이 작업은 SDK 브리지와 DevKit 공유 네트워크 경로에 대해 `sima-cli`가 추가한 일치하는 런타임 포워딩/NAT 규칙을 제거합니다.

## 영구 프로필을 제거합니다.

지속적으로 사용되는 NetworkManager 디스패처 프로필이 설치되어 있는 경우, 제거하기 전에 롤백 메시지가 표시됩니다. NetworkManager가 공유 연결을 다시 생성할 때 프로필은 SDK 브리지 포워딩을 다시 적용합니다.

대화형으로 제거하려면 다음을 수행하십시오.

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply
```

비대화형 방식으로 제거하려면:

```bash
sima-cli sdk network rollback --devkit <devkit-ip> --apply --remove-persistent-profile
```

지속적인 프로필을 제거하는 것은 SDK 네트워크 복구를 완전히 되돌리려는 경우 안전합니다. 동일한 Ubuntu 공유 네트워크 DevKit 연결을 계속 사용하는 경우 나중에 복구를 다시 실행해야 할 수 있습니다.

## 롤백 후

현재 상태를 확인하기 위해 진단 프로그램을 실행합니다.

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

SDK 네트워크 구성을 처음부터 다시 만들려면 다음 명령을 실행하세요.

```bash
sima-cli sdk setup --devkit <devkit-ip>
```

지속적인 공유 네트워크 복구가 필요한 자동화를 위해서는 다음을 사용하십시오.

```bash
sima-cli sdk setup --devkit <devkit-ip> --persistent-network-profile -y
```
