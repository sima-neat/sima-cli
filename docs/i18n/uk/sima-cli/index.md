# sima-cli Довідник з команд

Згенеровано довідкову документацію у форматі Markdown для інтерфейсу командного рядка sima-cli.

## Встановлення

Для більшості користувачів рекомендується встановити останню офіційну версію, завантаживши її за посиланням на публічний інсталятор, призначений для вашої операційної системи.

### Linux, macOS та DevKit.

Запустіть програму встановлення з командного рядка:

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/linux-mac.sh | bash
```

Після встановлення відкрийте нову консоль або перезавантажте профіль вашої оболонки, а потім перевірте, чи правильно встановлено програму:

```bash
sima-cli --version
```

### Windows PowerShell

Завантажте та запустіть інсталятор Windows з PowerShell:

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/windows.bat -OutFile windows.bat
.\windows.bat
```

Після встановлення відкрийте нове вікно командного рядка або PowerShell, а потім перевірте, чи правильно встановлено програму:

```powershell
sima-cli --version
```

### Розширені налаштування: оберіть гілку або версію.

Використовуйте `install.py` лише тоді, коли вам потрібно вибрати певну протестовану гілку або версію, а не встановлювати останню офіційну версію з PyPI.

Для Linux, macOS або DevKit:

```bash
curl -fsSL https://artifacts.neat.sima.ai/sima-cli/install.py -o sima-cli-install.py
python3 sima-cli-install.py
```

Встановіть певну гілку або версію:

```bash
python3 sima-cli-install.py feature/my-branch latest
python3 sima-cli-install.py v2.1.6 latest
```

У Windows за допомогою PowerShell:

```powershell
Invoke-WebRequest https://artifacts.neat.sima.ai/sima-cli/install.py -OutFile sima-cli-install.py
python .\sima-cli-install.py
```

Щоб встановити певну гілку або версію:

```powershell
python .\sima-cli-install.py feature/my-branch latest
python .\sima-cli-install.py v2.1.6 latest
```

Релізи, позначені тегами, наприклад, `v2.1.6`, встановлюються з загальнодоступного репозиторію PyPI. Встановлення з гілок передбачає використання протестованих артефактів з `artifacts.neat.sima.ai/sima-cli`.

Публічні релізи з PyPI також можна встановлювати безпосередньо:

```bash
pip install sima-cli
```

## Посібники

| Посібник | Опис. |
| --- | --- |
| [Операції з образами завантаження та мережевим завантаженням](guides/boot-image-and-netboot.md) | За допомогою `sima-cli bootimg` підготуйте локальні або завантажені образи, безпечно налаштуйте віддалене мережеве завантаження, відновіть доступ після зміни адреси та підготуйте eMMC до запису. |
| [Налаштування SDK та керування розширеннями](guides/sdk-setup.md) | Під час запуску `sima-cli sdk setup` виберіть додаткові служби SDK, розширення браузерного VS Code та джерело встановлення Model Compiler. |
| [Операції оновлення системи](guides/system-updates.md) | Використовуйте `sima-cli update` для щоденних збірок, підписаних пакетів SWU eLxr 3.0, перевірки A/B, підготовки сховища та обробки постійного накладеного шару. |
| [Neat SDK Налаштування мережі.](sdk-networking/index.md) | Neat SDK, також відомий як Neat Development Environment, працює всередині контейнера Docker. `sima-cli sdk setup` створює контейнер, готує точку монтування робочого простору хоста, запускає Insight, якщо його активовано, і публікує порти контейнера, які використовуються браузером хоста та DevKit під час розробки. |
| [Відмініть зміни мережевих налаштувань SDK.](sdk-networking/rollback.md) | Використовуйте функцію відкату, коли потрібно перевірити або скасувати зміни мережевих налаштувань хоста Linux, внесені під час налаштування SDK або відновлення мережі. |
| [Вирішення проблем із мережею SDK.](sdk-networking/troubleshooting.md) | Використовуйте мережевого лікаря, коли контейнер SDK, Insight інтерфейс користувача, DevKit SSH, RTSP, відео WebRTC або синхронізація робочого простору працюють не так, як очікувалося. |

## Команди вищого рівня.

| Команда | Опис. |
| --- | --- |
| [`sima-cli appzoo`](commands/sima-cli-appzoo.md) | Отримайте доступ до прикладів програм із App Zoo. |
| [`sima-cli bootimg`](commands/sima-cli-bootimg.md) | Підготуйте завантажувальний образ для SiMa DevKit. |
| [`sima-cli device`](commands/sima-cli-device.md) | Знайдіть пристрої, розташовані поблизу, в локальній мережі SiMa.ai. |
| [`sima-cli download`](commands/sima-cli-download.md) | Завантажте файл або цілу папку за вказаним URL-адресою. |
| [`sima-cli install`](commands/sima-cli-install.md) | Встановіть пакети SiMa. |
| [`sima-cli login`](commands/sima-cli-login.md) | Авторизуйтеся на порталі для розробників SiMa. |
| [`sima-cli logout`](commands/sima-cli-logout.md) | Вийдіть зі системи, видаливши збережені облікові дані та файли конфігурації. |
| [`sima-cli mla`](commands/sima-cli-mla.md) | Утиліти для прискорення машинного навчання. |
| [`sima-cli modelzoo`](commands/sima-cli-modelzoo.md) | Отримайте доступ до моделей із Model Zoo. |
| [`sima-cli neat`](commands/sima-cli-neat.md) | Знайдіть, завантажте та встановіть артефакти збірки Neat. |
| [`sima-cli network`](commands/sima-cli-network.md) | Налаштуйте мережеву IP-адресу на DevKit. |
| [`sima-cli nvme`](commands/sima-cli-nvme.md) | Виконуйте операції NVMe на Modalix DevKit. |
| [`sima-cli packages`](commands/sima-cli-packages.md) | Керуйте реєстром пакетів sima-cli (переглядайте список, здійснюйте перевірку, очищайте тощо). |
| [`sima-cli playbooks`](commands/sima-cli-playbooks.md) | Встановіть і керуйте сценаріями (Codex/Claude). |
| [`sima-cli sdcard`](commands/sima-cli-sdcard.md) | Підготуйте SD-карту як пристрій для зберігання даних для ранньої версії MLSoc DevKit або Modalix. |
| [`sima-cli sdk`](commands/sima-cli-sdk.md) | Керуйте та розгортайте контейнерні середовища SiMa SDK 2.0 (бета-версія). |
| [`sima-cli selfupdate`](commands/sima-cli-selfupdate.md) | Оновіть sima-cli вручну, завантаживши його з PyPI або за допомогою прямого посилання на файл. |
| [`sima-cli serial`](commands/sima-cli-serial.md) | Підключіться до послідовного інтерфейсу UART на платі розробника DevKit. |
| [`sima-cli update`](commands/sima-cli-update.md) | Оновлює SiMa DevKit, віддалений пристрій або хост Linux PCIe. |

## Повний перелік команд.

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
