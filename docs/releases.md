# Пакеты, релизы и обновления

Публичный проект: [Grek-Off/tailtray](https://github.com/Grek-Off/tailtray).
Релиз создаётся после push защищённого тега `vX.Y.Z`, совпадающего с версиями
в TOML, Python, RPM spec, Debian changelog и AppStream. Частичный релиз не
публикуется. Статус — [Release workflow](https://github.com/Grek-Off/tailtray/actions/workflows/release.yml),
готовые файлы — [Releases](https://github.com/Grek-Off/tailtray/releases).

Проверенный выпуск: [v0.1.6](https://github.com/Grek-Off/tailtray/releases/tag/v0.1.6),
коммит `3e310bb02a851a5f769672ea4e0571e48f8ba6d9`,
[production pipeline](https://github.com/Grek-Off/tailtray/actions/runs/38045667801):
23 jobs SUCCESS. Отдельная проверка без токена подтвердила 11 immutable assets,
21 подписанный payload истории v0.1.4/v0.1.5/v0.1.6 и точное совпадение source archive.

| Система | Пакеты | Проверка |
| --- | --- | --- |
| Fedora 43 | `.fc43.noarch.rpm`, `.fc43.src.rpm` | native build, установленный Qt/GTK runtime, DNF |
| Fedora 44 | `.fc44.noarch.rpm`, `.fc44.src.rpm` | native build, установленный Qt/GTK runtime, DNF |
| Debian 13 | `-1+debian13_all.deb` | native build, установленный Qt/GTK runtime, APT |
| Ubuntu 26.04 | `-1+ubuntu26.04_all.deb` | native build, установленный Qt/GTK runtime, APT |

Архитектура приложения `noarch`/`all`: Python без встроенных бинарных зависимостей.
CI проверяет x86_64; отдельного end-to-end arm64 прогона нет. Ubuntu 24.04 не
включена: её штатный архив не содержит нужных PySide6 QtWidgets/QtDBus.
Пакеты не включают Tailscale: сначала добавьте его официальный репозиторий.
Установка не переключает VPN, не назначает operator, не запускает daemon и
не создаёт автозапуск. Эти действия остаются за приложением и пользователем.

Дополнительно публикуются воспроизводимый `tailtray-X.Y.Z.tar.gz`, `SHA256SUMS`,
подпись `SHA256SUMS.asc`, публичный ключ и `tailtray-repository-X.Y.Z.tar.gz` —
подписанная история неизменяемых payloads. Архив исходников точно совпадает с
`git archive` релизного коммита, сжатым `gzip -n`. Все одиннадцать assets живут
в GitHub Release, а не только во временных workflow artifacts.

## Автотесты и публикация

На main/PR и в reusable CI релиза выполняются:

- Ruff, static-check, полный strict pytest: CLI, состояния/гонки,
  disconnect/connect, operator/login, профили, exit nodes, Qt/GTK и D-Bus, включая
  регрессию краша левого клика в 100 отдельных запусках.
- JUnit и coverage artifacts; coverage учитывает subprocesses. Это измерение,
  не обещание 100% проверенных сценариев.
- Установка каждого RPM/DEB в родном образе. Runtime-тесты импортируют
  `/usr/lib/tailtray`, а не checkout. Релизные tooling-тесты выполняются один раз.
- Репетиция подписи копий семи артефактов одноразовым ключом в tmpfs, реальные
  APT/RPM индексы, восстановление истории и четыре native потребителя.

В CI любой skip — ошибка. Deadline теста 60с, suite 180с, адресное пространство
2ГиБ. Pagination и загрузки ограничены; повтор страницы — ошибка, query-параметры
разбираются явно. Локальный запуск без native-зависимостей может иметь skips:

```fish
env PYTHONPATH=src .venv/bin/python -m pytest
```

Боевой release: read-only CI → защищённая подпись/draft → четыре проверки
настоящего подписанного репозитория → Pages → четыре анонимных HTTPS APT/DNF
потребителя → финальная проверка assets/истории → Release.
Ключ не доступен PR CI и не используется в репетиции. Actions использует
короткоживущий `github.token`, а не локальный PAT. Actions закреплены полными SHA;
публикации сериализованы. Готовые новые releases защищены GitHub immutability.

Сбой допускает повтор только с теми же исходными артефактами. Повтор проверяет
существующие assets и не перезаписывает их. При конфликте hash/size публикация
останавливается; перемещать старый тег или удалять assets для обхода нельзя.
Повторяйте failed jobs исходного run: пересборка уже подписанной версии может
дать отличающиеся native package bytes и закономерный конфликт.

## Репозиторий для DNF / APT

GitHub Releases сами по себе не являются APT/DNF-репозиторием. Pages раздаёт
статическое дерево подписанных индексов и пакетов:
`https://grek-off.github.io/tailtray`.
APT проверяет `InRelease`/`Release.gpg`; RPM проверяет пакеты и `repomd.xml`.
Токен, GitHub-аккаунт или вход в браузере потребителям не нужны.

Для новой установки, после успешного Release workflow:

```fish
bash scripts/install-repository.sh https://grek-off.github.io/tailtray
```

Скрипт проверяет ОС и pinned fingerprint, добавляет только TailTray и устанавливает
пакет. Отличающиеся конфигурации и ключи не перезаписывает. Fedora:
`gpgcheck=1`, `repo_gpgcheck=1`. APT: отдельный `Signed-By`, без `trusted=yes`.
Обновления после подключения приходят штатно:

```fish
sudo dnf upgrade --refresh       # Fedora
sudo apt-get update             # Debian / Ubuntu
sudo apt-get upgrade
```

Прежний `https://tailtray-657378.gitlab.io` сохранён. Уже установленные клиенты
не переключаются скрыто. Для добровольного перехода сохраните копию своего
TailTray repo-файла и отредактируйте только его через `sudoedit`:

- Fedora: `/etc/yum.repos.d/tailtray.repo`; в `baseurl` и `gpgkey` замените
  `https://tailtray-657378.gitlab.io` на `https://grek-off.github.io/tailtray`.
  Оставьте `/rpm/fedora/$releasever/`, `gpgcheck=1` и `repo_gpgcheck=1`.
- Debian/Ubuntu: `/etc/apt/sources.list.d/tailtray.sources`; замените только
  начало `URIs` на `https://grek-off.github.io/tailtray/apt`. Suite и отдельный
  `/usr/share/keyrings/tailtray-archive-keyring.asc` в `Signed-By` не меняйте.

Ключ и fingerprint одинаковы на обоих каналах. После редактирования обновите
метаданные; при ошибке верните сохранённый repo-файл. Не отключайте проверку
подписи. Автоматическое удаление старого канала не требуется.

## Подпись и следующий выпуск

Fingerprint: `D432326D1167240864F8345763FA04F99681DA62`.
Ключ: `packaging/repository-key.asc`. Приватный ключ не входит в Git, tar.gz,
пакеты или artifacts. Резервная копия — запись Secret Service
`application=tailtray purpose=release-signing project=86100130`, не plaintext-файл.
GitHub хранит тот же base64 ключ как environment secret `TAILTRAY_SIGNING_KEY`
в `release`, разрешённом только тегам `v*.*.*`; импорт — только в tmpfs.
Ключ действует два года; замена требует отдельной согласованной процедуры.

Обновите пять версий, changelog, проверьте и закоммитьте изменения. После CI:

```fish
git tag -a vX.Y.Z -m 'TailTray X.Y.Z'
git push origin vX.Y.Z
```

`origin` — GitHub; старый remote сохранён как `gitlab`. Локальный PAT остаётся
в Secret Service: [доступ и настройки](github-migration.md).
Main требует успешный all-jobs CI для PR; maintainer/admin может выполнять
обычные прямые изменения. Force-push и удаление main/релизных тегов запрещены.

## Сохранённый GitLab канал

Первый полностью проверенный публичный выпуск — GitLab `v0.1.5`, коммит
`34b965591aa4e9463dc6a7565ed1b6aad96d96df`,
[pipeline 2930998441](https://gitlab.com/grekov-labs/tailtray/-/pipelines/2930998441):
21 job, 707 JUnit checks, без ошибок/skips. GitLab Git/Generic Registry приватны,
Pages открыты. Старые конфигурации и релизы не удалены.

GitHub v0.1.5 содержит десять оригинальных файлов и подписанную историю четырнадцати
payloads v0.1.4/v0.1.5; перенос не пересобирал пакеты и не менял подписи.
Архив истории восстановлен и повторная публикация без изменений проверена.
Более ранние development tags сохранены, но не объявлены успешными releases.

Источники: [GitHub Releases API](https://docs.github.com/en/rest/releases/releases),
[Pages deployment](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[immutable releases](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes).
