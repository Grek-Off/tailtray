# Пакеты, релизы и обновления

Релиз создаётся автоматически после push защищённого тега `vX.Y.Z`, совпадающего
с версией в `pyproject.toml`, RPM spec и Debian changelog. Все четыре нативные
сборки и тесты должны пройти: частичный релиз не публикуется.

| Система | Пакет | Проверка CI |
| --- | --- | --- |
| Fedora 43 | `.fc43.noarch.rpm` и `.fc43.src.rpm` | сборка и установка через dnf |
| Fedora 44 | `.fc44.noarch.rpm` и `.fc44.src.rpm` | сборка и установка через dnf |
| Debian 13 | `-1+debian13_all.deb` | сборка и установка через apt |
| Ubuntu 26.04 | `-1+ubuntu26.04_all.deb` | сборка и установка через apt |

Архитектура приложения `noarch` / `all`: Python-код без встроенных бинарных
зависимостей. Установка и импорт зависимостей в CI проверяются на x86_64;
самостоятельной end-to-end проверки на arm64 нет. Ubuntu 24.04 не включена:
в её штатном архиве нет необходимых PySide6 QtWidgets/QtDBus.

Дополнительно публикуются исходники `tailtray-X.Y.Z.tar.gz`, `SHA256SUMS`,
отдельная подпись `SHA256SUMS.asc` и публичный ключ. Файлы живут в Generic
Package Registry, а не только во временных CI artifacts. Для загрузок и Releases
API используется короткоживущий `CI_JOB_TOKEN`, персональный токен в CI не нужен.
Опубликованный файл с тем же именем и версией никогда не перезаписывается.

## Автотесты CI

На каждом branch/MR, до релизного тега:

- Ruff, статические проверки и полный pytest: CLI, гонки refresh/операций,
  disconnect/connect, operator/login, профили, exit node, предупреждения,
  Qt/GTK/KDE и 100 независимых D-Bus запусков на регрессию краша.
- JUnit и Cobertura доступны в GitLab; coverage включает Python subprocesses.
  Это измерение покрытия, не заявление о 100% проверенных сценариев.
- После сборки каждый RPM/DEB устанавливается в своём образе. Проверяются
  модули и иконки, затем runtime-тесты импортируют `/usr/lib/tailtray`, а не
  исходники checkout. Чисто релизные тесты не дублируются на каждом дистрибутиве.
- Репетиция релиза подписывает копии семи артефактов одноразовым тестовым ключом
  в tmpfs, строит настоящие APT/RPM индексы, проверяет восстановление истории,
  повторный запуск и отказ при повреждённой подписи. Затем четыре нативных
  потребителя проверяют эти индексы и пакеты. Ничего не публикуется.

Пропуск теста в CI — ошибка. У тестов deadline 60с, у suite — 180с и ограничение
адресного пространства 2ГиБ. Поддельные API имеют конечные лимиты запросов;
query-параметры разбираются явно, не через поиск `page=1` в строке. Проверка
лимита загрузки использует 64 байта вместо ненужного выделения 128МиБ.
Настоящий релиз ждёт успешной репетиции и четырёх проверок репозиториев.
Боевой ключ не используется в репетиции; ephemeral signing key не экспортируется
в артефакты. Проверки не управляют VPN на пользовательском компьютере.

Локальный запуск из checkout (нужны dev-зависимости и нативные Qt/GTK):

```fish
env PYTHONPATH=src .venv/bin/python -m pytest
```

Для полного native-покрытия нужен Xvfb, private D-Bus и KDE platform theme.
Без них локальные проверки могут иметь skips; CI это не разрешает.

## Репозиторий для dnf / apt

GitLab Generic Registry сам по себе не является apt/dnf-репозиторием. CI создаёт
подписанные индексы RPM и APT и сохраняет их в артефакте `public/`. GitLab Pages
может раздавать эту папку по постоянному HTTPS-адресу. Метаданные APT подписаны
через `InRelease` / `Release.gpg`; у RPM подписаны пакеты и `repomd.xml`.

Владелец разрешил публичную раздачу пакетов и исходных архивов. Git-проект и
Generic Registry остаются приватными; только дерево `public/` раздаётся через
Pages с доступом `Everyone`. Адрес, полученный из API Pages:
`https://tailtray-657378.gitlab.io`. Публикацию включает protected-переменная
`TAILTRAY_PUBLISH_REPOSITORY=1`, а protected `TAILTRAY_REPOSITORY_URL` задаёт
этот HTTPS-адрес для проверки после deployment. Токен GitLab в apt/dnf не нужен
и не должен попадать в `.repo`, URL или shell history.

Каждый защищённый release-тег проходит четыре проверки опубликованного HTTPS
репозитория: Fedora 43/44, Debian 13 и Ubuntu 26.04. Они анонимно скачивают
публичный ключ с pinned fingerprint и подписанный manifest текущей версии,
проверяют индексы штатным apt/dnf и скачивают пакет с проверкой подписи/checksum.
Эти проверки выполняются только в disposable CI images, не на рабочей машине.

Перед установкой добавьте [официальный репозиторий Tailscale](https://pkgs.tailscale.com/stable/).
Затем из проверенного checkout выполните (команда совместима с fish):

```fish
bash scripts/install-repository.sh https://tailtray-657378.gitlab.io
```

Скрипт проверяет дистрибутив и fingerprint публичного ключа, добавляет только
TailTray-репозиторий и устанавливает пакет. Существующую отличающуюся конфигурацию
или ключ не перезаписывает. На Fedora остаются включены `gpgcheck=1` и
`repo_gpgcheck=1`; APT использует отдельный `Signed-By`, без `trusted=yes`.
После подключения этого репозитория новые релизы приходят обычным способом:

```fish
sudo dnf upgrade --refresh       # Fedora
sudo apt-get update             # Debian / Ubuntu
sudo apt-get upgrade
```

Предыдущие пакеты сохраняются. CI восстанавливает историю из подписанного
manifest в Generic Registry и проверяет hash каждого файла; сетевые/permission
ошибки не трактуются как «первый релиз». Старый тег не может откатить Pages к
репозиторию без более свежего опубликованного релиза.

В Registry неизменяемые payloads хранятся под плоскими именами `payload-<sha256
пути>`: реальный API GitLab возвращает HTTP 500 для сочетания вложенного пути
и `+` в имени DEB. Подписанный manifest с `registry_layout=flat-v1` сохраняет
нативные пути apt/dnf, а восстановление истории проверяет подпись и hash
каждого файла. Старый manifest без этого поля читается в прежнем формате.

## Подпись и выпуск следующей версии

Fingerprint: `D432326D1167240864F8345763FA04F99681DA62`.
Публичный ключ: `packaging/repository-key.asc`. Приватный ключ не входит в Git,
tar.gz, пакеты или artifacts. Локальная резервная копия находится в Secret Service
с атрибутами `application=tailtray purpose=release-signing project=86100130`.
GitLab хранит base64 ключа в protected/masked/hidden **file** variable
`TAILTRAY_SIGNING_KEY`; fingerprint — в protected `TAILTRAY_SIGNING_FINGERPRINT`.
Расшифровка и импорт выполняются только в tmpfs release-job. Ключ имеет срок
действия два года; его замена должна быть отдельной согласованной процедурой.

Для первоначальной настройки другого проекта предусмотрен
`python3 scripts/setup-release-signing.py --project PROJECT_ID` на Linux desktop
с Secret Service и авторизованным glab. Команда не заменяет существующие CI
переменные, не печатает приватный ключ и не использует plaintext backup.
Публичный ключ и fingerprint другого проекта нужно явно обновить в исходниках
после проверки. Повторно выполнять setup в уже настроенном проекте не нужно.

Для следующего релиза обновите версии в TOML, `src/tailtray/__init__.py`, RPM spec, Debian changelog и
AppStream metainfo, проверьте изменения и отправьте:

```fish
git tag -a vX.Y.Z -m 'TailTray X.Y.Z'
git push origin vX.Y.Z
```

CI использует GitLab shared runners без privileged mode или Docker socket.
Установка пакета не делает logout/reset, не меняет operator/exit node, не
перезапускает daemon и не создаёт autostart. Управление этими действиями остаётся
за приложением и пользователем.

Документация: [Generic Packages](https://docs.gitlab.com/user/packages/generic_packages/),
[job token API](https://docs.gitlab.com/ci/jobs/ci_job_token/),
[Pages access control](https://docs.gitlab.com/user/project/pages/pages_access_control/).
