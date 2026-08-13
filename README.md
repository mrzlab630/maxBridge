# maxBridge

Мост для мессенджера MAX (VK). Подключается к аккаунту MAX как пользователь через WebSocket, получает все сообщения в реальном времени и пересылает в Telegram. Предоставляет JSON-RPC 2.0 IPC-интерфейс для внешних приложений.

## Возможности

- **Мульти-аккаунт** — одновременная работа с несколькими аккаунтами MAX
- **QR-авторизация** — вход через сканирование QR-кода приложением MAX
- **Telegram оповещения** — пересылка сообщений из MAX в Telegram канал/чат (текст, все вложения, ответы и пересланные сообщения)
- **Telegram control bot** — команды `/status`, `/update`, `/auth` для проверки состояния и ручного запроса QR
- **Зашифрованные сессии** — Fernet AES-128-CBC + HMAC-SHA256, PBKDF2 480K итераций
- **TUI интерфейс** — терминальный интерфейс в стиле cyberpunk для управления
- **CLI командер** — текстовый интерактивный шелл
- **JSON-RPC 2.0 IPC** — Unix socket / TCP для внешних приложений
- **MCP-совместимая схема** — эндпоинт `schema` для AI-агентов
- **Мониторинг** — `health`, `stats`, `errors`
- **Свой протокол** — собственная реализация MAX WebSocket клиента
- **Устойчивое соединение** — keepalive, автоматический reconnect и аккуратный shutdown без шумных background exception
- **Безопасность** — SO_PEERCRED, rate limiting, idle timeout, symlink-защита

## Структура проекта

```
maxBridge/
└── cli/                           ← Основное приложение
    ├── src/maxbridge/
    │   ├── protocol/              ← Нативный MAX WebSocket клиент
    │   │   ├── max_client.py      ← Подключение, авторизация, RPC
    │   │   └── errors.py          ← Типизированные ошибки
    │   ├── auth/                  ← Авторизация
    │   │   ├── qr_auth.py         ← QR-код авторизация
    │   │   ├── sms_auth.py        ← SMS авторизация (fallback)
    │   │   ├── session.py         ← Зашифрованное хранение сессий
    │   │   ├── encryption.py      ← Fernet шифрование
    │   │   └── token_auth.py      ← Авторизация по токену
    │   ├── client/                ← Управление аккаунтами
    │   │   ├── account.py         ← Один MAX аккаунт
    │   │   ├── account_manager.py ← Реестр мульти-аккаунтов
    │   │   ├── connection.py      ← WebSocket + reconnect
    │   │   └── event_router.py    ← Роутинг по опкодам
    │   ├── bridge/                ← Мост сообщений
    │   │   ├── event_bus.py       ← Pub/sub с фильтрацией
    │   │   └── transformer.py     ← MAX пакет → UnifiedMessage
    │   ├── telegram/              ← Telegram оповещения
    │   │   ├── forwarder.py       ← Пересылка сообщений MAX → Telegram
    │   │   ├── control_bot.py     ← Telegram бот управления и QR flow
    │   │   └── config.py          ← Конфиг telegram.json
    │   ├── cache/                 ← LRU-кеш
    │   │   └── entity_cache.py    ← Кеш имён пользователей и чатов
    │   ├── handlers/              ← Обработчики
    │   │   └── message.py         ← Входящие сообщения
    │   ├── ipc/                   ← IPC сервер
    │   │   ├── server.py          ← Unix socket / TCP
    │   │   ├── methods.py         ← RPC методы
    │   │   ├── protocol.py        ← JSON-RPC 2.0
    │   │   ├── schema.py          ← MCP схема для AI
    │   │   ├── stats.py           ← Сборщик статистики
    │   │   └── rate_limiter.py    ← Token bucket
    │   ├── media/                 ← Медиа
    │   │   └── uploader.py        ← Upload/download файлов
    │   ├── tui/                   ← Терминальный интерфейс
    │   │   ├── app.py             ← Главное приложение
    │   │   ├── styles.py          ← Cyberpunk тема
    │   │   ├── helpers.py         ← Общие хелперы
    │   │   ├── accounts_store.py  ← Хранение аккаунтов
    │   │   └── screens/           ← Экраны TUI
    │   │       ├── qr.py          ← QR авторизация
    │   │       ├── chat_view.py   ← Просмотр чата
    │   │       ├── chat_list.py   ← Список чатов
    │   │       ├── sessions.py    ← Управление сессиями
    │   │       └── telegram.py    ← Настройки Telegram
    │   ├── utils/                 ← Утилиты
    │   ├── commander.py           ← CLI шелл
    │   ├── config.py              ← Загрузка YAML конфига
    │   └── main.py                ← Демон
    ├── tests/                     ← 187 юнит-тестов
    ├── examples/                  ← Пример IPC клиента
    ├── deploy/                    ← systemd + install.sh
    └── pyproject.toml
```

## Установка

```bash
git clone <repo-url> maxBridge
cd maxBridge/cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**Требования:** Python 3.10+

## Быстрый старт

### 1. TUI интерфейс (рекомендуется)

```bash
cd maxBridge/cli && source .venv/bin/activate
maxbridge-tui
```

Меню:
- 🔐 **Сессии** — добавление аккаунтов через QR, переключение активной сессии
- 💬 **Чаты** — список каналов/диалогов, просмотр истории, отправка сообщений
- 📨 **Telegram** — настройка пересылки сообщений в Telegram
- 🟢/🔴 **Демон** — запуск/остановка фонового сервиса

### 2. Демон (фоновый сервис)

```bash
# Авторизация (QR-код)
python -m maxbridge.main --auth-only

# Запуск
python -m maxbridge.main

# Фоновый запуск
# Допустим только когда systemd и PM2 остановлены.
nohup python -m maxbridge.main > /dev/null 2>&1 &

# С отладкой
python -m maxbridge.main --debug
```

## Авторизация

maxBridge использует **QR-код авторизацию**:

1. Запустите TUI (`maxbridge-tui`) → Сессии → 📱 Новая авторизация
2. На экране появится QR-код
3. Откройте MAX на телефоне → отсканируйте QR
4. Подтвердите вход на телефоне
5. Сессия зашифруется и сохранится

Поддерживается мульти-аккаунт — можно добавить несколько сессий.

## Telegram оповещения

Пересылка всех входящих сообщений из MAX в Telegram канал/чат.

### Настройка через TUI

1. `maxbridge-tui` → 📨 Telegram
2. Введите токен бота (`@BotFather` в Telegram)
3. Введите ID получателя (канал, группа или личный чат)
4. Нажмите 🧪 Тест — проверьте получение
5. Включите переключатель 🟢
6. Перезапустите демон

### Формат сообщений

```
Имя Отправителя → Название Чата
Текст сообщения
```

Медиа:
- **Фото** — все вложенные фото пересылаются как фото, подпись ставится к первому файлу
- **Видео** — все вложенные видео пересылаются как видео, при необходимости URL запрашивается через MAX download API
- **Голосовое** — пересылается как voice, если MAX вернул скачиваемый URL или file ID
- **Файлы** — все вложенные документы пересылаются как Telegram documents, при необходимости URL запрашивается через MAX download API
- **Ответы** — цитируемое сообщение выводится блоком `↩ Ответ на сообщение`
- **Пересланные** — вложенное сообщение выводится блоком `↪ Переслано`
- **Сервисные события** — системные сообщения и control actions приводятся к читаемому тексту

### Конфиг

Хранится в `data/telegram.json`:
```json
{
  "enabled": true,
  "bot_token": "000000000:EXAMPLE_TOKEN_REPLACE_ME",
  "chat_id": "-1000000000001",
  "allowed_chat_ids": ["-1000000000001", "1000000001"]
}
```

Все значения в примере фиктивные. Замените токен, ID получателя и список разрешённых чатов своими значениями.

`allowed_chat_ids` ограничивает, из каких Telegram чатов разрешены управляющие команды. Если поле не задано, по умолчанию используется `chat_id`.

### Бот управления

При включенном Telegram-конфиге maxBridge поднимает polling-бота управления:

- `/status` — краткое состояние MAX, Telegram и IPC
- `/update` — runtime-сводка по сообщениям, вложениям и RPC-ошибкам
- `/auth [account_id]` — ручной запрос нового QR-кода для входа в MAX

Команды обрабатываются только из чатов, перечисленных в `allowed_chat_ids`.

## Надежность

- WebSocket клиент поддерживает keepalive и автоматически инициирует reconnect при таймаутах и закрытии соединения
- Reconnect выполняется отдельной задачей с ограниченным числом попыток и повторной token auth
- При disconnect/reconnect корректно очищаются pending RPC futures и фоновые задачи, чтобы не оставлять `Task/Future exception was never retrieved`

## Конфигурация

Создайте `config/local.yaml`:

```yaml
accounts:
  personal:
    phone: "+79001234567"
    session_file: "data/personal.session"

security:
  key_file: "data/master.key"

ipc:
  transport: "unix"
  tcp_host: "127.0.0.1"
  tcp_port: 9100
  max_clients: 10

bridge:
  listen_chats: "all"
  cache_ttl: 600

logging:
  level: "WARNING"
  file: null

daemon:
  pid_file: "/tmp/maxbridge.pid"
```

Динамические аккаунты (добавленные через TUI) сохраняются в `data/accounts.json`.

## IPC API (JSON-RPC 2.0)

### Подключение

```bash
# Unix socket
python examples/ipc_client.py --subscribe

# Или через socat
socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/maxbridge.sock
```

### Методы

| Метод | Описание |
|---|---|
| `ping` | Проверка связи |
| `health` | Состояние системы |
| `status` | Статус аккаунтов |
| `stats` | Статистика |
| `errors` | Лог ошибок |
| `schema` | MCP схема для AI |
| `send_message` | Отправить текст |
| `send_photo` | Отправить фото |
| `send_file` | Отправить файл |
| `get_history` | История чата |
| `get_chat_info` | Инфо о чате |
| `get_user_info` | Профили пользователей |
| `get_download_url` | URL для скачивания |
| `subscribe` | Подписка на сообщения (с фильтрами) |
| `unsubscribe` | Отписка |
| `list_methods` | Список методов |

### Push-уведомления

```json
{
  "jsonrpc": "2.0",
  "method": "message",
  "params": {
    "account_id": "personal",
    "chat_id": 12345,
    "status": "message_new",
    "text": "Привет!",
    "sender_id": 67890,
    "sender_name": "Иван Петров",
    "chat_name": "DM",
    "chat_type": "DIALOG"
  }
}
```

## Production: локальная сборка, установка и запуск

По умолчанию production-инсталляция остаётся в текущем checkout: установщик собирает пакет
в `cli/.venv`, создаёт локальный конфиг и каталоги состояния и не меняет систему. systemd,
PM2 и ручной запуск — mutually exclusive
владельцы демона: в каждый момент должен работать ровно один supervisor. TUI и Commander
остаются клиентскими/operator surfaces и не запускают отдельный Telegram poller.

Перед запуском проверьте отсутствие второго владельца:

```bash
sudo systemctl status maxbridge --no-pager
pm2 list
pgrep -af '/usr/local/bin/maxbridge|\.venv/bin/maxbridge|maxbridge\.main'
```

`daemon.pid_file` и `MAXBRIDGE_DAEMON_PID_FILE` задают application PID lock. Это не
внутренние PID-файлы PM2 в `PM2_HOME`; настройка PM2 `pid_file` для приложения не нужна.

### Локальная установка (по умолчанию)

Получите **полный checkout** ветки `main`. Нельзя копировать на сервер только каталог
`deploy/`: установщику одновременно нужны `deploy/`, `src/`, `pyproject.toml` и остальные
файлы дерева `cli`.

```bash
git clone <URL-репозитория> maxBridge
./maxBridge/cli/deploy/install.sh
```

Скрипт определяет каталог `cli` относительно собственного пути, поэтому его можно вызвать
из любого cwd. Он создаёт `cli/.venv`, выполняет не editable-установку пакета и создаёт
`cli/config/local.yaml` из packaged default только при отсутствии файла. Существующий
локальный конфиг никогда не перезаписывается. Данные и логи остаются в `cli/data` и
`cli/logs`; права нового конфига по возможности устанавливаются в `0600`.

Development extras необязательны: `INSTALL_DEV=1 ./maxBridge/cli/deploy/install.sh`.

Если первый запуск установщика прервался после создания `.venv`, каталогов или конфига,
устраните причину и безопасно повторите ту же команду: локальные подготовительные шаги
идемпотентны, а существующий конфиг не будет перезаписан.

### Локальная сборка TUI

Для TUI без полного локального установщика используйте отдельную checkout-local сборку:

```bash
./cli/deploy/build-tui.sh
./cli/.venv/bin/maxbridge-tui
```

Скрипт собирает wheel в `cli/dist`, устанавливает только этот wheel в `cli/.venv` и выводит
абсолютные пути к wheel и TUI. В отличие от `install.sh`, он не создаёт конфиг, каталоги
состояния или daemon-процесс.

### Конфигурация, авторизация и первый локальный старт

```bash
cd maxBridge/cli
$EDITOR config/local.yaml
.venv/bin/maxbridge --auth-only -c config/local.yaml
.venv/bin/maxbridge -c config/local.yaml
.venv/bin/maxbridge-tui
```

TUI подключается к уже выбранному демону. Переключатель демона в TUI не заменяет systemd
или PM2: если процесс уже управляется supervisor-ом, не запускайте из TUI второй демон.

При запуске из `cli` загрузчик конфигурации сначала найдёт `config/local.yaml`, раньше
legacy-файла `/etc/maxbridge/config.yaml`. Проверить локально установленную версию можно
без предположения о наличии флага `--version`:

```bash
.venv/bin/python -c 'from importlib.metadata import version; print(version("maxbridge"))'
```

### Безопасное обновление checkout

Не используйте `git reset --hard`: он может уничтожить локальные операторские изменения.
Сначала убедитесь, что checkout чист, затем выполняйте fast-forward update и повторную
не editable-установку:

```bash
cd /путь/к/maxBridge
git status --short
git switch main
git pull --ff-only
./cli/deploy/install.sh
```

Если `git status --short` показывает изменения, остановитесь и сохраните/разберите их до
обновления; не затирайте их автоматически. Повторный установщик сохраняет локальный конфиг.

### Диагностика ошибок установщика

Ошибки `src/maxbridge/data/default.yaml: No such file or directory` означают, что на сервер
скопирован неполный checkout. Убедитесь, что имеются все файлы Python-пакета, и повторите:

```bash
cd /путь/к/maxBridge
git switch main
git pull --ff-only
test -f cli/src/maxbridge/data/default.yaml
test -f cli/pyproject.toml
./cli/deploy/install.sh
```

Не копируйте `default.yaml` поверх `cli/config/local.yaml` и не удаляйте существующий
конфиг ради повторного запуска.

### Опциональная ручная миграция в systemd

Файл `cli/deploy/maxbridge.service` остаётся примером для отдельной осознанной системной
миграции. Локальный установщик его не копирует и не активирует. Перед переносом checkout,
бинарника, конфига и состояния в service-каталоги сверьте пути unit с фактическим
размещением. Сохраняйте существующее legacy-состояние `/etc/maxbridge`; локальная установка
не требует его удаления. Не запускайте systemd одновременно с локальным процессом или PM2.

### PM2 — альтернативный supervisor

PM2 допустим вместо systemd, но никогда одновременно с ним. Перед переходом выполните
`sudo systemctl disable --now maxbridge`; перед возвратом к systemd удалите процесс из PM2
(`npm run pm2:delete`, затем `pm2 save`). PM2 запускает демон из локального virtualenv:

```bash
./maxBridge/cli/deploy/install.sh
cd maxBridge/cli
npm install -g pm2        # один раз
npm run pm2:prod
npm run pm2:status
npm run pm2:logs
```

Управление и сохранение процесса:

```bash
npm run pm2:restart
npm run pm2:delete
pm2 startup               # затем выполните напечатанную PM2 команду с sudo
npm run pm2:save
```

PM2 пишет логи в `cli/logs/maxbridge-out.log` и
`cli/logs/maxbridge-error.log`; application PID lock находится в
`cli/data/maxbridge.pid`.

### Ручной запуск

Ручной запуск разрешён только после остановки systemd и удаления/остановки процесса PM2:

```bash
sudo systemctl stop maxbridge
pm2 delete maxbridge
cd maxBridge/cli
nohup .venv/bin/maxbridge -c config/local.yaml > /dev/null 2>&1 &
```

### Восстановление Telegram polling после 409

Состояние `external_conflict` отключает только Telegram control-plane polling; MAX и
форвардинг продолжают работу. Аренда защищает один host и один Linux network namespace,
поэтому сначала найдите и остановите внешний consumer. Затем выполните явный
operator-triggered restart выбранного единственного supervisor-а. Автоматического retry
или повторного захвата аренды у конфликтовавшего объекта нет.

## Безопасность

- Fernet шифрование сессий (AES-128-CBC, PBKDF2 480K итераций)
- SO_PEERCRED проверка UID на Unix socket
- Rate limiting (token bucket per-client)
- Idle timeout 5 мин
- Symlink-защита на socket, PID, upload
- Upload directory allowlist
- PID file locking (`fcntl.flock`)
- Типизированные ошибки без traceback

## Тестирование

```bash
cd maxBridge/cli && source .venv/bin/activate
python -m pytest tests/ -v
```

Сейчас в проекте 189 unit-тестов.

## Лицензия

MIT
