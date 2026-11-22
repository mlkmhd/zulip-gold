# موجودی مسیرهای HTTP برای مهاجرت به Rust

مبنای این فهرست `dd0b414ed1` است. روش و مسیر از [OpenAPI](../zerver/openapi/zulip.yaml)
و handler و alias از تنظیمات توسعهٔ
[Django](../zproject/urls.py)، [Tornado](../zproject/tornado_urls.py)
و [سرویس اعلان push](../zilencer/urls.py) استخراج شده‌اند. فعال بودن مسیرهای push
در استقرار به تنظیمات همان سرویس وابسته است.

- OpenAPI شامل ۱۷۱ عملیات در ۱۲۲ مدخل است. سه مدخل
  `/real-time`، `/rest-error-handling` و `/zulip-outgoing-webhook`
  نمونه‌های مستندات هستند. ۱۶۸ عملیات دیگر به handler ثبت‌شده نگاشت شدند.
  [آزمون تطبیق OpenAPI و مسیریابی](../zerver/tests/test_openapi.py)
- در جدول‌های مسیریابی، روش‌های صریح و روش مصرف‌شدهٔ مسیرهای مستقیم آمده‌اند.
  در `rest_dispatch`، `GET` به‌طور ضمنی `HEAD` دارد، مگر اینکه جداگانه
  تعریف شده باشد. `OPTIONS` پیش از احراز هویت با `204` و هدر `Allow`
  پاسخ داده می‌شود. روش نامعتبر پاسخ `405` می‌گیرد.
  [REST dispatcher](../zerver/lib/rest.py)
- ستون «مسیریابی» اجرای پیش‌فرض هر عملیات OpenAPI را نشان می‌دهد. ادغام‌های
  جزئی PyO3 و اجرای توسعهٔ `/compatibility` در بخش وضعیت مهاجرت آمده‌اند.
  ممیزی وابستگی برای واژه‌های هشدار و خواندن متن‌های ذخیره‌شده
  در بخش‌های قرارداد آمده است.
- صفحات HTML، مسیرهای locale، افزونه‌های اختصاصی هر استقرار، SCIM و callbackهای
  SSO همچنان برای موجودی بعدی باقی‌اند.

## وضعیت مهاجرت

شمار فعلی Rust مستقل و اختیاری: ۳۱ از ۱۷۱ عملیات OpenAPI (۱۸٫۱٪)
و ۳۱ از ۱۹۰ عملیات صریح وب (۱۶٫۳٪). فهرست‌ها هم‌پوشانی دارند؛
`HEAD` متناظر جداگانه شمرده نشده و این اعداد درصد کل backend نیستند.

| عملیات یا جریان                                                                         | منطق اختیاری Rust                                                      | مسیر HTTP در توسعه و staging                                    | مسیر پیش‌فرض استقرار |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------------------- | -------------------- |
| `GET /api/v1/users/me/alert_words` و `HEAD` متناظر                                      | Basic Auth، خواندن PostgreSQL، سهمیهٔ مشترک Redis و صف فعالیت RabbitMQ | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `GET /json/users/me/alert_words` و `HEAD` متناظر                                        | نشست امضاشدهٔ Django، CSRF روش‌های امن، سهمیه و فعالیت                 | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست        | Django               |
| `GET /api/v1/saved_snippets` و `HEAD` متناظر                                            | Basic Auth، خواندن PostgreSQL، سهمیهٔ Redis و صف فعالیت RabbitMQ       | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `GET /json/saved_snippets` و `HEAD` متناظر                                              | نشست Django، رفتار CSRF روش‌های امن، سهمیه و فعالیت                    | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست        | Django               |
| `POST /json/saved_snippets` | نشست Django، اعتبارسنجی CSRF، ثبت متن و audit، اعلان پس از commit | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست و پروتکل | Django |
| `PATCH /json/saved_snippets/{saved_snippet_id}` | نشست Django، CSRF از هدر، ویرایش با شرط مالکیت و اعلان پس از commit | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست و پروتکل | Django |
| `DELETE /json/saved_snippets/{saved_snippet_id}` | نشست Django، CSRF از هدر، حذف با شرط مالکیت و اعلان پس از commit | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی با تنظیم نشست و پروتکل | Django |
| `POST /api/v1/saved_snippets`                                                           | ثبت متن و audit، اعلان پس از commit                                    | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `PATCH /api/v1/saved_snippets/{saved_snippet_id}`                                       | ویرایش با شرط مالکیت، اعلان پس از commit                               | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `DELETE /api/v1/saved_snippets/{saved_snippet_id}`                                      | حذف با شرط مالکیت، اعلان پس از commit                                  | Rust مستقیم، پروکسی توسعه یا nginx آزمایشی                      | Django               |
| `POST /api/v1/users/me/alert_words` و alias وب                                          | اعتبارسنجی و پاک‌سازی، درج گروهی، پاک‌سازی کش و رویداد پس از commit    | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `DELETE /api/v1/users/me/alert_words` و alias وب                                        | تراکنش PostgreSQL، پاک‌سازی Memcached و رویداد پس از commit             | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `GET /api/v1/drafts` و alias وب با `HEAD` متناظر                                        | شرط همگام‌سازی حساب، خواندن PostgreSQL و ارائهٔ گیرنده                | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `DELETE /api/v1/drafts/{draft_id}` و alias وب                                          | حذف محدود به مالک و رویداد بعد از commit                              | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `POST /api/v1/drafts` و alias وب | اعتبارسنجی شکل بسته، دسترسی گیرنده، ثبت گروه و پیش‌نویس، رویداد مالک | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `PATCH /api/v1/drafts/{draft_id}` و alias وب | بررسی مالکیت و گیرنده، ویرایش اتمیک و رویداد مالک | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/realm/linkifiers` و alias وب با `HEAD` متناظر                              | خواندن لینک‌سازهای سازمان با ترتیب اولویت از PostgreSQL              | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `GET /api/v1/realm/domains` و alias وب با `HEAD` متناظر                                 | خواندن دامنه‌های مجاز ایمیل سازمان از PostgreSQL                     | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی    | Django               |
| `GET /api/v1/user_groups` و alias وب با `HEAD` متناظر | فهرست گروه‌های سازمان با مجوز غیرمهمان، Basic/نشست، سهمیه و فعالیت | Rust مستقیم و nginx آزمایشی با پایلوت خاموشِ پیش‌فرض | Django |
| `GET /api/v1/user_groups/{user_group_id}/members` و `GET /api/v1/user_groups/{user_group_id}/subgroups` و aliasهای وب | اعضا یا زیرگروه‌های مستقیم و بازگشتی با همان مجوز غیرمهمان، سهمیه و فعالیت | Rust مستقیم و nginx آزمایشی با پایلوت جداگانه | Django |
| `GET /api/v1/realm/profile_fields` و alias وب با `HEAD` متناظر | تعاریف مرتب‌شدهٔ فیلدهای پروفایل سازمان از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/navigation_views` و alias وب با `HEAD` متناظر | تنظیمات شخصی نماهای ناوبری از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `POST /api/v1/navigation_views` و alias وب | ثبت نما و ممیزی در PostgreSQL و رویداد مالک | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `PATCH` و `DELETE /api/v1/navigation_views/{fragment}` و alias وب | ویرایش یا حذف نما، ممیزی و رویداد مالک | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/attachments` و alias وب با `HEAD` متناظر | فایل‌های مالک، پیام‌های مرتبط و مصرف فضای سازمان | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/realm/emoji` و alias وب با `HEAD` متناظر | ایموجی سازمان و نشانی عمومی فایل و تصویر ثابت | Rust مستقیم با تنظیم ریشهٔ نشانی یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/channel_folders` و alias وب با `HEAD` متناظر | پوشه‌های سازمان با فیلتر بایگانی و ترتیب نمایش از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `POST /api/v1/scheduled_messages` و `POST /api/v1/reminders` و aliasهای وب | درج گروهی رکوردهای معتبر، در تراکنش Django | Django با افزونهٔ اختیاری Rust | Django |
| `PATCH /api/v1/scheduled_messages/{scheduled_message_id}` و alias وب | ثبت تغییرات پس از قفل سطر و بررسی مجوز | Django با افزونهٔ اختیاری Rust | Django |
| worker تحویل پیام‌های زمان‌بندی‌شده و یادآوری‌ها | انتخاب و قفل کار آماده و ثبت نتیجه در PostgreSQL | پردازش Django با افزونهٔ اختیاری Rust | Django |
| `GET /api/v1/scheduled_messages` و alias وب با `HEAD` متناظر | پیام‌های زمان‌بندی‌شدهٔ ارسال‌نشدهٔ کاربر از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `GET /api/v1/reminders` و alias وب با `HEAD` متناظر | یادآوری‌های تحویل‌نشدهٔ کاربر از PostgreSQL | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `DELETE /api/v1/scheduled_messages/{scheduled_message_id}` و alias وب | حذف پیام زمان‌بندی‌شدهٔ مالک و پیوندهای پیوست در یک تراکنش | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `DELETE /api/v1/reminders/{reminder_id}` و alias وب | حذف یادآوری مالک و پیوندهای پیوست در یک تراکنش | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `POST /api/v1/users/me/status`، `GET/POST /api/v1/users/{user_id}/status` و aliasهای وب | ساخت بخشی از پاسخ و قواعد به‌روزرسانی با `ZULIP_RUST_USER_STATUS`      | Django                                                          | Django               |
| `POST /api/v1/register` و alias وب | قالب‌بندی وضعیت کاربران با `ZULIP_RUST_USER_STATUS` و پیش‌نویس‌ها با `ZULIP_RUST_DRAFT_SERIALIZATION` | Django | Django |
| `GET /api/v1/bot_storage` و alias وب با `HEAD` متناظر | خواندن همهٔ کلیدها یا کلیدهای درخواست‌شده برای ربات احرازشده | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `DELETE /api/v1/bot_storage` و alias وب | حذف اتمیک کلیدهای ربات احرازشده با `ZULIP_RUST_BOT_STORAGE_DELETES` | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `PUT /api/v1/bot_storage` و alias وب | درج یا به‌روزرسانی گروهی با سهمیهٔ اتمیک و پرچم `ZULIP_RUST_BOT_STORAGE_UPDATES` | Rust مستقیم یا هدایت اختیاری در پروکسی توسعه و nginx آزمایشی | Django |
| `POST /api/v1/users/me/subscriptions` و `POST /api/v1/channels/create` و aliasهای وب    | انتخاب رنگ هنگام ایجاد اشتراک با `ZULIP_RUST_STREAM_COLOR`             | Django                                                          | Django               |
| `GET /compatibility` و `HEAD /compatibility`                                            | handler مستقل HTTP                                                     | Rust مستقیم یا از پروکسی با `--rust-http-port` برای میزبان اصلی | Django               |

پرچم‌ها با مقدار `1` فعال‌اند. این جدول مسیرهای مرتبط با HTTP را ثبت می‌کند.
برای محل پیاده‌سازی و تست‌های هر مؤلفه به [مستند تحویل](MIGRATION_HANDOFF.md) رجوع کنید.

## قرارداد ایجاد پیش‌نویس

listener مستقل با `ZULIP_RUST_DRAFT_POSTS=1` هر دو مسیر ایجاد را
می‌پذیرد. پس از احراز هویت و بررسی همگام‌سازی، Rust شکل کل بسته را
قبل از اثر جانبی بررسی می‌کند. قواعد گیرنده و ثبت گروه با افزونهٔ
Django مشترک‌اند. ساخت گروه تراکنش جداگانه دارد. درج پیش‌نویس‌ها و
ساخت دادهٔ رویداد یک تراکنش دارند و انتشار `drafts/add` فقط برای
مالک و پس از commit انجام می‌شود. [راه‌اندازی](README.md#create-drafts-through-rust)

`POST /api/v1/drafts` از Basic Auth و `POST /json/drafts` از نشست و CSRF
استفاده می‌کند. Django تنظیم همگام‌سازی را بررسی می‌کند. با
`ZULIP_RUST_DRAFT_DIRECT_ACCESS=1`، Rust ابتدا شناسه و سازمان، سپس
دسترسی مهمان و در پایان فعال بودن گیرندگان پیام مستقیم را بررسی می‌کند.
فهرست ربات‌های میان‌سازمانی از تنظیمات Django می‌آید. با
`ZULIP_RUST_DRAFT_CHANNEL_ACCESS=1`، Rust گیرندهٔ
کانال را با همان اتصال PostgreSQL بررسی می‌کند. با `ZULIP_RUST_DRAFT_INPUT=1`
بررسی شکل بسته به Rust سپرده می‌شود. Rust نوع و مسیر نخستین خطای شکل
را به قالب‌ساز فعلی Django می‌دهد. Rust آخرین مقدار فیلد تکراری و
دقت اعداد صحیح بزرگ را حفظ می‌کند. خطای خواندن JSON و ورودی نزدیک
سقف طول عدد صحیح به Pydantic سپرده می‌شود. با
`ZULIP_RUST_DRAFT_TIMESTAMP=1`،
Rust زمان هر پیش‌نویس را با دقت میکروثانیه گرد می‌کند. در حالت پرچم
`ZULIP_RUST_DRAFT_CREATION=1`، Rust پیش‌نویس‌های بررسی‌شده را با cursor
همان تراکنش در `zerver_draft` درج می‌کند و شناسه‌ها را به ترتیب درخواست
برمی‌گرداند. Django رویداد `drafts/add` را پس از commit فقط برای مالک
ارسال می‌کند. فهرست خالی باعث درج سطر نمی‌شود و رویداد خالی مبنا را حفظ
می‌کند. تست‌های ثبت، بازگشت تراکنش و رویداد در
[test_drafts.py](../zerver/tests/test_drafts.py) و
[DraftActionTest](../zerver/tests/test_events.py) هستند.

در ایجاد و ویرایش، `ZULIP_RUST_DRAFT_GROUPS=1` جست‌وجو یا ایجاد گروه پیام
مستقیم، گیرنده و عضویت‌های آن را به Rust می‌سپارد. بررسی دسترسی پیش از
ایجاد گروه و عضویت انجام می‌شود. Rust هش اعضای متمایز، شامل فرستنده، و
قید یکتایی سطر گروه را با نویسندهٔ Django مشترک دارد. تراکنش ساخت گروه
پیش از تراکنش درج پیش‌نویس است. شکست اعتبارسنجی شکل کل بسته پیش از
ایجاد گروه رخ می‌دهد. خطای معنایی در پیش‌نویس بعدی می‌تواند گروه قبلی
را باقی بگذارد و در آن حالت سطر یا رویداد پیش‌نویس ایجاد نمی‌شود.

با `ZULIP_RUST_DRAFT_SERIALIZATION=1`، قالب پیش‌نویس در پاسخ فهرست، وضعیت
اولیه و رویدادهای Django از همان کد Rust استفاده می‌کند که خواندن مستقل
به کار می‌برد. Django اعضای پیام مستقیم را از کش فعلی می‌گیرد.

## قرارداد ویرایش پیش‌نویس

listener مستقل با `ZULIP_RUST_DRAFT_PATCHES=1` هر دو مسیر ویرایش را
می‌پذیرد. پس از احراز هویت، CSRF مسیر وب و بررسی همگام‌سازی، Rust ابتدا
شکل ورودی و سپس مالکیت پیش‌نویس را بررسی می‌کند. اعتبارسنجی معنایی و ساخت
گروه پس از تأیید مالکیت انجام می‌شوند. به‌روزرسانی و ساخت رویداد در یک
تراکنش‌اند و انتشار `drafts/update` پس از commit فقط برای مالک است.
[راه‌اندازی و بازگشت](README.md#edit-drafts-through-rust)

در مسیر Django، `PATCH` همگام‌سازی و مالکیت پیش‌نویس را در Django بررسی می‌کند.
پرچم‌های شکل ورودی و دسترسی به گیرندهٔ کانال و پیام مستقیم برای ایجاد
و ویرایش مشترک‌اند. با `ZULIP_RUST_DRAFT_EDIT=1`، به‌روزرسانی متن،
عنوان، گیرنده و زمان در Rust با شرط شناسه و مالک انجام می‌شود. Django
از cursor همان تراکنش استفاده می‌کند و `drafts/update` را فقط پس از
commit به مالک می‌فرستد. اگر ساخت رویداد خطا بدهد، ویرایش لغو می‌شود.
[تست ویرایش](../zerver/tests/test_drafts.py) و
[فعال‌سازی](README.md#run-the-compatibility-checks)

## قرارداد فضای ذخیره‌سازی ربات

`GET`، `DELETE` و `PUT` برای `/api/v1/bot_storage` و `/json/bot_storage` فقط برای
ربات احرازشده اجرا می‌شوند. احراز هویت API با Basic Auth و احراز هویت وب
با نشست Django است. حذف و نوشتن وب CSRF را بررسی می‌کنند. هر سه عملیات
سهمیهٔ `api_by_user` و ثبت فعالیت `get_storage`، `remove_storage` یا
`update_storage` را دارند.

- خواندن رکوردهای `zerver_botstoragedata` را به مالک محدود می‌کند.
  حذف پارامتر `keys` یا مقدار JSON `null` به معنی خواندن همهٔ کلیدهاست.
  تکرار کلید در خواندن یک مقدار برمی‌گرداند. کلید ناموجود پاسخ `400` دارد.
- حذف بدون `keys` یا با `null` همهٔ رکوردهای مالک را پاک می‌کند. برای
  فهرست صریح، Rust سطرهای منطبق را قفل و حذف می‌کند، به شرطی که همهٔ
  کلیدها موجود و متمایز باشند. فهرست خالی اثری روی داده ندارد. حذف
  کش یا رویداد همگام‌سازی ندارد.
- `PUT` دیکشنری `storage` با کلید و مقدار رشته‌ای را می‌گیرد. اندازهٔ
  نهایی فضای ربات برحسب نویسه‌های Unicode بعد از جایگزینی محاسبه می‌شود.
  بررسی سهمیه و upsert گروهی در یک تراکنش‌اند. Django و Rust برای نوشتن
  و حذف از قفل تراکنشی PostgreSQL برای همان ربات استفاده می‌کنند. این
  عملیات کش یا رویداد همگام‌سازی ندارد. تغییر مسیر پروکسی فقط روی `PUT`
  مستقیم اعمال می‌شود.

[تست مقایسه](../zerver/tests/test_embedded_bot_system.py)،
[فعال‌سازی و بازگشت](README.md#read-bot-storage-through-rust)

## قرارداد واژه‌های هشدار

`GET`، `POST` و `DELETE` برای هر دو مسیر `/api/v1/users/me/alert_words`
و `/json/users/me/alert_words` به view مشترک می‌رسند. API از Basic Auth
با ایمیل و کلید API و alias وب از نشست Django استفاده می‌کند. درخواست وب
مشمول CSRF است. هر سه عملیات به کاربر احرازشده محدودند. اعتبار حساب، سازمان
و میزبان بررسی می‌شود و ربات webhook ورودی مجوز این عملیات را ندارد.
هر دو مسیر از محدودیت نرخ `api_by_user` استفاده می‌کنند. احراز هویت،
ثبت فعالیت کلاینت و محدودیت نرخ هم جزو رفتار مسیر خواندنی‌اند.
[مسیریابی REST](../zerver/lib/rest.py)، [احراز هویت و کلاینت](../zerver/decorator.py)،
[محدودیت نرخ](../zerver/lib/rate_limiter.py)

| عملیات         | داده و تغییرات پایگاه داده                                                                                                                             | کش و رویداد                                                                                                          | تست مرجع                                                                                                                |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `GET` و `HEAD` | خواندن `word` از `zerver_alertword` با شناسهٔ کاربر احرازشده. پاسخ `GET` آرایهٔ `alert_words` را با املای ذخیره‌شده و بدون مرتب‌سازی صریح برمی‌گرداند. | خود view کش واژه‌های سازمان را استفاده نمی‌کند و رویداد واژهٔ هشدار منتشر نمی‌کند.                                   | `test_json_list_default` و `test_json_list_nonempty`                                                                    |
| `POST`         | اعتبارسنجی طول حداکثر ۱۰۰ نویسه پیش از پاک‌سازی، حذف فاصله‌های دو سر و رشته‌های خالی، حذف تکرار بدون حساسیت به بزرگی حروف و درج گروهی واژه‌های جدید.   | پاک‌کردن کش فهرست واژه‌ها و automaton تطبیق سازمان. رویداد `alert_words` بعد از commit برای همان کاربر ارسال می‌شود. | `test_json_list_add`، `test_api_add_normalizes_and_deduplicates_alert_words`، `test_basics` و `test_alert_words_events` |
| `DELETE`       | مقایسهٔ ورودی lowercase پایتون با `Lower("word")` در PostgreSQL و حذف گروهی رکوردهای همان کاربر.                                                       | پاک‌کردن همان دو کش و ارسال رویداد `alert_words` برای همان کاربر بعد از commit.                                      | `test_json_list_remove`، `test_remove_words_bulk_case_insensitive`، `test_basics` و `test_alert_words_events`           |

منابع قرارداد و تست‌ها
[view](../zerver/views/alert_words.py)، [دسترسی داده](../zerver/lib/alert_words.py)،
[مدل و کش](../zerver/models/alert_words.py)، [تراکنش و رویداد](../zerver/actions/alert_words.py)،
[تست واژه‌ها](../zerver/tests/test_alert_words.py) و [تست رویدادها](../zerver/tests/test_events.py).

با `ZULIP_RUST_ALERT_WORDS=1`، Rust کوئری پارامتری حذف را روی cursor
تراکنش Django اجرا می‌کند. ورودی با `str.lower()` پایتون و مقدار ذخیره‌شده با
`lower` PostgreSQL مقایسه می‌شود. Django دو کش سازمان را با همان سازوکار
فضای نام استقرار پاک می‌کند و انتشار رویداد را به commit موکول می‌کند.
درخواست با فهرست خالی همچنان پاسخ و رویداد فهرست فعلی را می‌دهد.
[راهنمای پرچم](README.md)، [تست حذف](../zerver/tests/test_alert_words.py)

با `ZULIP_RUST_ALERT_WORD_CACHE=1`، Django پیشوندهای فعال و کلید نهایی
هر دو کش را محاسبه می‌کند و Rust آن‌ها را از Memcached محلی حذف می‌کند.
این پرچم مستقل از حذف گروهی است و برای یک Memcached محلی بدون احراز هویت
فعال می‌شود. خطای اتصال یا پاسخ نامعتبر به تراکنش برمی‌گردد.
[راهنمای پرچم کش](README.md#invalidate-alert-word-caches-through-rust)

فرمان `export_rust_alert_word_cache` از تنظیمات Django پیشوندهای فیزیکی
و آدرس Memcached را در یک فایل منتشر می‌کند. در صورت تنظیم
`ZULIP_RUST_ALERT_WORD_CACHE_MANIFEST`، بررسی آمادگی Rust فایل جاری و
اتصال Memcached را اعتبارسنجی می‌کند.
[راهنمای انتشار فایل](README.md#share-djangos-alert-word-cache-namespace-with-zulip-http)

با `ZULIP_RUST_ALERT_WORD_LOWERCASE_FILE`، سرویس مستقیم Rust آرایهٔ JSON رشته‌ها
را از فرم یا query می‌گیرد و با قواعد صادرشده از Python همان استقرار کوچک
می‌کند. حذف و پاک‌سازی کش در تراکنش اختصاصی انجام می‌شوند و پس از commit
رویداد `alert_words` فقط برای همان کاربر به صف `notify_tornado` می‌رود.
نسخهٔ وب از نشست و هدر `X-CSRFToken` استفاده می‌کند. در استقرار چندفرایندی،
فایل مسیر صف را مطابق تقسیم‌بندی Django مشخص می‌کند. هدایت اختیاری و بازگشت
مستقل حذف در پروکسی توسعه و nginx آزمایشی تأیید شدند. Tornado در آزمون
ایزوله رویداد را فقط به صاحب واژه تحویل داد.
[تنظیم و تست مسیر مستقیم](README.md#delete-alert-words-through-rust)

با `ZULIP_RUST_ALERT_WORD_POSTS=1`، Rust مسیر افزودن API و وب را هم سرو می‌کند.
این مسیر پیش از trim طول ورودی را می‌سنجد، تکرارها را با قواعد Python حذف
می‌کند و پس از درج، همان کش و رویداد `alert_words` را به‌روزرسانی می‌کند.
در وب، توکن فرم POST بر هدر مقدم است. بازگشت مستقل هدایت POST در پروکسی‌ها
و تحویل رویداد در Tornado آزمایشی تأیید شده‌اند.
[تنظیم و تست POST](README.md#add-alert-words-through-rust)

تست HTTP با دادهٔ commit‌شده در Django و Rust، رفتار فهرست خالی، واژهٔ Unicode،
کلید نامعتبر، سازمان نادرست، حساب غیرفعال و ربات webhook را برای مسیر API
بررسی کرد. در حالت بدون اعتبارنامه،
`Accept` بر انتخاب پاسخ JSON یا هدایت به صفحهٔ ورود اثر دارد و هدر
`WWW-Authenticate` پاسخ JSON با پیشوند مسیر عوض می‌شود. تستِ سهمیه در Redis مشترک و ثبت فعالیت در RabbitMQ برای مسیر API پاس شد.
تست نشست وب، اعتبار امضا و هش نشست، هدرهای کوکی و رفتار CSRF برای `GET` و `HEAD` پاس شد. `OPTIONS` در Django است.
[نتیجهٔ مقایسهٔ HTTP](MIGRATION_HANDOFF.md#برشهای-قبلی-http)
[خطاهای API](../api_docs/rest-error-handling.md)، [middleware](../zerver/middleware.py)

## قرارداد پیش‌نویس‌ها

`GET /api/v1/drafts` و `GET /json/drafts` فقط پیش‌نویس‌های کاربر احرازشده را
از `zerver_draft` برمی‌گردانند. خاموش بودن همگام‌سازی پاسخ `400` دارد.
`count` از تعداد پیش‌نویس‌های برگشتی تشکیل می‌شود. ترتیب SQL Django صعودی
برحسب زمان آخرین ویرایش است. کانال با شناسهٔ کانال و پیام مستقیم با شناسهٔ
عضوها برمی‌گردد. گیرندهٔ ناموجود به `type: ""` و `to: []` تبدیل می‌شود.
`HEAD` هدرها را بدون بدنه برمی‌گرداند. خواندن، رویداد پیش‌نویس منتشر نمی‌کند.
خواندن و حذف با هدایت اختیاری و بازگشت در پروکسی توسعه و nginx آزمایشی
تأیید شده‌اند. `DELETE` پس از بررسی همگام‌سازی فقط ردیف مالک را حذف می‌کند
و `drafts/remove` را پس از commit می‌فرستد. ایجاد و ویرایش پرچم‌های مستقل دارند.
[view](../zerver/views/drafts.py)، [پیاده‌سازی Rust](src/http/drafts.rs)،
[تست مقایسه](../zerver/tests/test_drafts.py)

## قرارداد لینک‌سازهای سازمان

`GET /api/v1/realm/linkifiers` و `GET /json/realm/linkifiers` لینک‌سازهای
سازمان حساب احرازشده را به ترتیب فیلد `order` برمی‌گردانند. Rust
`zerver_realmfilter` را مستقیم می‌خواند و قالب‌های جایگزین و فیلدهای
اختیاری را حفظ می‌کند. سهمیه و فعالیت از پیکربندی مشترک استفاده می‌کنند.
ایجاد، ویرایش، تغییر ترتیب و حذف لینک‌ساز در Django انجام می‌شوند.
[پیاده‌سازی](src/http/linkifiers.rs)، [تست مقایسه](../zerver/tests/test_realm_linkifiers.py)

## قرارداد دامنه‌های مجاز سازمان

`GET /api/v1/realm/domains` و `GET /json/realm/domains` مقدارهای `domain`
و `allow_subdomains` را برای سازمان حساب احرازشده برمی‌گردانند. Rust
`zerver_realmdomain` را از PostgreSQL می‌خواند. مانند کوئری Django، ترتیب
صریحی تعریف نشده است. سهمیه و فعالیت از پیکربندی مشترک استفاده می‌کنند.
عملیات نوشتن در Django انجام می‌شوند.
[پیاده‌سازی](src/http/realm_domains.rs)، [تست مقایسه](../zerver/tests/test_realm_domains.py)

## قرارداد فیلدهای سفارشی پروفایل سازمان

`GET /api/v1/realm/profile_fields` و `GET /json/realm/profile_fields`
تعاریف سازمان حساب احرازشده را بر اساس `order` از
`zerver_customprofilefield` برمی‌گردانند. `field_data` رشتهٔ ذخیره‌شده
است و دو کلید `display_in_profile_summary` و `use_for_user_matching`
فقط در حالت true می‌آیند. مقادیر شخصی در جدول جداگانه نگه‌داری می‌شوند.
[پیاده‌سازی](src/http/custom_profile_fields.rs)،
[تست مقایسه](../zerver/tests/test_custom_profile_data.py)

## قرارداد نماهای ناوبری

`GET /api/v1/navigation_views` و `GET /json/navigation_views` بر اساس
شناسهٔ کاربر احرازشده از `zerver_navigationview` می‌خوانند. هر نما
`fragment`، `is_pinned` و `name` دارد. نام نمای داخلی می‌تواند
`null` باشد. ترتیب صریحی در قرارداد Django تعریف نشده است.
[پیاده‌سازی](src/http/navigation_views.rs)،
[تست مقایسه](../zerver/tests/test_navigation_views.py)

ایجاد نما در `POST /api/v1/navigation_views` و مسیر وب به‌صورت اختیاری
در Rust اجرا می‌شود. `fragment` و نام کوتاه‌شده از فاصله‌های ابتدا و انتها پس از
اعتبارسنجی ذخیره می‌شوند. نمای داخلی نام سفارشی ندارد. ثبت نما و audit
با نوع `NAVIGATION_VIEW_CREATED` و دادهٔ `fragment` در یک تراکنش انجام
می‌شوند. پس از commit رویداد `navigation_view/add` فقط به مالک می‌رسد.
قید پایگاه داده تکرار `(user, fragment)` را محدود می‌کند. Rust نوشتن هم‌زمان برای یک کاربر را پیش از بررسی نام تکراری قفل می‌کند.
Django نام تکراری را در view بررسی می‌کند و رقابت بین دو پیاده‌سازی
با محدودیت یکتایی نام در پایگاه داده پوشش داده نمی‌شود.
[view](../zerver/views/navigation_views.py)،
[تراکنش و رویداد](../zerver/actions/navigation_views.py)

`PATCH` فیلدهای اختیاری `name` و `is_pinned` را می‌پذیرد و برای هر
فیلد ارسال‌شده یک audit با مقدار قبلی و جدید ثبت می‌کند. درخواست
خالی رویداد `update` با `data` خالی می‌فرستد. `DELETE` نما و audit
را در یک تراکنش تغییر می‌دهد و سپس رویداد `remove` می‌فرستد. هر دو
روش فقط به fragment مالک دسترسی دارند. رقابت دو حذف از دو فرایند Rust
فقط یک audit و یک رویداد ایجاد کرد. هدایت مسیر جزئیات مستقل از مسیر
فهرست است.
[راهنمای مسیرهای جزئیات](README.md#edit-and-delete-navigation-views-through-rust)

## قرارداد فایل‌های آپلودشده

`GET /api/v1/attachments` و `GET /json/attachments` فایل‌های مالک
و `message_ids` مرتبط را برمی‌گردانند. مصرف فضای سازمان از آخرین
`upload_quota_used_bytes::day` بدون زیرگروه و مجموع اندازهٔ فایل‌های
پس از مرز آن، با احتساب خود مرز، محاسبه می‌شود. بدون snapshot،
مجموع اندازهٔ همهٔ فایل‌های سازمان استفاده می‌شود. Rust خواندن را
از یک snapshot در PostgreSQL انجام می‌دهد.
[پیاده‌سازی](src/http/attachments.rs)،
[تست مقایسه](../zerver/tests/test_attachments.py)

## قرارداد ایموجی‌های سفارشی

`GET /api/v1/realm/emoji` و `GET /json/realm/emoji` فقط ایموجی‌های
سازمان حساب احرازشده با `file_name` ثبت‌شده را نمایش می‌دهند. پاسخ هم
رکورد فعال و هم غیرفعال را شامل می‌شود. نشانی دانلود و تصویر ثابت
از ریشهٔ عمومی ذخیره‌سازی Django ساخته می‌شود. هر دو حالت ذخیره‌سازی
محلی و S3 با CDN در آزمون مقایسه پوشش داده شدند.
[پیاده‌سازی](src/http/realm_emoji.rs)،
[تست مقایسه](../zerver/tests/test_realm_emoji.py)

## قرارداد پوشه‌های کانال

`GET /api/v1/channel_folders` و `GET /json/channel_folders` پوشه‌های
سازمان حساب احرازشده را از `zerver_channelfolder` بر اساس `order`
برمی‌گردانند. `include_archived` به‌طور پیش‌فرض false است و فقط یک
مقدار بولی JSON می‌پذیرد. آخرین مقدار پارامتر تکراری اعمال می‌شود.
`ignored_parameters_unsupported` و خطاهای نوع و نحو JSON با Django
مقایسه شده‌اند. پاسخ HTML ذخیره‌شدهٔ توضیح، شناسهٔ اختیاری سازنده و
ثانیهٔ زمان ایجاد را حفظ می‌کند.
[پیاده‌سازی](src/http/channel_folders.rs)،
[تست مقایسه](../zerver/tests/test_channel_folders.py)

## قرارداد پیام‌های زمان‌بندی‌شده و یادآوری‌ها

`GET /api/v1/scheduled_messages` و `GET /api/v1/reminders` و مسیرهای
متناظر وب فقط رکوردهای تحویل‌نشدهٔ نوع درخواست‌شده را برای کاربر و
سازمان احرازشده از `zerver_scheduledmessage` بر اساس زمان ارسال
برمی‌گردانند. موارد ناموفق در فهرست می‌مانند. پیام کانال شناسهٔ کانال
و عنوان دارد. پیام مستقیم فهرست گیرنده‌ها را از جدول عضویت می‌سازد و
در پیام به خود، شناسهٔ مالک را نگه می‌دارد. یادآوری شناسهٔ پیام مرجع
را برمی‌گرداند.

`DELETE` در هر دو خانواده فقط ردیف هم‌نوع متعلق به فرستنده را حذف
می‌کند و برای شناسهٔ ناموجود، متعلق به کاربر دیگر یا نوع دیگر `404`
می‌دهد. پیوندهای همان ردیف در `zerver_attachment_scheduled_messages`
در همان تراکنش پاک می‌شوند. رکورد پیوست و پیوندهای دیگر آن حفظ می‌شوند.
مسیر وب نشست و CSRF را بررسی می‌کند. پس از commit یک رویداد
`scheduled_messages/remove` یا `reminders/remove` به مالک ارسال می‌شود.
فعال‌سازی و بازگشت هر مسیر مستقل است. Django درخواست‌های `POST` و
`PATCH` را احراز هویت و اعتبارسنجی می‌کند و Markdown و پیوست‌ها را
پردازش می‌کند. `ZULIP_RUST_SCHEDULED_JOB_INPUT=1` خواندن شناسه، زمان،
گیرنده و مقدار بولی را برای JSON معتبر به Rust می‌سپارد. قالب پاسخ
خطا و اعداد خارج از بازهٔ ۶۴بیتی در Pydantic می‌مانند.
[پرچم ورودی](README.md#parse-scheduled-job-input-through-rust)

پس از تجزیهٔ JSON، پل Rust می‌تواند زمان تحویل ایجاد و ویرایش پیام
زمان‌بندی‌شده و ایجاد یادآوری را با دقت میکروثانیه بررسی کند. شکل
گیرنده در `POST` پیام زمان‌بندی‌شده بعد از زمان تحویل بررسی می‌شود.
در `PATCH` ابتدا ردیف متعلق به مالک قفل می‌شود و سپس شکل گیرنده
با همان اعتبارسنج بررسی می‌شود.
Django ترتیب حذف شناسه‌های تکراری، کنترل مجوز، ترجمهٔ خطاها و
پردازش Markdown را مدیریت می‌کند. برش بدنه، عادی‌سازی عنوان موضوع
و حصار و محدودیت طول نقل‌قول یادآوری پرچم‌های جداگانه دارند.
قالب فیلدهای فهرست و رویدادهای افزودن و ویرایش Django با پرچم
اختیاری از نوع‌های پاسخ HTTP مستقل Rust استفاده می‌کند.
یافتن یا ساخت گروه پیام مستقیم و عضویت‌های آن می‌تواند از همان
پیاده‌سازی Rust پیش‌نویس‌ها استفاده کند. پیش‌فرض `read_by_sender` بر
اساس نوع کلاینت هم پرچم جداگانه دارد.
[پرچم‌های اعتبارسنجی و متن](README.md#validate-scheduled-jobs-and-prepare-message-text-through-rust)

در ایجاد، ویرایش و بررسی دوباره هنگام تحویل، پرچم
`ZULIP_RUST_SCHEDULED_SEND_PERMISSIONS` خواندن گروه‌های تودرتو، وجود
موضوع، اشتراک مشترک برای مهمان و سابقهٔ پیام‌های ارسال‌شده را به Rust
واگذار می‌کند. ارزیابی نقش گروه‌های سیستمی پیام مستقیم، سیاست موضوع
کانال و تشخیص کانال خالی ربات هم از همین پرچم استفاده می‌کنند. Django
ترتیب کنترل‌ها، ترجمهٔ خطاها و اعلان محدودشدهٔ مالک ربات را حفظ می‌کند.
`ZULIP_RUST_REMINDER_ACCESS` دریافت پیام و دسترسی به محتوای کانال محدود
را هنگام ایجاد و تحویل یادآوری از همان اتصال پایگاه داده بررسی می‌کند.
اگر دسترسی از بین برود، worker متن ذخیره‌شده را می‌فرستد.
`ZULIP_RUST_SCHEDULED_ATTACHMENTS` پیوند پیوست‌های مجاز را در تراکنش
ایجاد یا ویرایش ثبت یا حذف می‌کند. `ZULIP_RUST_MARKDOWN_UPLOAD_PATHS`
شناسهٔ پیوست را از شکل‌های رایج پیوند محلی استخراج می‌کند.
`ZULIP_RUST_SCHEDULED_ATTACHMENT_ACCESS` پیش از اتصال فایل، دریافت
پیام مستقیم، اشتراک فعال، سابقهٔ خصوصی و گروه‌های تودرتوی دسترسی
محتوا را بررسی می‌کند. بازسازی کش دیدپذیری در همان تراکنش انجام
می‌شود. پرچم مجوز پیوست از پرچم ثبت پیوند مستقل است. Django رندر
Markdown، کنترل مالکیت و کش عمومی و تغییر دیدپذیری هنگام تحویل را
مدیریت می‌کند. `ZULIP_RUST_URL_FRAGMENTS` کدگذاری بخش‌های پیوند کانال
و موضوع و مرتب‌سازی شناسه‌های پیام مستقیم را برای ساخت پیوند یادآوری
انجام می‌دهد. با `ZULIP_RUST_REMINDER_CONTEXT`، اعتبارسنجی یادداشت،
تشخیص محتوای widget، قالب پیوند موضوع و ترتیب mentionهای بی‌اعلان
در Rust انجام می‌شوند؛ ترجمه و بازیابی دادهٔ زمینه در Django می‌مانند.
[پرچم‌های مجوز، یادآوری و پیوست](README.md#check-scheduled-send-permissions-and-resolve-upload-paths-through-rust)

داده‌های آماده‌سازی Markdown پرچم‌های مستقل دارند: تطبیق واژه‌های
هشدار، استخراج نام کانال و موضوع، استخراج mentionهای کاربر و گروه،
خواندن اعضای فعال گروه‌های تودرتو، تجزیهٔ حصار Markdown و انتخاب
لنگر موضوع. تجزیهٔ حصار بین پیش‌پردازش بلوک و فهرست و بستن
نقل‌قول مشترک است. پرچم‌های `ZULIP_RUST_MARKDOWN_LISTS`،
`ZULIP_RUST_MARKDOWN_BLOCKS` و `ZULIP_RUST_MARKDOWN_WHITESPACE`
جداکننده‌های فهرست، nesting حصار و نرمال‌سازی فاصله و پایان خط را
در Rust انجام می‌دهند. ارث‌بری زبان در نقل‌قول، reset در spoiler و
پشتهٔ باز برای تکمیل نقل‌قول حفظ می‌شوند. پرچم
`ZULIP_RUST_MARKDOWN_QUOTE_MENTIONS` ذکرهای داخل نقل‌قول را بی‌اعلان
می‌کند و `ZULIP_RUST_MARKDOWN_INLINE_MENTIONS` متن، ویژگی‌های span
و دادهٔ اعلان را برای گیرندگانِ از قبل حل‌شده می‌سازد. دیدپذیری کاربر،
تطبیق نام و شناسه، نام ترجمه‌شدهٔ گروه سیستمی و escape نهایی در
Python هستند. برای سابقهٔ
خصوصی، ترتیب رسیدهای کاربر حفظ می‌شود؛ متن ناشناس لنگر پیام را
نمی‌بیند. رندر HTML، کنترل مجوز اعلان گروه و wildcard و پیش‌نمایش‌ها
هنوز در Django هستند. این پل‌ها عملیات HTTP مستقل جدیدی اضافه
نمی‌کنند. [پرچم‌های Markdown](README.md#prepare-reminder-context-and-markdown-mention-data-through-rust)

پل PyO3 می‌تواند درج رکورد و به‌روزرسانی پیش از تحویل را
در تراکنش Django انجام دهد. worker می‌تواند سطر آماده را به ترتیب
شناسه قفل کند و نتیجهٔ ارسال را در همان تراکنش با Rust ثبت کند.
[پرچم‌های عملیات پایگاه داده](README.md#run-scheduled-job-database-operations-through-rust)

[پیاده‌سازی](src/http/scheduled_messages.rs)،
[تست مقایسه](../zerver/tests/test_scheduled_messages.py)،
[فعال‌سازی و بازگشت](README.md#delete-scheduled-messages-and-reminders-through-rust)

## قرارداد متن‌های ذخیره‌شده

`GET /api/v1/saved_snippets` با Basic Auth فقط متن‌های کاربر احرازشده
را از `zerver_savedsnippet` برمی‌گرداند. نتیجه شامل `id`، `title`،
`content` و `date_created` برحسب ثانیهٔ صحیح UTC است. ترتیب صریحی
در کوئری Django و Rust وجود ندارد. view خواندنی دادهٔ متن‌ها را تغییر
نمی‌دهد، کش متن‌ها را استفاده نمی‌کند و رویداد `saved_snippets` منتشر نمی‌کند.
در صورت پیکربندی Redis و RabbitMQ، ثبت فعالیت با نام `get_saved_snippets`
پیش از بررسی سهمیهٔ `api_by_user` انجام می‌شود. کلاینت `internal` با
IP واقعی `127.0.0.1` یا `::1` معاف است. تنظیم debug در Django و معادل
آن در Rust همین معافیت را برای IP غیرمحلی هم فعال می‌کند.
[view](../zerver/views/saved_snippets.py)، [مدل](../zerver/models/saved_snippets.py)،
[تست HTTP](../zerver/tests/test_saved_snippets.py)

`HEAD` همان هدرها و بدنهٔ خالی دارد. Rust این دو روش را مستقیم یا از
پروکسی توسعه و nginx آزمایشی سرو می‌کند.

`POST` در هر دو پیاده‌سازی عنوان و محتوای فرم را اعتبارسنجی می‌کند و
ردیف `zerver_savedsnippet` را همراه با `zerver_realmauditlog` و نوع
`SAVED_SNIPPET_CREATED` در یک تراکنش ثبت می‌کند. پاسخ شناسهٔ تازهٔ
`saved_snippet_id` را می‌دهد. رویداد `saved_snippets/add` پس از commit
فقط برای صاحب متن به صف `notify_tornado` فرستاده می‌شود. برش Rust برای
یک فرایند Tornado فعال شده است و تنظیمات نوشتن آن مستقل از خواندن است.
[تراکنش و رویداد Django](../zerver/actions/saved_snippets.py)، [راهنمای staging برای POST](README.md#enable-saved-snippet-creation)

`PATCH /api/v1/saved_snippets/{saved_snippet_id}` عنوان یا محتوا یا هر دو را
برای صاحب متن به‌روز می‌کند. تاریخ ایجاد و فیلدهای حذف‌شده از درخواست
حفظ می‌شوند. درخواست بدون عنوان و محتوا موفق است و تغییری در متن یا
رویداد ویرایش ایجاد نمی‌کند. با ورودی معتبر برای تغییر، متن ناموجود و
متن متعلق به کاربر دیگر هر دو `404` می‌گیرند. پس از commit در PostgreSQL،
رویداد `saved_snippets/update` با متن کامل فقط برای صاحب متن از Tornado
تحویل می‌شود. هدایت PATCH مستقل از GET و POST است.
[تست مقایسه](../zerver/tests/test_saved_snippets.py)، [راهنمای staging برای PATCH](README.md#enable-saved-snippet-editing)

`DELETE /api/v1/saved_snippets/{saved_snippet_id}` فقط ردیف صاحب متن را
حذف می‌کند و audit قبلی را نگه می‌دارد. پس از commit، رویداد
`saved_snippets/remove` شامل شناسهٔ متن فقط برای صاحب آن فرستاده می‌شود.
متن ناموجود یا متعلق به دیگری `404` می‌گیرد. هدایت DELETE در staging
با عوض کردن map مستقل از PATCH قابل برگشت است.
[راهنمای staging برای DELETE](README.md#enable-saved-snippet-deletion)

`GET /json/saved_snippets` و `HEAD` همان فهرست شخصی را با نشست Django
برمی‌گردانند. Rust از اعتبارسنج مشترک امضا، انقضا و هش نشست، رفتار CSRF
روش‌های امن، و سهمیهٔ وب قبل از ثبت فعالیت استفاده می‌کند. مسیریابی
خواندن وب مستقل از API است. Django مسیر `OPTIONS` را سرو می‌کند.
[راهنمای staging برای خواندن وب](README.md#enable-saved-snippet-web-reads)

`POST /json/saved_snippets` با نشست Django و اعتبارسنجی CSRF اجرا می‌شود.
بررسی CSRF پیش از سهمیه، ثبت فعالیت و نوشتن است و برای POST با
`method=GET` نیز انجام می‌شود. Rust از همان تراکنش ایجاد API برای ثبت متن
و audit استفاده می‌کند و بعد از commit رویداد `saved_snippets/add` را فقط
برای صاحب متن منتشر می‌کند. هدایت POST وب از خواندن وب و مسیر API مستقل
قابل برگشت است.
[راهنمای staging برای ایجاد متن در وب](README.md#enable-saved-snippet-web-creation)

`PATCH /json/saved_snippets/{saved_snippet_id}` با نشست Django و مقایسهٔ
کوکی CSRF با هدر `X-CSRFToken` اجرا می‌شود. بررسی نشست و CSRF پیش از سهمیه
و ثبت فعالیت است. ویرایش از همان عملیات PostgreSQL و اعلان API استفاده
می‌کند. کش متن‌ها در این عملیات تغییر نمی‌کند. هدایت PATCH وب مستقل
است و Django درخواست POST با override روش را سرو می‌کند.
[تست مقایسه](../zerver/tests/test_saved_snippets.py)،
[راهنمای staging برای ویرایش متن در وب](README.md#enable-saved-snippet-web-editing)

`DELETE /json/saved_snippets/{saved_snippet_id}` با همان نشست و بررسی
هدر CSRF اجرا می‌شود. منطق حذف API فقط ردیف صاحب متن را حذف می‌کند.
audit ایجاد حفظ می‌شود و رویداد `saved_snippets/remove` پس از commit فقط
برای صاحب متن منتشر می‌شود. کش متن‌ها در این عملیات تغییر نمی‌کند.
تغییر map در nginx می‌تواند فقط DELETE را به Django برگرداند.
[راهنمای staging برای حذف متن در وب](README.md#enable-saved-snippet-web-deletion)

## فهرست OpenAPI

«مستندات» در ستون مسیریابی به مدخل‌های نمونه اشاره می‌کند. «توسعه» یعنی مسیر
فقط در تنظیمات توسعه ثبت می‌شود. «push» یعنی مسیر با URLconf سرویس اعلان
ثبت می‌شود. «Rust اختیاری» یعنی آن عملیات با تنظیم پروکسی توسعه یا nginx آزمایشی به Rust می‌رود.

| روش      | مسیر                                                    | operationId                            | handler                                                                        | مسیریابی       |
| -------- | ------------------------------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------ | -------------- |
| `GET`    | `/api/v1/attachments`                                   | `get-attachments`                      | `zerver.views.attachments.list_by_user`                                        | Rust اختیاری |
| `DELETE` | `/api/v1/attachments/{attachment_id}`                   | `remove-attachment`                    | `zerver.views.attachments.remove`                                              | Django         |
| `DELETE` | `/api/v1/bot_storage`                                   | `remove-bot-storage`                   | `zerver.views.storage.remove_storage`                                          | Rust اختیاری |
| `GET`    | `/api/v1/bot_storage`                                   | `get-bot-storage`                      | `zerver.views.storage.get_storage`                                             | Rust اختیاری |
| `PUT`    | `/api/v1/bot_storage`                                   | `update-bot-storage`                   | `zerver.views.storage.update_storage`                                          | Rust اختیاری |
| `GET`    | `/api/v1/bots/{bot_id}/api_key`                         | `get-bot-api-key`                      | `zerver.views.users.get_bot_api_key`                                           | Django         |
| `POST`   | `/api/v1/bots/{bot_id}/api_key/regenerate`              | `regenerate-bot-api-key`               | `zerver.views.users.regenerate_bot_api_key`                                    | Django         |
| `GET`    | `/api/v1/calls/bigbluebutton/create`                    | `create-big-blue-button-video-call`    | `zerver.views.video_calls.get_bigbluebutton_url`                               | Django         |
| `POST`   | `/api/v1/calls/constructorgroups/create`                | `create-constructor-groups-video-call` | `zerver.views.video_calls.make_constructor_groups_video_call`                  | Django         |
| `POST`   | `/api/v1/calls/google_meet/create`                      | `create-google-meet-video-call`        | `zerver.views.video_calls.make_google_meet_video_call`                         | Django         |
| `POST`   | `/api/v1/calls/nextcloud_talk/create`                   | `create-nextcloud-talk-video-call`     | `zerver.views.video_calls.create_nextcloud_talk_url`                           | Django         |
| `POST`   | `/api/v1/calls/webex/create`                            | `create-webex-video-call`              | `zerver.views.video_calls.make_webex_video_call`                               | Django         |
| `GET`    | `/api/v1/channel_folders`                               | `get-channel-folders`                  | `zerver.views.channel_folders.get_channel_folders`                             | Rust اختیاری |
| `PATCH`  | `/api/v1/channel_folders`                               | `patch-channel-folders`                | `zerver.views.channel_folders.reorder_realm_channel_folders`                   | Django         |
| `POST`   | `/api/v1/channel_folders/create`                        | `create-channel-folder`                | `zerver.views.channel_folders.create_channel_folder`                           | Django         |
| `PATCH`  | `/api/v1/channel_folders/{channel_folder_id}`           | `update-channel-folder`                | `zerver.views.channel_folders.update_channel_folder`                           | Django         |
| `POST`   | `/api/v1/channels/create`                               | `create-channel`                       | `zerver.views.streams.create_channel`                                          | Django         |
| `DELETE` | `/api/v1/default_streams`                               | `remove-default-stream`                | `zerver.views.streams.remove_default_stream`                                   | Django         |
| `POST`   | `/api/v1/default_streams`                               | `add-default-stream`                   | `zerver.views.streams.add_default_stream`                                      | Django         |
| `POST`   | `/api/v1/dev_fetch_api_key`                             | `dev-fetch-api-key`                    | `zerver.views.development.dev_login.api_dev_fetch_api_key`                     | Django (توسعه) |
| `GET`    | `/api/v1/dev_list_users`                                | `dev-list-users`                       | `zerver.views.development.dev_login.api_dev_list_users`                        | Django (توسعه) |
| `GET`    | `/api/v1/drafts`                                        | `get-drafts`                           | `zerver.views.drafts.fetch_drafts`                                             | Rust اختیاری |
| `POST`   | `/api/v1/drafts`                                        | `create-drafts`                        | `zerver.views.drafts.create_drafts`                                            | Rust اختیاری |
| `DELETE` | `/api/v1/drafts/{draft_id}`                             | `delete-draft`                         | `zerver.views.drafts.delete_draft`                                             | Rust اختیاری |
| `PATCH`  | `/api/v1/drafts/{draft_id}`                             | `edit-draft`                           | `zerver.views.drafts.edit_draft`                                               | Rust اختیاری |
| `DELETE` | `/api/v1/events`                                        | `delete-queue`                         | `zerver.tornado.views.cleanup_event_queue`                                     | Tornado        |
| `GET`    | `/api/v1/events`                                        | `get-events`                           | `zerver.tornado.views.get_events`                                              | Tornado        |
| `GET`    | `/api/v1/export/realm`                                  | `get-realm-exports`                    | `zerver.views.realm_export.get_realm_exports`                                  | Django         |
| `POST`   | `/api/v1/export/realm`                                  | `export-realm`                         | `zerver.views.realm_export.export_realm`                                       | Django         |
| `GET`    | `/api/v1/export/realm/consents`                         | `get-realm-export-consents`            | `zerver.views.realm_export.get_users_export_consents`                          | Django         |
| `DELETE` | `/api/v1/export/realm/{export_id}`                      | `delete-realm-export`                  | `zerver.views.realm_export.delete_realm_export`                                | Django         |
| `POST`   | `/api/v1/fetch_api_key`                                 | `fetch-api-key`                        | `zerver.views.auth.api_fetch_api_key`                                          | Django         |
| `GET`    | `/api/v1/get_stream_id`                                 | `get-stream-id`                        | `zerver.views.streams.json_get_stream_id`                                      | Django         |
| `GET`    | `/api/v1/invites`                                       | `get-invites`                          | `zerver.views.invite.get_user_invites`                                         | Django         |
| `POST`   | `/api/v1/invites`                                       | `send-invites`                         | `zerver.views.invite.invite_users_backend`                                     | Django         |
| `POST`   | `/api/v1/invites/multiuse`                              | `create-invite-link`                   | `zerver.views.invite.generate_multiuse_invite_backend`                         | Django         |
| `DELETE` | `/api/v1/invites/multiuse/{invite_id}`                  | `revoke-invite-link`                   | `zerver.views.invite.revoke_multiuse_invite`                                   | Django         |
| `DELETE` | `/api/v1/invites/{invite_id}`                           | `revoke-email-invite`                  | `zerver.views.invite.revoke_user_invite`                                       | Django         |
| `POST`   | `/api/v1/invites/{invite_id}/resend`                    | `resend-email-invite`                  | `zerver.views.invite.resend_user_invite_email`                                 | Django         |
| `POST`   | `/api/v1/jwt/fetch_api_key`                             | `jwt-fetch-api-key`                    | `zerver.views.auth.jwt_fetch_api_key`                                          | Django         |
| `POST`   | `/api/v1/mark_all_as_read`                              | `mark-all-as-read`                     | `zerver.views.message_flags.mark_all_as_read`                                  | Django         |
| `POST`   | `/api/v1/mark_stream_as_read`                           | `mark-stream-as-read`                  | `zerver.views.message_flags.mark_stream_as_read`                               | Django         |
| `POST`   | `/api/v1/mark_topic_as_read`                            | `mark-topic-as-read`                   | `zerver.views.message_flags.mark_topic_as_read`                                | Django         |
| `GET`    | `/api/v1/messages`                                      | `get-messages`                         | `zerver.views.message_fetch.get_messages_backend`                              | Django         |
| `POST`   | `/api/v1/messages`                                      | `send-message`                         | `zerver.views.message_send.send_message_backend`                               | Django         |
| `POST`   | `/api/v1/messages/flags`                                | `update-message-flags`                 | `zerver.views.message_flags.update_message_flags`                              | Django         |
| `POST`   | `/api/v1/messages/flags/narrow`                         | `update-message-flags-for-narrow`      | `zerver.views.message_flags.update_message_flags_for_narrow`                   | Django         |
| `GET`    | `/api/v1/messages/matches_narrow`                       | `check-messages-match-narrow`          | `zerver.views.message_fetch.messages_in_narrow_backend`                        | Django         |
| `POST`   | `/api/v1/messages/render`                               | `render-message`                       | `zerver.views.message_send.render_message_backend`                             | Django         |
| `DELETE` | `/api/v1/messages/{message_id}`                         | `delete-message`                       | `zerver.views.message_edit.delete_message_backend`                             | Django         |
| `GET`    | `/api/v1/messages/{message_id}`                         | `get-message`                          | `zerver.views.message_edit.json_fetch_raw_message`                             | Django         |
| `PATCH`  | `/api/v1/messages/{message_id}`                         | `update-message`                       | `zerver.views.message_edit.update_message_backend`                             | Django         |
| `GET`    | `/api/v1/messages/{message_id}/history`                 | `get-message-history`                  | `zerver.views.message_edit.get_message_edit_history`                           | Django         |
| `DELETE` | `/api/v1/messages/{message_id}/reactions`               | `remove-reaction`                      | `zerver.views.reactions.remove_reaction`                                       | Django         |
| `POST`   | `/api/v1/messages/{message_id}/reactions`               | `add-reaction`                         | `zerver.views.reactions.add_reaction`                                          | Django         |
| `GET`    | `/api/v1/messages/{message_id}/read_receipts`           | `get-read-receipts`                    | `zerver.views.read_receipts.read_receipts`                                     | Django         |
| `POST`   | `/api/v1/messages/{message_id}/report`                  | `report-message`                       | `zerver.views.message_report.report_message_backend`                           | Django         |
| `POST`   | `/api/v1/messages/{message_id}/typing`                  | `set-typing-status-for-message-edit`   | `zerver.views.typing.send_message_edit_notification_backend`                   | Django         |
| `POST`   | `/api/v1/mobile_push/e2ee/test_notification`            | `e2ee-test-notify`                     | `zerver.views.push_notifications.send_e2ee_test_push_notification_api`         | Django         |
| `POST`   | `/api/v1/mobile_push/register`                          | `register-push-device`                 | `zerver.views.push_notifications.register_push_device`                         | Django         |
| `POST`   | `/api/v1/mobile_push/test_notification`                 | `test-notify`                          | `zerver.views.push_notifications.send_test_push_notification_api`              | Django         |
| `GET`    | `/api/v1/navigation_views`                              | `get-navigation-views`                 | `zerver.views.navigation_views.get_navigation_views`                           | Rust اختیاری |
| `POST`   | `/api/v1/navigation_views`                              | `add-navigation-view`                  | `zerver.views.navigation_views.add_navigation_view`                            | Rust اختیاری |
| `DELETE` | `/api/v1/navigation_views/{fragment}`                   | `remove-navigation-view`               | `zerver.views.navigation_views.remove_navigation_view`                         | Rust اختیاری |
| `PATCH`  | `/api/v1/navigation_views/{fragment}`                   | `edit-navigation-view`                 | `zerver.views.navigation_views.update_navigation_view`                         | Rust اختیاری |
| `POST`   | `/real-time`                                            | —                                      | —                                                                              | مستندات        |
| `POST`   | `/api/v1/realm/deactivate`                              | `deactivate-realm`                     | `zerver.views.realm.deactivate_realm`                                          | Django         |
| `GET`    | `/api/v1/realm/domains`                                 | `get-realm-domains`                    | `zerver.views.realm_domains.list_realm_domains`                                | Rust اختیاری |
| `POST`   | `/api/v1/realm/domains`                                 | `add-realm-domain`                     | `zerver.views.realm_domains.create_realm_domain`                               | Django         |
| `DELETE` | `/api/v1/realm/domains/{domain}`                        | `delete-realm-domain`                  | `zerver.views.realm_domains.delete_realm_domain`                               | Django         |
| `PATCH`  | `/api/v1/realm/domains/{domain}`                        | `patch-realm-domain`                   | `zerver.views.realm_domains.patch_realm_domain`                                | Django         |
| `GET`    | `/api/v1/realm/emoji`                                   | `get-custom-emoji`                     | `zerver.views.realm_emoji.list_emoji`                                          | Rust اختیاری |
| `DELETE` | `/api/v1/realm/emoji/{emoji_name}`                      | `deactivate-custom-emoji`              | `zerver.views.realm_emoji.delete_emoji`                                        | Django         |
| `POST`   | `/api/v1/realm/emoji/{emoji_name}`                      | `upload-custom-emoji`                  | `zerver.views.realm_emoji.upload_emoji`                                        | Django         |
| `POST`   | `/api/v1/realm/filters`                                 | `add-linkifier`                        | `zerver.views.realm_linkifiers.create_linkifier`                               | Django         |
| `DELETE` | `/api/v1/realm/filters/{filter_id}`                     | `remove-linkifier`                     | `zerver.views.realm_linkifiers.delete_linkifier`                               | Django         |
| `PATCH`  | `/api/v1/realm/filters/{filter_id}`                     | `update-linkifier`                     | `zerver.views.realm_linkifiers.update_linkifier`                               | Django         |
| `GET`    | `/api/v1/realm/linkifiers`                              | `get-linkifiers`                       | `zerver.views.realm_linkifiers.list_linkifiers`                                | Rust اختیاری |
| `PATCH`  | `/api/v1/realm/linkifiers`                              | `reorder-linkifiers`                   | `zerver.views.realm_linkifiers.reorder_linkifiers`                             | Django         |
| `POST`   | `/api/v1/realm/playgrounds`                             | `add-code-playground`                  | `zerver.views.realm_playgrounds.add_realm_playground`                          | Django         |
| `DELETE` | `/api/v1/realm/playgrounds/{playground_id}`             | `remove-code-playground`               | `zerver.views.realm_playgrounds.delete_realm_playground`                       | Django         |
| `GET`    | `/api/v1/realm/presence`                                | `get-presence`                         | `zerver.views.presence.get_statuses_for_realm`                                 | Django         |
| `GET`    | `/api/v1/realm/profile_fields`                          | `get-custom-profile-fields`            | `zerver.views.custom_profile_fields.list_realm_custom_profile_fields`          | Rust اختیاری |
| `PATCH`  | `/api/v1/realm/profile_fields`                          | `reorder-custom-profile-fields`        | `zerver.views.custom_profile_fields.reorder_realm_custom_profile_fields`       | Django         |
| `POST`   | `/api/v1/realm/profile_fields`                          | `create-custom-profile-field`          | `zerver.views.custom_profile_fields.create_realm_custom_profile_field`         | Django         |
| `DELETE` | `/api/v1/realm/profile_fields/{field_id}`               | `delete-custom-profile-field`          | `zerver.views.custom_profile_fields.delete_realm_custom_profile_field`         | Django         |
| `PATCH`  | `/api/v1/realm/profile_fields/{field_id}`               | `update-custom-profile-field`          | `zerver.views.custom_profile_fields.update_realm_custom_profile_field`         | Django         |
| `POST`   | `/api/v1/realm/test_welcome_bot_custom_message`         | `test-welcome-bot-custom-message`      | `zerver.views.welcome_bot_custom_message.send_test_welcome_bot_custom_message` | Django         |
| `PATCH`  | `/api/v1/realm/user_settings_defaults`                  | `update-realm-user-settings-defaults`  | `zerver.views.realm.update_realm_user_settings_defaults`                       | Django         |
| `POST`   | `/api/v1/register`                                      | `register-queue`                       | `zerver.views.events_register.events_register_backend`                         | Django         |
| `POST`   | `/api/v1/register_client_device`                        | `register-client-device`               | `zerver.views.devices.register_device`                                         | Django         |
| `GET`    | `/api/v1/reminders`                                     | `get-reminders`                        | `zerver.views.scheduled_messages.fetch_reminders`                              | Rust اختیاری |
| `POST`   | `/api/v1/reminders`                                     | `create-message-reminder`              | `zerver.views.reminders.create_reminders_message_backend`                      | Django با افزونهٔ اختیاری Rust |
| `DELETE` | `/api/v1/reminders/{reminder_id}`                       | `delete-reminder`                      | `zerver.views.reminders.delete_reminder`                                       | Rust اختیاری |
| `POST`   | `/api/v1/remotes/push/e2ee/register`                    | `register-remote-push-device`          | `zilencer.views.register_remote_push_device_for_e2ee_push_notification`        | Django (push)  |
| `POST`   | `/api/v1/remove_client_device`                          | `remove-client-device`                 | `zerver.views.devices.remove_device`                                           | Django         |
| `POST`   | `/rest-error-handling`                                  | `rest-error-handling`                  | —                                                                              | مستندات        |
| `GET`    | `/api/v1/saved_snippets`                                | `get-saved-snippets`                   | `zerver.views.saved_snippets.get_saved_snippets`                               | Rust اختیاری |
| `POST`   | `/api/v1/saved_snippets`                                | `create-saved-snippet`                 | `zerver.views.saved_snippets.create_saved_snippet`                             | Rust اختیاری |
| `DELETE` | `/api/v1/saved_snippets/{saved_snippet_id}`             | `delete-saved-snippet`                 | `zerver.views.saved_snippets.delete_saved_snippet`                             | Rust اختیاری |
| `PATCH`  | `/api/v1/saved_snippets/{saved_snippet_id}`             | `edit-saved-snippet`                   | `zerver.views.saved_snippets.edit_saved_snippet`                               | Rust اختیاری |
| `GET`    | `/api/v1/scheduled_messages`                            | `get-scheduled-messages`               | `zerver.views.scheduled_messages.fetch_scheduled_messages`                     | Rust اختیاری |
| `POST`   | `/api/v1/scheduled_messages`                            | `create-scheduled-message`             | `zerver.views.scheduled_messages.create_scheduled_message_backend`             | Django با افزونهٔ اختیاری Rust |
| `DELETE` | `/api/v1/scheduled_messages/{scheduled_message_id}`     | `delete-scheduled-message`             | `zerver.views.scheduled_messages.delete_scheduled_messages`                    | Rust اختیاری |
| `PATCH`  | `/api/v1/scheduled_messages/{scheduled_message_id}`     | `update-scheduled-message`             | `zerver.views.scheduled_messages.update_scheduled_message_backend`             | Django با افزونهٔ اختیاری Rust |
| `GET`    | `/api/v1/server_settings`                               | `get-server-settings`                  | `zerver.views.auth.api_get_server_settings`                                    | Django         |
| `PATCH`  | `/api/v1/settings`                                      | `update-settings`                      | `zerver.views.user_settings.json_change_settings`                              | Django         |
| `GET`    | `/api/v1/streams`                                       | `get-streams`                          | `zerver.views.streams.get_streams_backend`                                     | Django         |
| `DELETE` | `/api/v1/streams/{stream_id}`                           | `archive-stream`                       | `zerver.views.streams.deactivate_stream_backend`                               | Django         |
| `GET`    | `/api/v1/streams/{stream_id}`                           | `get-stream-by-id`                     | `zerver.views.streams.get_stream_backend`                                      | Django         |
| `PATCH`  | `/api/v1/streams/{stream_id}`                           | `update-stream`                        | `zerver.views.streams.update_stream_backend`                                   | Django         |
| `POST`   | `/api/v1/streams/{stream_id}/delete_topic`              | `delete-topic`                         | `zerver.views.streams.delete_in_topic`                                         | Django         |
| `GET`    | `/api/v1/streams/{stream_id}/email_address`             | `get-stream-email-address`             | `zerver.views.streams.get_stream_email_address`                                | Django         |
| `DELETE` | `/api/v1/streams/{stream_id}/icon`                      | `delete-channel-picture`               | `zerver.views.channel_icon.delete_channel_icon`                                | Django         |
| `GET`    | `/api/v1/streams/{stream_id}/icon`                      | `get-channel-picture`                  | `zerver.views.channel_icon.serve_channel_icon`                                 | Django         |
| `POST`   | `/api/v1/streams/{stream_id}/icon`                      | `upload-channel-picture`               | `zerver.views.channel_icon.upload_channel_icon`                                | Django         |
| `GET`    | `/api/v1/streams/{stream_id}/members`                   | `get-subscribers`                      | `zerver.views.streams.get_subscribers_backend`                                 | Django         |
| `GET`    | `/api/v1/thumbnail/status/{realm_id_str}/{filename}`    | `check-thumbnail-status`               | `zerver.views.thumbnail.check_thumbnail_status`                                | Django         |
| `POST`   | `/api/v1/typing`                                        | `set-typing-status`                    | `zerver.views.typing.send_notification_backend`                                | Django         |
| `GET`    | `/api/v1/user_groups`                                   | `get-user-groups`                      | `zerver.views.user_groups.get_user_groups`                                     | Rust اختیاری |
| `POST`   | `/api/v1/user_groups/create`                            | `create-user-group`                    | `zerver.views.user_groups.add_user_group`                                      | Django         |
| `PATCH`  | `/api/v1/user_groups/{user_group_id}`                   | `update-user-group`                    | `zerver.views.user_groups.edit_user_group`                                     | Django         |
| `POST`   | `/api/v1/user_groups/{user_group_id}/deactivate`        | `deactivate-user-group`                | `zerver.views.user_groups.deactivate_user_group`                               | Django         |
| `GET`    | `/api/v1/user_groups/{user_group_id}/members`           | `get-user-group-members`               | `zerver.views.user_groups.get_user_group_members`                              | Rust اختیاری |
| `POST`   | `/api/v1/user_groups/{user_group_id}/members`           | `update-user-group-members`            | `zerver.views.user_groups.update_user_group_backend`                           | Django         |
| `GET`    | `/api/v1/user_groups/{user_group_id}/members/{user_id}` | `get-is-user-group-member`             | `zerver.views.user_groups.get_is_user_group_member`                            | Django         |
| `GET`    | `/api/v1/user_groups/{user_group_id}/subgroups`         | `get-user-group-subgroups`             | `zerver.views.user_groups.get_subgroups_of_user_group`                         | Rust اختیاری |
| `POST`   | `/api/v1/user_groups/{user_group_id}/subgroups`         | `update-user-group-subgroups`          | `zerver.views.user_groups.update_subgroups_of_user_group`                      | Django         |
| `POST`   | `/api/v1/user_topics`                                   | `update-user-topic`                    | `zerver.views.user_topics.update_user_topic`                                   | Django         |
| `POST`   | `/api/v1/user_uploads`                                  | `upload-file`                          | `zerver.views.upload.upload_file_backend`                                      | Django         |
| `GET`    | `/api/v1/user_uploads/{realm_id_str}/{filename}`        | `get-file-temporary-url`               | `zerver.views.upload.serve_file_url_backend`                                   | Django         |
| `GET`    | `/api/v1/users`                                         | `get-users`                            | `zerver.views.users.get_members_backend`                                       | Django         |
| `POST`   | `/api/v1/users`                                         | `create-user`                          | `zerver.views.users.create_user_backend`                                       | Django         |
| `DELETE` | `/api/v1/users/me`                                      | `deactivate-own-user`                  | `zerver.views.users.deactivate_user_own_backend`                               | Django         |
| `GET`    | `/api/v1/users/me`                                      | `get-own-user`                         | `zerver.views.users.get_profile_backend`                                       | Django         |
| `DELETE` | `/api/v1/users/me/alert_words`                          | `remove-alert-words`                   | `zerver.views.alert_words.remove_alert_words`                                  | Rust اختیاری |
| `GET`    | `/api/v1/users/me/alert_words`                          | `get-alert-words`                      | `zerver.views.alert_words.list_alert_words`                                    | Rust اختیاری |
| `POST`   | `/api/v1/users/me/alert_words`                          | `add-alert-words`                      | `zerver.views.alert_words.add_alert_words`                                     | Rust اختیاری |
| `DELETE` | `/api/v1/users/me/android_gcm_reg_id`                   | `remove-fcm-token`                     | `zerver.views.push_notifications.remove_android_reg_id`                        | Django         |
| `POST`   | `/api/v1/users/me/android_gcm_reg_id`                   | `add-fcm-token`                        | `zerver.views.push_notifications.add_android_reg_id`                           | Django         |
| `POST`   | `/api/v1/users/me/api_key/regenerate`                   | `regenerate-api-key`                   | `zerver.views.user_settings.regenerate_api_key`                                | Django         |
| `DELETE` | `/api/v1/users/me/apns_device_token`                    | `remove-apns-token`                    | `zerver.views.push_notifications.remove_apns_device_token`                     | Django         |
| `POST`   | `/api/v1/users/me/apns_device_token`                    | `add-apns-token`                       | `zerver.views.push_notifications.add_apns_device_token`                        | Django         |
| `DELETE` | `/api/v1/users/me/avatar`                               | `delete-avatar`                        | `zerver.views.user_settings.delete_avatar_backend`                             | Django         |
| `POST`   | `/api/v1/users/me/avatar`                               | `upload-avatar`                        | `zerver.views.user_settings.set_avatar_backend`                                | Django         |
| `POST`   | `/api/v1/users/me/dm_conversations/pin`                 | `update-dm-conversation-pin`           | `zerver.views.direct_message_groups.update_dm_conversation_pin`                | Django         |
| `DELETE` | `/api/v1/users/me/muted_users/{muted_user_id}`          | `unmute-user`                          | `zerver.views.muted_users.unmute_user`                                         | Django         |
| `POST`   | `/api/v1/users/me/muted_users/{muted_user_id}`          | `mute-user`                            | `zerver.views.muted_users.mute_user`                                           | Django         |
| `POST`   | `/api/v1/users/me/presence`                             | `update-presence`                      | `zerver.views.presence.update_active_status_backend`                           | Django         |
| `DELETE` | `/api/v1/users/me/profile_data`                         | `remove-profile-data`                  | `zerver.views.custom_profile_fields.remove_user_custom_profile_data`           | Django         |
| `PATCH`  | `/api/v1/users/me/profile_data`                         | `update-profile-data`                  | `zerver.views.custom_profile_fields.update_user_custom_profile_data`           | Django         |
| `POST`   | `/api/v1/users/me/status`                               | `update-status`                        | `zerver.views.presence.update_user_status_backend`                             | Django         |
| `DELETE` | `/api/v1/users/me/subscriptions`                        | `unsubscribe`                          | `zerver.views.streams.remove_subscriptions_backend`                            | Django         |
| `GET`    | `/api/v1/users/me/subscriptions`                        | `get-subscriptions`                    | `zerver.views.streams.list_subscriptions_backend`                              | Django         |
| `POST`   | `/api/v1/users/me/subscriptions`                        | `subscribe`                            | `zerver.views.streams.add_subscriptions_backend`                               | Django         |
| `PATCH`  | `/api/v1/users/me/subscriptions/muted_topics`           | `mute-topic`                           | `zerver.views.user_topics.update_muted_topic`                                  | Django         |
| `POST`   | `/api/v1/users/me/subscriptions/properties`             | `update-subscription-settings`         | `zerver.views.streams.update_subscription_properties_backend`                  | Django         |
| `PATCH`  | `/api/v1/users/me/subscriptions/{stream_id}`            | `update-subscription-property`         | `zerver.views.streams.update_subscriptions_property`                           | Django         |
| `GET`    | `/api/v1/users/me/{stream_id}/topics`                   | `get-stream-topics`                    | `zerver.views.streams.get_topics_backend`                                      | Django         |
| `GET`    | `/api/v1/users/{email}`                                 | `get-user-by-email`                    | `zerver.views.users.get_user_by_email`                                         | Django         |
| `PATCH`  | `/api/v1/users/{email}`                                 | `update-user-by-email`                 | `zerver.views.users.update_user_by_email_api`                                  | Django         |
| `GET`    | `/api/v1/users/{user_id_or_email}/presence`             | `get-user-presence`                    | `zerver.views.presence.get_presence_backend`                                   | Django         |
| `DELETE` | `/api/v1/users/{user_id}`                               | `deactivate-user`                      | `zerver.views.users.deactivate_user_backend`                                   | Django         |
| `GET`    | `/api/v1/users/{user_id}`                               | `get-user`                             | `zerver.views.users.get_member_backend`                                        | Django         |
| `PATCH`  | `/api/v1/users/{user_id}`                               | `update-user`                          | `zerver.views.users.update_user_by_id_api`                                     | Django         |
| `GET`    | `/api/v1/users/{user_id}/channels`                      | `get-user-channels`                    | `zerver.views.users.get_subscribed_channels_backend`                           | Django         |
| `POST`   | `/api/v1/users/{user_id}/reactivate`                    | `reactivate-user`                      | `zerver.views.users.reactivate_user_backend`                                   | Django         |
| `GET`    | `/api/v1/users/{user_id}/status`                        | `get-user-status`                      | `zerver.views.presence.get_status_backend`                                     | Django         |
| `POST`   | `/api/v1/users/{user_id}/status`                        | `update-status-for-user`               | `zerver.views.presence.update_user_status_admin`                               | Django         |
| `GET`    | `/api/v1/users/{user_id}/subscriptions/{stream_id}`     | `get-subscription-status`              | `zerver.views.users.get_subscription_backend`                                  | Django         |
| `POST`   | `/zulip-outgoing-webhook`                               | `zulip-outgoing-webhooks`              | —                                                                              | مستندات        |

## عملیات خارج از OpenAPI زیر `/api/v1/`

مسیرهای مستقیم و عملیات ثبت‌شده در URLconfهای Django و سرویس push. شرح view flagها در [REST dispatcher](../zerver/lib/rest.py) آمده است.

| روش      | مسیر                                               | handler                                                                               | مسیریابی      | view flags                   |
| -------- | -------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------- | ---------------------------- |
| `GET`    | `/api/v1/bots`                                     | `zerver.views.users.get_bots_backend`                                                 | Django        | —                            |
| `POST`   | `/api/v1/bots`                                     | `zerver.views.users.add_bot_backend`                                                  | Django        | —                            |
| `DELETE` | `/api/v1/bots/{bot_id}`                            | `zerver.views.users.deactivate_bot_backend`                                           | Django        | —                            |
| `PATCH`  | `/api/v1/bots/{bot_id}`                            | `zerver.views.users.patch_bot_backend`                                                | Django        | —                            |
| `POST`   | `/api/v1/calls/zoom/create`                        | `zerver.views.video_calls.make_zoom_video_call`                                       | Django        | —                            |
| `POST`   | `/api/v1/default_stream_groups/create`             | `zerver.views.streams.create_default_stream_group`                                    | Django        | —                            |
| `DELETE` | `/api/v1/default_stream_groups/{group_id}`         | `zerver.views.streams.remove_default_stream_group`                                    | Django        | —                            |
| `PATCH`  | `/api/v1/default_stream_groups/{group_id}`         | `zerver.views.streams.update_default_stream_group_info`                               | Django        | —                            |
| `PATCH`  | `/api/v1/default_stream_groups/{group_id}/streams` | `zerver.views.streams.update_default_stream_group_streams`                            | Django        | —                            |
| `GET`    | `/api/v1/messages/summary`                         | `zerver.views.message_summary.get_messages_summary`                                   | Django        | `intentionally_undocumented` |
| `PATCH`  | `/api/v1/realm`                                    | `zerver.views.realm.update_realm`                                                     | Django        | —                            |
| `DELETE` | `/api/v1/realm/icon`                               | `zerver.views.realm_icon.delete_icon_backend`                                         | Django        | —                            |
| `GET`    | `/api/v1/realm/icon`                               | `zerver.views.realm_icon.get_icon_backend`                                            | Django        | —                            |
| `POST`   | `/api/v1/realm/icon`                               | `zerver.views.realm_icon.upload_icon`                                                 | Django        | —                            |
| `DELETE` | `/api/v1/realm/logo`                               | `zerver.views.realm_logo.delete_logo_backend`                                         | Django        | —                            |
| `GET`    | `/api/v1/realm/logo`                               | `zerver.views.realm_logo.get_logo_backend`                                            | Django        | —                            |
| `POST`   | `/api/v1/realm/logo`                               | `zerver.views.realm_logo.upload_logo`                                                 | Django        | —                            |
| `GET`    | `/api/v1/realm/subdomain/{subdomain}`              | `zerver.views.realm.check_subdomain_available`                                        | Django        | —                            |
| `POST`   | `/api/v1/remotes/push/e2ee/notify`                 | `zilencer.views.remote_server_send_e2ee_push_notification`                            | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/push/notify`                      | `zilencer.views.remote_server_notify_push`                                            | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/push/register`                    | `zilencer.views.register_remote_push_device`                                          | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/push/test_notification`           | `zilencer.views.remote_server_send_test_notification`                                 | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/push/unregister`                  | `zilencer.views.unregister_remote_push_device`                                        | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/push/unregister/all`              | `zilencer.views.unregister_all_remote_push_devices`                                   | Django (push) | `intentionally_undocumented` |
| `POST`   | `/api/v1/remotes/server/analytics`                 | `zilencer.views.remote_server_post_analytics`                                         | Django (push) | —                            |
| `GET`    | `/api/v1/remotes/server/analytics/status`          | `zilencer.views.remote_server_check_analytics`                                        | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/billing`                   | `corporate.views.remote_billing_page.remote_realm_billing_entry`                      | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/deactivate`                | `zilencer.views.deactivate_remote_server`                                             | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/register`                  | `zilencer.views.register_remote_server`                                               | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/register/transfer`         | `zilencer.views.transfer_remote_server_registration`                                  | Django (push) | —                            |
| `POST`   | `/api/v1/remotes/server/register/verify_challenge` | `zilencer.views.verify_registration_transfer_challenge_ack_endpoint`                  | Django (push) | —                            |
| `PATCH`  | `/api/v1/settings/display`                         | `zerver.views.user_settings.json_change_settings`                                     | Django        | `intentionally_undocumented` |
| `PATCH`  | `/api/v1/settings/notifications`                   | `zerver.views.user_settings.json_change_settings`                                     | Django        | `intentionally_undocumented` |
| `PATCH`  | `/api/v1/settings/web_display_preferences`         | `zerver.views.user_settings.update_web_display_preferences`                           | Django        | `intentionally_undocumented` |
| `PATCH`  | `/api/v1/settings/web_ui_version`                  | `zerver.views.user_settings.update_web_ui_version`                                    | Django        | `intentionally_undocumented` |
| `POST`   | `/api/v1/submessage`                               | `zerver.views.submessage.process_submessage`                                          | Django        | —                            |
| `POST`   | `/api/v1/users/me/onboarding_steps`                | `zerver.views.onboarding_steps.mark_onboarding_step_as_read`                          | Django        | `intentionally_undocumented` |
| `POST`   | `/api/v1/zcommand`                                 | `zerver.views.message_send.zcommand_backend`                                          | Django        | —                            |
| `POST`   | `/api/v1/zulip-services/verify/{access_token}/`    | `zerver.views.push_notifications.self_hosting_registration_transfer_challenge_verify` | Django        | —                            |

## مسیرهای وب زیر `/json/`

مسیرهای مشترک handler جدول‌های API را دارند. ستون مسیریابی، مسیرهای مخصوص وب را هم مشخص می‌کند.

| مسیر                                                  | روش‌ها                   | مسیریابی          |
| ----------------------------------------------------- | ------------------------ | ----------------- |
| `/json/antispam_challenge`                            | `GET`                    | Django (مخصوص وب) |
| `/json/attachments`                                   | `GET`                    | Rust اختیاری |
| `/json/attachments/{attachment_id}`                   | `DELETE`                 | Django            |
| `/json/bot_storage`                                   | `DELETE`, `GET`, `PUT`   | هر سه روش در Rust اختیاری |
| `/json/bots`                                          | `GET`, `POST`            | Django            |
| `/json/bots/{bot_id}`                                 | `DELETE`, `PATCH`        | Django            |
| `/json/bots/{bot_id}/api_key`                         | `GET`                    | Django            |
| `/json/bots/{bot_id}/api_key/regenerate`              | `POST`                   | Django            |
| `/json/calls/bigbluebutton/create`                    | `GET`                    | Django            |
| `/json/calls/constructorgroups/create`                | `POST`                   | Django            |
| `/json/calls/google_meet/create`                      | `POST`                   | Django            |
| `/json/calls/nextcloud_talk/create`                   | `POST`                   | Django            |
| `/json/calls/webex/create`                            | `POST`                   | Django            |
| `/json/calls/zoom/create`                             | `POST`                   | Django            |
| `/json/channel_folders`                               | `GET`, `PATCH`           | `GET` در Rust اختیاری، نوشتن در Django |
| `/json/channel_folders/create`                        | `POST`                   | Django            |
| `/json/channel_folders/{channel_folder_id}`           | `PATCH`                  | Django            |
| `/json/channels/create`                               | `POST`                   | Django            |
| `/json/default_stream_groups/create`                  | `POST`                   | Django            |
| `/json/default_stream_groups/{group_id}`              | `DELETE`, `PATCH`        | Django            |
| `/json/default_stream_groups/{group_id}/streams`      | `PATCH`                  | Django            |
| `/json/default_streams`                               | `DELETE`, `POST`         | Django            |
| `/json/drafts`                                        | `GET`, `POST`            | Rust اختیاری برای هر روش |
| `/json/drafts/{draft_id}`                             | `DELETE`, `PATCH`        | Rust اختیاری برای هر روش |
| `/json/events`                                        | `DELETE`, `GET`          | Tornado           |
| `/json/export/realm`                                  | `GET`, `POST`            | Django            |
| `/json/export/realm/consents`                         | `GET`                    | Django            |
| `/json/export/realm/{export_id}`                      | `DELETE`                 | Django            |
| `/json/fetch_api_key`                                 | `POST`                   | Django (مخصوص وب) |
| `/json/get_stream_id`                                 | `GET`                    | Django            |
| `/json/invites`                                       | `GET`, `POST`            | Django            |
| `/json/invites/multiuse`                              | `POST`                   | Django            |
| `/json/invites/multiuse/{invite_id}`                  | `DELETE`                 | Django            |
| `/json/invites/{invite_id}`                           | `DELETE`                 | Django            |
| `/json/invites/{invite_id}/resend`                    | `POST`                   | Django            |
| `/json/mark_all_as_read`                              | `POST`                   | Django            |
| `/json/mark_stream_as_read`                           | `POST`                   | Django            |
| `/json/mark_topic_as_read`                            | `POST`                   | Django            |
| `/json/messages`                                      | `GET`, `POST`            | Django            |
| `/json/messages/flags`                                | `POST`                   | Django            |
| `/json/messages/flags/narrow`                         | `POST`                   | Django            |
| `/json/messages/matches_narrow`                       | `GET`                    | Django            |
| `/json/messages/render`                               | `POST`                   | Django            |
| `/json/messages/summary`                              | `GET`                    | Django            |
| `/json/messages/{message_id}`                         | `DELETE`, `GET`, `PATCH` | Django            |
| `/json/messages/{message_id}/history`                 | `GET`                    | Django            |
| `/json/messages/{message_id}/reactions`               | `DELETE`, `POST`         | Django            |
| `/json/messages/{message_id}/read_receipts`           | `GET`                    | Django            |
| `/json/messages/{message_id}/report`                  | `POST`                   | Django            |
| `/json/messages/{message_id}/typing`                  | `POST`                   | Django            |
| `/json/mobile_push/e2ee/test_notification`            | `POST`                   | Django            |
| `/json/mobile_push/register`                          | `POST`                   | Django            |
| `/json/mobile_push/test_notification`                 | `POST`                   | Django            |
| `/json/navigation_views`                              | `GET`, `POST`            | Rust اختیاری |
| `/json/navigation_views/{fragment}`                   | `DELETE`, `PATCH`        | Rust اختیاری |
| `/json/realm`                                         | `PATCH`                  | Django            |
| `/json/realm/deactivate`                              | `POST`                   | Django            |
| `/json/realm/domains`                                 | `GET`, `POST`            | `GET` در Rust اختیاری، `POST` در Django |
| `/json/realm/domains/{domain}`                        | `DELETE`, `PATCH`        | Django            |
| `/json/realm/emoji`                                   | `GET`                    | Rust اختیاری |
| `/json/realm/emoji/{emoji_name}`                      | `DELETE`, `POST`         | Django            |
| `/json/realm/filters`                                 | `POST`                   | Django            |
| `/json/realm/filters/{filter_id}`                     | `DELETE`, `PATCH`        | Django            |
| `/json/realm/icon`                                    | `DELETE`, `GET`, `POST`  | Django            |
| `/json/realm/import/status/{confirmation_key}`        | `GET`                    | Django (مخصوص وب) |
| `/json/realm/linkifiers`                              | `GET`, `PATCH`           | `GET` در Rust اختیاری، `PATCH` در Django |
| `/json/realm/logo`                                    | `DELETE`, `GET`, `POST`  | Django            |
| `/json/realm/playgrounds`                             | `POST`                   | Django            |
| `/json/realm/playgrounds/{playground_id}`             | `DELETE`                 | Django            |
| `/json/realm/presence`                                | `GET`                    | Django            |
| `/json/realm/profile_fields`                          | `GET`, `PATCH`, `POST`   | `GET` در Rust اختیاری، نوشتن در Django |
| `/json/realm/profile_fields/{field_id}`               | `DELETE`, `PATCH`        | Django            |
| `/json/realm/subdomain/{subdomain}`                   | `GET`                    | Django            |
| `/json/realm/test_welcome_bot_custom_message`         | `POST`                   | Django            |
| `/json/realm/user_settings_defaults`                  | `PATCH`                  | Django            |
| `/json/register`                                      | `POST`                   | Django            |
| `/json/register_client_device`                        | `POST`                   | Django            |
| `/json/reminders`                                     | `GET`, `POST`            | `GET` در Rust اختیاری، `POST` در Django با درج اختیاری Rust |
| `/json/reminders/{reminder_id}`                       | `DELETE`                 | Rust اختیاری |
| `/json/remove_client_device`                          | `POST`                   | Django            |
| `/json/saved_snippets`                                | `GET`, `POST`            | Rust اختیاری |
| `/json/saved_snippets/{saved_snippet_id}`             | `DELETE`, `PATCH`        | Rust اختیاری |
| `/json/scheduled_messages`                            | `GET`, `POST`            | `GET` در Rust اختیاری، `POST` در Django با درج اختیاری Rust |
| `/json/scheduled_messages/{scheduled_message_id}`     | `DELETE`, `PATCH`        | `DELETE` در Rust اختیاری، `PATCH` در Django با ثبت اختیاری Rust |
| `/json/self-hosted-billing`                           | `GET`                    | Django (مخصوص وب) |
| `/json/settings`                                      | `PATCH`                  | Django            |
| `/json/settings/display`                              | `PATCH`                  | Django            |
| `/json/settings/notifications`                        | `PATCH`                  | Django            |
| `/json/settings/web_display_preferences`              | `PATCH`                  | Django            |
| `/json/settings/web_ui_version`                       | `PATCH`                  | Django            |
| `/json/streams`                                       | `GET`                    | Django            |
| `/json/streams/{stream_id}`                           | `DELETE`, `GET`, `PATCH` | Django            |
| `/json/streams/{stream_id}/delete_topic`              | `POST`                   | Django            |
| `/json/streams/{stream_id}/email_address`             | `GET`                    | Django            |
| `/json/streams/{stream_id}/icon`                      | `DELETE`, `GET`, `POST`  | Django            |
| `/json/streams/{stream_id}/members`                   | `GET`                    | Django            |
| `/json/submessage`                                    | `POST`                   | Django            |
| `/json/thumbnail/status/{realm_id_str}/{filename}`    | `GET`                    | Django            |
| `/json/typing`                                        | `POST`                   | Django            |
| `/json/user_groups`                                   | `GET`                    | Rust اختیاری |
| `/json/user_groups/create`                            | `POST`                   | Django            |
| `/json/user_groups/{user_group_id}`                   | `PATCH`                  | Django            |
| `/json/user_groups/{user_group_id}/deactivate`        | `POST`                   | Django            |
| `/json/user_groups/{user_group_id}/members`           | `GET`, `POST`            | `GET` در Rust اختیاری، `POST` در Django |
| `/json/user_groups/{user_group_id}/members/{user_id}` | `GET`                    | Django            |
| `/json/user_groups/{user_group_id}/subgroups`         | `GET`, `POST`            | `GET` در Rust اختیاری، `POST` در Django |
| `/json/user_topics`                                   | `POST`                   | Django            |
| `/json/user_uploads`                                  | `POST`                   | Django            |
| `/json/user_uploads/{realm_id_str}/{filename}`        | `GET`                    | Django            |
| `/json/users`                                         | `GET`, `POST`            | Django            |
| `/json/users/me`                                      | `DELETE`, `GET`          | Django            |
| `/json/users/me/alert_words`                          | `DELETE`, `GET`, `POST`  | Rust اختیاری |
| `/json/users/me/android_gcm_reg_id`                   | `DELETE`, `POST`         | Django            |
| `/json/users/me/apns_device_token`                    | `DELETE`, `POST`         | Django            |
| `/json/users/me/avatar`                               | `DELETE`, `POST`         | Django            |
| `/json/users/me/dm_conversations/pin`                 | `POST`                   | Django            |
| `/json/users/me/muted_users/{muted_user_id}`          | `DELETE`, `POST`         | Django            |
| `/json/users/me/onboarding_steps`                     | `POST`                   | Django            |
| `/json/users/me/presence`                             | `POST`                   | Django            |
| `/json/users/me/profile_data`                         | `DELETE`, `PATCH`        | Django            |
| `/json/users/me/status`                               | `POST`                   | Django            |
| `/json/users/me/subscriptions`                        | `DELETE`, `GET`, `POST`  | Django            |
| `/json/users/me/subscriptions/muted_topics`           | `PATCH`                  | Django            |
| `/json/users/me/subscriptions/properties`             | `POST`                   | Django            |
| `/json/users/me/subscriptions/{stream_id}`            | `PATCH`                  | Django            |
| `/json/users/me/{stream_id}/topics`                   | `GET`                    | Django            |
| `/json/users/{email}`                                 | `GET`, `PATCH`           | Django            |
| `/json/users/{user_id_or_email}/presence`             | `GET`                    | Django            |
| `/json/users/{user_id}`                               | `DELETE`, `GET`, `PATCH` | Django            |
| `/json/users/{user_id}/channels`                      | `GET`                    | Django            |
| `/json/users/{user_id}/reactivate`                    | `POST`                   | Django            |
| `/json/users/{user_id}/status`                        | `GET`, `POST`            | Django            |
| `/json/users/{user_id}/subscriptions/{stream_id}`     | `GET`                    | Django            |
| `/json/zcommand`                                      | `POST`                   | Django            |

## مسیرهای داخلی و مسیرهای ویژهٔ بدون پیشوند API

چهار مسیر ارتباط سرویس‌ها و مسیرهای سلامت، گزارش CSP و تحویل فایل از URLconfها استخراج شده‌اند. مسیرهای تحویل فایل با پرچم `override_api_url_scheme` از نشست و احراز هویت API پشتیبانی می‌کنند. [مسیریابی Django](../zproject/urls.py)، [مسیریابی Tornado](../zproject/tornado_urls.py)

| روش         | مسیر                                                                   | handler پایتون                                          | مسیریابی              |
| ----------- | ---------------------------------------------------------------------- | ------------------------------------------------------- | --------------------- |
| `POST`      | `/api/internal/notify_tornado`                                         | `zerver.tornado.views.notify`                           | Tornado               |
| `POST`      | `/api/internal/tusd`                                                   | `zerver.views.tusd.handle_tusd_hook`                    | Django                |
| `POST`      | `/api/internal/web_reload_clients`                                     | `zerver.tornado.views.web_reload_clients`               | Tornado               |
| `POST`      | `/api/v1/events/internal`                                              | `zerver.tornado.views.get_events_internal`              | Tornado               |
| `GET`       | `/avatar/{email}`                                                      | `zerver.views.users.avatar_by_email`                    | Django                |
| `GET`       | `/avatar/{email}/medium`                                               | `zerver.views.users.avatar_medium`                      | Django                |
| `GET`       | `/avatar/{user_id}`                                                    | `zerver.views.users.avatar_by_id`                       | Django                |
| `GET`       | `/avatar/{user_id}/medium`                                             | `zerver.views.users.avatar_medium`                      | Django                |
| `GET, HEAD` | `/compatibility`                                                       | `zerver.views.compatibility.check_global_compatibility` | Django / Rust (توسعه) |
| `GET`       | `/health`                                                              | `zerver.views.health.health`                            | Django                |
| `POST`      | `/report/csp_violations`                                               | `zerver.views.report.report_csp_violations`             | Django                |
| `GET`       | `/thumbnail`                                                           | `zerver.views.thumbnail.backend_serve_thumbnail`        | Django                |
| `GET`       | `/user_avatars/{path}`                                                 | `zerver.views.upload.serve_local_avatar_unauthed`       | Django                |
| `GET`       | `/user_uploads/download/{realm_id_str}/{filename}`                     | `zerver.views.upload.serve_file_download_backend`       | Django                |
| `GET`       | `/user_uploads/temporary/{token}/{filename}`                           | `zerver.views.upload.serve_file_unauthed_from_token`    | Django                |
| `GET`       | `/user_uploads/thumbnail/{realm_id_str}/{filename}/{thumbnail_format}` | `zerver.views.upload.serve_file_backend`                | Django                |
| `GET`       | `/user_uploads/{realm_id_str}/{filename}`                              | `zerver.views.upload.serve_file_backend`                | Django                |

## Webhookهای ورودی

۹۶ مسیر ثبت‌شده برای ۹۲ اتصال، شامل چهار alias قدیمی. مسیرها در Django ثبت می‌شوند. `POST` روش ارسال رویداد است. Dropbox برای اعتبارسنجی آدرس، درخواست challenge غیر `POST` را نیز پردازش می‌کند. جدول بر اساس ثبت مسیر تهیه شده و همهٔ روش‌های پذیرفته‌شده به تفکیک handler ممیزی نشده‌اند. [ثبت اتصال‌ها](../zerver/lib/integrations.py)، [handler در Dropbox](../zerver/webhooks/dropbox/view.py)

| اتصال                 | مسیرها                                                                | handler                                                                    |
| --------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `airbrake`            | `/api/v1/external/airbrake`                                           | `zerver.webhooks.airbrake.view.api_airbrake_webhook`                       |
| `airbyte`             | `/api/v1/external/airbyte`                                            | `zerver.webhooks.airbyte.view.api_airbyte_webhook`                         |
| `alertmanager`        | `/api/v1/external/alertmanager`                                       | `zerver.webhooks.alertmanager.view.api_alertmanager_webhook`               |
| `ansibletower`        | `/api/v1/external/ansibletower`                                       | `zerver.webhooks.ansibletower.view.api_ansibletower_webhook`               |
| `appfollow`           | `/api/v1/external/appfollow`                                          | `zerver.webhooks.appfollow.view.api_appfollow_webhook`                     |
| `appveyor`            | `/api/v1/external/appveyor`                                           | `zerver.webhooks.appveyor.view.api_appveyor_webhook`                       |
| `azuredevops`         | `/api/v1/external/azuredevops`                                        | `zerver.webhooks.azuredevops.view.api_azuredevops_webhook`                 |
| `basecamp`            | `/api/v1/external/basecamp`                                           | `zerver.webhooks.basecamp.view.api_basecamp_webhook`                       |
| `beanstalk`           | `/api/v1/external/beanstalk`                                          | `zerver.webhooks.beanstalk.view.api_beanstalk_webhook`                     |
| `beeminder`           | `/api/v1/external/beeminder`                                          | `zerver.webhooks.beeminder.view.api_beeminder_webhook`                     |
| `bitbucket`           | `/api/v1/external/bitbucket`, `/api/v1/external/bitbucket2`           | `zerver.webhooks.bitbucket.view.api_bitbucket_webhook`                     |
| `bitbucketdatacenter` | `/api/v1/external/bitbucketdatacenter`, `/api/v1/external/bitbucket3` | `zerver.webhooks.bitbucketdatacenter.view.api_bitbucketdatacenter_webhook` |
| `buildbot`            | `/api/v1/external/buildbot`                                           | `zerver.webhooks.buildbot.view.api_buildbot_webhook`                       |
| `canarytoken`         | `/api/v1/external/canarytoken`                                        | `zerver.webhooks.canarytoken.view.api_canarytoken_webhook`                 |
| `circleci`            | `/api/v1/external/circleci`                                           | `zerver.webhooks.circleci.view.api_circleci_webhook`                       |
| `codeship`            | `/api/v1/external/codeship`                                           | `zerver.webhooks.codeship.view.api_codeship_webhook`                       |
| `crashlytics`         | `/api/v1/external/crashlytics`                                        | `zerver.webhooks.crashlytics.view.api_crashlytics_webhook`                 |
| `dbt`                 | `/api/v1/external/dbt`                                                | `zerver.webhooks.dbt.view.api_dbt_webhook`                                 |
| `delighted`           | `/api/v1/external/delighted`                                          | `zerver.webhooks.delighted.view.api_delighted_webhook`                     |
| `dialogflow`          | `/api/v1/external/dialogflow`                                         | `zerver.webhooks.dialogflow.view.api_dialogflow_webhook`                   |
| `dropbox`             | `/api/v1/external/dropbox`                                            | `zerver.webhooks.dropbox.view.api_dropbox_webhook`                         |
| `dropboxsign`         | `/api/v1/external/dropboxsign`, `/api/v1/external/hellosign`          | `zerver.webhooks.dropboxsign.view.api_dropboxsign_webhook`                 |
| `errbit`              | `/api/v1/external/errbit`                                             | `zerver.webhooks.errbit.view.api_errbit_webhook`                           |
| `flock`               | `/api/v1/external/flock`                                              | `zerver.webhooks.flock.view.api_flock_webhook`                             |
| `freshdesk`           | `/api/v1/external/freshdesk`                                          | `zerver.webhooks.freshdesk.view.api_freshdesk_webhook`                     |
| `freshping`           | `/api/v1/external/freshping`                                          | `zerver.webhooks.freshping.view.api_freshping_webhook`                     |
| `freshstatus`         | `/api/v1/external/freshstatus`                                        | `zerver.webhooks.freshstatus.view.api_freshstatus_webhook`                 |
| `front`               | `/api/v1/external/front`                                              | `zerver.webhooks.front.view.api_front_webhook`                             |
| `gitea`               | `/api/v1/external/gitea`                                              | `zerver.webhooks.gitea.view.api_gitea_webhook`                             |
| `github`              | `/api/v1/external/github`                                             | `zerver.webhooks.github.view.api_github_webhook`                           |
| `githubsponsors`      | `/api/v1/external/githubsponsors`                                     | `zerver.webhooks.github.view.api_github_webhook`                           |
| `gitlab`              | `/api/v1/external/gitlab`                                             | `zerver.webhooks.gitlab.view.api_gitlab_webhook`                           |
| `gocd`                | `/api/v1/external/gocd`                                               | `zerver.webhooks.gocd.view.api_gocd_webhook`                               |
| `gogs`                | `/api/v1/external/gogs`                                               | `zerver.webhooks.gogs.view.api_gogs_webhook`                               |
| `gong`                | `/api/v1/external/gong`                                               | `zerver.webhooks.gong.view.api_gong_webhook`                               |
| `gosquared`           | `/api/v1/external/gosquared`                                          | `zerver.webhooks.gosquared.view.api_gosquared_webhook`                     |
| `grafana`             | `/api/v1/external/grafana`                                            | `zerver.webhooks.grafana.view.api_grafana_webhook`                         |
| `greenhouse`          | `/api/v1/external/greenhouse`                                         | `zerver.webhooks.greenhouse.view.api_greenhouse_webhook`                   |
| `groove`              | `/api/v1/external/groove`                                             | `zerver.webhooks.groove.view.api_groove_webhook`                           |
| `harbor`              | `/api/v1/external/harbor`                                             | `zerver.webhooks.harbor.view.api_harbor_webhook`                           |
| `helloworld`          | `/api/v1/external/helloworld`                                         | `zerver.webhooks.helloworld.view.api_helloworld_webhook`                   |
| `heroku`              | `/api/v1/external/heroku`                                             | `zerver.webhooks.heroku.view.api_heroku_webhook`                           |
| `homeassistant`       | `/api/v1/external/homeassistant`                                      | `zerver.webhooks.homeassistant.view.api_homeassistant_webhook`             |
| `ifttt`               | `/api/v1/external/ifttt`                                              | `zerver.webhooks.ifttt.view.api_ifttt_webhook`                             |
| `intercom`            | `/api/v1/external/intercom`                                           | `zerver.webhooks.intercom.view.api_intercom_webhook`                       |
| `jira`                | `/api/v1/external/jira`                                               | `zerver.webhooks.jira.view.api_jira_webhook`                               |
| `jotform`             | `/api/v1/external/jotform`                                            | `zerver.webhooks.jotform.view.api_jotform_webhook`                         |
| `json`                | `/api/v1/external/json`                                               | `zerver.webhooks.json.view.api_json_webhook`                               |
| `librato`             | `/api/v1/external/librato`                                            | `zerver.webhooks.librato.view.api_librato_webhook`                         |
| `lidarr`              | `/api/v1/external/lidarr`                                             | `zerver.webhooks.lidarr.view.api_lidarr_webhook`                           |
| `linear`              | `/api/v1/external/linear`                                             | `zerver.webhooks.linear.view.api_linear_webhook`                           |
| `mention`             | `/api/v1/external/mention`                                            | `zerver.webhooks.mention.view.api_mention_webhook`                         |
| `netlify`             | `/api/v1/external/netlify`                                            | `zerver.webhooks.netlify.view.api_netlify_webhook`                         |
| `newrelic`            | `/api/v1/external/newrelic`                                           | `zerver.webhooks.newrelic.view.api_newrelic_webhook`                       |
| `notion`              | `/api/v1/external/notion`                                             | `zerver.webhooks.notion.view.api_notion_webhook`                           |
| `opencollective`      | `/api/v1/external/opencollective`                                     | `zerver.webhooks.opencollective.view.api_opencollective_webhook`           |
| `openproject`         | `/api/v1/external/openproject`                                        | `zerver.webhooks.openproject.view.api_openproject_webhook`                 |
| `opensearch`          | `/api/v1/external/opensearch`                                         | `zerver.webhooks.opensearch.view.api_opensearch_webhook`                   |
| `opsgenie`            | `/api/v1/external/opsgenie`                                           | `zerver.webhooks.opsgenie.view.api_opsgenie_webhook`                       |
| `pagerduty`           | `/api/v1/external/pagerduty`                                          | `zerver.webhooks.pagerduty.view.api_pagerduty_webhook`                     |
| `papertrail`          | `/api/v1/external/papertrail`                                         | `zerver.webhooks.papertrail.view.api_papertrail_webhook`                   |
| `patreon`             | `/api/v1/external/patreon`                                            | `zerver.webhooks.patreon.view.api_patreon_webhook`                         |
| `pingdom`             | `/api/v1/external/pingdom`                                            | `zerver.webhooks.pingdom.view.api_pingdom_webhook`                         |
| `radarr`              | `/api/v1/external/radarr`                                             | `zerver.webhooks.radarr.view.api_radarr_webhook`                           |
| `raygun`              | `/api/v1/external/raygun`                                             | `zerver.webhooks.raygun.view.api_raygun_webhook`                           |
| `redmine`             | `/api/v1/external/redmine`                                            | `zerver.webhooks.redmine.view.api_redmine_webhook`                         |
| `reviewboard`         | `/api/v1/external/reviewboard`                                        | `zerver.webhooks.reviewboard.view.api_reviewboard_webhook`                 |
| `rhodecode`           | `/api/v1/external/rhodecode`                                          | `zerver.webhooks.rhodecode.view.api_rhodecode_webhook`                     |
| `rundeck`             | `/api/v1/external/rundeck`                                            | `zerver.webhooks.rundeck.view.api_rundeck_webhook`                         |
| `semaphore`           | `/api/v1/external/semaphore`                                          | `zerver.webhooks.semaphore.view.api_semaphore_webhook`                     |
| `sentry`              | `/api/v1/external/sentry`                                             | `zerver.webhooks.sentry.view.api_sentry_webhook`                           |
| `shortcut`            | `/api/v1/external/shortcut`, `/api/v1/external/clubhouse`             | `zerver.webhooks.shortcut.view.api_shortcut_webhook`                       |
| `slack`               | `/api/v1/external/slack`                                              | `zerver.webhooks.slack.view.api_slack_webhook`                             |
| `slack_incoming`      | `/api/v1/external/slack_incoming`                                     | `zerver.webhooks.slack_incoming.view.api_slack_incoming_webhook`           |
| `sonarqube`           | `/api/v1/external/sonarqube`                                          | `zerver.webhooks.sonarqube.view.api_sonarqube_webhook`                     |
| `sonarr`              | `/api/v1/external/sonarr`                                             | `zerver.webhooks.sonarr.view.api_sonarr_webhook`                           |
| `splunk`              | `/api/v1/external/splunk`                                             | `zerver.webhooks.splunk.view.api_splunk_webhook`                           |
| `statuspage`          | `/api/v1/external/statuspage`                                         | `zerver.webhooks.statuspage.view.api_statuspage_webhook`                   |
| `stripe`              | `/api/v1/external/stripe`                                             | `zerver.webhooks.stripe.view.api_stripe_webhook`                           |
| `taiga`               | `/api/v1/external/taiga`                                              | `zerver.webhooks.taiga.view.api_taiga_webhook`                             |
| `teamcity`            | `/api/v1/external/teamcity`                                           | `zerver.webhooks.teamcity.view.api_teamcity_webhook`                       |
| `thinkst`             | `/api/v1/external/thinkst`                                            | `zerver.webhooks.thinkst.view.api_thinkst_webhook`                         |
| `transifex`           | `/api/v1/external/transifex`                                          | `zerver.webhooks.transifex.view.api_transifex_webhook`                     |
| `travis`              | `/api/v1/external/travis`                                             | `zerver.webhooks.travis.view.api_travis_webhook`                           |
| `trello`              | `/api/v1/external/trello`                                             | `zerver.webhooks.trello.view.api_trello_webhook`                           |
| `updown`              | `/api/v1/external/updown`                                             | `zerver.webhooks.updown.view.api_updown_webhook`                           |
| `uptimerobot`         | `/api/v1/external/uptimerobot`                                        | `zerver.webhooks.uptimerobot.view.api_uptimerobot_webhook`                 |
| `wekan`               | `/api/v1/external/wekan`                                              | `zerver.webhooks.wekan.view.api_wekan_webhook`                             |
| `wordpress`           | `/api/v1/external/wordpress`                                          | `zerver.webhooks.wordpress.view.api_wordpress_webhook`                     |
| `zabbix`              | `/api/v1/external/zabbix`                                             | `zerver.webhooks.zabbix.view.api_zabbix_webhook`                           |
| `zapier`              | `/api/v1/external/zapier`                                             | `zerver.webhooks.zapier.view.api_zapier_webhook`                           |
| `zendesk`             | `/api/v1/external/zendesk`                                            | `zerver.webhooks.zendesk.view.api_zendesk_webhook`                         |

## تکمیل parser بلوک Markdown: جداکننده و پاراگراف

`ZULIP_RUST_MARKDOWN_HORIZONTAL_RULES` تشخیص جداکننده و تقسیم بلوک‌های
اطراف را به Rust می‌سپارد. `ZULIP_RUST_MARKDOWN_PARAGRAPHS` تشخیص بلوک
خالی، حذف فاصلهٔ ابتدای پاراگراف و اتصال متن در فهرست فشرده را منتقل
می‌کند. هر دو مستقل و پیش‌فرض خاموش‌اند. ساخت Element، رندر inline،
metadata کامل، مجوز اعلان و orchestration ارسال همچنان در Django
هستند. این برش عملیات HTTP مستقلی اضافه نمی‌کند؛ شمار مسیرها همان
۲۸ از ۱۷۱ عملیات OpenAPI و ۲۸ از ۱۹۰ عملیات وب است.
[فعال‌سازی و بازگشت](README.md#parse-markdown-horizontal-rules-and-paragraphs-through-rust)

## تکمیل parser نقل‌قول و فهرست Markdown

`ZULIP_RUST_MARKDOWN_BLOCKQUOTES` مرز نقل‌قول و حذف نشان ابتدای خط را
به Rust می‌سپارد. پرچم `ZULIP_RUST_MARKDOWN_LIST_BLOCKS` تشخیص و تقسیم
آیتم‌های فهرست و شمارهٔ آغاز را منتقل می‌کند؛ با پیش‌پردازشگر
`ZULIP_RUST_MARKDOWN_LISTS` مستقل است. پرچم
`ZULIP_RUST_MARKDOWN_LIST_INDENT` سطح تورفتگی، انتخاب والد در مسیر
آخرین فرزند و حذف تورفتگی کامل دو فاصله‌ای را محاسبه می‌کند.
این پل‌ها مستقل و پیش‌فرض خاموش‌اند. ساخت و پیمایش درخت، رندر inline،
serialization و دادهٔ کامل رندر همچنان در Django هستند. شمار مسیرهای
مستقل همان ۲۸ از ۱۷۱ عملیات OpenAPI و ۲۸ از ۱۹۰ عملیات وب است؛
فایل Python حذف نشده است.
[فعال‌سازی و بازگشت](README.md#parse-markdown-blockquotes-and-lists-through-rust)

## تکمیل parser کد و سطرهای جدول Markdown

`ZULIP_RUST_MARKDOWN_INLINE_CODE` تشخیص backtick و backslash جفت‌شده،
محدودهٔ match برحسب کاراکتر Python و escaping متن کد را منتقل می‌کند.
`ZULIP_RUST_MARKDOWN_CODE_BLOCKS` حذف تورفتگی، تقسیم ادامهٔ متن، ادغام
کد مجاور و بازگرداندن خط خالی پس از کد را به Rust می‌سپارد.
`ZULIP_RUST_MARKDOWN_TABLES` تشخیص header و separator، وضعیت border
و تقسیم سلول با حفظ pipe و backtick escapeشده را محاسبه می‌کند.
هر سه مستقل و پیش‌فرض خاموش‌اند و surrogate را به Python می‌سپارند.
ساخت Element و جدول، alignment، پیمایش و serialization سند و دادهٔ
کامل رندر همچنان در Django هستند. مسیر email gateway همچنان parser
کد تورفته را ثبت نمی‌کند. شمار مسیرهای مستقل همان ۲۸ از ۱۷۱ عملیات
OpenAPI و ۲۸ از ۱۹۰ عملیات وب است؛ فایل Python حذف نشده است.
[کد inline](README.md#parse-markdown-inline-code-through-rust)،
[کد تورفته](README.md#plan-markdown-indented-code-blocks-through-rust) و
[سطر جدول](README.md#parse-markdown-tables-through-rust)

## قالب‌بندی متن و فیلدهای لینک داخلی

`ZULIP_RUST_MARKDOWN_EMPHASIS` تشخیص bold، italic و خط‌خورده را در
Rust انجام می‌دهد. `ZULIP_RUST_MARKDOWN_CHANNEL_SPANS` URL، attribute
و متن نمایشی لینک کانال، موضوع و پیام را می‌سازد. کنترل دیدپذیری،
سیاست موضوع و انتخاب لنگر همچنان در Django هستند. ساخت Element،
escaping، رندر تودرتو، serialization و metadata کامل رندر در Python
باقی‌اند. هر دو پرچم مستقل و پیش‌فرض خاموش‌اند و fallback قبلی حفظ
شده است. این برش عملیات HTTP مستقلی اضافه نکرده؛ شمار مسیرها همان
۲۸ از ۱۷۱ عملیات OpenAPI و ۲۸ از ۱۹۰ عملیات وب است و فایل Python
حذف نشده است.
[قالب‌بندی](README.md#parse-markdown-text-emphasis-through-rust)،
[فیلدهای لینک](README.md#build-markdown-channel-link-fields-through-rust)

## چیدمان جدول و اجرای فهرست Markdown

`ZULIP_RUST_MARKDOWN_TABLE_LAYOUT` تراز ستون‌ها، حذف فاصلهٔ ASCII
سلول‌ها و کوتاه یا بلندکردن ردیف تا عرض جدول را در Rust محاسبه می‌کند.
ردیف مصنوعی جدول بدون داده، attribute تراز ندارد؛ ردیف واقعی دارای
سلول خالی، تراز ستون را حفظ می‌کند. این پرچم از تشخیص و تقسیم سطر
با `ZULIP_RUST_MARKDOWN_TABLES` مستقل است.

`ZULIP_RUST_MARKDOWN_LIST_LAYOUT` انتخاب والد یا ادامهٔ فهرست قبلی،
اولین آیتم loose، عملیات آیتم تو‌در‌تو، شمارهٔ آغاز و تصمیم تبدیل
متن و tail قبلی به پاراگراف را به Rust می‌سپارد. شمارهٔ فارسی و صفر
ابتدایی، نوع متن atomic و ترتیب تغییر درخت و وضعیت parser حفظ می‌شوند.
این پرچم از parser بلوک، تورفتگی و پیش‌پردازش فهرست مستقل است؛
تنظیمات سفارشی پشتیبانی‌نشده و Unicode نامعتبر پیش از تغییر وضعیت
به runner پایتون واگذار می‌شوند.

`ZULIP_RUST_MARKDOWN_LIST_INDENT_LAYOUT` مقصد اجرای بلوک تورفته و
مقدار حذف تورفتگی را در Rust تعیین می‌کند. تصمیم ساخت پاراگراف برای
متن قبلی نیز در همان برنامه است؛ ساخت Element، نوع atomic متن و
ترتیب وضعیت `detabbed` هنگام موفقیت و خطای parser در Python حفظ
می‌شوند. این پرچم پیش‌فرض خاموش و از اجرای فهرست و helperهای
تورفتگی مستقل است. تنظیمات سفارشی، node غیررشته‌ای و Unicode
نامعتبر پیش از تغییر وضعیت به runner اصلی برمی‌گردند.
[تورفتگی](README.md#plan-markdown-list-indentation-through-rust)

هر سه پرچم پیش‌فرض خاموش‌اند. ساخت Element، رندر بازگشتی، stash و
placeholderهای inline، serialization و دادهٔ کامل رندر همچنان در
Python هستند. شمار مسیرهای مستقل همان ۲۸ از ۱۷۱ عملیات OpenAPI و
۲۸ از ۱۹۰ عملیات وب است؛ فایل Python حذف نشده است.
[جدول](README.md#plan-markdown-table-layout-through-rust)،
[فهرست](README.md#plan-markdown-list-execution-through-rust)

## اعمال الگو و پیمایش درخت inline

`ZULIP_RUST_MARKDOWN_INLINE_APPLY` کنترل اعمال الگوهای جدید و
`ZULIP_RUST_MARKDOWN_INLINE_TREE` پیمایش درخت inline را در Rust
اجرا می‌کنند. هر دو مستقل و پیش‌فرض خاموش‌اند. رفتار callback،
stash، اجداد، parent map، متن atomic و تغییرات پیش از خطا حفظ
می‌شود. regex، handler، Element و پردازش placeholder هنوز Python
هستند؛ AST بومی و renderer مستقل تکمیل نشده‌اند. شمار عملیات
مستقل همان ۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب است.
[اعمال الگو](README.md#apply-markdown-inline-matches-through-rust)،
[پیمایش درخت](README.md#traverse-inline-markdown-trees-through-rust)

## موتور یکپارچهٔ inline

پرچم پیش‌فرض خاموش `ZULIP_RUST_MARKDOWN_INLINE_ENGINE` پیمایش،
pattern dispatch، اعمال match، بازسازی اجداد، ساخت placeholder و
بازکردن بازگشتی placeholderها را در یک runtime اجرا می‌کند.
فراخوانی داخلی متدهای اصلی مستقیم است؛ callbackهای سفارشی و
جایگزین‌شده همچنان زنده و Python هستند. خاموش‌کردن engine انتخاب
چهار پل مستقل قبلی inline را بازمی‌گرداند.

Element، رشته، regex، handlerها و جست‌وجوی placeholder هنوز Python
هستند؛ این برش AST بومی یا رندر مستقل محسوب نمی‌شود. پس از ۶۴
فراخوانی بومی تودرتو مسیر Python محدودیت recursion را اعمال می‌کند؛
عمق دقیق خطا و فریم‌های traceback برابر نیستند. تست profile حذف
فراخوانی wrapperهای اصلی را بررسی می‌کند؛ سنجش release در مستند
تحویل ثبت شده و پرچم پیش‌فرض خاموش می‌ماند.

تعداد عملیات مستقل تغییر نکرده است: ۲۸ از ۱۷۱ عملیات OpenAPI و
۲۸ از ۱۹۰ عملیات صریح وب. فایل Python حذف و مسیر پیش‌فرض استقرار
تغییر نکرده است. مالکیت بومی درخت و متن و metadata کامل رندر و
اعلان همچنان پیش‌نیاز نوشتن مستقل‌اند.

## هستهٔ inline قابل ساخت بدون Python

feature مستقل `native-markdown` همان برنامه‌ریز درخت، متن و stash
بومی را با API عمومی Rust در دسترس قرار می‌دهد. ساخت مستقل به
Python یا PyO3 وابسته نیست و featureهای HTTP و Python هر دو از
همین هسته استفاده می‌کنند. پنج تست یکپارچهٔ عمومی، قرارداد گراف و
stash و guard و واگذاری را بدون Django بررسی می‌کنند. ابزار مقایسهٔ
حالت کامل، پذیرش بومی و واگذاری را جداگانه گزارش می‌کند.
[ساخت مستقل](README.md#use-the-planner-from-rust)،
[مقایسهٔ حالت](README.md#compare-complete-native-and-python-state)

handlerهای باقی‌مانده، metadata، highlighting، TeX و preview هنوز
برای رندر مستقل لازم‌اند. این مرحله عملیات HTTP تازه‌ای را منتقل
نمی‌کند: شمار عملیات مستقل ۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب است.
مسیر استقرار پیش‌فرض، پرچم‌های runtime و فایل‌های Python تغییر
نکرده‌اند.

## ایموجی نام‌دار در هستهٔ inline بومی

پرچم موجود `ZULIP_RUST_MARKDOWN_NATIVE_INLINE` اکنون نام‌های ASCII
ایموجی را نیز در همان runtime بومی پردازش می‌کند. Rust با کاتالوگ
جاری و ایموجی‌های فعال سازمان، گره‌های `span` و `img` و ویژگی‌های
مرتب آن‌ها را می‌سازد. اولویت نام سفارشی، نام ناشناخته، متن atomic
و stash حفظ می‌شوند. نام غیر ASCII و callback یا پیکربندی سفارشی
پیش از تغییر درخت به مسیر مرجع واگذار می‌شود.

API عمومی `native-markdown` کاتالوگ `NamedEmoji` و ویژگی‌های گره را
بدون وابستگی Python در اختیار renderer مستقل آینده می‌گذارد؛ مسیر
Django هنوز snapshot و اعتبارسنجی و serialization را انجام می‌دهد.
این مرحله عملیات HTTP جدیدی را منتقل و پرچم پیش‌فرضی را فعال نمی‌کند.
شمار عملیات مستقل همان ۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب است.
[روش اجرا و حدود پشتیبانی](README.md#run-the-native-inline-markdown-planner)

## شکلک متنی در هستهٔ inline بومی

handler شمارهٔ ۹ و کاتالوگ `Emoticons` تشخیص مرز ASCII، ترتیب
جایگزینی، تنظیم جاری فرستنده و ساخت گرهٔ Unicode را در Rust انجام
می‌دهند. محتویات code و وضعیت کامل stash و درخت حفظ می‌شوند؛
پیکربندی ناسازگار یا helper سفارشی پیش از mutation واگذار می‌شود.
همان پرچم `ZULIP_RUST_MARKDOWN_NATIVE_INLINE` پیش‌فرض خاموش است.
این مرحله عملیات HTTP تازه‌ای را مستقل نمی‌کند؛ شمار همان ۲۸ از
۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب و شمار فایل‌های Python حذف‌شده صفر است.
[قرارداد اجرا](README.md#run-the-native-inline-markdown-planner)

## ایموجی Unicode در هستهٔ inline بومی

handler شمارهٔ ۱۰ و `UnicodeEmoji`، grammar، qualification، کاتالوگ
نام و ساخت span را در Rust اجرا می‌کنند. propertyهای صریح renderer
با ابزار محلی از `regex 2026.9.29` تولید می‌شوند؛ نسخه یا helper و
regex ناسازگار پیش از mutation واگذار می‌شود. پرچم native همچنان
پیش‌فرض خاموش است؛ بهبود سرعتی اثبات نشده است. مقایسهٔ نهایی تمام
Unicode scalarها و ۱۴٬۳۹۷ حالت کامل، بدون اختلاف بود.

این مرحله عملیات HTTP تازه‌ای را مستقل نمی‌کند: شمار همان ۲۸ از
۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب و شمار فایل‌های Python حذف‌شده صفر است.
لینک، mention و metadata کامل و اتصال renderer به listener هنوز
باقی‌اند. [قرارداد و بازتولید](README.md#run-the-native-inline-markdown-planner)

## parser مستقل لینک، تصویر و صوت

ماژول `markdown_link_syntax` با feature موجود `native-markdown`،
بازهٔ UTF-8 برچسب، مقصد، عنوان و پایان لینک و syntax مشترک تصویر
و صوت را بدون Python مشخص می‌کند. قواعد nested bracket و quote
مرجع حفظ می‌شوند؛ پایان منفی parser مرجع خطای صریح نیازمند
واگذاری است. ده تست عمومی Rust و ۳۵۰٬۰۴۶ مقایسهٔ syntax با
Python-Markdown 3.10.3 بدون اختلاف گذشتند.

این API آماده‌سازی است؛ dispatch زنده، بازکردن placeholder، بررسی
امنیت URL، بازنویسی محلی، انتخاب رسانه و metadata هنوز در مسیر
مرجع‌اند. پرچم تازه، تغییر پیش‌فرض و عملیات HTTP تازه‌ای ندارد.
شمار مستقل همان ۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب و شمار فایل‌های
Python حذف‌شده صفر است.
[قرارداد و بازتولید](README.md#parse-inline-link-and-image-syntax-from-rust)

## آماده‌سازی متن مقصد و عنوان لینک

APIهای عمومی `markdown_link_syntax::unescape` و `prepare_text` با
feature موجود `native-markdown` جایگزینی یک‌مرحله‌ای stash و ترتیب
strip، dequote و فاصله‌های Python را بدون Python اجرا می‌کنند.
snapshot متنی عنصرها قرارداد ورودی است؛ استخراج آن از arena و
اتصال handler زنده، sanitizer، بازنویسی محلی و metadata باقی‌اند.
مقصد خروجی همچنان نامطمئن است و نباید مستقیم رندر شود.

هفت تست تازه و ۱٬۲۵۷٬۱۳۸ مقایسهٔ متن لینک و تصویر/صوت با
Python-Markdown 3.10.3 بدون اختلاف گذشتند. این API آماده‌سازی،
عملیات HTTP تازه یا حذف Python نیست؛ شمار همان ۲۸ از ۱۷۱ OpenAPI
و ۲۸ از ۱۹۰ وب و شمار فایل‌های Python حذف‌شده صفر است.
[قرارداد و بازتولید](README.md#parse-inline-link-and-image-syntax-from-rust)

## snapshot متن stash از arena بومی

API عمومی `markdown_native_inline::snapshot_text_stash` متن عنصرهای
stash را با ترتیب `ElementTree.itertext()`، tail فرزندان و بدون tail
ریشه، مستقیماً از arena می‌سازد. عنصر مشترک در هر محل حضور شمرده
می‌شود و متن atomic و placeholder تودرتو بدون تفسیر باقی می‌مانند.
چرخه، اندیس نامعتبر و سقف کار یا متن به واگذاری کل plan نیاز دارند.

هشت تست جدید و ۱۰۰٬۰۰۵ مقایسه با ElementTree روی میزبان بدون
اختلاف گذشتند. این API آماده‌سازی به dispatch زنده متصل نشده است؛
handler لینک، sanitizer، بازنویسی، رسانه و metadata هنوز باقی‌اند.
شمار همان ۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب است و فایل Python
حذف نشده است.
[قرارداد و بازتولید](README.md#snapshot-native-inline-stash-text)

## لینک صریح در planner بومی

پرچم موجود و پیش‌فرض خاموش `ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1`
روی CPython 3.12 اکنون لینک صریح را از parser و snapshot متن stash
تا sanitizer، سیاست file، بازنویسی محلی و متن atomic در Rust اجرا
می‌کند. لینک پاسخ و فرزندان sender/excerpt نیز بومی هستند.
authority غیر ASCII یا براکت‌دار، helper و regex سفارشی یا سقف
اسکن ۸ MiB، کل plan را پیش از تغییر درخت میزبان واگذار می‌کند.
سیاست‌های جاری هر بار خوانده می‌شوند؛ ElementTree همچنان خروجی
را escape و serialize می‌کند. رسانه، مجوز پیوست و metadata اعلان
هنوز منتقل نشده‌اند و renderer مستقل کامل وجود ندارد.

مقایسهٔ مستقل sanitizer و لینک روی باینری نهایی Cargo، ۱٬۲۳۲٬۹۵۲
حالت را بدون اختلاف گذراند. گزارش پذیرش native و واگذاری را جدا
ثبت می‌کند؛ این شمار به معنی پذیرش بومی همهٔ حالت‌ها نیست.
عملیات مستقل همان ۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب و شمار فایل
Python حذف‌شده صفر است. مسیر HTTP یا پیش‌فرض تازه‌ای فعال نشد.
[قرارداد و بازتولید](README.md#render-supported-explicit-links-natively)

## آماده‌سازی تصویر و اثر پیوست بدون Python

API عمومی `markdown_media::prepare_image` با feature موجود
`native-markdown`، parser مشترک، restore متن، sanitizer، بازنویسی
محلی و lookup preview مجاز را به attributeهای مرتب spinner و اثر
معوق ثبت پیوست تبدیل می‌کند. رد نمایش می‌تواند اثر پیوست داشته
باشد؛ واگذاری باید کل plan را پیش از mutation کنار بگذارد.
`markdown_media::attachment_path` قرارداد URL joining مرجع، مسیر
Unicode، بخش نقطه و خالی و پارامتر semicolon را حفظ می‌کند. کلید
خام preview با مسیر نرمال‌شدهٔ پیوست متفاوت است.

هفت تست عمومی و ۱٬۴۲۲٬۰۳۲ مقایسهٔ نهایی بدون اختلاف گذشتند؛ گزارش
پذیرش و واگذاری را جدا ثبت می‌کند. API مستقل هنوز به dispatch زنده
وصل نیست. صوت، guard helperها و commit اتمی metadata و درخت باقی‌اند.
renderer مستقل کامل، پرچم یا مسیر HTTP تازه ایجاد نشد؛ شمار عملیات
همان ۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب و فایل Python حذف‌شده صفر است.
[قرارداد و بازتولید](README.md#prepare-uploaded-images-and-attachment-effects-natively)

## API مستقل syntax و فیلدهای mention — ۲۰۲۶-۱۱-۲۰

`2f33d44209` scanner مشترک mention را با offset بایتی برای caller
بومی در دسترس می‌گذارد. `d93e4e0dec` آماده‌سازی `name|id`، span و
اثر اعلان را بدون وابستگی Python فراهم می‌کند. lookup مجاز، قواعد
lowercase و نام نمایشی گروه همچنان از snapshot میزبان تأمین می‌شوند.
سقف تبدیل عدد و رقم‌های decimal از همان runtime گرفته می‌شوند.

مقایسهٔ scanner روی ۴٬۶۶۷٬۳۳۸ حالت بدون اختلاف بود؛ fingerprint مرجع
Python 3.12 و 3.15 برابر است. مقایسهٔ فیلدها روی ۱۰۱٬۲۱۴ حالت در
Python 3.12 و ۱۰۱٬۳۰۴ حالت در میزبان نیز بدون اختلاف گذشت. snapshot
نهایی native شامل ۱۲۴ تست موفق است. این‌ها APIهای آماده‌سازی‌اند؛
اتصال به planner، guardها و commit اتمی metadata اعلان هنوز باقی‌اند.
موتور کامل Markdown یا تحویل اعلان مستقل Rust تأیید نشده است.

پرچم، پیش‌فرض یا مسیر HTTP تازه‌ای فعال نشد؛ شمار عملیات مستقل
همان ۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب و فایل Python حذف‌شده صفر است.
[قرارداد و بازتولید](README.md#prepare-mention-syntax-and-notification-fields-natively)

## صوت بومی و برنامه‌ریزی رسانه با metadata معوق

`markdown_media::prepare_audio` انتخاب MIME و preview مجاز، عنوان
خام، sanitizer و بازنویسی، استثناهای static و سازمان و URLهای
Camo را بدون Python آماده می‌کند. snapshot غایب و خالی متفاوت‌اند؛
رد صوت می‌تواند اثر پیوست داشته باشد و عنوان صریح لینک پیش از
این اثر رد می‌شود. جدول MIME از تنظیمات زندهٔ CPython 3.12 POSIX
می‌آید؛ وابستگی تازه‌ای اضافه نشد.

`markdown_native_inline::run_with_media` انتخاب صوت و تصویر را با
ترتیب registry در arena اجرا می‌کند. اثرهای پیوست و has_image تا
موفقیت کل plan نگه داشته می‌شوند؛ ترتیب و تکرار ناشی از اسکن مجدد
حفظ می‌شوند و خطا یا سقف منابع کل plan را واگذار می‌کند.

مقایسهٔ صوت، ۱٬۵۶۷٬۲۲۴ حالت، و مقایسهٔ planner، ۶۰۰٬۲۵۲ حالت،
بدون اختلاف داشتند؛ پذیرش و واگذاری در گزارش‌ها جدا هستند. Cargo
نهایی همهٔ featureها ۲۰۱ تست موفق و یک integration قدیمی ignored
داشت. این APIها هنوز به dispatch زنده و bridge وصل نشده‌اند و
renderer کامل مستقل نیستند. guard helperها و snapshot مجاز و
commit اتمی درخت و metadata در adapter قدم بعدی است. مسیر HTTP و
پیش‌فرض تازه‌ای فعال نشد؛ شمار عملیات همان ۲۸ از ۱۷۱ OpenAPI و
۲۸ از ۱۹۰ وب است و فایل Python حذف‌شده صفر باقی ماند.
[قرارداد صوت](README.md#prepare-audio-and-camo-attributes-natively) و
[planner رسانه](README.md#plan-native-media-with-pending-metadata)

## arena بومی mention و اثرهای معوق اعلان

`7e57b05e4c`، آماده‌سازی mentionهای کاربر، گروه و wildcard را در
planner مستقل با kindهای ۱۷ و ۱۸ اجرا می‌کند. دادهٔ مجاز، نام‌های
خام و نتیجهٔ lookup میزبان، نام نمایشی محلی‌شده و سیاست decimal
زنده از snapshot می‌آیند. lookup ناموفقِ صریح با کلید غایب فرق دارد؛
کلید غایب کل plan را واگذار می‌کند تا syntax ساخته‌شده در handler
قبلی، بدون lookup معتبر رد نشود.

مجموعه‌های اعلان و دو پرچم wildcard تا موفقیت کل arena معوق‌اند.
caller باید آن‌ها را با حفظ metadata قبلی و همراه درخت و همهٔ
اثرهای دیگر commit کند. شکست guard، محدودیت تبدیل عدد یا بودجهٔ
منابع، درخت و اثرها را با هم واگذار می‌کند. هشت تست عمومی جدید و
مقایسهٔ ۳۷۵٬۹۳۶ حالت با موتور واقعی inline در میزبان بدون اختلاف
گذشتند؛ mutant حذف اعلان، ۹۰۰ اختلاف داشت. Cargo مستقل میزبان
۱۶۱ تست native و ۴۰ تست بدون feature داشت. این API به adapter
زنده یا PyO3 متصل نشده و renderer کامل مستقل نیست.

شمار عملیات مستقل همان ۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب و شمار
فایل Python حذف‌شده صفر است. هیچ پیش‌فرض تازه‌ای فعال نشد.
[قرارداد و بازتولید](README.md#plan-native-mentions-with-pending-notification-effects)

## رندر بومی لینک کانال، موضوع و پیام

سه کامیت `229caf1aa3`، `139ecfb46e` و `d4803663b5` به‌ترتیب syntax و
فیلد مستقل، برنامه‌ریزی arena و اتصال guarded به پل Django را اضافه
می‌کنند. همان پرچم `ZULIP_RUST_MARKDOWN_NATIVE_INLINE=1` استفاده می‌شود
و پیش‌فرض همچنان خاموش است. snapshot فقط از نام‌های مجاز، سیاست cache
کانال و anchor موضوع خوانده می‌شود؛ realm با فیلد deferred یا helper
سفارشی بدون lookup تازه و پیش از mutation به Python واگذار می‌شود.

تقدم registry، شناسهٔ رشته‌ای پیام با رقم Unicode و صفر ابتدایی، موضوع
خالی، متن atomic، metadata قبلی و کنارگذاشتن کل plan در خطا حفظ شده‌اند.
این پیشرفت داخل renderer و پل PyO3 است؛ شمار عملیات مستقل همچنان ۲۸ از
۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب و شمار فایل‌های Python حذف‌شده صفر است.
[تأیید، محدودیت‌ها و ادامهٔ کار](MIGRATION_HANDOFF.md#لینکهای-کانال-موضوع-و-پیام-در-planner-بومی--۲۰۲۶-۱۱-۲۰)

## رندر زندهٔ mention و commit اعلان — ۲۰۲۶-۱۱-۲۰

`17ca756f56` با همان پرچم پیش‌فرض خاموش
`ZULIP_RUST_MARKDOWN_NATIVE_INLINE`، mention کاربر، گروه و wildcard
را از snapshot مجاز به planner و پل PyO3 متصل می‌کند. lookupهای
خام و ناموفق، lowercase میزبان، ترجمهٔ گروه سیستمی و حد عدد زنده
حفظ می‌شوند؛ کوئری تازه یا تغییر مجوز وجود ندارد. فیلد deferred،
نوع غیرمعمول، surrogate، snapshot ناقص و سقف منابع، کل plan را
پیش از mutation واگذار می‌کنند. `@` عادی به snapshot نیاز ندارد.

اثرهای اعلان تنها پس از موفقیت کل گراف، به setهای قبلی افزوده و
پرچم‌های wildcard با مقدار قبلی OR می‌شوند. شکست دیرهنگام همهٔ
درخت، registry، processor و rendering metadata را حفظ می‌کند.
منبع و خروجی snapshot بودجهٔ مشترک دارند؛ تکرار نام canonical
در aliasهای خام مختلف نیز پیش از کپی PyO3 حساب می‌شود.

مقایسهٔ نهایی Python 3.12.3 و Markdown 3.10.3 با seed برابر `962833`، تعداد ۳۰٬۰۹۹ گراف را بدون اختلاف گذراند: ۱۳٬۹۷۶ اجرای native و ۱۶٬۱۲۳ واگذاری به Python.
ده تست coverage، تمام ۸۸ statement helper و همهٔ خط‌های اجرایی
تازهٔ adapter را اجرا کردند. تست‌های backend در دو حالت ۲۸۶ مورد
داشتند؛ دو skip شناخته‌شده در حالت روشن و subtestهای مختص native
در حالت خاموش ثبت شدند. ماتریس پل‌های قدیمی mention و اعلان و
trigger ربات، در هر حالت ۳۶ تست موفق داشت. Cargo میزبان ۴۰ و ۱۶۲
تست بدون feature و native و Cargo همهٔ featureهای VM، ۲۲۲ تست
موفق و یک integration قدیمی ignored داشتند. lint، mypy، rustfmt
و Clippy موفق بودند. وابستگی‌ها فقط از کش خوانده شدند.

مسیر HTTP و پیش‌فرض استقرار تغییر نکرد؛ شمار عملیات مستقل همان
۲۸ از ۱۷۱ OpenAPI و ۲۸ از ۱۹۰ وب است. renderer کامل، adapter رسانه
و listener زمان‌بندی هنوز کامل نیستند و فایل Python حذف نشد.
[قرارداد و بازتولید](README.md#render-mentions-through-the-guarded-native-inline-bridge)

## انتقال رسانه به planner از PyO3 — ۲۰۲۶-۱۱-۲۰

- کامیت `ee6c09ea7b` با `plan_markdown_native_inline_media`، snapshot تصویر و صوت و سیاست‌های
  MIME، static و Camo را به arena منتقل می‌کند و در کنار خروجی سازگار
  planner، مسیر پیوست‌های معوق و `has_image` می‌دهد. lookup یا مجوز جدید
  صادر نمی‌شود؛ adapter زندهٔ guarded در Django هنوز باقی است.
- ده آزمون مستقیم extension، ترکیب با کانال و اعلان و شکست اتمی را
  پوشش می‌دهند. مقایسهٔ مرجع روی ۱۲۰٬۲۵۲ حالت، ۱۲۰٬۱۶۲ پذیرش و ۹۰
  واگذاری، بدون اختلاف بود؛ mutant حذف پیوست با ۸۶ اختلاف رد شد.
  دو حالت پرچم در مجموعهٔ Markdown و planner، هر کدام ۲۲۸ تست موفق
  داشتند. رابط قبلی در ۱٬۰۹۹ حالت بدون اختلاف ماند.
- Cargo میزبان ۴۰ تست بدون feature و ۱۶۲ تست native و Linux تعداد
  ۲۲۲ تست همهٔ featureها با یک integration قدیمی ignored داشت؛ ساخت release،
  Clippy و lint از کش انجام شدند و بسته‌ای دانلود نشد. پوشش عملیات
  HTTP و بازنشستگی Python تغییر نکرد: ۲۸/۱۷۱ OpenAPI، ۲۸/۱۹۰ وب و
  صفر فایل بازنشسته. این برش ادعای renderer مستقل کامل یا بهبود سرعت
  ندارد.

## اتصال زندهٔ رسانه به planner بومی

- کامیت `54e1867fba` صوت و تصویر آپلودشده را با snapshot مجاز preview
  و جدول MIME و تنظیمات زنده، زیر پرچم قبلی و پیش‌فرض خاموشِ native inline
  به Rust می‌سپارد. درخت و اعلان و اثرهای پیوست با موفقیت کامل اعمال
  می‌شوند؛ نامزد ردشده، ترتیب و تکرار مسیرها و هویت list حفظ می‌شوند.
- دوازده تست تازه و coverage تمام ۹۰ statement helper و خط‌های تازهٔ
  adapter را پوشش دادند. دو حالت پرچم هر کدام ۲۹۸ تست Markdown، planner،
  زمان‌بندی و یادآوری و ۱۷ تست thumbnail همراه پل قبلی آپلود را گذراندند.
- مقایسهٔ extension واقعی ۳۰٬۱۳۵ گراف، شامل ۱۴٬۲۸۲ پذیرش و
  ۱۵٬۸۵۳ واگذاری، بدون اختلاف داشت؛ mutant حذف metadata رسانه با
  ۱۰ اختلاف رد شد. این برش پوشش renderer کامل، مجوز واقعی پیوست یا
  benchmark نیست. CPython 3.12 POSIX و آپلود محلی پوشش دارند؛ snapshot
  ریشهٔ عمومی آپلود ابری هنوز مرحلهٔ بعدی است.
- wheel موجود دوباره استفاده و بسته‌ای دانلود نشد. lint و mypy موفق
  بودند؛ پیش‌فرض، HTTP و بازنشستگی Python تغییر نکردند: ۲۸/۱۷۱ OpenAPI،
  ۲۸/۱۹۰ وب و صفر فایل بازنشسته.

## snapshot آپلود ابری در رسانهٔ بومی

- کامیت `3d74508eda` ریشهٔ عمومی backend استاندارد S3 را از شیء
  مقداردهی‌شدهٔ موجود به planner رسانه می‌دهد. ساخت client، فراخوانی
  helper و درخواست شبکه در مسیر native وجود ندارند؛ backend سفارشی
  یا آماده‌نشده و snapshot نامعتبر به Python واگذار می‌شوند.
- سه تست تازه، معافیت Camo و مرز prefix و port، ترتیب رسانه، guardها
  و نبود constructor و callback را بررسی می‌کنند. helper رسانه با
  ۱۰۲ statement پوشش کامل دارد. مقایسهٔ extension واقعی روی
  ۲۰٬۱۵۳ گراف، شامل ۹٬۶۶۹ پذیرش و ۱۰٬۴۸۴ واگذاری،
  بدون اختلاف بود؛ ترتیب attribute صریحاً مقایسه و mutant معکوس‌کردن آن رد شد. مجموعهٔ مرتبط در هر دو حالت پرچم ۳۲۱ تست داشت.
- wheel و کش موجود استفاده شدند و بسته‌ای دانلود نشد. پرچم، HTTP،
  پیش‌فرض استقرار و بازنشستگی Python تغییر نکردند: ۲۸/۱۷۱ OpenAPI،
  ۲۸/۱۹۰ وب و صفر فایل بازنشسته. highlighting، TeX، preview و listener
  زمان‌بندی همچنان باقی‌اند؛ renderer مستقل کامل یا سرعت بیشتر ادعا نمی‌شود.

## timestamp تقویمی در inline بومی

- `<time:YYYY-MM-DD…>` با ساعت دارای colon، اعشار و offset در Rust
  برنامه‌ریزی می‌شود. متن اصلی atomic می‌ماند و زمان با دقت میکروثانیه
  و قواعد CPython 3.12 به UTC تبدیل می‌شود؛ تاریخ نامعتبر و overflow
  خروجی escapeشدهٔ قبلی را دارند.
- همان پرچم native با پیش‌فرض خاموش، guard هویت type و constructor و
  handler و regex و سقف تجمعی ۸ MiB استفاده می‌شود. شکل PyO3 حفظ شده
  است؛ wheel تازه لازم است و نسخهٔ قدیمی واگذار می‌کند. درخت، اعلان و
  پیوست با هم commit می‌شوند. گراف مشترک با نیاز به HTML و شکل‌های
  فشرده، هفته‌ای و خارج از grammar همچنان fallback دارند.
- هفت تست تازه، profiler بدون callback تبدیل زمان و corpus دارای
  ورودی تقویمی و گراف ساده و مشترک اضافه شدند. این برش renderer کامل،
  benchmark یا عملیات HTTP تازه نیست؛ شمار همان ۲۸/۱۷۱ OpenAPI و
  ۲۸/۱۹۰ وب و صفر فایل Python بازنشسته است.

## قالب فشردهٔ تاریخ و ساعت در Rust

- کامیت `ae902b2c90` پس از برش `5e74804562`، `YYYYMMDD` و ساعت و offset فشرده با انتخاب مستقل
  جداکننده پشتیبانی می‌شوند. اعشار و UTC و حالت‌های خطا همان رفتار
  مرجع را دارند. شکل ناقص، جداکنندهٔ ناسازگار داخل یک تاریخ یا clock
  و هفتهٔ ISO به Python واگذار می‌شوند.
- یک تست Rust و یک تست Python و گسترش corpus کامل گراف اضافه شدند.
  wheel تقویمی حفظ و wheel تازه به‌صورت آفلاین در مسیر جدا نصب شد؛
  وابستگی تازه‌ای دانلود نشد. عملیات HTTP تازه و بازنشستگی فایل Python
  در این برش نیست؛ شمار همان ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰ وب است.

- مقایسهٔ نهایی extension واقعی با seed برابر `962837` شامل ۶۰٬۱۵۳
  گراف، ۴۴٬۲۸۴ پذیرش و ۱۵٬۸۶۹ واگذاری و صفر اختلاف بود؛ mutant UTC
  با ۱۰ اختلاف از ۱۶۳ حالت رد شد. در هر دو حالت پرچم ۳۲۹ تست OK
  بودند؛ هشت تست coverage تمام خط‌های اجرایی تازهٔ adapter را پوشش دادند.
- Cargo میزبان ۱۶۹ تست native و Linux تعداد ۱۳۱ تست مرتبط با همهٔ
  featureها گذراندند. Clippy همهٔ targetها، lint هدفمند و mypy و README
  موفق بودند؛ هشدارهای مستندهای فارسی با baseline برابر ماندند.
  پیش‌فرض تغییر نکرد و قالب هفتهٔ ISO، HTML و renderer کامل و listener
  همچنان کار باقی‌مانده‌اند. جزئیات و proof در سند تحویل ثبت شده‌اند.

## تاریخ هفته‌ای ISO در planner بومی

- کامیت `446c082835` قالب‌های `YYYY-Www-D` و `YYYYWwwD`، دوشنبه
  بدون روز صریح، اعتبار هفتهٔ ۵۳ و تبدیل سال تقویمی را در Rust
  پشتیبانی می‌کند. جداکنندهٔ رقم و خط تیره مطابق CPython 3.12
  انتخاب می‌شود؛ UTC و خطا و guard و commit اتمیک حفظ شده‌اند.
- extension واقعی ۸۵٬۱۵۳ گراف با صفر اختلاف و آزمون مستقیم تاریخ‌ها
  ۹٬۰۴۷٬۵۶۴ حالت با صفر اختلاف داشت. mutant با ۱۰ اختلاف از ۲۱۸
  حالت رد شد. در هر دو حالت پرچم ۳۴۴ تست OK بودند؛ Cargo میزبان
  ۴۰ تست بدون feature و ۱۷۰ native و Linux تعداد ۱۳۲ تست مرتبط با
  همهٔ featureها گذراند. Clippy، rustfmt و lint شامل mypy موفق بودند.
- wheel آفلاین جدا نصب و کش‌ها و wheelهای مبنا حفظ شدند؛ بسته‌ای
  دانلود نشد. پیش‌فرض، HTTP و بازنشستگی Python تغییر نکردند:
  ۲۸/۱۷۱ OpenAPI، ۲۸/۱۹۰ وب و صفر فایل بازنشسته. باقی‌ماندهٔ renderer
  و listener و شرط بازنشستگی در انتهای سند تحویل آمده‌اند.

## entityهای HTML در planner بومی

- کامیت `f5309c36df` تشخیص نام و عدد entity را با همان
  `ENTITY_RE` مرجع به Rust منتقل می‌کند. HTML stash، درخت، mention
  و پیوست به صورت اتمیک اعمال می‌شوند؛ decode و serializer مرجع
  حفظ شده‌اند و HTML خام فعال نمی‌شود.
- entrypoint تازهٔ `plan_markdown_native_inline_html` اثرهای HTML را
  جدا برمی‌گرداند؛ دو entrypoint قدیمی و wheel قبلی سازگار می‌مانند.
  پرچم همان native inline و پیش‌فرض خاموش است. guardها و بودجهٔ
  تجمعی ۸ MiB در [مستند تحویل](MIGRATION_HANDOFF.md) توضیح داده شده‌اند.
- extension واقعی روی ۴۰٬۱۵۲ گراف، ۲۷٬۸۸۵ پذیرش native و ۱۲٬۲۶۷
  واگذاری و صفر اختلاف داشت؛ mutant حذف اثر HTML با ۱۰ اختلاف از
  ۱۶۲ حالت رد شد. ترتیب attributeها، HTML stash و تمام metadata و
  حفظ ورودی پیش از fallback مقایسه شدند؛ این تست exhaustive نیست.
- در هر دو حالت پرچم ۳۵۰ تست موفق، و چهار تست مستقیم پل، هفت تست
  coverage و probe رندر کامل موفق بودند. Cargo میزبان ۴۰ تست بدون
  feature و ۱۷۲ native و Linux تعداد ۱۳۴ تست مرتبط با همهٔ featureها
  گذراندند. Clippy، rustfmt و lint هدفمند شامل mypy و README پاس شدند.
- wheel تازه جدا و کاملاً آفلاین ساخته شد؛ بسته‌ای دانلود و کشی حذف
  نشد. مسیر HTTP، پیش‌فرض و بازنشستگی فایل Python تغییر نکردند:
  ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰ وب و صفر فایل بازنشسته. TeX،
  highlighting، preview و orchestration listener همچنان باقی‌اند.

## لینک‌های خودکار در inline بومی — ۲۰۲۶-۱۱-۲۱

- کامیت `b7b263b8de` matcher و handler لینک خودکار را به arena مستقل
  Rust می‌برد. کلاس word از CPython 3.12 و Unicode 15.0، TLD و scheme
  و file links زنده، تقدم غیرحریص host و مسیر و مرز و punctuation
  مرجع حفظ شده‌اند. sanitizer همان کد موجود Rust است.
- kind جدید ۲۱ و entrypoint دارای snapshot و cache محدود، اثر درخت و
  HTML و اعلان و پیوست را اتمیک نگه می‌دارند. هر سه entrypoint قبلی
  و wheel قدیمی سازگارند؛ تنظیم یا helper نامنطبق fallback دارد.
- extension واقعی روی ۴۵٬۱۵۲ گراف، ۳۴٬۰۷۲ پذیرش native و
  ۱۱٬۰۸۰ واگذاری و صفر اختلاف داشت. oracle مستقل روی
  ۴٬۵۴۸٬۵۶۸ محدوده، شامل تمام scalarهای Unicode در چهار جایگاه، بدون
  اختلاف گذشت. این تست exhaustive همهٔ رشته‌ها یا benchmark نیست.
- ۳۵۷ تست در هر دو حالت، هشت تست coverage، چهار تست مستقیم پل و
  probe رندر کامل موفق بودند؛ همهٔ ۴۷ statement تازهٔ adapter و
  snapshot و ثبت callback پوشش داشتند. Cargo میزبان ۴۰ بدون feature
  و ۱۷۶ native و Linux تعداد ۱۳۸ تست مرتبط با همهٔ featureها گذراند.
  Clippy، rustfmt، lint هدفمند شامل mypy و README و پیام کامیت‌ها پاس شدند.
- بسته‌ای دانلود و cache یا wheel قبلی حذف نشد. پیش‌فرض و HTTP و
  بازنشستگی Python تغییر نکردند: ۲۸/۱۷۱ OpenAPI، ۲۸/۱۹۰ وب و صفر فایل
  بازنشسته. TeX، highlighting، preview و renderer کامل و orchestration
  listener باقی‌اند. جزئیات و شرط بازنشستگی در انتهای مستند تحویل است.

## اجرای محلی KaTeX در Rust — ۲۰۲۶-۱۱-۲۱

- کامیت `6d3b0dc580` با `ZULIP_RUST_MARKDOWN_TEX=1` اجرای فرایند
  محلی KaTeX را در Linux به هستهٔ مستقل Rust و پل PyO3 منتقل می‌کند.
  اجرای بدون shell، pipeهای هم‌زمان، آزادشدن GIL، خطاهای ایجاد فرایند،
  بسته‌شدن زودهنگام stdin، descriptorهای ارث‌پذیر و بازنشانی سیگنال
  حفظ می‌شوند. جداسازی ناموجود پیش از اجرای KaTeX به Python واگذار
  می‌شود؛ سه خطای واقعی seccomp، هر کدام دقیقاً یک fallback داشتند.
  KaTeX همچنان JavaScript است؛ locale و decode و newline و strip،
  کلاینت سرویس و تبدیل HTML به LaTeX در Python باقی‌اند. پرچم مستقل
  و پیش‌فرض خاموش است؛ wheel قدیمی و سیستم غیر Linux fallback دارند.
  ۴۳۴ اجرای واقعی، شامل ۴۶ موفق و ۳۸۸ ناموفق، بدون اختلاف گذشت؛
  mutant حذف display-mode با ۱۳ اختلاف رد شد. ۱۴ تست جدید Django،
  ۹ تست مستقیم و ۳۷۱ تست در هر دو حالت موفق بودند؛ حالت روشن دو
  skip شناخته‌شدهٔ RabbitMQ داشت. coverage همهٔ ۱۵ statement تازهٔ
  adapter و probe سه رندر کامل را ثبت کرد. میزبان ۴۰ تست Cargo
  بدون feature و ۱۸۲ native و Linux تعداد ۱۱۸ library و ۲۶ integration
  مرتبط با همهٔ featureها را گذراندند؛ Clippy و lint و mypy موفق بودند.
  wheel جدا و تمام وابستگی‌ها آفلاین بودند؛ هیچ package یا کش وابستگی
  حذف نشد. خروجی‌های کامپایل قدیمی برای فضا پس از آرشیو و بررسی hash
  کنار گذاشته شدند. شواهد، محدودیت‌ها و گام بعد در بخش TeX سند تحویل‌اند.
  پیش‌فرض، HTTP و بازنشستگی فایل Python تغییر نکردند:
  ۲۸/۱۷۱ OpenAPI، ۲۸/۱۹۰ وب و صفر فایل بازنشسته.

## تشخیص TeX در Rust — ۲۰۲۶-۱۱-۲۱

- کامیت `16a03429b9` با پرچم مستقل و پیش‌فرض خاموش
  `ZULIP_RUST_MARKDOWN_TEX_SYNTAX=1` delimiter، مرز Unicode و
  captureهای legacy Pattern را به Rust منتقل می‌کند. الگو و runtime
  نامنطبق، Unicode پشتیبانی‌نشده، surrogate، ورودی بزرگ و wheel قدیمی
  fallback دارند. planner قبل از renderer واگذار می‌شود؛ HTML stash
  و ترتیب فراخوانی همان مسیر قبلی‌اند. این handler کامل TeX نیست.
- ۶٬۶۶۳٬۸۱۲ مقایسه با الگوی زندهٔ Python و صفر اختلاف، mutant با
  ۱۲ اختلاف از ۱۸ مورد، هفت تست Django، دو تست مستقیم و ۳۷۸ تست
  در هر دو حالت موفق بودند. coverage و سه رندر کامل با تمام metadata
  و wheel قدیمی نیز بررسی شدند. Linux تعداد ۱۴۷ تست Cargo مرتبط
  گذراند و Clippy، rustfmt و lint شامل mypy و README موفق بودند.
- دانلود بسته انجام نشد؛ wheelها و کش dependency حفظ شدند. کمبود
  فضا و بازیابی محیط و آرشیو خروجی کامپایل در سند تحویل ثبت شده‌اند.
  مسیر HTTP، پیش‌فرض و شمار بازنشستگی Python تغییر نکردند:
  ۲۸/۱۷۱ OpenAPI، ۲۸/۱۹۰ وب و صفر فایل بازنشسته.
  [جزئیات و گام بعد](MIGRATION_HANDOFF.md)

## handlerهای TeX در Rust — ۲۰۲۶-۱۱-۲۱

- کامیت `035f977980` با `ZULIP_RUST_MARKDOWN_TEX_HANDLERS=1`
  handlerهای inline و بلوک math را در جایگاه قبلی callback اجرا می‌کند.
  renderer، ElementTree و helperهای Python باقی‌اند؛ planner پیش از
  رندر TeX واگذار می‌شود. خطا و stash تکرار نمی‌شوند و wheel قدیمی
  fallback دارد. ۴۰٬۰۲۶ مقایسه بدون اختلاف، ۳۹۰ تست در هر حالت و
  شش رندر واقعی با metadata برابر موفق بودند. نه statement تازهٔ
  adapter پوشش دارند؛ lint و mypy و Clippy موفق بودند. ساخت آفلاین
  بود و کش و wheelهای قبلی حفظ شدند. HTTP و پیش‌فرض تغییر نکردند:
  ۲۸/۱۷۱ OpenAPI، ۲۸/۱۹۰ وب و صفر فایل Python بازنشسته.
  [شواهد و محدودیت‌ها](MIGRATION_HANDOFF.md)

## تبدیل HTML ریاضی به LaTeX خام — ۲۰۲۶-۱۱-۲۱

- کامیت `5f7c72295e` با `ZULIP_RUST_RAW_LATEX=1` کنترل تبدیل
  محتوای اعلان و ایمیل را به Rust می‌برد؛ lxml و XPath باقی‌اند.
  انتخاب دقیق، annotation خالی، tail، snapshot تودرتو و تغییرهای جزئی
  هنگام خطا حفظ می‌شوند. ۲۰٬۰۰۶ درخت بدون اختلاف، ده تست تازه و
  ۴۲۲ تست در هر دو حالت موفق بودند؛ پنج statement تازهٔ adapter
  پوشش دارند. چهار رندر واقعی و wheel قبلی نیز metadata و درخت و
  push برابر داشتند. Cargo، Clippy، mypy و lint هدفمند موفق بودند.
  ساخت آفلاین بود؛ کش و wheelهای قبلی حفظ شدند. HTTP و پیش‌فرض
  تغییر نکردند: ۲۸/۱۷۱ OpenAPI، ۲۸/۱۹۰ وب و صفر فایل بازنشسته.
  [قرارداد](README.md#convert-katex-html-to-raw-latex-through-rust)

## پاسخ سرویس KaTeX در Rust — ۲۰۲۶-۱۱-۲۱

- کامیت `8460d8d74d` با `ZULIP_RUST_KATEX_RESPONSE=1` تصمیم‌های
  پاسخ، decode و strip و log خطای سرویس را به Rust می‌برد. transport
  و گرفتن خطای درخواست در Python باقی‌اند. ۲۰٬۰۶۰ مقایسه بدون اختلاف،
  نه تست تازه و ۴۳۱ تست در هر دو حالت موفق بودند؛ پنج statement
  تازه یا تغییریافته پوشش دارند. probe واقعی localhost برای wheel
  تازه و قبلی هر کدام ۱۴ درخواست با فرم، نتیجه و log برابر داشت؛
  رندر کامل و metadata نیز با CLI برابر بود. Cargo، Clippy، mypy و
  lint هدفمند موفق بودند. ساخت و نصب آفلاین بود؛ هیچ کش یا wheel
  قبلی حذف نشد. این کلاینت HTTP بومی یا بازنشستگی فایل Python نیست.
  پیش‌فرض و HTTP تغییر نکردند: ۲۸/۱۷۱ OpenAPI، ۲۸/۱۹۰ وب و صفر
  فایل بازنشسته.
  [قرارداد](README.md#handle-katex-service-responses-through-rust)

## خروجی CLI در Rust — ۲۰۲۶-۱۱-۲۱

- کامیت `f6b57a4d05` با همان `ZULIP_RUST_MARKDOWN_TEX=1` پردازش
  UTF-8 معتبر، newline و strip خروجی CLI را بومی می‌کند. codecهای
  دیگر و خطاهای decode مسیر Python دارند و subprocess تکرار نمی‌شود.
  ۱٬۲۳۲٬۱۰۴ مقایسه بدون اختلاف، شش تست تازه و ۴۳۷ تست در هر دو
  حالت موفق بودند. هر سه statement تازهٔ adapter پوشش دارند. پنج
  رندر واقعی و wheel قبلی تمام metadata برابر داشتند. Cargo، Clippy،
  mypy و lint هدفمند موفق بودند؛ ساخت و نصب آفلاین بود. locale،
  encode ورودی و transport سرویس هنوز Python هستند؛ فایل بازنشسته
  نشد. HTTP و پیش‌فرض همان ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰ وب باقی‌اند.
  [قرارداد](README.md#normalize-katex-cli-output-through-rust)

## کنترل highlighting بلوک کد در Rust — ۲۰۲۶-۱۱-۲۱

- کامیت `dd45bd6736` با `ZULIP_RUST_CODE_HIGHLIGHT=1` با پیش‌فرض خاموش، کش تنظیمات و ساخت
  highlighter و metadata زبان را از طریق PyO3 کنترل می‌کند. Pygments،
  lxml، header parsing و گرفتن خطای hilite در Python باقی‌اند. ۵٬۰۷۰
  مقایسه شامل ۵۰۹ خطا بدون اختلاف و پنج رندر کامل با همهٔ metadata
  برابر بودند؛ wheel قبلی نیز fallback را گذراند. نه تست تازه و
  ۴۴۶ تست در هر دو حالت موفق بودند؛ سیزده statement adapter پوشش
  دارند. Cargo، Clippy، mypy و lint هدفمند موفق بودند. ساخت و نصب آفلاین
  بود و هیچ cache یا wheel قبلی حذف نشد. این یک renderer مستقل نیست؛
  شمار HTTP همان ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰ وب و بازنشستگی Python صفر است.
  [قرارداد](README.md#orchestrate-fenced-code-highlighting-through-rust)

## parser بومی header بلوک کد — ۲۰۲۶-۱۱-۲۱

- کامیت `ad1071ff68` با `ZULIP_RUST_CODE_HEADER=1` و پیش‌فرض خاموش،
  syntax خط اول و عادی‌سازی LF را در Rust انجام می‌دهد. lowercase و
  تبدیل شمارهٔ خطوط هنوز Python هستند. ۳٬۴۹۴٬۱۶۹ مقایسه بدون اختلاف،
  هشت تست تازه و ۴۵۴ تست در هر دو حالت موفق بودند؛ هر ۱۶ statement
  تازه پوشش دارند. هفت رندر کامل و wheel قبلی با تمام metadata برابر
  بودند. Cargo، Clippy، mypy و lint موفق بودند. ساخت و نصب آفلاین بود
  و cacheها حفظ شدند. شمار HTTP همان ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰ وب و
  بازنشستگی Python صفر است.
  [قرارداد](README.md#parse-fenced-code-headers-through-rust)

## escape بومی HTML بلوک کد — ۲۰۲۶-۱۱-۲۱

- کامیت `b34b7a149f` با همان `ZULIP_RUST_CODE_HIGHLIGHT=1` و
  پیش‌فرض خاموش، escape بلوک کد غیرفعال یا خطادار را در Rust انجام
  می‌دهد. ۱٬۱۸۸٬۰۵۶ مقایسه بدون اختلاف، شش تست تازه و ۴۶۰ تست در
  هر دو حالت موفق بودند؛ هر پنج statement تازه پوشش دارند. چهار
  رندر کامل با context لاگ و همهٔ metadata و wheel قبلی برابر بودند.
  Cargo، Clippy، mypy و lint موفق بودند؛ ساخت و نصب آفلاین و کش‌ها
  محفوظ بودند. Pygments و lxml و renderer کامل هنوز باقی‌اند. شمار
  HTTP همان ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰ وب و بازنشستگی Python صفر است.
  [قرارداد](README.md#escape-fenced-code-html-through-rust)

## انتخاب مشترک fence نقل‌قول در Rust — ۲۰۲۶-۱۱-۲۱

- کامیت `d49da6b896` با همان `ZULIP_RUST_MARKDOWN_FENCES=1` و
  پیش‌فرض خاموش، scanner موجود یادآوری را برای helper عمومی انتخاب
  fence مشترک می‌کند. ۳٬۴۹۳٬۴۵۴ مقایسه بدون اختلاف، شش تست تازه و
  ۷۴۵ تست در هر دو حالت موفق بودند؛ هر پنج statement تازه پوشش
  دارند. quote reply، گزارش، onboarding و webhookهای GitHub و GitLab
  نیز تست شدند. سه رندر کامل با متن و metadata و wheel قبلی برابر
  بودند. Cargo، Clippy، mypy و lint موفق و ساخت و نصب آفلاین بودند؛
  کش‌ها حفظ شدند. HTTP همان ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰ وب و صفر فایل
  Python بازنشسته است.
  [قرارداد](README.md#choose-unused-quote-fences-through-rust)

## تکمیل حصارهای باز — ۲۱ نوامبر ۲۰۲۶

`ZULIP_RUST_MARKDOWN_BLOCKS=1` علاوه بر planner موجود، helper عمومی
`close_unclosed_fences` را از طریق `close_unclosed_markdown_fences`
به نرمال‌ساز و planner Rust متصل می‌کند؛ ورودی اصلی حفظ و حصارها به
ترتیب معکوس nesting اضافه می‌شوند. این مسیر برای ورودی‌های پذیرفته‌شده
دیگر Markdown و handlerهای Python را جهت تشخیص حصار نمی‌سازد.
wheel قدیمی، subclass، surrogate، متن بیش از ۸ MiB و زبان پیش‌فرض غیر
ASCII fallback دارند. flag پیش‌فرض خاموش، شمار HTTP مستقل ۲۸/۱۷۱
OpenAPI و ۲۸/۱۹۰ وب و بازنشستگی Python صفر باقی است.
[شواهد و کار باقی‌مانده](MIGRATION_HANDOFF.md)

## نرمال‌سازی مقدارهای header بلوک کد — ۲۱ نوامبر ۲۰۲۶

پرچم موجود و خاموش `ZULIP_RUST_CODE_HEADER` اکنون lowercase زبان ASCII و
شماره‌خط ASCII با علامت و underscore معتبر در محدودهٔ signed 64-bit را
در Rust انجام می‌دهد. Unicode، عدد بزرگ، token حداقل ۶۴۰ بایتی، helper
سفارشی و wheel قدیمی مسیر Python دارند؛ ترتیب تغییر وضعیت و خطا حفظ است.
۱٬۲۲۰٬۵۶۹ مقایسهٔ مستقیم بدون اختلاف، ۷۵۴ تست در هر دو حالت و هفت رندر
کامل برابر با wheel تازه و قبلی ثبت شدند. وابستگی یا دانلود تازه و
بازنشستگی فایل Python وجود ندارد. شمار HTTP همان ۲۸/۱۷۱ OpenAPI و
۲۸/۱۹۰ وب است. جزئیات و محدودیت‌ها در آخرین بخش سند تحویل و
[قرارداد](README.md#normalize-fenced-code-header-values-through-rust) آمده‌اند.

## درخواست سرویس و ورودی CLI در Rust — ۲۱ نوامبر ۲۰۲۶

- کامیت `ff708a66c5` با پرچم موجود و خاموش `ZULIP_RUST_KATEX_RESPONSE`
  ساخت درخواست KaTeX را به Rust می‌برد؛ `KatexSession`، HTTP client،
  timeout و مرز catch و logging همان قبلی‌اند. قابلیت request و response
  مستقل است. ۲۰٬۰۶۰ مقایسه بدون اختلاف، شش تست تازه، پانزده تست coverage
  و رگرسیون نهایی ۷۶۰ تستی در هر دو حالت موفق بودند. چهار statement
  تازه پوشش دارند؛ پنج رندر کامل و probe واقعی loopback با wheel تازه
  و قبلی برابر بودند. jobهای اولیهٔ هم‌پوشان متوقف و تأیید نهایی ترتیبی
  انجام شد. کلاینت HTTP مستقل یا بازنشستگی Python نیست.
  [قرارداد](README.md#issue-katex-service-requests-through-rust)
- کامیت `4b73c2f8ff` با پرچم موجود و خاموش `ZULIP_RUST_MARKDOWN_TEX`
  انتخاب encoding و تبدیل رشتهٔ معمولی UTF-8 به stdin را بومی می‌کند.
  codecها و encoder سفارشی و خطاهای surrogate مسیر Python دارند؛
  ترتیب fallback و شروع process حفظ است. ۱٬۳۵۴٬۱۷۲ مقایسه بدون اختلاف،
  هفت تست تازه، ۲۷ تست coverage و رگرسیون ۷۶۷ تستی در هر دو حالت
  موفق بودند. پنج statement تازه یا جایگزین‌شده پوشش دارند؛ پنج رندر
  واقعی و تمام metadata با wheel تازه و قبلی برابر بودند.
  [قرارداد](README.md#select-and-encode-katex-cli-input-through-rust)

Cargo library، Clippy تمام targetها، rustfmt میزبان، lint شامل mypy و
README موفق بودند؛ کل integrationهای Cargo اجرا نشدند. ساخت و نصب
آفلاین و بدون دانلود package یا وابستگی تازه بود؛ cacheها و wheelهای
قبلی حفظ شدند. HTTP مستقل و پیش‌فرض تغییر نکرده‌اند: ۲۸/۱۷۱ OpenAPI،
۲۸/۱۹۰ وب و صفر فایل Python بازنشسته. main planner هنوز به preflight
گراف و اثرهای deferred و حفظ وضعیت exception نیاز دارد.
[شواهد و کار باقی‌مانده](MIGRATION_HANDOFF.md)


## decode پاسخ موفق KaTeX در Rust — ۲۰۲۶-۱۱-۲۱

- `7bc4a1a69a` با پرچم موجود و خاموش `ZULIP_RUST_KATEX_RESPONSE=1`،
  UTF-8 decode و trim پاسخ موفق exact bytes را در Rust انجام می‌دهد.
  newline داخلی حفظ می‌شود؛ UTF-8 نامعتبر و decoder سفارشی رفتار قبلی دارند.
- ۱٬۱۹۷٬۸۶۱ مقایسه با Python بدون اختلاف، ۱۲ تست متمرکز و رگرسیون ۷۷۰
  تستی در هر دو حالت موفق بودند. probe واقعی loopback با wheel تازه و قبلی
  نتیجه، خطا، لاگ و تمام metadata را برابر یافت. وابستگی یا دانلود تازه نیست.
- این پل PyO3 است؛ شمار HTTP مستقل همان ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰ وب و
  بازنشستگی فایل Python صفر است.
  [قرارداد](README.md#decode-successful-katex-http-responses-through-rust)

## پیش‌بررسی گراف TeX بدون رندر — ۲۰۲۶-۱۱-۲۱

- `bf18a4aa13` API خالص `markdown_tex_preflight::preflight` را اضافه
  می‌کند؛ تمام ترکیب‌های موفقیت و شکست معمول، حداکثر شش فرمول، پیش
  از دریافت آرگومان‌های renderer بررسی می‌شوند. stash، شمارندهٔ HTML
  و متن مصرفی لینک‌ها برای هر شاخه جدا هستند؛ خطای دیرهنگام کل گراف
  را پیش از هر effect واگذار می‌کند. انتخاب نتیجه handler اجرا نمی‌کند.
- نه تست تازه، ۹۶٬۸۶۴ مقایسه با Python شامل ۸۲٬۰۷۶ حالت پذیرفته‌شده
  بدون اختلاف و رگرسیون ۳۲۰ تستی در هر دو حالت موفق بودند. Cargo و
  Clippy منتخب، rustfmt و lint شامل mypy موفق‌اند؛ دانلود package صفر است.
- این API هنوز به adapter متصل نیست. checkpoint و حفظ وضعیت هنگام
  exception و سنجش هزینه پیش‌نیاز فعال‌سازی‌اند؛ Python و HTTP مستقل
  تغییری نکرده‌اند: ۲۸/۱۷۱ OpenAPI، ۲۸/۱۹۰ وب و صفر فایل بازنشسته.
  [قرارداد](README.md#preflight-deferred-tex-outcomes-without-rendering)
  [شواهد و گام بعد](MIGRATION_HANDOFF.md)

## checkpointهای renderer و ادامهٔ یک‌بارمصرف TeX — ۲۰۲۶-۱۱-۲۱

- `ec1f724215` به API خالص preflight، checkpoint پیش از هر renderer و
  `begin/body/complete` اضافه می‌کند. continuation فقط یک بار مصرف می‌شود؛
  انتخاب نتیجه handler یا fallback اجرا نمی‌کند. بودجهٔ مشترک گراف و متن
  checkpointها را هم شامل می‌شود؛ اثرهای آینده زودتر اعمال نمی‌شوند.
- هفت تست تازه، ۳۶۶٬۴۸۰ مقایسهٔ وضعیت exception شامل
  ۹۸٬۴۳۶ پذیرفته‌شده و ۱۲۱٬۰۸۰ مقایسهٔ حالت نهایی شامل
  ۱۰۱٬۰۵۲ پذیرفته‌شده بدون اختلاف بودند. دو mutant رد شدند؛ ۳۲۰
  تست در هر حالت، Cargo و Clippy منتخب و lint شامل mypy موفق بودند.
- API هنوز adapter پایتون ندارد. اعمال checkpoint با هویت و اثرهای درست
  در host و سنجش هزینه گام بعدند. وابستگی و دانلود package تازه صفر است؛
  cacheها و wheelها حفظ‌اند. HTTP مستقل همان ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰
  وب و بازنشستگی Python صفر است.
  [قرارداد](README.md#checkpoint-deferred-tex-before-each-renderer-call)
  [شواهد و کار بعدی](MIGRATION_HANDOFF.md)

## اعمال host checkpoint در Rust — ۲۰۲۶-۱۱-۲۱

- `566c91f6e7` ابزار داخلی `MarkdownInlineCheckpoint` را اضافه می‌کند:
  هویت Elementها و attributeها، stash و parent map حفظ می‌شود؛ HTML و
  پیوست فقط suffix تازه می‌گیرند و metadata با حالت قبلی ادغام می‌شود.
  خطای اعمال، وضعیت نیمه‌تمام و exception اصلی را نگه می‌دارد و retry
  را رد می‌کند. چرخه‌های ارجاع Python در GC شرکت می‌کنند.
- ده تست تازه، ۱۵۱٬۹۷۶ مقایسهٔ وضعیت در ۶۱٬۰۸۰ زنجیره بدون اختلاف،
  mutant ده‌اختلافی، ۳۳۰ تست در هر حالت و Cargo و Clippy و lint منتخب
  موفق بودند. package و وابستگی تازه صفر و wheelها و cacheها حفظ‌اند.
- هنوز اتصال renderer، guardهای زنده و قرارداد aliasهای ancestor و
  تغییرات callback و سنجش هزینه باقی است. ابزار consumer تولیدی ندارد؛
  مسیر و پرچم پیش‌فرض و بازنشستگی Python تغییری نکرده‌اند: ۲۸/۱۷۱
  OpenAPI، ۲۸/۱۹۰ وب و صفر فایل Python بازنشسته.
  [قرارداد](README.md#apply-cumulative-inline-checkpoints-to-python-objects)
  [شواهد و گام بعد](MIGRATION_HANDOFF.md)

## حفظ هویت ancestorها در checkpoint — ۲۰۲۶-۱۱-۲۱

- `27c615d986` تاریخچهٔ محدودِ فهرست‌های بازنشستهٔ ancestor را به
  preflight و به‌روزرسانی aliasهای زنده را به committer اضافه می‌کند.
  حالت معمول planner تغییر نمی‌کند و فراخوانی قدیمی بدون history سازگار است.
- چهار تست Python و سه تست Rust تازه؛ ۱۴ تست متمرکز، ۳۳۴ تست در هر
  حالت، library و integrationهای منتخب Rust، Clippy و lint موفق‌اند.
  ۱۲۱٬۰۸۰ زنجیره شامل ۱۰۱٬۲۱۶ پذیرفته‌شده و ۲۹۶٬۸۶۸ وضعیت بدون اختلاف
  بودند؛ دو mutant رد شدند. دانلود package و وابستگی تازه صفر است.
- هنوز اتصال به renderer واقعی، هویت متن immutable و guard تغییرات
  callback باقی است. HTTP مستقل همان ۲۸/۱۷۱ OpenAPI و ۲۸/۱۹۰ وب و
  بازنشستگی Python صفر است.
  [قرارداد](README.md#apply-cumulative-inline-checkpoints-to-python-objects)
  [شواهد و گام بعد](MIGRATION_HANDOFF.md)
