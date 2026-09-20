# codesys-mcp-server

MCP-сервер для разработки ПЛК в **CODESYS V3** (IEC 61131-3, Structured Text):
чтение и изменение проектов «как инженер» — прямо из Claude Desktop, Cursor,
VS Code или любого MCP-клиента.

- **40 инструментов**: документация CODESYS + автоматизация проектов
- **Автоматизация**: POUs (программы/ФБ/функции/методы), DUT, GVL, сборка,
  библиотеки (установка/подключение/валидация), внешние файлы, Alarm/Unit
  Conversion (native XML), **online-цикл** (чтение/запись/force переменных)
- **Безопасность**: снапшоты перед правками, dry-run для батчей,
  обязательное подтверждение деструктивных операций
- **Скорость**: тёплый демон держит headless CODESYS открытым — вызовы
  занимают ~1–3 с вместо 20–90 с холодного старта

## Требования

- Windows с установленным CODESYS V3.5 (ScriptEngine — штатный компонент)
- Node.js ≥ 20
- Проект не должен быть открыт в GUI CODESYS во время операций (блокировка файла)

## Установка

```bash
npm install
npm run build
```

Запуск stdio (так его вызывают MCP-клиенты):

```bash
node dist/src/cli.js --stdio
# или через bin:
npx codesys-mcp --stdio
```

## Конфигурация клиентов

Задайте путь к CODESYS и профиль через `env` (значения по умолчанию —
примеры, обязательно укажите свои):

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "codesys": {
      "command": "node",
      "args": ["D:/tools/codesys-mcp-server/dist/src/cli.js", "--stdio"],
      "env": {
        "CODESYS_EXE": "C:/Program Files/CODESYS/CODESYS/Common/CODESYS.exe",
        "CODESYS_PROFILE": "CODESYS V3.5 SP21 Patch 1"
      }
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`) — тот же блок под ключом `mcpServers`.

**VS Code** (`.vscode/mcp.json` в рабочей папке или пользовательские
настройки):

```json
{
  "servers": {
    "codesys": {
      "type": "stdio",
      "command": "node",
      "args": ["D:/tools/codesys-mcp-server/dist/src/cli.js", "--stdio"],
      "env": {
        "CODESYS_EXE": "C:/Program Files/CODESYS/CODESYS/Common/CODESYS.exe",
        "CODESYS_PROFILE": "CODESYS V3.5 SP21 Patch 1"
      }
    }
  }
}
```

### Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `CODESYS_EXE` | Путь к CODESYS.exe | авто-поиск в `C:\Program Files\CODESYS` |
| `CODESYS_PROFILE` | Имя установленного профиля | `CODESYS V3.5 SP21 Patch 1` |
| `CODESYS_BRIDGE_DIR` | Папка моста (bridge.py) | `<пакет>/bridge` |
| `CODESYS_BRIDGE_TIMEOUT_MS` | Таймаут вызова | `240000` |
| `CODESYS_BRIDGE_AUTOSTART` | `0` — не поднимать демон автоматически | авто |

## Как это работает

```
MCP-клиент (stdio)
  └─ Node MCP-сервер (src/)
       └─ очередь задач → bridge_daemon (тёплый headless CODESYS, --noUI)
            └─ bridge.py (ScriptEngine, IronPython): открытые проекты, online-сессии
```

Сервер сам поднимает тёплый демон при первом вызове и маршрутизирует задачи
в него. Демон можно поднять вручную: `bridge/bridge_daemon.cmd` (см.
`bridge/README` в исходниках). Каждая операция сохраняет проект.

## Инструменты

**Проект:** `codesys_project_open`, `codesys_project_info`,
`codesys_list_objects`, `codesys_get_object`, `codesys_create_pou`,
`codesys_create_member`, `codesys_create_dut`, `codesys_create_gvl`,
`codesys_set_code`, `codesys_save_project`, `codesys_build`,
`codesys_eval`, `codesys_batch`, `codesys_add_file`.

**Библиотеки:** `codesys_validate_library`, `codesys_lib_install`,
`codesys_lib_add`, `codesys_lib_remove`, `codesys_lib_list` — библиотека
подключается к проекту по ссылке (namespace), без копирования объектов.

**Alarm/UnitConversion (нетекстовые объекты):**
`codesys_native_export`, `codesys_native_import` — XML round-trip через
IArchivable. `import_native` ДОБАВЛЯЕТ копию: цикл обновления —
export → правка XML → удалить старый → import.

**Online-цикл (требуется запущенный рантайм, напр. служба
«CODESYS Control Win V3» и Gateway):** `codesys_online_login`,
`codesys_online_read`, `codesys_online_write`, `codesys_online_force`,
`codesys_online_unforce`, `codesys_online_status`, `codesys_online_logout`.

**Безопасность:** `codesys_snapshot`, `codesys_snapshot_list`,
`codesys_snapshot_restore` (требует `confirm`), `codesys_delete_object`
(требует `confirm`), `codesys_batch` с `dry_run: true`.

**Документация CODESYS:** `search_codesys_docs`, `get_codesys_topic`,
`get_codesys_page`, `codesys_writing_guidance`, `list_codesys_pdfs`,
`search_codesys_pdfs`, `get_codesys_pdf`, `crawl_codesys_help`.

## Пример сессии

```
codesys_build(project, application)                 → 0 ошибок, 0 предупреждений
codesys_online_login(project, application)          → logged_in: true
codesys_online_read(project, application, expressions=["PRG_Doser.xReady"])
codesys_online_write(project, application, values={"PRG_Doser.rTarget": "7.5"})
codesys_online_force(project, application, values={"PRG_Doser.bDropCmd": "TRUE"})
codesys_online_unforce(project, application)        → unforced: all
```

## Ограничения и известные грабли

- `npm install` в Git Bash может падать с `ERR_INVALID_ARG_TYPE` на postinstall
  esbuild (баг npm 10.9 + Git Bash). Обход: `npm install --ignore-scripts`,
  затем `npm run build`. На обычной cmd/PowerShell обычно не воспроизводится.


- Только Windows (зависит от CODESYS ScriptEngine).
- Один headless CODESYS на машину: не запускайте второй демон и не держите
  проект открытым в GUI одновременно с операциями.
- `OnlineChangeOption` недостижим через IronPython: `online_login`
  использует режим «полная загрузка при рассинхроне» (0). Online change
  между логинами делайте из GUI.
- `import_native` добавляет копию объекта, а не заменяет (особенность CODESYS).
- NVL — текстовый объект: правится через `codesys_get_object`/
  `codesys_set_code`, как GVL.
- Интерфейсы: создавать пустыми (`codesys_create_pou` с `declaration`), члены
  — только дочерними объектами; `set_code` декларации интерфейса ломает его.

## Лицензия

MIT (см. LICENSE).
