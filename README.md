# eSIM болон Windows лицензийн борлуулалтын агент

Гадаад руу аялах хүмүүст eSIM дата багц, мөн Windows лиценз зардаг Telegram бот. LLM (Groq API) харилцагчийн
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

Товчлуурын урсгал (1–6) нь LLM-гүйгээр, тодорхой кодоор ажилладаг тул мөнгөтэй холбоотой хэсэг
найдвартай. Харилцагч чөлөөт текст бичвэл ("Японд 7 хоног явна, аль нь тохирох вэ?",
"Миний утас eSIM дэмжих үү?") LLM агент хариулж, шаардлагатай бол хэрэгслээр багц харуулах,
нэхэмжлэх үүсгэх, захиалгын төлөв шалгах, eSIM дахин илгээх боломжтой.

## Бүтэц

```
src/esim_bot/
  main.py            FastAPI: вэб дэлгүүр, /telegram/webhook, /qpay/callback, /health
  web/               Вэб дэлгүүр: нүүр, eSIM (/esim, /esim/<sku>), лиценз (/license, /license/<sku>),
                     сим суулгах заавар (/install), түгээмэл асуулт (/faq), захиалга шалгах (/check)
  mailer.py          И-мэйл илгээх (Mailgun API, SMTP, эсвэл локалд .eml файл)
  bot.py             Сувгаас хамааралгүй ботын логик (товчлуурын урсгал, төлбөр, хүргэлт)
  agent/
    loop.py          LLM agent loop (Groq, OpenAI-compatible Chat Completions + tool calling)
    tools.py         Агентын хэрэгслүүд
    prompts.py       System prompt
  orders.py          Захиалга үүсгэх, төлбөр шалгах, eSIM олгох (idempotent)
  fulfillment.py     Захиалгад eSIM / лицензийн түлхүүр олгох (PoolProvider)
  payments/qpay.py   QPay v2 клиент
  payments/fake.py   Локал туршилтын хуурамч QPay
  channels/          Telegram суваг (+ ирээдүйд Messenger)
  delivery.py        eSIM QR зураг, суулгах зааврын текст
  catalog.py         data/catalog.yaml-аас улс, бүс, багц, лиценз унших
  cli.py             Удирдлагын командууд
data/
  catalog.yaml       Улс, олон улсын бүс, eSIM багц, Windows лиценз, үнэ
  esims.example.csv  eSIM сангийн CSV загвар
  licenses.example.csv  Лицензийн түлхүүрийн CSV загвар (package_sku, product_key)
```

## Эхлүүлэх

```bash
uv sync
cp .env.example .env    # GROQ_API_KEY болон бусад утгыг бөглөнө
```

### 1. Терминал дээр турших (Telegram, QPay хэрэггүй)

```bash
uv run esim chat --fake-payments
```

Товчлуурыг дугаараар нь сонгоно, `/pay` бичвэл хуурамч төлбөр төлөгдсөнд тооцогдоно. QR зургууд
`out/` хавтаст хадгалагдана. Чөлөөт текст бичихэд `GROQ_API_KEY` хэрэгтэй.

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

### 3. Вэб дэлгүүр (компьютер дээрээ)

```bash
# PowerShell: $env:FAKE_PAYMENTS="true"; uv run uvicorn esim_bot.main:app --port 8000
FAKE_PAYMENTS=true uv run uvicorn esim_bot.main:app --port 8000
```

Браузерт http://localhost:8000 нээнэ. Бүтээгдэхүүн сонгож, и-мэйлээ оруулаад захиалгын хуудсан дээрх
"(Туршилт) Төлсөнд тооцох" товчийг дарвал eSIM/лиценз хуудсанд гарна. Mailgun, SMTP тохируулаагүй бол
харилцагчид очих и-мэйл `out/emails/*.eml` файлд хадгалагдана (Outlook, Mail-ээр нээж үзнэ).

Вэб захиалгын урсгал: нүүр хуудас → `/checkout` (и-мэйл) → `/order/{id}?t=...` (QPay QR, утсан
дээр банкны апп-ын товч) → төлбөр ормогц хуудас өөрөө шинэчлэгдэж eSIM QR / лицензийн түлхүүр гарна,
мөн и-мэйлээр очно. Захиалгын холбоос нууц түлхүүртэй тул зөвхөн харилцагч өөрөө нээнэ.

#### И-мэйл (Mailgun)

1. https://www.mailgun.com дээр бүртгүүлнэ. Эхэндээ өгдөг sandbox домэйн зөвхөн Mailgun-д
   бүртгэсэн (authorized recipients) хаяг руу илгээнэ — туршилтад тохиромжтой.
2. Жинхэнэ харилцагч руу илгээхэд өөрийн домэйноо (жишээ нь `mg.nomaddata.mn`) Sending → Domains-д
   нэмж, Mailgun-ы өгсөн DNS бичлэгүүдийг (SPF, DKIM, MX) домэйны бүртгэгч дээрээ нэмээд Verify дарна.
3. API key-ээ (Mailgun → API Security) аваад `.env`-д бичнэ. EU бүсэд домэйн үүсгэсэн бол `MAILGUN_REGION=eu`:
   ```
   MAILGUN_API_KEY=...
   MAILGUN_DOMAIN=mg.nomaddata.mn
   MAILGUN_REGION=us
   MAIL_FROM=NomadData <no-reply@mg.nomaddata.mn>
   ```
4. Шалгана: `uv run esim test-email таны@хаяг.mn`. Сервер асахдаа ч аль хэрэгслээр илгээхийг
   ("И-мэйл: Mailgun (...)") log-д бичнэ.

Mailgun-ы оронд SMTP (`SMTP_HOST`...) ашиглаж болно. И-мэйл илгээж чадаагүй ч захиалга зогсохгүй —
QR код, түлхүүр захиалгын хуудсанд гарч, админд мэдэгдэл очно.

### 4. Сервер дээр (webhook + QPay callback)

1. Серверээ гадагш нээнэ (жишээ нь `ngrok http 8000`) — URL-аа `PUBLIC_BASE_URL`-д бичнэ.
2. eSIM сангаа оруулна:
   ```bash
   uv run esim import-esims data/esims.example.csv
   uv run esim import-licenses data/licenses.example.csv
   uv run esim stock
   ```
3. Сервер асааж, webhook бүртгэнэ:
   ```bash
   uv run uvicorn esim_bot.main:app --port 8000
   uv run esim set-webhook
   ```

QPay sandbox-ийн тестийн утгууд (`TEST_MERCHANT` / `123456` / `TEST_INVOICE`) `.env.example`-д
бий. Production-д QPay-тэй гэрээ байгуулж авсан client_id, secret, invoice_code-оо тавина.

#### Жинхэнэ QPay руу шилжих

1. `.env`-д:
   ```
   FAKE_PAYMENTS=false
   QPAY_BASE_URL=https://merchant.qpay.mn/v2
   QPAY_CLIENT_ID=...          # QPay-ээс авсан
   QPAY_CLIENT_SECRET=...
   QPAY_INVOICE_CODE=...
   CALLBACK_SECRET=...         # uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   `CALLBACK_SECRET` анхдагч хэвээр эсвэл QPay-ийн тест утгууд үлдсэн бол сервер асахгүй, юуг засахыг хэлнэ.
   `CALLBACK_SECRET`-ийг дараа нь солибол өмнөх захиалгын холбоосууд ажиллахаа болино.
2. `uv run esim qpay-test` — 100₮-ийн жинхэнэ нэхэмжлэх үүсгэж (`out/qpay-test.png` QR), банкны
   аппаар төлөхөд "Төлбөр орлоо" гэж хэлнэ. Захиалга, eSIM-ийн нөөцөд хүрэхгүй. Домэйн шаардахгүй.
3. Домэйнгүй локал дээр ч жинхэнэ захиалга ажиллана: QPay callback ирэхгүй ч захиалгын хуудас
   нээлттэй байхад QPay-ээс 5 секунд тутам шалгана. Харин туршилтын (`TEST-...`) eSIM-тэй хуучин
   `esim.db`-тэй бол жинхэнэ мөнгөөр туршилтын eSIM олгоно — тусдаа сан ашиглана:
   `DATABASE_URL=sqlite+aiosqlite:///./live.db`.

> Хөгжүүлэлтийн явцад өгөгдлийн сангийн бүтэц өөрчлөгдвөл бот "бүтэц хуучирсан" гэж мэдэгдэнэ.
> Туршилтын өгөгдөл бол `esim.db` файлыг устгаад дахин асаана.

## eSIM нөөц (CSV)

Нийлүүлэгчээс авсан eSIM-үүдийг CSV-ээр санд оруулна. Багана: `package_sku,iccid,smdp_address,activation_code`
(`data/esims.example.csv`-г загвар болгоно). `package_sku` нь `data/catalog.yaml`-ийн sku-тай яг ижил байна.

```bash
uv run esim import-esims esims-2026-10.csv   # алдаатай мөр байвал юу ч оруулахгүй, мөрийн дугаараар нь хэлнэ
uv run esim stock                            # бүтээгдэхүүн бүрийн үлдэгдэл, дууссаныг тэмдэглэнэ
```

- Нөөцгүй бүтээгдэхүүн сайт, бот дээр "Түр дууссан" гэж харагдаж, төлбөр авахгүй. Нэг захиалгад
  үлдэгдлээс илүү тоо авах боломжгүй.
- Борлуулалтын дараа үлдэгдэл `LOW_STOCK_THRESHOLD` (анхдагч 3) буюу түүнээс доош болвол админы
  Telegram-д мэдэгдэл очно.
- Аль хэдийн оруулсан ICCID-ийг дахин оруулахгүй тул ижил файлыг давтан ажиллуулахад аюулгүй.
- Нийлүүлэгч `LPA:1$rsp.example.com$ABC123` хэлбэрээр өгвөл `$`-аар хувааж `smdp_address`
  (`rsp.example.com`), `activation_code` (`ABC123`) болгоно.
- CSV-д идэвхжүүлэх код байгаа тул нууцаар хадгална: git-д оруулахгүй (`.gitignore`-д `*.csv` бий),
  оруулсны дараа серверээс устгана.

## Windows лиценз

`data/catalog.yaml`-ийн `licenses` хэсэгт бүтээгдэхүүн, үнэ, тайлбар, идэвхжүүлэх зааврыг бичнэ
(жишээ үнэ — заавал солино). Түлхүүрүүдийг CSV-ээр (`package_sku,product_key`) санд оруулна:
`uv run esim import-licenses keys.csv`. Төлбөр ормогц түлхүүр, идэвхжүүлэх заавар харилцагчид очно.
Түлхүүрийг Microsoft-ын албан ёсны дистрибьютер/борлуулагчаас авна.

## Тест

```bash
uv run pytest
```

## LLM-ийн тохиргоо (Groq)

- Түлхүүрээ https://console.groq.com/keys-оос аваад `.env`-ийн `GROQ_API_KEY`-д бичнэ. Үнэгүй
  түвшинд минут, өдрийн хязгаар бий — өөрийн хязгаараа Groq console-ийн Limits хуудаснаас харна.
- Загвар: `openai/gpt-oss-120b` (`GROQ_MODEL`-оор солино). `llama-3.3-70b-versatile` 2026-08-16-нд
  Groq-ийн үнэгүй/Developer түвшинд хаагдсан. `GROQ_REASONING_EFFORT` (low/medium/high) нь зөвхөн
  gpt-oss загваруудад — өөр загвар сонговол хоосон үлдээнэ.
- OpenAI-compatible API тул `GROQ_BASE_URL`, `GROQ_MODEL`-ийг сольж OpenAI, Gemini, Ollama зэрэгт
  холбож болно.
- `GROQ_API_KEY` хоосон бол товчлуурын урсгал бүрэн ажиллаж, чөлөөт текстэд "цэсээр үйлчилж
  байна" гэж хариулна.
- Загварт зөвхөн сүүлийн 20 мессежийг илгээнэ (токены хязгаарт багтаана). 12 цаг идэвхгүй байх
  эсвэл `/start` дарахад шинэ яриа эхэлнэ.

## Цаашид хийх

- [ ] Facebook Messenger суваг (`channels/messenger.py` — `Channel` интерфейсийг хэрэгжүүлнэ)
- [ ] eSIM / лицензийн нийлүүлэгчийн API-аар шууд үүсгэх provider (`fulfillment.py`-ийн `FulfillmentProvider`)
- [ ] Төлөгдөөгүй нэхэмжлэхийг үе үе шалгах background poller (callback ирээгүй тохиолдолд)
- [ ] Олон instance-аар ажиллуулах бол Postgres + DB түвшний lock (одоо нэг процессын lock)
- [ ] Админ самбар: захиалга, үлдэгдэл, NEEDS_ATTENTION захиалгууд
