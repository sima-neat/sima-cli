# SDK 네트워킹 문제 해결

SDK 컨테이너, Insight UI, DevKit SSH, RTSP, WebRTC 비디오 또는 작업 공간 동기화가 예상대로 작동하지 않을 때 네트워크 진단 도구를 사용하십시오.

진단 명령어는 읽기 전용입니다. 호스트 경로, Docker 컨테이너 상태, 게시된 포트, Insight 포트 맵, 방화벽 상태 및 선택된 Linux 네트워킹 세부 정보를 검사합니다.

## 빠르게 확인해 보세요.

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

둘 이상의 SDK 컨테이너가 있는 경우, 컨테이너 이름을 전달합니다.

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --container <container-name>
```

## 지원 자료를 모으세요.

SiMa 지원팀의 도움이 필요할 때, 다음 자료를 준비하세요:

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --collect
```

출력 위치를 선택하려면:

```bash
sima-cli sdk doctor network --devkit <devkit-ip> --collect --output ~/sima-sdk-network-doctor.tar.gz
```

이 패키지에는 정리된 호스트, Docker, 라우트, 방화벽, NetworkManager, 그리고 Insight 포트 맵 진단 정보가 포함되어 있습니다. 이 패키지는 네트워크 지원을 위해 제공됩니다. SSH 키, Docker 자격 증명 파일, 브라우저 쿠키 또는 기타 중요한 정보를 수동으로 아카이브에 추가하지 마십시오.

## 일반적으로 나타나는 결과

| 찾기 | 의미 | 권장 조치 |
| --- | --- | --- |
| `vpn-route` | DevKit에 접속하는 경로는 VPN 또는 터널 인터페이스를 거칩니다. | VPN 연결을 끊거나 라우팅 설정을 조정하여 DevKit이 물리적 DevKit 인터페이스를 사용하도록 합니다. |
| `missing-simasdkbridge` | SDK 컨테이너가 실행 중이지만 `simasdkbridge`에 연결되지 않았습니다. | `sima-cli sdk setup`을 사용하여 SDK를 다시 생성하거나 재시작합니다. DevKit 워크플로의 경우 VS Code 개발 컨테이너를 직접 시작하지 않도록 합니다. |
| `host-network-mode` | SDK 컨테이너는 Docker 호스트 네트워킹을 사용하여 시작되었습니다. | `sima-cli sdk setup`을 사용하여 SDK를 다시 생성하십시오. 호스트 네트워킹은 지원되는 Insight 포트 모델이 아닙니다. |
| `port-map-mismatch` | Docker에서 게시된 포트가 생성된 Insight 포트 맵과 일치하지 않습니다. | SDK 컨테이너를 다시 생성하여 `sima-cli`가 포트를 재생성하고 Insight 구성 설정을 함께 업데이트할 수 있도록 합니다. |
| `stale-port-bindings` | 중지된 SDK 컨테이너에는 더 이상 사용할 수 없는 Docker 포트 바인딩이 저장되어 있습니다. | `sima-cli sdk setup`을 사용하여 컨테이너를 삭제하거나 다시 생성합니다. |
| `nm-shared-iptables-blocking` | NetworkManager의 공유 네트워크 기능은 Docker 규칙이 적용되기 전에 SDK 브리지 트래픽을 차단합니다. | `sima-cli sdk network repair --devkit <devkit-ip>`를 실행합니다. 수정 사항이 재연결 또는 재부팅 후에도 유지되어야 하는 경우 `--persist`를 추가합니다. |
| `container-devkit-reachability` | SDK 컨테이너에서 DevKit에 대한 SSH 연결 또는 핑 연결 가능성을 확인할 수 없습니다. | DevKit의 IP 주소, 케이블/네트워크 경로, DevKit SSH 서비스, 그리고 호스트 방화벽 설정을 확인하십시오. |

## DevKit 종속성 다운로드 실패

DevKit이 권장되는 공유 네트워크 링크를 사용하는 경우, 인터넷 액세스는 호스트에 의존합니다. DevKit에서 패키지 설치, 종속성 다운로드 또는 외부 서비스 액세스가 실패하는 경우 다음을 확인하세요.

- 호스트 컴퓨터는 인터넷에 접속할 수 있습니다.
- 호스트의 공유 네트워크 인터페이스는 여전히 활성 상태입니다.
- VPN 라우팅이 DevKit 경로를 제대로 처리하지 못하고 있습니다.
- Linux NetworkManager가 공유 연결을 다시 생성한 경우 포워딩/NAT 규칙이 여전히 적용됩니다.

실행:

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

의사가 NetworkManager 공유 네트워크 차단 문제에 대해 보고하는 경우, 의사 보고서에 표시된 복구 명령을 실행합니다.

## Linux 공유 네트워크 라우팅 복구

Ubuntu/Linux 호스트에서 NetworkManager를 사용하여 네트워크를 공유하는 경우 다음 명령을 실행합니다.

```bash
sima-cli sdk network repair --devkit <devkit-ip>
```

런타임 복구를 적용한 후 영구 디스패처 후크를 설치하려면 다음을 수행하십시오.

```bash
sima-cli sdk network repair --devkit <devkit-ip> --persist
```

수정 작업은 SDK 브리지 및 감지된 DevKit와 연결된 공유 네트워크 경로에 한정됩니다. 이 작업은 Docker를 호스트 네트워킹으로 전환하거나 전역 `FORWARD` 정책을 `ACCEPT`로 설정하지 않습니다.

## 수리 후 확인하십시오.

의사 프로그램을 다시 실행하세요.

```bash
sima-cli sdk doctor network --devkit <devkit-ip>
```

그런 다음 다음에서 보고한 Insight URL을 엽니다.

```bash
neat --json
```

브라우저에서 Insight를 열 수 없는 경우, 호스트 브라우저에서 `mainUI` 호스트 포트에 접근할 수 있는지 확인하고, SDK 컨테이너가 `sima-cli sdk setup`에 의해 시작되었는지 확인하십시오.

## Insight 뷰어 URL 라우팅

Insight가 활성화된 SDK 컨테이너를 생성하거나 다시 생성할 때 SDK 설정은 호스트에 마운트된 `insight-config/url-routing` 파일에 `direct`를 기록합니다. 이 값은 `proxy`를 포함한 이전 라우팅 선언을 대체합니다. 명령줄 매개변수는 필요하지 않습니다. Insight는 로컬 실행 힌트를 공유 프록시 원본으로 처리하는 대신 브라우저 요청 호스트와 게시된 `videoUI` 포트를 사용하여 뷰어 URL을 구성합니다.

SDK 설정은 `launch-url` 또는 `browser-ice-address`를 변경하지 않습니다. CloudEx DevKit 페어링은 브라우저 URL 라우팅과 별개입니다. `--no-insight` 및 `--minimal`은 라우팅 선언을 기록하지 않습니다. 기존 컨테이너를 재사용할 때는 이 생성 시 초기화를 실행하지 않습니다.

호스팅된 ALB/공유 인그레스 프로비저닝은 **SDK 설정 후**에 라우팅 재정의를 적용해야 합니다. 공개 URL을 `launch-url`에, `proxy`를 `url-routing`에 기록한 다음 Insight를 다시 시작하고 `/api/health` 및 `/api/viewer-url`을 확인하십시오. SDK 컨테이너를 다시 생성하면 라우팅이 `direct`로 초기화되므로 호스팅 프로비저닝 단계는 매번 `proxy`를 다시 적용해야 합니다. 이 통합은 호스팅 팀이 담당합니다. CloudEx 전송과 ALB/nginx 규칙은 별도로 구성됩니다.

이전 로컬 컨테이너가 Insight 백엔드 포트의 뷰어 URL을 반환하여 404 오류가 발생하는 경우 `/home/docker/.insight-config/url-routing`에 `direct`를 기록하고 해당 컨테이너 안에서 `insight-admin restart`를 실행하십시오. 이 로컬 복구 방법을 호스팅된 ALB/프록시 환경에 적용하지 마십시오.
