# Tadawal

حزمة **Trader** (11 مهارة لـ Claude) مثبّتة داخل المشروع في `.claude/skills/`.

- المصدر: https://github.com/Jakeschincariol/trader-skill (commit `5189e79b9a480755a3733ac9713f5353981dbe18`)، الرخصة MIT (`.claude/skills/TRADER-SKILL-LICENSE`).
- افتح هذا المستودع في Claude Code وتظهر المهارات كأوامر: `/trader-copycat`، `/trader-news`، `/trader-pelosi`، …
- بياناتك الشخصية (المحفظة والإعدادات) تُحفظ في مجلد `trader/`، وهو مستثنى من git عن طريق `.gitignore`.
- قبل استخدام المهارات اللي تقرأ من SEC، حط اسمك وإيميلك في متغير البيئة `SEC_USER_AGENT`.

ليست نصيحة مالية. التداول الورقي (paper) هو الوضع الافتراضي.
