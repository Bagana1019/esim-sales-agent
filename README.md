# eSIM борлуулалтын агент

Гадаад руу аялах хүмүүст eSIM дата багц зардаг Telegram бот. Claude (Anthropic API) харилцагчийн
чөлөөт асуултад хариулж, багц санал болгоно. Төлбөрийг QPay-ээр авч, төлбөр баталгаажмагц eSIM-ийн
QR код, SM-DP+ хаяг, суулгах зааврыг чатаар автоматаар илгээнэ.

## Урсгал

```
Харилцагч (Telegram)
  │ 1. /start → улсаа товчоор сонгоно (🇨🇳 Хятад, 🇰🇷 БНСУ, 🇯🇵 Япон, 🇹🇭 Тайланд)
  ▼
Бот → 2. Багцуудыг товчоор харуулна ("5 хоног / Нийт 3GB - 25,000₮")
  │
Харилцагч → багцаа сонгоно
  │
Бот → 3. QPay нэхэмжлэх үүсгэж, QR + "Банкны аппаар төлөх" + "Төлбөр шалгах" товч илгээнэ
  │
Харилцагч банкны аппаар төлнө
  │
QPay → /qpay/callback  (эсвэл харилцагч "Төлбөр шалгах" дарна)
  │ 4. Сервер QPay payment/check-ээр төлбөрийг баталгаажуулна (callback-д шууд итгэхгүй)
  ▼
5. eSIM сангаас ашиглагдаагүй профайл олгоно
  │
6. QR зураг + SM-DP+ код + суулгах заавар чатаар очно
```

Товчлуурын урсгал (1–6) нь Claude-гүйгээр, тодорхой кодоор ажилладаг тул мөнгөтэй холбоотой хэсэг
найдвартай. Харилцагч чөлөөт текст бичвэл ("Японд 7 хоног явна, аль нь тохирох вэ?",
"Миний утас eSIM дэмжих үү?") Claude агент хариулж, шаардлагатай бол хэрэгслээр багц харуулах,
нэхэмжлэх үүсгэх, захиалгын төлөв шалгах, eSIM дахин илгээх боломжтой.

## Бүтэц

```
src/esim_bot/
  main.py            FastAPI: /telegram/webhook, /qpay/callback, /health
  bot.py             Сувгаас хамааралгүй ботын логик (товчлуурын урсгал, төлбөр, хүргэлт)
  agent/
    loop.py          Claude agent loop (Messages API + tool use, гараар бичсэн давталт)
    tools.py         Агентын хэрэгслүүд
    prompts.py       System prompt
  orders.py          Захиалга үүсгэх, төлбөр шалгах, eSIM олгох (idempotent)
  esim.py            eSIM provider интерфейс + сангаас олгогч (PoolProvider)
  payments/qpay.py   QPay v2 клиент
  payments/fake.py   Локал туршилтын хуурамч QPay
  channels/          Telegram суваг (+ ирээдүйд Messenger)
  delivery.py        eSIM QR зураг, суулгах зааврын текст
  catalog.py         data/catalog.yaml-аас улс, багц унших
  cli.py             Удирдлагын командууд
data/
  catalog.yaml       Улс, багц, үнэ
  esims.example.csv  eSIM сангийн CSV загвар
```

## Эхлүүлэх

```bash
uv sync
cp .env.example .env    # ANTHROPIC_API_KEY болон бусад утгыг бөглөнө
```

### 1. Терминал дээр турших (Telegram, QPay хэрэггүй)

```bash
uv run esim chat --fake-payments
```

Товчлуурыг дугаараар нь сонгоно, `/pay` бичвэл хуурамч төлбөр төлөгдсөнд тооцогдоно. QR зургууд
`out/` хавтаст хадгалагдана. Чөлөөт текст бичихэд `ANTHROPIC_API_KEY` хэрэгтэй.

### 2. Telegram дээр компьютер дээрээ турших (ngrok хэрэггүй)

1. @BotFather-аас бот үүсгэж (`/newbot`) token-оо `.env`-ийн `TELEGRAM_BOT_TOKEN`-д бичнэ.
2. Ботоо асаана:
   ```bash
   uv run esim poll --fake-payments
   ```
3. Telegram-аас ботоо хайж олоод **Start** дарна. QR гарсны дараа чатад `/pay` гэж бичээд
   "Төлбөр шалгах" дарвал eSIM ирнэ (туршилтын eSIM-ууд санд автоматаар нэмэгдэнэ).

`--fake-payments`-гүй ажиллуулбал жинхэнэ QPay (sandbox) руу холбогдоно. Энэ үед callback ирэхгүй ч
"Төлбөр шалгах" товч QPay-ээс шууд шалгадаг тул ажиллана. Терминалд гарах `chat_id=...`-г
`ADMIN_CHAT_ID`-д бичвэл админы мэдэгдэл тэр чат руу очно.

### 3. Сервер дээр (webhook + QPay callback)

1. Серверээ гадагш нээнэ (жишээ нь `ngrok http 8000`) — URL-аа `PUBLIC_BASE_URL`-д бичнэ.
2. eSIM сангаа оруулна:
   ```bash
   uv run esim import-esims data/esims.example.csv
   uv run esim stock
   ```
3. Сервер асааж, webhook бүртгэнэ:
   ```bash
   uv run uvicorn esim_bot.main:app --port 8000
   uv run esim set-webhook
   ```

QPay sandbox-ийн тестийн утгууд (`TEST_MERCHANT` / `123456` / `TEST_INVOICE`) `.env.example`-д
бий. Production-д QPay-тэй гэрээ байгуулж авсан client_id, secret, invoice_code-оо тавина.

## Тест

```bash
uv run pytest
```

## Claude-ийн тохиргоо

- Загвар: `claude-opus-5-5` (`ANTHROPIC_MODEL`-оор солино), effort `low` — чатад хурдан хариу өгнө.
- Server-side fallback (`fallbacks: "default"`) идэвхтэй: аюулгүй байдлын шүүлтүүр хүсэлтээс
  татгалзвал Anthropic өөр загвар дээр автоматаар дахин ажиллуулна.
- Ярианы түүхийг зөвхөн нэмж бичдэг (append-only), system prompt, хэрэгслүүд тогтмол — prompt cache
  ажиллаж, thinking блокууд хүчинтэй хэвээр үлдэнэ. 12 цаг идэвхгүй байх, 60-аас олон мессеж болох
  эсвэл `/start` дарахад шинэ яриа эхэлнэ.

## Цаашид хийх

- [ ] Facebook Messenger суваг (`channels/messenger.py` — `Channel` интерфейсийг хэрэгжүүлнэ)
- [ ] eSIM нийлүүлэгчийн API-аар шууд үүсгэх provider (`esim.py`-ийн `EsimProvider`)
- [ ] Төлөгдөөгүй нэхэмжлэхийг үе үе шалгах background poller (callback ирээгүй тохиолдолд)
- [ ] Олон instance-аар ажиллуулах бол Postgres + DB түвшний lock (одоо нэг процессын lock)
- [ ] Админ самбар: захиалга, үлдэгдэл, NEEDS_ATTENTION захиалгууд
