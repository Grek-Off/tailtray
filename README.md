# TailTray

[![CI](https://github.com/Grek-Off/tailtray/actions/workflows/ci.yml/badge.svg)](https://github.com/Grek-Off/tailtray/actions/workflows/ci.yml)
[Releases](https://github.com/Grek-Off/tailtray/releases) · [Changelog](CHANGELOG.md)

An independent, unofficial Linux tray controller for the official Tailscale
daemon. Native KDE Plasma menus, a GTK/AppIndicator fallback, saved tailnet
switching, exit-node selection, device/IP lists and connection recovery —
without reimplementing the VPN or replacing `tailscaled`.

RPM packages target Fedora 43/44; DEB packages target Debian 13 and Ubuntu
26.04. See [signed packages and updates](docs/releases.md) for the currently
verified distribution channel. The public source and preserved tags are on
[GitHub](https://github.com/Grek-Off/tailtray); the
[migration checklist](docs/github-migration.md) records release verification.

Maintained by [Владимир Греков / Vladimir Grekov](https://github.com/Grek-Off),
with AI-assisted development by Codex/OpenAI. See [credits](CONTRIBUTORS.md),
[contribution guide](CONTRIBUTING.md), [security policy](SECURITY.md),
[code of conduct](CODE_OF_CONDUCT.md) and
the [MIT license](LICENSE). TailTray is not an official Tailscale product and
is not endorsed by Tailscale or OpenAI. Its independently drawn dot-grid icon
does not change that affiliation.

## Документация на русском

Неофициальный нативный tray-клиент для официального Tailscale daemon на
Linux. Первая целевая платформа — Fedora с GNOME или KDE.

TailTray не реализует VPN и не заменяет `tailscaled`: сетевой движок,
WireGuard-ключи, device identity и состояние узла остаются в официальном
системном сервисе. Приложение даёт ему компактный desktop-интерфейс.

## Что уже работает

- подключение и отключение без запуска терминала;
- состояние, имя устройства, Tailscale IP и проблемы health check;
- самоконтрастная dot-grid иконка в стиле Tailscale: чёрно-белые точки с
  противоположным контуром видны на светлой и тёмной панели; для KDE Plasma
  используется приватный набор RGBA PNG, который не перекрашивается темой;
- нативный Qt 6 tray/menu backend на KDE Plasma; GTK/AppIndicator остаётся
  fallback-ом для GNOME и других окружений;
- компактное верхнее меню только с основными действиями; аккаунты, настройки,
  авторизация, обслуживание и параметры приложения разложены по вложенным меню;
- список устройств текущего tailnet с online/offline состоянием и всеми
  Tailscale IP (клик копирует адрес);
- отдельное меню `Tailnet`: переключение сохранённых сетей/профилей и добавление новой;
- выбор, отключение и автоматический подбор exit node;
- DNS, subnet routes, LAN access через exit node, shields-up и update check;
- запуск `tailscaled` и выдача текущему пользователю operator-прав через
  стандартный polkit-диалог;
- проверка operator при запуске и после смены профиля; восстановление прав
  до автологина, без повторных запросов пароля после отмены;
- полный restart: `tailscale down` → restart systemd service → ожидание daemon →
  `tailscale up`;
- автоматический реконнект после неожиданного `Stopped` с ограниченным backoff;
  ручной **Disconnect** сохраняется как намерение и не отменяется автоматикой;
- состояние exit node различает активный, недоступный и неизвестный статус;
  health-предупреждение не выдаётся за доказанный отказ узла;
- автозапуск вместе с desktop-сессией;
- обычный однократный browser login;
- опциональный unattended login с auth key в GNOME Keyring/libsecret.

## Права управления и восстановление входа

На первом свежем статусе TailTray читает `operator` из `tailscale get --json all`.
Если указан другой пользователь или operator пуст, приложение запускает
`pkexec tailscale set --operator=<текущий Unix-пользователь>`: пароль запрашивает
системный polkit, TailTray его не получает и не хранит. После команды права
проверяются повторно, затем при `NeedsLogin` предлагается вход в браузере.
Если operator уже правильный, запрос пароля не появляется.

Отмена или ошибка не запускает новые диалоги на каждом опросе. В меню остаются
`Grant this user control…` и текст ошибки; повторить можно вручную, также через
`More → Authentication`. Там же есть `Sign in…`. Неизвестные права при таймауте
или неподдерживаемом `get` не считаются доказанным отсутствием operator.
Права управления не подменяют статус VPN: сеть может работать, даже если этот
пользователь пока не может менять настройки.

Восстановление входа в текущий профиль использует **bare `tailscale up`**, без
logout/reset и без изменения exit node/DNS. `tailscale login` используется
только при добавлении новой сети: эта команда создаёт пустой профиль.
Ручной Disconnect не отменяется ни проверкой operator, ни автологином.

## Переключение tailnet

В меню **Tailnet: …** выберите сохранённую сеть. Показываются название tailnet
и аккаунт, поэтому сети под одним логином различимы. Переключение вызывает
`tailscale switch PROFILE_ID` без logout, reset или повторного login. Списки
устройств, exit node и настройки обновляются из нового ответа daemon.

Если нужной сети ещё нет, выберите **Add tailnet…** и завершите официальный
вход в браузере, выбрав нужную tailnet. Сессия веб-консоли сама по себе не
добавляет профиль на компьютер. При добавлении клиент переключается на новый
вход; если передумали, можно вернуться к сохранённой сети через это же меню.
Одновременно активна только одна сеть. Повторная авторизация нужна, если
профиль ещё не добавлен или его ключ устройства истёк:
[официальный fast user switching](https://tailscale.com/docs/features/client/fast-user-switching).

Опциональный reusable auth key остаётся в keyring; в config хранится только
ID его профиля. Задавать ключ нужно после входа в нужную сеть. При смене tailnet
ключ другой сети автоматически не используется. Старый ключ без привязки
переносится автоматически только из однозначной однопрофильной конфигурации;
если профилей уже несколько, задайте ключ заново в нужной сети.

## Почему не порт официального Windows GUI

Официальных исходников Windows GUI нет. В
[`tailscale/tailscale`](https://github.com/tailscale/tailscale) открыты daemon,
CLI и большая часть общей логики, но Tailscale отдельно указывает, что GUI
wrappers для Windows и macOS закрыты. Поэтому TailTray опирается на открытые
контракты и поведение официального
[`client/systray`](https://github.com/tailscale/tailscale/blob/main/client/systray/systray.go),
а интерфейс реализует самостоятельно.

## Установка на Fedora

Сначала установите официальный Tailscale из его репозитория. Затем:

```bash
./scripts/install-fedora.sh
tailtray
```

Installer проверяет зависимости, останавливает только установленный экземпляр
TailTray, устанавливает файлы приложения, побайтно сверяет каждый Python-модуль
с checkout и запускает проверенную версию. Это также работает, если предыдущий
экземпляр уже успел упасть. Затем installer включает `tailscaled`, назначает
текущего пользователя operator-ом и добавляет TailTray в
`~/.config/autostart`. Он не выполняет Tailscale logout/reset и не меняет
настройки tailnet. Проверить, совпадает ли установленная версия с checkout,
можно без `sudo`:

```bash
./scripts/install-fedora.sh --check
```

Для ручной установки зависимостей:

```bash
sudo dnf install tailscale python3-gobject python3-pyside6 libappindicator-gtk3 libsecret libnotify
```

GNOME по умолчанию может не показывать StatusNotifier/AppIndicator icons. Если
иконки нет, установите и включите расширение:

```bash
sudo dnf install gnome-shell-extension-appindicator
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
```

В KDE Plasma дополнительное расширение обычно не требуется. TailTray передаёт
Plasma собственный каталог растровых tray-иконок, чтобы исключить подхват старой
symbolic-иконки из глобального кэша темы. На KDE автоматически выбирается
нативный Qt 6 backend: дерево меню и действия имеют постоянные идентификаторы,
а текст, доступность и команды обновляются на месте. Обновление не зависит от
события закрытия меню: после `down` тот же пункт становится **Connect**.
TailTray помечает StatusNotifierItem как menu-only, поэтому левый и
правый клик открывают одно и то же нативное меню Plasma без отдельного popup-окна.
Для диагностики backend можно явно выбрать через `TAILTRAY_BACKEND=qt` или
`TAILTRAY_BACKEND=gtk`.

Управление командами общее для обоих интерфейсов: операции выполняются
последовательно, а устаревшие ответы опроса после изменения состояния
отбрасываются. Disconnect во время подключения запоминается и выполняется
после текущей команды. Ошибка чтения дополнительных настроек не стирает
состояние соединения; свежие профили и настройки кэшируются на 30 секунд.

Обрыв связи с coordination server может оставить существующие туннели
работоспособными. TailTray показывает предупреждение и позволяет daemon
восстановить связь; автоматический `up` применяется к `Stopped`, а не к каждому
сетевому предупреждению. `NeedsMachineAuth` требует одобрения устройства,
а не повторной авторизации. Подробные переходы и границы проверки описаны в
[модели состояния](docs/connection-state.md).

Запуск прямо из checkout без установки:

```bash
make run
```

## Авторизация без бесконечного login loop

В нормальной Linux-установке авторизация не принадлежит tray-приложению.
Системный `tailscaled` хранит её в `/var/lib/tailscale/tailscaled.state` и
работает ещё до входа пользователя в desktop. Поэтому TailTray не вызывает
login при каждом старте: если daemon уже авторизован, приложение сразу
показывает текущее состояние.

Если daemon сообщает `NeedsLogin`, по умолчанию TailTray один раз открывает
официальный login URL. Для полностью автоматической повторной регистрации
можно выбрать **Set up automatic sign-in…** и сохранить reusable auth key.
Ключ хранится через Secret Service в пользовательском keyring, а при передаче
CLI используется временный файл с mode `0600`; ключ не попадает в JSON config
или process arguments.

Reusable auth key является bearer credential. Ограничивайте его tags,
pre-approval и сроком действия. Для обычного ноутбука лучше один browser login
и отключённый key expiration для конкретного устройства, чем вечный
широкопривилегированный ключ.

Если авторизация всё равно теряется после перезагрузки, запустите read-only
диагностику:

```bash
./scripts/diagnose.sh
```

Частые причины:

- `tailscaled` запускается вручную вместо systemd unit;
- задан `--state=mem:` или временный custom `--state`;
- `/var/lib/tailscale` очищается/не сохраняется;
- администратор tailnet включил истечение node key;
- локальному пользователю не назначен `--operator`.

Официальная документация: [Linux installation](https://tailscale.com/docs/install/linux),
[Linux operator permission](https://tailscale.com/docs/reference/troubleshooting/linux/linux-operator-permission),
[auth keys](https://tailscale.com/docs/features/access-control/auth-keys),
[daemon config file](https://tailscale.com/kb/1654/tailscaled-config-file).

## Пакеты и релизы GitLab

CI проверяет приложение (включая Qt/GTK и D-Bus под Xvfb), затем собирает
исходники, RPM для Fedora 43/44 и отдельные DEB для Debian 13 / Ubuntu 26.04. Пакеты имеют архитектуру
`noarch`/`all`; они не включают сам Tailscale. Перед установкой добавьте его
официальный репозиторий. Установка пакетов не переключает VPN, не назначает
operator и не запускает сервисы автоматически.

Задания используют GitLab shared runners; privileged mode и проброс Docker
socket не нужны. На каждом целевом дистрибутиве CI устанавливает пакет и
проверяет импорт Qt/GTK/D-Bus-зависимостей.

Для релиза обновите версию в `pyproject.toml`, `src/tailtray/__init__.py`, `packaging/tailtray.spec`,
`packaging/debian/changelog` и AppStream `data/io.github.tailtray.metainfo.xml`,
закоммитьте изменения и отправьте тег `vX.Y.Z`.
Тег обязан совпадать с версией проекта. Пайплайн создаёт GitLab Release и
прикрепляет RPM, DEB, архив `tailtray-X.Y.Z.tar.gz`, `SHA256SUMS` и подпись через Generic
Package Registry: ссылки не зависят от срока хранения временных CI artifacts.
Авторизация публикации — встроенный `CI_JOB_TOKEN`, без персонального токена
в CI variables. Ключ подписи — защищённая скрытая file-переменная; в Git хранится
только публичный ключ. Приватность файлов наследуется от GitLab-проекта.

Подписанные репозитории для обновлений через `dnf` / `apt` также собираются CI.
С разрешения владельца они опубликованы через GitLab Pages; проверенный релиз
`v0.1.5` доступен без GitLab-аккаунта. При переносе на GitHub этот адрес
сохраняется до проверки нового канала обновлений.
Установка, подписи, история версий и выпуск следующего релиза:
[docs/releases.md](docs/releases.md).

Локальная сборка из закоммиченного HEAD:

```fish
bash scripts/build-packages.sh source
bash scripts/build-packages.sh rpm  # Fedora: rpm-build, python3, git
bash scripts/build-packages.sh deb  # Debian: dpkg-dev, debhelper-compat, fakeroot, build-essential, python3, git
```

Здесь Bash запускается явно, поскольку сборочный скрипт написан на Bash.
Результаты находятся в `dist/`. Незафиксированные файлы не входят в архив.

## Проверка приложения

```bash
make check
```

Core-тесты не требуют работающего daemon или графической сессии. Полная ручная
проверка требует desktop session, запущенного `tailscaled` и доступа к его Unix
socket.

В графической сессии можно также запустить интеграционные автотесты:

```fish
env TAILTRAY_TEST_NATIVE_MENU=1 make check
```

Они проверяют настоящий Qt-экспорт `/MenuBar` через отдельную D-Bus-шину:
пять циклов Disconnect/Connect без события закрытия меню, а также обновление
GTK-виджетов. Команды daemon подставные: эти тесты не отключают рабочий VPN.
Требуются PySide6, GTK 3, `dbus-run-session` и `gdbus`.

Проект распространяется по лицензии MIT и не связан с Tailscale Inc.
Tailscale и WireGuard являются товарными знаками их владельцев.
