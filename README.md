# ⚡ VOLT — Self-Hosted голосовой мессенджер с 2K 60fps стримами и Hi-Fi плеером

Лёгкий, приватный self-hosted аналог Discord для общения небольшой компании (3–6 человек), построенный на **LiveKit SFU**, **Python**, **FFmpeg (`libsoxr`)** и нативном десктоп-клиенте на **Rust (`tao` + `wry`)**.

Спроектирован так, чтобы стабильно работать даже на недорогом VPS (2 vCPU / 2 GB RAM), выдавать **2K 60fps** демонстрацию экрана без искусственных лимитов битрейта и не обрываться при сворачивании в фоне во время игр.

---

## 📸 Интерфейс (Dark Liquid Glass)

### Голосовой канал, живой чат и встроенный Hi-Fi плеер
![Голосовой канал и чат](screenshots/02_voice_channel_chat.png)

### Лобби и привязка сессии к железу
![Лобби](screenshots/01_lobby.png)

### Встроенный плеер VOLT MUSIC (Очередь, зацикливание, студийный EQ)
| Очередь и управление повтором | Студийный эквалайзер (`libsoxr 28-bit`) |
| :---: | :---: |
| ![VOLT MUSIC Очередь](screenshots/03_music_deck_queue.png) | ![VOLT MUSIC Эквалайзер](screenshots/04_music_deck_eq.png) |

### Настройки микрофона (тест и усиление 0–200%) и 2K 60fps стрим
| Аудио, тест микрофона и громкость | Мульти-стрим 2560×1440 • 60 FPS |
| :---: | :---: |
| ![Настройки микрофона](screenshots/05_settings_audio.png) | ![Демонстрация экрана 2K](screenshots/06_screen_share_2k.png) |

---

## ✨ Ключевые возможности

* **Голос и 2K 60fps демонстрация экрана без ограничений:**
  * Передача видео до `2560×1440 @ 60fps` (`VP9`, `25 Mbps`) через WebRTC SFU (LiveKit).
  * Переключение между несколькими одновременными стримами прямо внутри окна без принудительного перекидывания зрителей.
  * Индивидуальная регулировка громкости каждого участника (`0–200%`), тест микрофона с прослушиванием себя в реальном времени и усиление микрофона до `200%`.
* **Встроенный студийный музыкальный плеер (`VOLT MUSIC` в `music-bot/`):**
  * Аудиопоток `48 kHz Stereo Opus` (`260+ kbps`, `DTX=off`, `RED=off`) с точной синхронизацией пакетов каждые `20.0 мс`.
  * Мастеринг-цепочка на `FFmpeg` + `libsoxr` (28-bit precision), лимитер, стереорасширение и 4 пресета эквалайзера (*Студийный*, *Бас*, *Рок*, *Чистый*).
  * Поддержка поиска, прямых ссылок (**Spotify, YouTube Music, YouTube, SoundCloud, Apple Music, Яндекс Музыка**), загрузки локальных аудиофайлов (`FLAC / MP3 / WAV / OGG / M4A`), встроенного радио и зацикливания (`Выкл / Плейлист / Один трек`).
  * Автоматический выход из пустого канала через 5 минут отсутствия людей.
* **Нативный Windows-клиент на Rust (`VOLT.exe` < 1 МБ):**
  * Написан на `tao` + `wry` (`Edge WebView2`) с кастомной иконкой и вшитым автообновлением с сервера (проверка `SHA-256`, прогресс-бар `0–100%`, бесшовная подмена и перезапуск).
  * **Аппаратная привязка сессии:** вычисляет криптографический `HWID` на базе Windows `MachineGuid` — никаких фантомных дубликатов при переподключении и защита имени владельца на сервере.
  * **Защита от фризов в играх:** принудительно отключает фоновый троттлинг таймеров и occlusion в `WebView2`, чтобы голосовой сокет не засыпал, когда приложение свёрнуто за полноэкранной игрой.
* **Сетевая устойчивость:**
  * Встроенный сервис защиты ядра (`deploy/volt-dpishield.service`) отсекает фейковые `badseq` (`seq+10000`) пакеты DPI-обходов на TCP-порту 443, предотвращая повреждение SACK-буфера при отправке крупных WebRTC SDP-офферов.

---

## 🏗 Архитектура и структура проекта

```text
volt-voice/
├── docker-compose.yml          # Единый стек: LiveKit + Caddy + volt-app + volt-bot
├── .env.example                # Шаблон переменных окружения и ключей
├── server/                     # Модульный веб-бэкенд и диспетчер запросов
│   ├── app.py                  # Точка входа HTTP-сервера и таблица маршрутизации
│   ├── config.py               # Конфигурация и переменные окружения
│   ├── router.py               # Декларативный роутер запросов (GET / POST / Prefix)
│   ├── models/                 # [M] Потокобезопасное состояние комнат и сессий железа
│   │   └── state.py
│   ├── services/               # [S] Сервисный слой (JWT, Twirp RPC, хэширование, проксирование)
│   │   ├── token_service.py    # Выпуск JWT-токенов LiveKit
│   │   ├── livekit_client.py   # Twirp RPC клиент LiveKit (список/удаление участников)
│   │   ├── static_service.py   # SHA-256 кэш файлов и инъекция хэша
│   │   └── music_proxy.py      # Стриминг команд и загрузка файлов в плеер
│   ├── controllers/            # [C] Контроллеры эндпоинтов
│   │   ├── room_controller.py  # Присутствие, комнаты, heartbeat и leave
│   │   ├── token_controller.py # Генерация токенов доступа и манифест версий
│   │   ├── music_controller.py # Управление плеером и загрузка аудиофайлов
│   │   └── static_controller.py# Отдача веб-клиента, пульта и бинарников
│   ├── Dockerfile
│   └── static/                 # [V] Клиентский UI (Dark Liquid Glass SPA, WebRTC, плеер)
│       ├── index.html
│       ├── music.html
│       ├── livekit-client.umd.min.js
│       └── anime.min.js
├── music-bot/                  # Модульный Hi-Fi аудиосервис VOLT MUSIC (Python + LiveKit RTC + FFmpeg)
│   ├── bot.py                  # Тонкая точка входа (~30 строк) и фабрика aiohttp
│   ├── config.py               # Каталог радиостанций, пресеты EQ и калибровка громкости (-18.5 dB)
│   ├── models/                 # [M] PlaybackState: очередь, история треков и режимы повтора
│   │   └── playback_state.py
│   ├── services/               # [S] Сервисный слой аудио
│   │   ├── token_service.py    # Выпуск JWT-токена бота
│   │   ├── track_resolver.py   # Поиск и резолвинг (Spotify, Apple, Яндекс, YT, SoundCloud)
│   │   └── audio_engine.py     # Потоковый FFmpeg + libsoxr 28-bit (20мс PCM-нарезка)
│   ├── controllers/            # [C] Контроллеры жизненного цикла и API
│   │   ├── music_controller.py # Подключение к LiveKit, 5-мин автовыход, очередь и зацикливание
│   │   └── http_routes.py      # Декларативные aiohttp роуты (/status, /play, /loop, /eq и др.)
│   ├── requirements.txt
│   └── Dockerfile
├── desktop/
│   └── volt_desktop/           # Нативный Windows-клиент на Rust (Wry / Tao / Self-Updater / HWID)
│       ├── Cargo.toml
│       ├── build.rs
│       ├── icon.ico
│       └── src/main.rs
├── deploy/                     # Конфиги прокси, LiveKit и systemd-защиты порта 443
│   ├── Caddyfile
│   ├── livekit.yaml.template
│   └── volt-dpishield.service
├── tests/                      # Сквозные модульные и интеграционные тесты
│   ├── test_server_modular.py
│   └── test_music_bot_modular.py
└── screenshots/                # Скриншоты интерфейса
```

---

## 🚀 Быстрый запуск сервера (Docker Compose)

1. **Клонируйте репозиторий и подготовьте конфиги:**
   ```bash
   cp .env.example .env
   cp deploy/livekit.yaml.template deploy/livekit.yaml
   ```
2. **Заполните `.env` и `deploy/livekit.yaml`:**
   * Укажите свой домен (`DOMAIN=voice.example.com`) с A-записью на ваш VPS.
   * Сгенерируйте надёжный `LIVEKIT_API_SECRET` (минимум 32 символа) и укажите его одинаково в `.env` и в `deploy/livekit.yaml`.
   * Откройте порты на сервере: `80/tcp`, `443/tcp`, `7881/tcp` и `50000-50100/udp`.
3. **Запустите стек одной командой:**
   ```bash
   docker compose up -d --build
   ```
4. *(Опционально)* Если пользователи подключаются через DPI-обходы с `badseq` (`seq+10000`) на порту 443, активируйте фильтр на хосте:
   ```bash
   sudo cp deploy/volt-dpishield.service /etc/systemd/system/
   sudo systemctl enable --now volt-dpishield
   ```

---

## 🖥 Десктоп-клиент для Windows (`VOLT.exe`)

### Вариант 1: Готовый бинарник (GitHub Releases)
Скачайте **`VOLT-v1.2.0-windows-x64.exe`** из раздела [**Releases**](https://github.com/nikitos7626/volt-voice/releases/latest) (~760 КБ).
При первом запуске появится окно ввода адреса вашего сервера (также можно просто положить файл `volt_server.txt` с URL вашего сервера рядом с `.exe`).

### Вариант 2: Сборка из исходников под свой домен
Для сборки десктоп-клиента с зашитым адресом вашего сервера понадобится [Rust](https://rustup.rs/):

```powershell
cd desktop/volt_desktop
$env:VOLT_SERVER_URL = "https://voice.example.com"
cargo build --release
```

Готовый исполняемый файл появится в `desktop/volt_desktop/target/release/volt.exe`. При помещении его в `server/static/VOLT.exe` клиенты смогут скачивать его с веб-версии и автоматически обновляться по кнопке в интерфейсе.