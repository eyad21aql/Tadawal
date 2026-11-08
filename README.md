# Tadawal — حزمة مهارات تداول لـ Claude

11 مهارة لـ Claude لتداول **الأسهم الأمريكية** مع **ميتاتريدر 5 (MT5)**.
تبدأ على **حساب تجريبي (paper)**، وما يتنفذ أي أمر إلا بعد فحص المخاطر وموافقتك الصريحة.

| # | المهارة | الأمر | وش تسوي |
|---|---|---|---|
| 1 | Entry Timer — مؤقّت الدخول | `entry-timer` | هل الحين وقت دخول؟ اتجاه على فريمين، المتوسطات، RSI، MACD، الدعم والمقاومة، توقيت الجلسة. وإذا لا: وش تنتظر بالضبط |
| 2 | News Monitor — مراقب الأخبار | `news-monitor` | أخبار Yahoo وإفصاحات 8-K من SEC لأسهمك، الجديد فقط، مع تصنيف الخطورة |
| 3 | Chart Scanner — ماسح الشارت | `chart-scanner` | نماذج الشموع والقمة/القاع المزدوج والرأس والكتفين والمثلثات والقنوات والاختراقات والدايفرجنس، مع رسم SVG |
| 4 | Risk Manager — مدير المخاطر | `risk-manager` | حجم الصفقة من الستوب (1% افتراضياً) وفحص PASS/BLOCK لكل أمر |
| 5 | Time Machine — آلة الزمن | `time-machine` | اختبار رجعي بدون نظر للمستقبل: عائد، تراجع، نسبة نجاح، مقارنة بالشراء والاحتفاظ، walk-forward |
| 6 | Exit Planner — مخطط الخروج | `exit-planner` | الستوب والهدف وخطة جني الأرباح والوقف المتحرك |
| 7 | Price Alerts — تنبيهات الأسعار | `price-alerts` | فوق/تحت/تقاطع/نسبة تغير/جرس الافتتاح، مع ساعات NYSE وعطلها |
| 8 | Order Executor — منفّذ الأوامر | `order-executor` | يرسل الأمر لـ MT5 أو الحساب التجريبي بعد موافقتك فقط |
| 9 | Budget Manager — مدير الميزانية | `budget-manager` | توزيع رأس المال وسجل الصفقات وتقرير الأداء |
| 10 | Pelosi Tracker — متتبع بيلوسي | `pelosi-tracker` | صفقات أعضاء الكونغرس من إفصاحات مجلس النواب الرسمية |
| 11 | Earnings Analyst — محلل الأرباح | `earnings-analyst` | نتائج الشركة ربع بربع من SEC: الإيرادات والهوامش وEPS والنمو |

## التثبيت

**المتطلبات:** Python 3.8 أو أحدث، وما تحتاج تثبت أي مكتبة. لربط ميتاتريدر:
Windows مع `pip install MetaTrader5`. ولمتتبع بيلوسي يفضّل تثبيت `pdftotext` (poppler).

### 1) Claude Code كـ plugin (الأفضل)
```
/plugin marketplace add eyad21aql/Tadawal
/plugin install tadawal@tadawal
```
تظهر المهارات بأسماء مثل `/tadawal:entry-timer`.

### 2) نسخ يدوي إلى Claude Code
```bash
git clone https://github.com/eyad21aql/Tadawal.git
cp -r Tadawal/skills/* ~/.claude/skills/
```
لازم تنسخ مجلد `_lib` مع المهارات، لأن فيه المكتبة المشتركة. في Windows المسار هو
`%USERPROFILE%\.claude\skills\`.

### 3) موقع أو تطبيق Claude
```bash
python tools/build_zips.py      # -> dist/<skill>.zip
```
بعدها: Settings ← Capabilities ← فعّل Code execution ← Skills ← Upload، وارفع
ملفات الـ ZIP الـ 11 واحد واحد. كل ملف فيه نسخة من المكتبة المشتركة.
ملاحظة: بيئة موقع Claude ما تقدر تتصل بميتاتريدر، وممكن تكون مواقع البيانات
محجوبة فيها. للاستخدام الكامل استخدم Claude Code على جهازك.

## الإعداد لأول مرة

1. انسخ `config.example.json` إلى `trader/config.json` في مجلد مشروعك، وعدّل
   رأس المال والحدود.
2. حط اسمك وإيميلك لـ SEC، لأن الهيئة تشترط إن كل طلب يعرّف عن صاحبه:
   `"sec_user_agent": "Your Name you@example.com"`، أو متغير البيئة `SEC_USER_AGENT`.
3. كل الملفات اللي تكتبها المهارات تنحفظ في مجلد `trader/`، وهو مستثنى من git.

## ربط ميتاتريدر 5

1. ثبّت MT5 من الوسيط وسجّل دخول على **حساب ديمو**.
2. نفّذ `pip install MetaTrader5`.
3. في MT5: من Tools ← Options ← Expert Advisors فعّل Allow algorithmic trading،
   واضغط زر **Algo Trading**.
4. في `trader/config.json` غيّر `"mode": "mt5"`. وإذا اسم السهم عند الوسيط مختلف
   (مثل `AAPL.US` أو `#AAPL`)، حط `"symbol_suffix": ".US"` أو استخدم `symbol_map`.
5. تأكد من الربط بالأمر `python skills/order-executor/order.py account`، لازم يظهر رقم الحساب و`demo`.

الحسابات **الحقيقية مرفوضة** إلا إذا غيّرت `"allow_live": true` بنفسك.
Claude ممنوع يغيّر هذا الإعداد.

## طريقة العمل

```
chart-scanner / entry-timer  ->  exit-planner  ->  risk-manager size --write
        ->  risk-manager check (PASS)  ->  order-executor tickets  ->  موافقتك  ->  place
news-monitor / pelosi-tracker / price-alerts   (مراقبة دورية)
budget-manager report / exit-planner review    (متابعة)
time-machine                                   (اختبار أي فكرة قبل المال الحقيقي)
```

## الحماية

- ما ينفَّذ أي أمر بدون ستوب، ولا بدون PASS من مدير المخاطر عمره أقل من ساعة.
- أي تعديل على الأمر يلغي الـ PASS، ورمز التذكرة لازم يطابق محتوى الأمر.
- لازم تكتب موافقة صريحة ("نعم" أو "yes")، وأي كلمة نفي أو تردد ترفض الأمر.
- الستوب يتحرك لصالحك فقط، ما يتوسّع.
- حد خسارة يومي، وحد لعدد الصفقات المفتوحة، وحد لحجم المركز.
- كل تنفيذ ينسجل مع نص موافقتك في `trader/executions.json`.

## الاختبارات

```bash
python -m unittest discover -s tests -v
```
الاختبارات تشتغل بدون إنترنت على بيانات تجريبية، وفيها ميتاتريدر وهمي لاختبار مسار الأوامر.

## حدود يجب معرفتها

- بيانات Yahoo مجانية ومتأخرة، وواجهتها غير رسمية وممكن تتغير. أسعار MT5 حية.
- إفصاحات الكونغرس تتأخر لحد 45 يوم، والمبالغ فيها نطاقات مو أرقام دقيقة.
- بيانات SEC ما فيها توقعات المحللين ولا موعد النتائج القادم، فـ Claude يبحث عنها في الويب.
- النتائج السابقة لا تضمن المستقبل. **هذا ليس نصيحة مالية.**
