import os
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command
import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# Настройки
TOKEN = os.getenv("TOKEN")
ADMIN_ID = 2021080653
TIMEZONE = ZoneInfo('Asia/Yekaterinburg')

# Инициализация бота
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Пути к БД
DB_NAME = 'fitness_bot.db'

# FSM для админ-действий
class AdminStates(StatesGroup):
    waiting_for_workout_time = State()
    editing_workout_time = State()
    waiting_for_notification_message = State()

# Создание таблиц
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_type TEXT,
                date_time TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                schedule_id INTEGER,
                FOREIGN KEY(schedule_id) REFERENCES schedules(id),
                UNIQUE(user_id, schedule_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cancellations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                schedule_id INTEGER,
                timestamp TEXT
            )
        ''')
        await db.commit()

# Начальное меню
def main_menu_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="Записаться на тренировку", callback_data='register_start')
    kb.button(text="Мои записи", callback_data='my_registrations')
    kb.button(text="Отменить запись", callback_data='cancel_registration')
    kb.button(text="Расписание на неделю", callback_data='show_schedule')
    kb.adjust(1)
    return kb.as_markup()

# Меню администратора
def admin_menu_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="Кто записался?", callback_data='admin_view_registrations')
    kb.button(text="Кто отписался?", callback_data='admin_view_cancellations')
    kb.button(text="Редактировать расписание", callback_data='admin_edit_schedule')
    kb.button(text="Расписание", callback_data='admin_show_schedule')
    kb.button(text="Отменить чью-то запись", callback_data='admin_cancel_user')
    kb.button(text="Сообщить всем", callback_data='admin_notify_custom')
    kb.button(text="В начало", callback_data='start')
    kb.adjust(1)
    return kb.as_markup()

# Кнопка "Назад" и "В начало"
def back_button():
    kb = InlineKeyboardBuilder()
    kb.button(text="В начало", callback_data='start')
    kb.button(text="Назад", callback_data='back')
    kb.adjust(2)
    return kb.as_markup()

# Отправка напоминания
async def send_reminder():
    now = datetime.now(TIMEZONE)
    reminder_time = now + timedelta(hours=4)
    reminder_str = reminder_time.strftime('%Y-%m-%d %H:%M')

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT s.date_time, s.workout_type, r.user_id
            FROM registrations r
            JOIN schedules s ON r.schedule_id = s.id
            WHERE s.date_time LIKE ?
        ''', (f"{reminder_str[:16]}%",))
        rows = await cursor.fetchall()

        for _, _, user_id in rows:
            try:
                await bot.send_message(
                    user_id,
                    "Напоминаем о предстоящей тренировке! Если не сможете прийти, отпишитесь."
                )
            except Exception:
                pass

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        await db.commit()

    if user_id == ADMIN_ID:
        await message.answer("Привет, администратор!", reply_markup=admin_menu_keyboard())
    else:
        await message.answer("Привет! Выберите действие:", reply_markup=main_menu_keyboard())

@dp.callback_query(F.data == 'register_start')
async def register_start(call: CallbackQuery):
    workouts_kb = InlineKeyboardBuilder()
    workouts_kb.button(text="Джампинг", callback_data='select_workout_jumping')
    workouts_kb.button(text="Жиротопка", callback_data='select_workout_lipolitics')
    workouts_kb.button(text="В начало", callback_data='start')
    await call.message.edit_text("Выберите тип тренировки:", reply_markup=workouts_kb.as_markup())

async def show_schedule_for_workout(call: CallbackQuery, workout_type: str):
    now = datetime.now(TIMEZONE)
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT id, date_time FROM schedules
            WHERE workout_type = ? AND date_time > ?
            ORDER BY date_time
        ''', (workout_type, now.strftime('%Y-%m-%d %H:%M')))
        rows = await cursor.fetchall()

    available_sessions = []
    for sch_id, dt in rows:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor2 = await db.execute('''
                SELECT COUNT(*) FROM registrations WHERE schedule_id = ?
            ''', (sch_id,))
            count = (await cursor2.fetchone())[0]

        if workout_type == 'Джампинг' and count >= 15:
            continue  # Пропускаем полные

        available_sessions.append((sch_id, dt))

    kb = InlineKeyboardBuilder()
    for sch_id, dt in available_sessions:
        dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
        kb.button(text=f"{dt_formatted}", callback_data=f'register_to_{sch_id}')
    kb.adjust(1)
    kb.button(text="Назад", callback_data='register_start')
    await call.message.edit_text(f"Доступные даты для {workout_type}:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith('select_workout_'))
async def select_workout(call: CallbackQuery):
    if call.data == 'select_workout_jumping':
        await show_schedule_for_workout(call, 'Джампинг')
    elif call.data == 'select_workout_lipolitics':
        await show_schedule_for_workout(call, 'Жиротопка')

@dp.callback_query(F.data.startswith('register_to_'))
async def register_to_workout(call: CallbackQuery):
    sch_id = int(call.data.split('_')[-1])
    user_id = call.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("INSERT INTO registrations (user_id, schedule_id) VALUES (?, ?)", (user_id, sch_id))
            await db.commit()
            await call.message.edit_text("Вы успешно записались!", reply_markup=back_button())
        except aiosqlite.IntegrityError:
            await call.message.edit_text("Вы уже записаны на эту тренировку.", reply_markup=back_button())

@dp.callback_query(F.data == 'my_registrations')
async def my_registrations(call: CallbackQuery):
    user_id = call.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT s.workout_type, s.date_time
            FROM registrations r
            JOIN schedules s ON r.schedule_id = s.id
            WHERE r.user_id = ?
            ORDER BY s.date_time
        ''', (user_id,))
        rows = await cursor.fetchall()

    if not rows:
        await call.message.edit_text("У вас нет записей.", reply_markup=back_button())
        return

    msg = "Ваши записи:\n"
    for t, dt in rows:
        dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
        msg += f"- {t} | {dt_formatted}\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="В начало", callback_data='start')
    kb.button(text="Назад", callback_data='back')
    kb.adjust(2)

    await call.message.edit_text(msg, reply_markup=kb.as_markup())

@dp.callback_query(F.data == 'cancel_registration')
async def cancel_registration_start(call: CallbackQuery):
    user_id = call.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT r.id, s.workout_type, s.date_time
            FROM registrations r
            JOIN schedules s ON r.schedule_id = s.id
            WHERE r.user_id = ?
            ORDER BY s.date_time
        ''', (user_id,))
        rows = await cursor.fetchall()

    if not rows:
        await call.message.edit_text("У вас нет записей.", reply_markup=back_button())
        return

    kb = InlineKeyboardBuilder()
    for reg_id, t, dt in rows:
        dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
        kb.button(text=f"{t} | {dt_formatted}", callback_data=f'cancel_reg_{reg_id}')
    kb.adjust(1)
    kb.button(text="Назад", callback_data='start')
    await call.message.edit_text("Выберите запись для отмены:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith('cancel_reg_'))
async def cancel_registration_final(call: CallbackQuery):
    reg_id = int(call.data.split('_')[-1])
    user_id = call.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        # Получаем данные о тренировке перед удалением
        cursor = await db.execute('''
            SELECT schedule_id FROM registrations WHERE id = ?
        ''', (reg_id,))
        row = await cursor.fetchone()
        if row:
            schedule_id = row[0]
            # Записываем в лог отписавшихся
            timestamp = datetime.now(TIMEZONE).isoformat()
            await db.execute('''
                INSERT INTO cancellations (user_id, schedule_id, timestamp)
                VALUES (?, ?, ?)
            ''', (user_id, schedule_id, timestamp))
        
        await db.execute("DELETE FROM registrations WHERE id = ?", (reg_id,))
        await db.commit()
    
    await call.message.edit_text("Запись отменена.", reply_markup=back_button())

@dp.callback_query(F.data == 'show_schedule')
async def show_week_schedule(call: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT workout_type, date_time FROM schedules
            WHERE date_time BETWEEN ? AND ?
            ORDER BY date_time
        ''', (
            datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M'),
            (datetime.now(TIMEZONE) + timedelta(days=7)).strftime('%Y-%m-%d %H:%M')
        ))
        rows = await cursor.fetchall()

    msg = "Расписание на неделю:\n"
    for t, dt in rows:
        dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
        msg += f"- {t} | {dt_formatted}\n"

    kb = InlineKeyboardBuilder()
    kb.button(text="В начало", callback_data='start')
    kb.button(text="Назад", callback_data='back')
    kb.adjust(2)

    await call.message.edit_text(msg, reply_markup=kb.as_markup())

# --- АДМИН-ФУНКЦИИ ---

@dp.callback_query(F.data == 'admin_show_schedule')
async def admin_show_schedule(call: CallbackQuery):
    await show_week_schedule(call)

@dp.callback_query(F.data == 'admin_view_registrations')
async def admin_view_registrations(call: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT u.username, s.workout_type, s.date_time
            FROM registrations r
            JOIN users u ON r.user_id = u.user_id
            JOIN schedules s ON r.schedule_id = s.id
            ORDER BY s.date_time
        ''')
        rows = await cursor.fetchall()

    msg = "Записавшиеся пользователи:\n"
    if rows:
        for u, t, dt in rows:
            dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
            msg += f"- {u} | {t} | {dt_formatted}\n"
    else:
        msg = "Никто не записался."

    await call.message.edit_text(msg, reply_markup=admin_menu_keyboard())

@dp.callback_query(F.data == 'admin_view_cancellations')
async def admin_view_cancellations(call: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT u.username, s.workout_type, s.date_time, c.timestamp
            FROM cancellations c
            JOIN users u ON c.user_id = u.user_id
            JOIN schedules s ON c.schedule_id = s.id
            ORDER BY c.timestamp DESC
        ''')
        rows = await cursor.fetchall()

    msg = "Отписавшиеся пользователи:\n"
    if rows:
        for u, t, dt, ts in rows:
            dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
            ts_formatted = datetime.fromisoformat(ts).strftime('%d.%m.%Y %H:%M')
            msg += f"- {u} | {t} | {dt_formatted} | {ts_formatted}\n"
    else:
        msg = "Никто не отписался."

    await call.message.edit_text(msg, reply_markup=admin_menu_keyboard())

@dp.callback_query(F.data == 'admin_edit_schedule')
async def admin_edit_schedule_start(call: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="Добавить тренировку", callback_data='add_workout')
    kb.button(text="Удалить тренировку", callback_data='delete_workout')
    kb.button(text="Изменить тренировку", callback_data='edit_workout')
    kb.button(text="Обновить расписание на неделю", callback_data='admin_reset_schedule_confirm')
    kb.button(text="Назад", callback_data='admin_panel')
    await call.message.edit_text("Редактирование расписания:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == 'admin_reset_schedule_confirm')
async def admin_reset_schedule_confirm(call: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, обновить", callback_data='admin_reset_schedule')
    kb.button(text="Нет, вернуться", callback_data='admin_edit_schedule')
    await call.message.edit_text("Вы уверены, что хотите обновить расписание на следующую неделю?", reply_markup=kb.as_markup())

@dp.callback_query(F.data == 'admin_reset_schedule')
async def admin_reset_schedule(call: CallbackQuery):
    await load_default_schedule()
    await call.message.edit_text("Расписание обновлено на следующую неделю.", reply_markup=admin_menu_keyboard())

async def load_default_schedule():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM schedules")
        now = datetime.now(TIMEZONE)
        week_later = now + timedelta(days=7)

        schedule = []
        for i in range((week_later - now).days):
            day = now + timedelta(days=i)
            d = day.strftime('%Y-%m-%d')
            if day.weekday() == 0:  # Пн
                schedule.append(('Джампинг', f"{d} 10:00"))
                schedule.append(('Джампинг', f"{d} 19:30"))
            elif day.weekday() == 2:  # Ср
                schedule.append(('Джампинг', f"{d} 10:00"))
                schedule.append(('Жиротопка', f"{d} 19:30"))
            elif day.weekday() == 4:  # Пт
                schedule.append(('Джампинг', f"{d} 10:00"))
                schedule.append(('Джампинг', f"{d} 19:30"))
            elif day.weekday() == 5:  # Сб
                schedule.append(('Жиротопка', f"{d} 13:00"))

        for t, dt in schedule:
            await db.execute("INSERT INTO schedules (workout_type, date_time) VALUES (?, ?)", (t, dt))
        await db.commit()

@dp.callback_query(F.data == 'add_workout')
async def add_workout_start(call: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text="Джампинг", callback_data='choose_workout_type_add_jumping')
    kb.button(text="Жиротопка", callback_data='choose_workout_type_add_lipolitics')
    kb.button(text="Назад", callback_data='admin_edit_schedule')
    await call.message.edit_text("Выберите тип тренировки для добавления:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith('choose_workout_type_add_'))
async def choose_workout_type_add(call: CallbackQuery, state: FSMContext):
    workout_type = 'Джампинг' if 'jumping' in call.data else 'Жиротопка'
    await state.update_data(workout_type=workout_type)
    await call.message.edit_text("Введите дату и время в формате: 01.02.2026 10:00")
    await state.set_state(AdminStates.waiting_for_workout_time)

@dp.message(AdminStates.waiting_for_workout_time)
async def handle_add_workout_input(message: Message, state: FSMContext):
    input_text = message.text.strip()
    try:
        dt_obj = datetime.strptime(input_text, '%d.%m.%Y %H:%M')
        dt_str = dt_obj.strftime('%Y-%m-%d %H:%M')
        data = await state.get_data()
        workout_type = data.get('workout_type', 'Неизвестно')

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''
                INSERT INTO schedules (workout_type, date_time) VALUES (?, ?)
            ''', (workout_type, dt_str))
            await db.commit()

        await message.answer(f"Тренировка '{workout_type}' добавлена на {input_text}.", reply_markup=admin_menu_keyboard())
        await state.clear()
    except ValueError:
        await message.answer("Неверный формат. Попробуйте снова: 01.02.2026 10:00")

@dp.callback_query(F.data == 'delete_workout')
async def delete_workout_start(call: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT id, workout_type, date_time FROM schedules
            ORDER BY date_time
        ''')
        rows = await cursor.fetchall()

    if not rows:
        await call.message.edit_text("Нет тренировок для удаления.", reply_markup=admin_menu_keyboard())
        return

    kb = InlineKeyboardBuilder()
    for sch_id, t, dt in rows:
        dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
        kb.button(text=f"{t} | {dt_formatted}", callback_data=f'delete_workout_{sch_id}')
    kb.adjust(1)
    kb.button(text="Назад", callback_data='admin_edit_schedule')
    await call.message.edit_text("Выберите тренировку для удаления:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith('delete_workout_'))
async def delete_workout_final(call: CallbackQuery):
    sch_id = int(call.data.split('_')[-1])

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM schedules WHERE id = ?", (sch_id,))
        await db.commit()

    await call.message.edit_text("Тренировка удалена.", reply_markup=admin_menu_keyboard())

@dp.callback_query(F.data == 'edit_workout')
async def edit_workout_start(call: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT id, workout_type, date_time FROM schedules
            ORDER BY date_time
        ''')
        rows = await cursor.fetchall()

    if not rows:
        await call.message.edit_text("Нет тренировок для изменения.", reply_markup=admin_menu_keyboard())
        return

    kb = InlineKeyboardBuilder()
    for sch_id, t, dt in rows:
        dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
        kb.button(text=f"{t} | {dt_formatted}", callback_data=f'edit_workout_{sch_id}')
    kb.adjust(1)
    kb.button(text="Назад", callback_data='admin_edit_schedule')
    await call.message.edit_text("Выберите тренировку для изменения:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith('edit_workout_'))
async def edit_workout_choose_field(call: CallbackQuery, state: FSMContext):
    sch_id = int(call.data.split('_')[-1])
    await state.update_data(editing_sch_id=sch_id)

    kb = InlineKeyboardBuilder()
    kb.button(text="Изменить тип", callback_data='edit_field_type')
    kb.button(text="Изменить дату и время", callback_data='edit_field_datetime')
    kb.button(text="Назад", callback_data='edit_workout')
    await call.message.edit_text("Что вы хотите изменить?", reply_markup=kb.as_markup())

@dp.callback_query(F.data == 'edit_field_type')
async def edit_workout_type(call: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text="Джампинг", callback_data='set_workout_type_jumping')
    kb.button(text="Жиротопка", callback_data='set_workout_type_lipolitics')
    kb.button(text="Назад", callback_data='edit_workout')
    await call.message.edit_text("Выберите новый тип тренировки:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith('set_workout_type_'))
async def set_workout_type_final(call: CallbackQuery, state: FSMContext):
    new_type = 'Джампинг' if 'jumping' in call.data else 'Жиротопка'
    data = await state.get_data()
    sch_id = data.get('editing_sch_id')

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            UPDATE schedules SET workout_type = ? WHERE id = ?
        ''', (new_type, sch_id))
        await db.commit()

    await call.message.edit_text(f"Тип тренировки изменён на '{new_type}'.", reply_markup=admin_menu_keyboard())
    await state.clear()

@dp.callback_query(F.data == 'edit_field_datetime')
async def edit_workout_datetime_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("Введите новую дату и время в формате: 01.02.2026 10:00")
    await state.set_state(AdminStates.editing_workout_time)

@dp.message(AdminStates.editing_workout_time)
async def edit_workout_datetime_final(message: Message, state: FSMContext):
    input_text = message.text.strip()
    try:
        dt_obj = datetime.strptime(input_text, '%d.%m.%Y %H:%M')
        dt_str = dt_obj.strftime('%Y-%m-%d %H:%M')
        data = await state.get_data()
        sch_id = data.get('editing_sch_id')

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''
                UPDATE schedules SET date_time = ? WHERE id = ?
            ''', (dt_str, sch_id))
            await db.commit()

        await message.answer(f"Время тренировки изменено на {input_text}.", reply_markup=admin_menu_keyboard())
        await state.clear()
    except ValueError:
        await message.answer("Неверный формат. Попробуйте снова: 01.02.2026 10:00")

@dp.callback_query(F.data == 'admin_notify_custom')
async def admin_notify_custom_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("Введите сообщение для отправки всем записавшимся:")
    await state.set_state(AdminStates.waiting_for_notification_message)

@dp.message(AdminStates.waiting_for_notification_message)
async def admin_notify_custom_send(message: Message, state: FSMContext):
    text = message.text
    # Получаем всех пользователей, записавшихся на любую тренировку
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT DISTINCT r.user_id FROM registrations r
        ''')
        user_ids = [row[0] for row in await cursor.fetchall()]

    for uid in user_ids:
        try:
            await bot.send_message(uid, f"📢 {text}")
        except Exception:
            pass

    await message.answer("Сообщение отправлено всем записавшимся.", reply_markup=admin_menu_keyboard())
    await state.clear()

@dp.callback_query(F.data == 'admin_panel')
async def admin_panel_redirect(call: CallbackQuery):
    await call.message.edit_text("Привет, администратор!", reply_markup=admin_menu_keyboard())

@dp.callback_query(F.data == 'start')
async def go_home(call: CallbackQuery):
    user_id = call.from_user.id
    if user_id == ADMIN_ID:
        await call.message.edit_text("Привет, администратор!", reply_markup=admin_menu_keyboard())
    else:
        await call.message.edit_text("Выберите действие:", reply_markup=main_menu_keyboard())

@dp.callback_query(F.data == 'back')
async def go_back(call: CallbackQuery):
    await call.message.edit_text("Выберите действие:", reply_markup=main_menu_keyboard())

@dp.callback_query(F.data == 'admin_cancel_user')
async def admin_cancel_user_start(call: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT r.id, u.username, s.workout_type, s.date_time
            FROM registrations r
            JOIN users u ON r.user_id = u.user_id
            JOIN schedules s ON r.schedule_id = s.id
            ORDER BY s.date_time
        ''')
        rows = await cursor.fetchall()

    if not rows:
        await call.message.edit_text("Нет записей для отмены.", reply_markup=admin_menu_keyboard())
        return

    kb = InlineKeyboardBuilder()
    for reg_id, u, t, dt in rows:
        dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
        kb.button(text=f"{u} | {t} | {dt_formatted}", callback_data=f'admin_cancel_reg_{reg_id}')
    kb.adjust(1)
    kb.button(text="Назад", callback_data='admin_panel')
    await call.message.edit_text("Выберите запись для отмены:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith('admin_cancel_reg_'))
async def admin_cancel_user_final(call: CallbackQuery):
    reg_id = int(call.data.split('_')[-1])

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT r.user_id, s.workout_type, s.date_time
            FROM registrations r
            JOIN schedules s ON r.schedule_id = s.id
            WHERE r.id = ?
        ''', (reg_id,))
        row = await cursor.fetchone()
        if row:
            user_id, workout, dt = row
            dt_formatted = datetime.strptime(dt, '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')
            
            # Удаляем запись
            await db.execute("DELETE FROM registrations WHERE id = ?", (reg_id,))
            await db.commit()

            # Отправляем пользователю уведомление
            try:
                await bot.send_message(user_id, f"Ваша запись на тренировку '{workout}' ({dt_formatted}) была отменена администратором.")
            except Exception:
                pass

    await call.message.edit_text("Запись отменена.", reply_markup=admin_menu_keyboard())

async def main():
    await init_db()
    await load_default_schedule()

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(send_reminder, CronTrigger(hour='*/4'))
    scheduler.start()

    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
