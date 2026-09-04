# TailTray

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
- переключение сохранённых аккаунтов/профилей;
- выбор, отключение и автоматический подбор exit node;
- DNS, subnet routes, LAN access через exit node, shields-up и update check;
- запуск `tailscaled` и выдача текущему пользователю operator-прав через
  стандартный polkit-диалог;
- полный restart: `tailscale down` → restart systemd service → ожидание daemon →
  `tailscale up`;
- автозапуск вместе с desktop-сессией;
- обычный однократный browser login;
- опциональный unattended login с auth key в GNOME Keyring/libsecret.

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

Installer проверяет зависимости, устанавливает файлы приложения, включает
`tailscaled`, назначает текущего пользователя operator-ом и добавляет TailTray
в `~/.config/autostart`. Если установленный TailTray уже запущен, installer
аккуратно перезапускает его после копирования файлов, чтобы процесс не продолжал
исполнять старую версию из памяти. Он не выполняет Tailscale logout/reset и не
меняет настройки tailnet.

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
нативный Qt 6 backend: дерево меню строится заранее и не заменяется, пока меню
открыто. Для диагностики backend можно явно выбрать через
`TAILTRAY_BACKEND=qt` или `TAILTRAY_BACKEND=gtk`.

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

## Разработка и проверка

```bash
make check
```

Core-тесты не требуют работающего daemon или графической сессии. Полная ручная
проверка требует desktop session, запущенного `tailscaled` и доступа к его Unix
socket.

Проект распространяется по лицензии MIT и не связан с Tailscale Inc.
Tailscale и WireGuard являются товарными знаками их владельцев.
