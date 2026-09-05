# Firefox Native Messaging POC — переключение вкладок

Минимальный, проверяемый POC канала связи:

```
test_host.py --(unix socket)--> nm_host.py --(Native Messaging, stdio)--> Firefox Extension --(browser.tabs)--> переключение вкладки
```

## Почему два python-файла в native-host/, а не один

Firefox сам порождает native-messaging-host процесс при вызове
`browser.runtime.connectNative()` и общается с ним **только** через
stdin/stdout этого процесса. Отдельный, независимо запущенный скрипт не
может напрямую писать в stdin уже работающего host-процесса — это разные
процессы без общего канала.

Поэтому роли разделены:

- **`nm_host.py`** — это и есть настоящий Native Messaging Host. Его
  запускает Firefox. Он говорит с Firefox строго по протоколу Native
  Messaging (4-байтовый префикс длины + UTF-8 JSON). Дополнительно он
  открывает локальный Unix-сокет `/tmp/nm_tab_switcher.sock`, чтобы можно
  было послать ему команду "снаружи" — это не замена Native Messaging, а
  просто мостик, без которого ручное тестирование невозможно в принципе.
- **`test_host.py`** — CLI-скрипт, который запускаешь руками. Он
  подключается к сокету `nm_host.py`, отправляет JSON-команду и печатает
  ответ, пришедший от расширения.

## Структура проекта

```
firefox-native-messaging-poc/
├── extension/
│   ├── manifest.json
│   └── background.js
├── native-host/
│   ├── nm_host.py               # спавнится Firefox'ом
│   ├── test_host.py             # запускаешь руками для теста
│   └── native-host-manifest.json
└── README.md
```

## Требования

- Fedora Linux, Firefox (родной пакет или Flatpak — см. примечание ниже).
- `python3` (в Fedora уже установлен), сторонние pip-пакеты не нужны —
  используется только стандартная библиотека.

## Установка

Все команды — от обычного пользователя, root не нужен.

### 1. Сделать host-скрипт исполняемым

```bash
cd firefox-native-messaging-poc
chmod +x native-host/nm_host.py
```

### 2. Зарегистрировать Native Messaging Host

Firefox на Linux ищет манифесты хостов в
`~/.mozilla/native-messaging-hosts/<name>.json`, где `<name>` совпадает с
полем `"name"` внутри манифеста (`com.local.native_tab_switcher`).

Подставляем абсолютный путь к `nm_host.py` в манифест и копируем его на
место:

```bash
mkdir -p ~/.mozilla/native-messaging-hosts

ABS_PATH="$(pwd)/native-host/nm_host.py"

sed "s|__ABSOLUTE_PATH_TO_NM_HOST__/nm_host.py|${ABS_PATH}|" \
    native-host/native-host-manifest.json \
    > ~/.mozilla/native-messaging-hosts/com.local.native_tab_switcher.json
```

Проверить, что путь подставился правильно:

```bash
cat ~/.mozilla/native-messaging-hosts/com.local.native_tab_switcher.json
```

> **Flatpak Firefox на Fedora.** Начиная с Firefox 102 Flatpak-версия
> читает манифесты native-messaging-хостов из того же
> `~/.mozilla/native-messaging-hosts/` через portal, так что отдельных
> шагов обычно не требуется. Если подключение всё же не удаётся — первым
> делом проверь `flatpak info org.mozilla.firefox`, чтобы понять, какая
> версия Firefox установлена, это влияет на диагностику.

### 3. Extension ID

Extension ID уже зафиксирован в `extension/manifest.json`
(`browser_specific_settings.gecko.id`):

```
native-tab-switcher-poc@local.test
```

Именно этот ID указан в `allowed_extensions` манифеста хоста — менять их
нужно синхронно, если решишь переименовать.

### 4. Загрузить расширение во временном режиме

1. Открой `about:debugging#/runtime/this-firefox`.
2. Нажми **"Load Temporary Add-on…"**.
3. Выбери файл `extension/manifest.json`.

Поскольку ID задан явно в манифесте, он останется тем же самым при каждой
повторной загрузке (в отличие от расширений без явного ID, которым Firefox
генерирует случайный временный ID).

## Тест

1. Открой минимум 3 вкладки в Firefox.
2. Убедись, что расширение загружено (шаг выше) — оно подключается к
   native host сразу при загрузке.
3. Запусти:

   ```bash
   python3 native-host/test_host.py 2
   ```

Ожидаемый результат в терминале:

```
Sent command:    {"command": "next_tab", "tab": 2}
Received response: {"status": "ok", "command": "next_tab", "tab": 2}
```

И визуально — Firefox переключится на третью открытую вкладку (индексация
с 0).

## Логи и отладка

- **Логи фонового скрипта расширения**: на странице
  `about:debugging#/runtime/this-firefox` нажми **"Inspect"** рядом с
  расширением — откроется devtools с консолью, где видны все сообщения
  `[NativeTabSwitcher] ...`.
- **Логи native host процесса**: пишутся в
  `/tmp/nm_host.log` (путь также выводится через `tempfile.gettempdir()`,
  на стандартной Fedora-системе это `/tmp`).

  ```bash
  tail -f /tmp/nm_host.log
  ```

## Обработка ошибок (уже реализовано)

`background.js` возвращает `{"status": "error", "message": "..."}` и не
падает при:

- отсутствии `command`;
- неизвестной команде;
- отсутствии `tab`;
- нечисловом/отрицательном индексе вкладки;
- индексе, выходящем за число открытых вкладок;
- ошибке `browser.tabs.query` / `browser.tabs.update`.

`nm_host.py` не падает при обрыве соединения с Firefox, некорректном JSON
от `test_host.py` и таймауте ожидания ответа от расширения (5 секунд).

## Что дальше

Это самостоятельный, изолированный POC — существующий Python watcher он
не трогает и с ним не интегрирован. Следующий шаг (за рамками этого
этапа) — научить watcher писать в `/tmp/nm_tab_switcher.sock` тем же
протоколом, что использует `test_host.py`, вместо запуска отдельного
CLI-процесса.
