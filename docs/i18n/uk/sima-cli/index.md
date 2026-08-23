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
| [ Налаштування мережевого підключення Neat SDK ](sdk-networking/index.md) | Зрозумійте, як налаштовано мережеве підключення SDK, Docker, Insight та DevKit. |
| [Усунення несправностей мережевого підключення SDK](sdk-networking/troubleshooting.md) | Запустіть мережеву діагностику, відновіть роботу спільної мережі та маршрутизації Linux, а також зберіть пакети даних для технічної підтримки. |
| [Відмініть зміни мережевих налаштувань SDK](sdk-networking/rollback.md) | Перегляньте та скасуйте зміни, внесені під час налаштування або відновлення мережевих параметрів SDK Linux. |

## Команди вищого рівня.

| Команда | Опис. |
| --- | --- |
| [`sima-cli appzoo`](../../../sima-cli/commands/sima-cli-appzoo.md) | Отримайте доступ до прикладів програм із App Zoo. |
| [`sima-cli bootimg`](../../../sima-cli/commands/sima-cli-bootimg.md) | Підготуйте завантажувальний образ для SiMa DevKit. |
| [`sima-cli device`](../../../sima-cli/commands/sima-cli-device.md) | Знайдіть пристрої, розташовані поблизу, в локальній мережі SiMa.ai. |
| [`sima-cli download`](../../../sima-cli/commands/sima-cli-download.md) | Завантажте файл або цілу папку за вказаним URL-адресою. |
| [`sima-cli install`](../../../sima-cli/commands/sima-cli-install.md) | Встановіть пакети SiMa. |
| [`sima-cli login`](../../../sima-cli/commands/sima-cli-login.md) | Авторизуйтеся на порталі для розробників SiMa. |
| [`sima-cli logout`](../../../sima-cli/commands/sima-cli-logout.md) | Вийдіть зі системи, видаливши збережені облікові дані та файли конфігурації. |
| [`sima-cli mla`](../../../sima-cli/commands/sima-cli-mla.md) | Утиліти для прискорення машинного навчання. |
| [`sima-cli modelzoo`](../../../sima-cli/commands/sima-cli-modelzoo.md) | Отримайте доступ до моделей із Model Zoo. |
| [`sima-cli neat`](../../../sima-cli/commands/sima-cli-neat.md) | Знайдіть, завантажте та встановіть артефакти збірки Neat. |
| [`sima-cli network`](../../../sima-cli/commands/sima-cli-network.md) | Налаштуйте мережеву IP-адресу на DevKit. |
| [`sima-cli nvme`](../../../sima-cli/commands/sima-cli-nvme.md) | Виконуйте операції NVMe на Modalix DevKit. |
| [`sima-cli packages`](../../../sima-cli/commands/sima-cli-packages.md) | Керуйте реєстром пакетів sima-cli (переглядайте список, здійснюйте перевірку, очищайте тощо). |
| [`sima-cli playbooks`](../../../sima-cli/commands/sima-cli-playbooks.md) | Встановіть і керуйте сценаріями (Codex/Claude). |
| [`sima-cli sdcard`](../../../sima-cli/commands/sima-cli-sdcard.md) | Підготуйте SD-карту як пристрій для зберігання даних для ранньої версії MLSoc DevKit або Modalix. |
| [`sima-cli sdk`](../../../sima-cli/commands/sima-cli-sdk.md) | Керуйте та розгортайте контейнерні середовища SiMa SDK 2.0 (бета-версія). |
| [`sima-cli selfupdate`](../../../sima-cli/commands/sima-cli-selfupdate.md) | Оновіть sima-cli вручну, завантаживши його з PyPI або за допомогою прямого посилання на файл. |
| [`sima-cli serial`](../../../sima-cli/commands/sima-cli-serial.md) | Підключіться до послідовного інтерфейсу UART на платі розробника DevKit. |
| [`sima-cli update`](../../../sima-cli/commands/sima-cli-update.md) | Оновіть програмне забезпечення на пристрої SiMa DevKit або на віддаленому пристрої SiMa. |

## Повний перелік команд.

- [`sima-cli`](../../../sima-cli/commands/sima-cli.md)
- [`sima-cli appzoo`](../../../sima-cli/commands/sima-cli-appzoo.md)
- [`sima-cli bootimg`](../../../sima-cli/commands/sima-cli-bootimg.md)
- [`sima-cli device`](../../../sima-cli/commands/sima-cli-device.md)
- [`sima-cli download`](../../../sima-cli/commands/sima-cli-download.md)
- [`sima-cli install`](../../../sima-cli/commands/sima-cli-install.md)
- [`sima-cli login`](../../../sima-cli/commands/sima-cli-login.md)
- [`sima-cli logout`](../../../sima-cli/commands/sima-cli-logout.md)
- [`sima-cli mla`](../../../sima-cli/commands/sima-cli-mla.md)
- [`sima-cli modelzoo`](../../../sima-cli/commands/sima-cli-modelzoo.md)
- [`sima-cli neat`](../../../sima-cli/commands/sima-cli-neat.md)
- [`sima-cli network`](../../../sima-cli/commands/sima-cli-network.md)
- [`sima-cli nvme`](../../../sima-cli/commands/sima-cli-nvme.md)
- [`sima-cli packages`](../../../sima-cli/commands/sima-cli-packages.md)
- [`sima-cli playbooks`](../../../sima-cli/commands/sima-cli-playbooks.md)
- [`sima-cli sdcard`](../../../sima-cli/commands/sima-cli-sdcard.md)
- [`sima-cli sdk`](../../../sima-cli/commands/sima-cli-sdk.md)
- [`sima-cli selfupdate`](../../../sima-cli/commands/sima-cli-selfupdate.md)
- [`sima-cli serial`](../../../sima-cli/commands/sima-cli-serial.md)
- [`sima-cli update`](../../../sima-cli/commands/sima-cli-update.md)
- [`sima-cli appzoo clone`](../../../sima-cli/commands/sima-cli-appzoo-clone.md)
- [`sima-cli appzoo describe`](../../../sima-cli/commands/sima-cli-appzoo-describe.md)
- [`sima-cli appzoo get`](../../../sima-cli/commands/sima-cli-appzoo-get.md)
- [`sima-cli appzoo list`](../../../sima-cli/commands/sima-cli-appzoo-list.md)
- [`sima-cli device discover`](../../../sima-cli/commands/sima-cli-device-discover.md)
- [`sima-cli mla meminfo`](../../../sima-cli/commands/sima-cli-mla-meminfo.md)
- [`sima-cli modelzoo describe`](../../../sima-cli/commands/sima-cli-modelzoo-describe.md)
- [`sima-cli modelzoo get`](../../../sima-cli/commands/sima-cli-modelzoo-get.md)
- [`sima-cli modelzoo list`](../../../sima-cli/commands/sima-cli-modelzoo-list.md)
- [`sima-cli neat artifacts`](../../../sima-cli/commands/sima-cli-neat-artifacts.md)
- [`sima-cli neat download`](../../../sima-cli/commands/sima-cli-neat-download.md)
- [`sima-cli neat install`](../../../sima-cli/commands/sima-cli-neat-install.md)
- [`sima-cli neat sdk`](../../../sima-cli/commands/sima-cli-neat-sdk.md)
- [`sima-cli packages build`](../../../sima-cli/commands/sima-cli-packages-build.md)
- [`sima-cli packages list`](../../../sima-cli/commands/sima-cli-packages-list.md)
- [`sima-cli packages show`](../../../sima-cli/commands/sima-cli-packages-show.md)
- [`sima-cli playbooks apply`](../../../sima-cli/commands/sima-cli-playbooks-apply.md)
- [`sima-cli playbooks delete`](../../../sima-cli/commands/sima-cli-playbooks-delete.md)
- [`sima-cli playbooks describe`](../../../sima-cli/commands/sima-cli-playbooks-describe.md)
- [`sima-cli playbooks install`](../../../sima-cli/commands/sima-cli-playbooks-install.md)
- [`sima-cli playbooks list`](../../../sima-cli/commands/sima-cli-playbooks-list.md)
- [`sima-cli playbooks remove`](../../../sima-cli/commands/sima-cli-playbooks-remove.md)
- [`sima-cli playbooks update`](../../../sima-cli/commands/sima-cli-playbooks-update.md)
- [`sima-cli sdk doctor`](../../../sima-cli/commands/sima-cli-sdk-doctor.md)
- [`sima-cli sdk elxr`](../../../sima-cli/commands/sima-cli-sdk-elxr.md)
- [`sima-cli sdk ls`](../../../sima-cli/commands/sima-cli-sdk-ls.md)
- [`sima-cli sdk model`](../../../sima-cli/commands/sima-cli-sdk-model.md)
- [`sima-cli sdk mpk`](../../../sima-cli/commands/sima-cli-sdk-mpk.md)
- [`sima-cli sdk neat`](../../../sima-cli/commands/sima-cli-sdk-neat.md)
- [`sima-cli sdk network`](../../../sima-cli/commands/sima-cli-sdk-network.md)
- [`sima-cli sdk remove`](../../../sima-cli/commands/sima-cli-sdk-remove.md)
- [`sima-cli sdk ros2`](../../../sima-cli/commands/sima-cli-sdk-ros2.md)
- [`sima-cli sdk run`](../../../sima-cli/commands/sima-cli-sdk-run.md)
- [`sima-cli sdk setup`](../../../sima-cli/commands/sima-cli-sdk-setup.md)
- [`sima-cli sdk start`](../../../sima-cli/commands/sima-cli-sdk-start.md)
- [`sima-cli sdk stop`](../../../sima-cli/commands/sima-cli-sdk-stop.md)
- [`sima-cli sdk yocto`](../../../sima-cli/commands/sima-cli-sdk-yocto.md)
- [`sima-cli sdk doctor network`](../../../sima-cli/commands/sima-cli-sdk-doctor-network.md)
- [`sima-cli sdk network repair`](../../../sima-cli/commands/sima-cli-sdk-network-repair.md)
- [`sima-cli sdk network rollback`](../../../sima-cli/commands/sima-cli-sdk-network-rollback.md)
