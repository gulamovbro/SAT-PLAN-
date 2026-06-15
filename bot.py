import os
import json
import asyncio
from datetime import datetime, timedelta
import httpx

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── Tokens (Render environment variables dan oladi) ──────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "8888412070:AAFadO4orY5iH4RbK3Hs-VKtRVVKOv_ew0I")
CLAUDE_KEY  = os.getenv("CLAUDE_API_KEY", "")   # Render da kiritasiz

# ── Bot & Dispatcher ──────────────────────────────────────────────────────────
bot  = Bot(token=BOT_TOKEN)
dp   = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")

# ── Foydalanuvchilar ma'lumotlari (xotirada, keyinroq DB ga o'tkazamiz) ──────
users: dict = {}   # { chat_id: { name, exam_date, level, target, daily_time, plan, week } }

# ── FSM holatlari ─────────────────────────────────────────────────────────────
class Reg(StatesGroup):
    name       = State()
    exam_date  = State()
    level      = State()
    target     = State()
    daily_time = State()

# ─────────────────────────────────────────────────────────────────────────────
#  Yordamchi: inline tugmalar
# ─────────────────────────────────────────────────────────────────────────────
def kb_level():
    b = InlineKeyboardBuilder()
    opts = [
        ("Hali imtihon topshirmaganman", "yoq"),
        ("800 dan past",                 "800past"),
        ("800 – 1000",                   "800_1000"),
        ("1000 – 1200",                  "1000_1200"),
        ("1200 – 1400",                  "1200_1400"),
        ("1400 dan yuqori",              "1400plus"),
    ]
    for text, data in opts:
        b.button(text=text, callback_data=f"level:{data}")
    b.adjust(1)
    return b.as_markup()

def kb_target():
    b = InlineKeyboardBuilder()
    for score in ["1000","1100","1200","1300","1400","1500","1550"]:
        b.button(text=f"{score}+ ball", callback_data=f"target:{score}")
    b.adjust(2)
    return b.as_markup()

def kb_time():
    b = InlineKeyboardBuilder()
    for t in ["1 soat","1.5 soat","2 soat","3 soat","4+ soat"]:
        b.button(text=t, callback_data=f"time:{t}")
    b.adjust(2)
    return b.as_markup()

def kb_main(chat_id):
    b = InlineKeyboardBuilder()
    b.button(text="📅 Bugungi darsim",    callback_data="today")
    b.button(text="📋 To'liq reja",       callback_data="fullplan")
    b.button(text="📊 Progressim",        callback_data="progress")
    b.button(text="🔄 Yangi reja olish",  callback_data="restart")
    b.adjust(2)
    return b.as_markup()

# ─────────────────────────────────────────────────────────────────────────────
#  Claude API orqali reja tuzish
# ─────────────────────────────────────────────────────────────────────────────
async def build_plan_ai(name, level, target, daily_time, days_left, weeks_count):
    """Claude API dan reja so'raydi. API key yo'q bo'lsa statik reja beradi."""

    if not CLAUDE_KEY:
        return build_plan_static(name, level, target, daily_time, days_left, weeks_count)

    prompt = f"""Sen SAT tayyorgarlik mutaxassisisan. O'zbek tilida javob ber.

Talaba: {name}
Daraja: {level}
Maqsad: {target} ball
Vaqt: {days_left} kun ({weeks_count} hafta)
Kunlik: {daily_time}

Faqat JSON qaytargin (boshqa hech narsa yozma):
{{
  "weeks": [
    {{
      "week": 1,
      "title": "Hafta nomi",
      "days": [
        {{"day": "Dushanba", "subject": "Mavzu", "desc": "Nima qiladi", "duration": "{daily_time}"}}
      ]
    }}
  ],
  "resources": ["Khan Academy SAT", "College Board rasmiy testlar", "Bluebook app"],
  "tip": "Eng muhim maslahat"
}}

{weeks_count} hafta, haftada 6 kun (yakshanba dam olish). O'zbek tilida."""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": CLAUDE_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 3000,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
        data = r.json()
        text = data["content"][0]["text"]
        clean = text.replace("```json","").replace("```","").strip()
        return json.loads(clean)
    except Exception as e:
        print(f"Claude xato: {e}")
        return build_plan_static(name, level, target, daily_time, days_left, weeks_count)


def build_plan_static(name, level, target, daily_time, days_left, weeks_count):
    """Claude API siz statik reja."""
    day_names = ["Dushanba","Seshanba","Chorshanba","Payshanba","Juma","Shanba"]

    math = [
        ("Algebra asoslari", "Linear equations va inequalities. Khan Academy: Algebra 1"),
        ("Advanced Math", "Quadratic equations va polynomials. College Board practice"),
        ("Problem Solving", "Word problems. Official SAT Practice Tests"),
        ("Geometry", "Circles, triangles, coordinate geometry. Khan Academy"),
        ("Statistics & Data", "Mean, median, scatterplots, probability"),
        ("Math Mock Test", "To'liq math section — vaqt bilan ishlash"),
    ]
    reading = [
        ("Reading — Literary", "Fiction passage tahlili. Asosiy g'oyani topish"),
        ("Reading — Science", "Scientific passage o'qish va savollar"),
        ("Writing & Grammar", "Punctuation, sentence structure, transitions"),
        ("Vocabulary in Context", "Qiyinroq so'zlarni kontekstdan tushunish"),
        ("Reading — History", "Historical documents o'qish"),
        ("Reading Mock Test", "To'liq Reading & Writing section mock test"),
    ]

    topics = math + reading
    week_titles = [
        "Poydevor — asoslarni mustahkamlash",
        "Ko'nikmalarni chuqurlashtirish",
        "Amaliyot va tezlik oshirish",
        "Zaif tomonlarni yopish",
        "Mock testlar bosqichi",
        "Yuqori ball strategiyalari",
        "Yakuniy tayyorgarlik",
        "Imtihon oldi takrorlash",
    ]

    weeks = []
    idx = 0
    for w in range(min(weeks_count, 8)):
        days = []
        for d in range(6):
            s, desc = topics[idx % len(topics)]
            idx += 1
            days.append({"day": day_names[d], "subject": s, "desc": desc, "duration": daily_time})
        weeks.append({"week": w+1, "title": week_titles[w], "days": days})

    return {
        "weeks": weeks,
        "resources": [
            "📚 Khan Academy SAT — khanacademy.org/sat (bepul, rasmiy)",
            "📖 College Board — collegeboard.org (rasmiy practice testlar)",
            "📱 Bluebook App — raqamli SAT uchun rasmiy ilova",
            "📕 Official SAT Study Guide — 8 ta mock test",
        ],
        "tip": "Har kuni o'qishdan oldin 5 daqiqa avvalgi mavzuni takrorlang!"
    }

# ─────────────────────────────────────────────────────────────────────────────
#  Bugungi darsni hisoblash
# ─────────────────────────────────────────────────────────────────────────────
def get_today_lesson(user: dict) -> str:
    plan = user.get("plan")
    if not plan:
        return None

    start = user.get("start_date", datetime.now().date().isoformat())
    start_date = datetime.fromisoformat(start).date()
    today = datetime.now().date()
    delta = (today - start_date).days

    week_idx  = delta // 7
    day_idx   = delta % 7

    weeks = plan.get("weeks", [])
    if week_idx >= len(weeks):
        return None
    week = weeks[week_idx]
    if day_idx >= len(week["days"]):
        return f"🎉 Bu hafta dam olish kuni! Yakshanba — hordiq oling."

    d = week["days"][day_idx]
    return (
        f"📅 *{today.strftime('%d.%m.%Y')} — {d['day']}*\n\n"
        f"📖 *{d['subject']}*\n"
        f"📝 {d['desc']}\n"
        f"⏱ Vaqt: *{d['duration']}*\n\n"
        f"💪 Omad, {user['name']}\\!"
    )

# ─────────────────────────────────────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    cid = msg.chat.id

    if cid in users and users[cid].get("plan"):
        await msg.answer(
            f"👋 Xush kelibsiz, *{users[cid]['name']}*\\!\n\n"
            f"SAT rejangiz tayyor\\. Nima qilmoqchisiz?",
            parse_mode="MarkdownV2",
            reply_markup=kb_main(cid)
        )
        return

    await msg.answer(
        "🎯 *SAT Tayyorgarlik Botiga xush kelibsiz\\!*\n\n"
        "Men sizga shaxsiy kunlik SAT reja tuzib beraman va har kuni dars eslatmasini yuboraman\\.\n\n"
        "Boshlaylik\\! Ismingizni yozing 👇",
        parse_mode="MarkdownV2"
    )
    await state.set_state(Reg.name)

# ─────────────────────────────────────────────────────────────────────────────
#  Ro'yxatdan o'tish — FSM
# ─────────────────────────────────────────────────────────────────────────────
@dp.message(Reg.name)
async def step_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text.strip())
    await msg.answer(
        f"Zo'r, *{msg.text.strip()}*\\! 🙌\n\n"
        "📅 SAT imtihon sanangizni kiriting\\:\n"
        "Format: `DD.MM.YYYY` — masalan `15.08.2025`",
        parse_mode="MarkdownV2"
    )
    await state.set_state(Reg.exam_date)

@dp.message(Reg.exam_date)
async def step_date(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    try:
        exam = datetime.strptime(txt, "%d.%m.%Y").date()
        days_left = (exam - datetime.now().date()).days
        if days_left < 7:
            await msg.answer("⚠️ Sana kamida 1 hafta oldin bo'lishi kerak\\. Qayta kiriting:", parse_mode="MarkdownV2")
            return
        await state.update_data(exam_date=txt, days_left=days_left)
        await msg.answer(
            f"✅ *{txt}* — imtihonga *{days_left} kun* qoldi\\!\n\n"
            "📊 Hozirgi SAT darajangiz?",
            parse_mode="MarkdownV2",
            reply_markup=kb_level()
        )
        await state.set_state(Reg.level)
    except ValueError:
        await msg.answer("⚠️ Format noto'g'ri\\. Misol: `15.08.2025`", parse_mode="MarkdownV2")

@dp.callback_query(F.data.startswith("level:"), Reg.level)
async def step_level(cb: CallbackQuery, state: FSMContext):
    level = cb.data.split(":")[1]
    level_names = {
        "yoq":"Hali imtihon topshirmaganman",
        "800past":"800 dan past",
        "800_1000":"800–1000",
        "1000_1200":"1000–1200",
        "1200_1400":"1200–1400",
        "1400plus":"1400+"
    }
    await state.update_data(level=level_names[level])
    await cb.message.edit_text(
        f"✅ Daraja: *{level_names[level]}*\n\n🏆 Maqsad balingiz?",
        parse_mode="MarkdownV2",
        reply_markup=kb_target()
    )
    await state.set_state(Reg.target)

@dp.callback_query(F.data.startswith("target:"), Reg.target)
async def step_target(cb: CallbackQuery, state: FSMContext):
    target = cb.data.split(":")[1]
    await state.update_data(target=target)
    await cb.message.edit_text(
        f"✅ Maqsad: *{target}\\+ ball*\n\n⏱ Kuniga qancha vaqt ajrata olasiz?",
        parse_mode="MarkdownV2",
        reply_markup=kb_time()
    )
    await state.set_state(Reg.daily_time)

@dp.callback_query(F.data.startswith("time:"), Reg.daily_time)
async def step_time(cb: CallbackQuery, state: FSMContext):
    daily_time = cb.data.split(":")[1]
    await state.update_data(daily_time=daily_time)
    data = await state.get_data()
    await state.clear()

    await cb.message.edit_text(
        f"⚙️ *{data['name']}, rejangiz tuzilmoqda\\.\\.\\.*\n\n"
        f"📅 Imtihon: {data['exam_date']}\n"
        f"🎯 Maqsad: {data['target']}\\+ ball\n"
        f"⏱ Kunlik: {daily_time}\n\n"
        f"Bir oz kuting\\.\\.\\. 🤖",
        parse_mode="MarkdownV2"
    )

    days_left   = data["days_left"]
    weeks_count = min((days_left // 7), 8)

    plan = await build_plan_ai(
        data["name"], data["level"],
        data["target"], daily_time,
        days_left, weeks_count
    )

    cid = cb.message.chat.id
    users[cid] = {
        "name":       data["name"],
        "exam_date":  data["exam_date"],
        "level":      data["level"],
        "target":     data["target"],
        "daily_time": daily_time,
        "days_left":  days_left,
        "plan":       plan,
        "start_date": datetime.now().date().isoformat(),
        "week":       0,
    }

    # Birinchi haftani ko'rsat
    w = plan["weeks"][0]
    days_text = "\n".join(
        f"  *{d['day']}* — {d['subject']} \\({d['duration']}\\)"
        for d in w["days"]
    )

    tip_safe = plan['tip'].replace('.','\\.')
    resources_text = "\n".join(f"  • {r}" for r in plan["resources"])

    await cb.message.edit_text(
        f"✅ *{data['name']}, rejangiz tayyor\\!*\n\n"
        f"📅 Imtihonga *{days_left} kun*, *{weeks_count} hafta*\n"
        f"🏆 Maqsad: *{data['target']}\\+ ball*\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"*1\\-hafta: {w['title']}*\n\n"
        f"{days_text}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"📚 *Manbalar:*\n{resources_text}\n\n"
        f"💡 {tip_safe}\n\n"
        f"_Har kuni ertalab 8:00 da dars eslatmasini olasiz\\!_ 🔔",
        parse_mode="MarkdownV2",
        reply_markup=kb_main(cid)
    )

# ─────────────────────────────────────────────────────────────────────────────
#  Callback tugmalar
# ─────────────────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "today")
async def cb_today(cb: CallbackQuery):
    cid = cb.message.chat.id
    user = users.get(cid)
    if not user:
        await cb.answer("Avval /start bosing!")
        return
    lesson = get_today_lesson(user)
    if not lesson:
        await cb.message.answer("🎉 Reja tugadi\\! Imtihonga tayyor bo'lganingiz bilan tabriklaymiz\\!", parse_mode="MarkdownV2")
        return
    await cb.message.answer(lesson, parse_mode="MarkdownV2", reply_markup=kb_main(cid))
    await cb.answer()

@dp.callback_query(F.data == "fullplan")
async def cb_fullplan(cb: CallbackQuery):
    cid = cb.message.chat.id
    user = users.get(cid)
    if not user:
        await cb.answer("Avval /start bosing!")
        return
    plan = user["plan"]
    text = f"📋 *{user['name']} — To'liq SAT rejasi*\n\n"
    for w in plan["weeks"]:
        text += f"*{w['week']}\\-hafta: {w['title']}*\n"
        for d in w["days"]:
            subj = d['subject'].replace('-','\\-')
            text += f"  {d['day']}: {subj}\n"
        text += "\n"
    await cb.message.answer(text, parse_mode="MarkdownV2", reply_markup=kb_main(cid))
    await cb.answer()

@dp.callback_query(F.data == "progress")
async def cb_progress(cb: CallbackQuery):
    cid = cb.message.chat.id
    user = users.get(cid)
    if not user:
        await cb.answer("Avval /start bosing!")
        return
    start = datetime.fromisoformat(user["start_date"]).date()
    today = datetime.now().date()
    days_done = (today - start).days
    days_left = user["days_left"] - days_done
    pct = min(int(days_done / user["days_left"] * 100), 100)
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)

    await cb.message.answer(
        f"📊 *Progressingiz*\n\n"
        f"`{bar}` {pct}%\n\n"
        f"✅ O'tilgan kunlar: *{days_done}*\n"
        f"⏳ Qolgan kunlar: *{max(days_left,0)}*\n"
        f"🏆 Maqsad: *{user['target']}\\+ ball*\n\n"
        f"_Davom eting, zo'r ketayapsiz\\!_ 💪",
        parse_mode="MarkdownV2",
        reply_markup=kb_main(cid)
    )
    await cb.answer()

@dp.callback_query(F.data == "restart")
async def cb_restart(cb: CallbackQuery, state: FSMContext):
    cid = cb.message.chat.id
    if cid in users:
        del users[cid]
    await state.clear()
    await cb.message.answer(
        "🔄 Yangi reja tuzamiz\\! Ismingizni yozing:",
        parse_mode="MarkdownV2"
    )
    await state.set_state(Reg.name)
    await cb.answer()

# ─────────────────────────────────────────────────────────────────────────────
#  Kunlik eslatma — har kuni 08:00 Toshkent vaqtida
# ─────────────────────────────────────────────────────────────────────────────
async def send_daily_reminders():
    for cid, user in list(users.items()):
        if not user.get("plan"):
            continue
        lesson = get_today_lesson(user)
        if not lesson:
            continue
        try:
            await bot.send_message(
                cid,
                f"☀️ *Xayrli tong, {user['name']}\\!*\n\n{lesson}\n\n"
                f"_Bugungi darsni bajaring — maqsadingizga yaqinlashing\\!_ 🎯",
                parse_mode="MarkdownV2",
                reply_markup=kb_main(cid)
            )
        except Exception as e:
            print(f"Eslatma yuborishda xato {cid}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
#  /help
# ─────────────────────────────────────────────────────────────────────────────
@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(
        "🤖 *SAT Tayyorgarlik Bot — Yordam*\n\n"
        "/start — Yangi reja yaratish\n"
        "/today — Bugungi darsim\n"
        "/plan — To'liq reja\n"
        "/progress — Progressim\n"
        "/help — Yordam\n\n"
        "_Har kuni 08:00 da dars eslatmasi keladi\\._",
        parse_mode="MarkdownV2"
    )

@dp.message(Command("today"))
async def cmd_today(msg: Message):
    user = users.get(msg.chat.id)
    if not user:
        await msg.answer("Avval /start bosing\\!", parse_mode="MarkdownV2")
        return
    lesson = get_today_lesson(user)
    await msg.answer(lesson or "🎉 Reja tugadi\\! Tabriklaymiz\\!", parse_mode="MarkdownV2", reply_markup=kb_main(msg.chat.id))

@dp.message(Command("plan"))
async def cmd_plan(msg: Message):
    await cb_fullplan.__wrapped__(type('cb', (), {'message': msg, 'answer': lambda s: None})())

# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────
async def main():
    scheduler.add_job(send_daily_reminders, "cron", hour=8, minute=0)
    scheduler.start()
    print("✅ SAT Tayyorgarlik Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
