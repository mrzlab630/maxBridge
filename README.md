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
  "bot_token": "123456:ABC-DEF...",
  "chat_id": "-1001234567890",
  "allowed_chat_ids": ["-1001234567890", "145185443"]
}
```

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

## Деплой

### systemd

```bash
sudo bash deploy/install.sh
sudo systemctl start maxbridge
```

### Ручной запуск

```bash
nohup python -m maxbridge.main > /dev/null 2>&1 &
```

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

Сейчас в проекте 187 unit-тестов.

## Лицензия

MIT
