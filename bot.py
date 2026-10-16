import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from bson import ObjectId
from bson.errors import InvalidId

import config
from database import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("giveaway-bot")

router = Router()


# ══════════════════════════════════════════════════════════════════
# Callback data
# ══════════════════════════════════════════════════════════════════
class MenuCB(CallbackData, prefix="menu"):
    action: str  # create | manage | back


class ManageCB(CallbackData, prefix="mng"):
    action: str  # select | addvote | removevote | leaderboard | end | endconfirm | back
    code: str


class VoteCB(CallbackData, prefix="vote"):
    code: str
    pid: str


class JoinCheckCB(CallbackData, prefix="jchk"):
    code: str


# ══════════════════════════════════════════════════════════════════
# FSM
# ══════════════════════════════════════════════════════════════════
class CreateGiveaway(StatesGroup):
    waiting_channel = State()


class AdjustVote(StatesGroup):
    waiting_input = State()  # data: adjust_action ("addvote"/"removevote"), adjust_code


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════
def mention(user_id: int, name: str) -> str:
    safe = name.replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡️ ᴄʀᴇᴀᴛᴇ ɢɪᴠᴇᴀᴡᴀʏ",
                    callback_data=MenuCB(action="create").pack(),
                    style="success",
                ),
                InlineKeyboardButton(
                    text="👨🏼‍💻 ᴍᴀɴᴀɢᴇ",
                    callback_data=MenuCB(action="manage").pack(),
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏵 ᴀᴅᴅ ᴍᴇ",
                    url=f"https://t.me/{config.BOT_USERNAME}?startgroup=true",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="❤️ ᴏᴡɴᴇʀ",
                    url=f"https://t.me/{config.OWNER_USERNAME}",
                    style="danger",
                ),
            ],
        ]
    )


def back_kb(callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 ʙᴀᴄᴋ", callback_data=callback, style="danger")]]
    )


def manage_panel_kb(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ ᴀᴅᴅ ᴠᴏᴛᴇ",
                    callback_data=ManageCB(action="addvote", code=code).pack(),
                    style="success",
                ),
                InlineKeyboardButton(
                    text="➖ ʀᴇᴍᴏᴠᴇ ᴠᴏᴛᴇ",
                    callback_data=ManageCB(action="removevote", code=code).pack(),
                    style="danger",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏆 ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ",
                    callback_data=ManageCB(action="leaderboard", code=code).pack(),
                    style="primary",
                ),
                InlineKeyboardButton(
                    text="🛑 ᴇɴᴅ ɢɪᴠᴇᴀᴡᴀʏ",
                    callback_data=ManageCB(action="end", code=code).pack(),
                    style="danger",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 ʙᴀᴄᴋ",
                    callback_data=MenuCB(action="manage").pack(),
                    style="danger",
                )
            ],
        ]
    )


async def resolve_channel(bot: Bot, raw: str):
    """Resolve a public @username or a numeric -100 id to a Chat object."""
    raw = raw.strip()
    if raw.startswith("@") or raw.startswith("-") or raw.lstrip("-").isdigit():
        target = raw if raw.startswith("@") else int(raw)
        return await bot.get_chat(target)
    raise ValueError("invalid format")


async def is_bot_admin(bot: Bot, chat_id: int) -> bool:
    member = await bot.get_chat_member(chat_id, bot.id)
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


async def is_user_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramBadRequest:
        return False
    return member.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    )


def vote_button_text(votes: int) -> str:
    return f"🗳 ᴠᴏᴛᴇ ({votes})"


async def build_entry_text(name_html: str, user_id: int) -> str:
    return f"😎 <b>Name:</b> {name_html}\n💌 <b>ID:</b> <code>{user_id}</code>"


async def post_entry_card(bot: Bot, channel_id: int, code: str, participant: dict):
    text = await build_entry_text(
        mention(participant["user_id"], participant["name"]), participant["user_id"]
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=vote_button_text(participant["votes"]),
                    callback_data=VoteCB(code=code, pid=str(participant["_id"])).pack(),
                    style="success",
                )
            ]
        ]
    )
    msg = await bot.send_message(channel_id, text, reply_markup=kb)
    await db.set_entry_message(participant["_id"], msg.message_id)


# ══════════════════════════════════════════════════════════════════
# Maintenance-mode gate (applies to everyone except OWNER_ID)
# ══════════════════════════════════════════════════════════════════
@router.message.outer_middleware()
async def maintenance_message_gate(handler, event: Message, data):
    if event.from_user and event.from_user.id != config.OWNER_ID:
        enabled = await db.get_setting("bot_enabled", True)
        if not enabled:
            await event.answer(
                "🚧 ᴛʜᴇ ʙᴏᴛ ɪꜱ ᴄᴜʀʀᴇɴᴛʟʏ ᴜɴᴅᴇʀ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ. ᴘʟᴇᴀꜱᴇ ᴄʜᴇᴄᴋ ʙᴀᴄᴋ ʟᴀᴛᴇʀ."
            )
            return
    return await handler(event, data)


@router.callback_query.outer_middleware()
async def maintenance_callback_gate(handler, event: CallbackQuery, data):
    if event.from_user and event.from_user.id != config.OWNER_ID:
        enabled = await db.get_setting("bot_enabled", True)
        if not enabled:
            await event.answer("🚧 Bot is under maintenance.", show_alert=True)
            return
    return await handler(event, data)


# ══════════════════════════════════════════════════════════════════
# /start (also handles deep-link giveaway join payload)
# ══════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot, state: FSMContext):
    await state.clear()
    user = message.from_user
    await db.add_user(user.id, user.username, user.full_name)

    payload = command.args
    if payload:
        await handle_join(message, bot, payload.strip())
        return

    text = (
        f"ʜᴇʟʟᴏ {mention(user.id, user.full_name)}\n"
        "ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴛʜᴇ ɢɪᴠᴇᴀᴡᴀʏ ᴠᴏᴛɪɴɢ ʙᴏᴛ!\n"
        "ʜᴇʀᴇ ʏᴏᴜ ᴄᴀɴ:\n"
        "• 🗳️ ᴄʀᴇᴀᴛᴇ ʏᴏᴜʀ ᴏᴡɴ ᴠᴏᴛᴇ ɢɪᴠᴇᴀᴡᴀʏ\n"
        "• 📊 ᴛʀᴀᴄᴋ ʟɪᴠᴇ ᴠᴏᴛᴇ ᴄᴏᴜɴᴛꜱ\n"
        "• 🏆 ᴠɪᴇᴡ ᴡɪɴɴᴇʀꜱ ᴀꜰᴛᴇʀ ᴇᴀᴄʜ ɢɪᴠᴇᴀᴡᴀʏ ᴇɴᴅꜱ\n"
        "⚠️ ᴇᴀᴄʜ ᴜꜱᴇʀ ᴄᴀɴ ᴄᴀꜱᴛ ᴏɴʟʏ ᴏɴᴇ ᴠᴏᴛᴇ ᴘᴇʀ ɢɪᴠᴇᴀᴡᴀʏ.\n"
        "🍀 ɢᴏᴏᴅ ʟᴜᴄᴋ, ᴀɴᴅ ᴛʜᴀɴᴋꜱ ꜰᴏʀ ᴘᴀʀᴛɪᴄɪᴘᴀᴛɪɴɢ!"
    )
    await message.answer(text, reply_markup=main_menu_kb())


# ══════════════════════════════════════════════════════════════════
# Main menu callbacks
# ══════════════════════════════════════════════════════════════════
@router.callback_query(MenuCB.filter(F.action == "create"))
async def cb_create(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateGiveaway.waiting_channel)
    await callback.message.edit_text(
        "⚡ <b>CREATE GIVEAWAY</b>\n\n"
        "👈 Send your channel information:\n\n"
        "Public: <code>@username</code>\n"
        "Private: <code>-1001234567890</code>\n\n"
        "ℹ️ Make sure the bot is an <b>admin</b> in that channel first.",
        reply_markup=back_kb(MenuCB(action="back").pack()),
    )
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == "back"))
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = callback.from_user
    text = (
        f"ʜᴇʟʟᴏ {mention(user.id, user.full_name)}\n"
        "ᴡᴇʟᴄᴏᴍᴇ ʙᴀᴄᴋ ᴛᴏ ᴛʜᴇ ᴍᴀɪɴ ᴍᴇɴᴜ!"
    )
    await callback.message.edit_text(text, reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(MenuCB.filter(F.action == "manage"))
async def cb_manage(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    giveaways = await db.get_active_giveaways_by_owner(callback.from_user.id)
    if not giveaways:
        await callback.message.edit_text(
            "📭 ʏᴏᴜ ʜᴀᴠᴇ ɴᴏ ᴀᴄᴛɪᴠᴇ ɢɪᴠᴇᴀᴡᴀʏꜱ.",
            reply_markup=back_kb(MenuCB(action="back").pack()),
        )
        await callback.answer()
        return

    rows = [
        [
            InlineKeyboardButton(
                text=f"📢 {g['channel_title']}",
                callback_data=ManageCB(action="select", code=g["code"]).pack(),
                style="primary",
            )
        ]
        for g in giveaways
    ]
    rows.append(
        [InlineKeyboardButton(text="🔙 ʙᴀᴄᴋ", callback_data=MenuCB(action="back").pack(), style="danger")]
    )
    await callback.message.edit_text(
        "👨🏼‍💻 <b>Select a giveaway to manage:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════
# Create giveaway — receive channel info
# ══════════════════════════════════════════════════════════════════
@router.message(CreateGiveaway.waiting_channel, ~F.text.startswith("/"))
async def process_channel_info(message: Message, state: FSMContext, bot: Bot):
    raw = (message.text or "").strip()
    try:
        chat = await resolve_channel(bot, raw)
    except (ValueError, TelegramBadRequest):
        await message.answer(
            "❌ ᴄᴏᴜʟᴅɴ'ᴛ ꜰɪɴᴅ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ. ᴍᴀᴋᴇ ꜱᴜʀᴇ ᴛʜᴇ ꜰᴏʀᴍᴀᴛ ɪꜱ ᴄᴏʀʀᴇᴄᴛ ᴀɴᴅ "
            "ᴛʜᴇ ʙᴏᴛ ʜᴀꜱ ᴀʟʀᴇᴀᴅʏ ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ᴛᴏ ɪᴛ:\n\n"
            "Public: <code>@username</code>\nPrivate: <code>-1001234567890</code>"
        )
        return

    if not await is_bot_admin(bot, chat.id):
        await message.answer(
            "❌ ɪ'ᴍ ɴᴏᴛ ᴀɴ ᴀᴅᴍɪɴ ɪɴ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ.\n"
            "ᴘʟᴇᴀꜱᴇ ᴀᴅᴅ ᴍᴇ ᴀꜱ ᴀᴅᴍɪɴ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ."
        )
        return

    giveaway = await db.create_giveaway(
        owner_id=message.from_user.id,
        channel_id=chat.id,
        channel_title=chat.title or chat.username or str(chat.id),
        channel_username=chat.username,
    )

    join_link = f"https://t.me/{config.BOT_USERNAME}?start={giveaway['code']}"
    join_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎉 ᴊᴏɪɴ ɢɪᴠᴇᴀᴡᴀʏ", url=join_link, style="success")]]
    )
    announce = await bot.send_message(
        chat.id,
        "🎉 <b>A new giveaway has started!</b>\n\n"
        "Tap the button below to join and get a chance to win!",
        reply_markup=join_kb,
    )
    await db.set_join_message(giveaway["_id"], announce.message_id)

    await state.clear()
    await message.answer(
        "✅ <b>Giveaway created!</b>\n\n"
        f"📢 Channel: {giveaway['channel_title']}\n"
        f"🔗 Join link: {join_link}\n\n"
        "The join post has been published in your channel.",
        reply_markup=main_menu_kb(),
    )


# ══════════════════════════════════════════════════════════════════
# Join flow (from deep link)
# ══════════════════════════════════════════════════════════════════
async def handle_join(message: Message, bot: Bot, code: str):
    giveaway = await db.get_giveaway_by_code(code)
    if not giveaway:
        await message.answer("❌ ᴛʜɪꜱ ɢɪᴠᴇᴀᴡᴀʏ ʟɪɴᴋ ɪꜱ ɪɴᴠᴀʟɪᴅ.")
        return
    if giveaway["status"] != "active":
        await message.answer("⛔ ᴛʜɪꜱ ɢɪᴠᴇᴀᴡᴀʏ ʜᴀꜱ ᴀʟʀᴇᴀᴅʏ ᴇɴᴅᴇᴅ.")
        return

    user = message.from_user
    if not await is_user_member(bot, giveaway["channel_id"], user.id):
        channel_url = (
            f"https://t.me/{giveaway['channel_username']}"
            if giveaway.get("channel_username")
            else None
        )
        buttons = []
        if channel_url:
            buttons.append([InlineKeyboardButton(text="📢 ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ", url=channel_url, style="primary")])
        buttons.append(
            [
                InlineKeyboardButton(
                    text="✅ ɪ'ᴠᴇ ᴊᴏɪɴᴇᴅ",
                    callback_data=JoinCheckCB(code=code).pack(),
                    style="success",
                )
            ]
        )
        await message.answer(
            "⚠️ ʏᴏᴜ ᴍᴜꜱᴛ ᴊᴏɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ꜰɪʀꜱᴛ ᴛᴏ ᴘᴀʀᴛɪᴄɪᴘᴀᴛᴇ.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        return

    await register_participant(message.answer, bot, giveaway, user)


async def register_participant(reply_func, bot: Bot, giveaway: dict, user):
    participant, is_new = await db.add_participant(
        giveaway["_id"], user.id, user.full_name, user.username
    )
    if not is_new:
        await reply_func("ℹ️ ʏᴏᴜ'ʀᴇ ᴀʟʀᴇᴀᴅʏ ʀᴇɢɪꜱᴛᴇʀᴇᴅ ɪɴ ᴛʜɪꜱ ɢɪᴠᴇᴀᴡᴀʏ!")
        return

    await post_entry_card(bot, giveaway["channel_id"], giveaway["code"], participant)
    await reply_func(
        "🎉 ʏᴏᴜ'ᴠᴇ ᴊᴏɪɴᴇᴅ ᴛʜᴇ ɢɪᴠᴇᴀᴡᴀʏ!\n"
        "ʏᴏᴜʀ ᴇɴᴛʀʏ ʜᴀꜱ ʙᴇᴇɴ ᴘᴏꜱᴛᴇᴅ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ. ɢᴏᴏᴅ ʟᴜᴄᴋ! 🍀"
    )


@router.callback_query(JoinCheckCB.filter())
async def cb_join_check(callback: CallbackQuery, callback_data: JoinCheckCB, bot: Bot):
    giveaway = await db.get_giveaway_by_code(callback_data.code)
    if not giveaway or giveaway["status"] != "active":
        await callback.answer("This giveaway is no longer active.", show_alert=True)
        return

    user = callback.from_user
    if not await is_user_member(bot, giveaway["channel_id"], user.id):
        await callback.answer("❌ You still haven't joined the channel.", show_alert=True)
        return

    await callback.answer("✅ Membership verified!")
    await register_participant(callback.message.answer, bot, giveaway, user)


# ══════════════════════════════════════════════════════════════════
# Voting
# ══════════════════════════════════════════════════════════════════
@router.callback_query(VoteCB.filter())
async def cb_vote(callback: CallbackQuery, callback_data: VoteCB, bot: Bot):
    giveaway = await db.get_giveaway_by_code(callback_data.code)
    if not giveaway or giveaway["status"] != "active":
        await callback.answer("⛔ This giveaway has ended.", show_alert=True)
        return

    try:
        pid = ObjectId(callback_data.pid)
    except InvalidId:
        await callback.answer("Invalid participant.", show_alert=True)
        return

    participant = await db.get_participant(pid)
    if not participant:
        await callback.answer("Participant not found.", show_alert=True)
        return

    voter = callback.from_user
    if voter.id == participant["user_id"]:
        await callback.answer("🙅 You can't vote for yourself!", show_alert=True)
        return

    if not await is_user_member(bot, giveaway["channel_id"], voter.id):
        await callback.answer(
            "⚠️ You must be a member of the channel to vote.", show_alert=True
        )
        return

    ok = await db.cast_vote(giveaway["_id"], pid, voter.id)
    if not ok:
        await callback.answer("✅ You've already voted in this giveaway!", show_alert=True)
        return

    updated = await db.get_participant(pid)
    new_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=vote_button_text(updated["votes"]),
                    callback_data=callback_data.pack(),
                    style="success",
                )
            ]
        ]
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except TelegramBadRequest:
        pass
    await callback.answer("🗳 Vote recorded!")


# ══════════════════════════════════════════════════════════════════
# Manage panel
# ══════════════════════════════════════════════════════════════════
async def require_owner_giveaway(callback: CallbackQuery, code: str):
    giveaway = await db.get_giveaway_by_code(code)
    if not giveaway or giveaway["owner_id"] != callback.from_user.id:
        await callback.answer("Not found or you're not the owner.", show_alert=True)
        return None
    return giveaway


@router.callback_query(ManageCB.filter(F.action == "select"))
async def cb_manage_select(callback: CallbackQuery, callback_data: ManageCB, state: FSMContext):
    await state.clear()
    giveaway = await require_owner_giveaway(callback, callback_data.code)
    if not giveaway:
        return
    count = await db.get_participant_count(giveaway["_id"])
    await callback.message.edit_text(
        f"📢 <b>{giveaway['channel_title']}</b>\n"
        f"👥 Participants: {count}\n"
        f"🗳 Total votes: {giveaway['total_votes']}\n\n"
        "Choose an action:",
        reply_markup=manage_panel_kb(giveaway["code"]),
    )
    await callback.answer()


@router.callback_query(ManageCB.filter(F.action == "leaderboard"))
async def cb_leaderboard(callback: CallbackQuery, callback_data: ManageCB):
    giveaway = await require_owner_giveaway(callback, callback_data.code)
    if not giveaway:
        return
    top = await db.get_leaderboard(giveaway["_id"], config.LEADERBOARD_SIZE)
    total = await db.get_participant_count(giveaway["_id"])

    if not top:
        text = "📭 No participants yet."
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, p in enumerate(top):
            rank = medals[i] if i < 3 else f"{i + 1}."
            lines.append(
                f"{rank} {p['name']} (<code>{p['user_id']}</code>) — <b>{p['votes']}</b> votes"
            )
        text = "🏆 <b>Live Leaderboard</b>\n\n" + "\n".join(lines)
    text += f"\n\n👥 <b>Total participants:</b> {total}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 ʀᴇꜰʀᴇꜱʜ",
                    callback_data=ManageCB(
                        action="leaderboard", code=giveaway["code"]
                    ).pack(),
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 ʙᴀᴄᴋ",
                    callback_data=ManageCB(action="select", code=giveaway["code"]).pack(),
                    style="danger",
                )
            ],
        ]
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass
    await callback.answer("🔄 Refreshed")


@router.callback_query(ManageCB.filter(F.action.in_({"addvote", "removevote"})))
async def cb_ask_vote_input(
    callback: CallbackQuery, callback_data: ManageCB, state: FSMContext
):
    giveaway = await require_owner_giveaway(callback, callback_data.code)
    if not giveaway:
        return

    await state.set_state(AdjustVote.waiting_input)
    await state.update_data(adjust_action=callback_data.action, adjust_code=giveaway["code"])

    if callback_data.action == "addvote":
        prompt = "ꜱᴇɴᴅ ᴛʜᴇ ᴜꜱᴇʀ ɪᴅ ᴡɪᴛʜ ᴠᴏᴛᴇ ᴀᴍᴏᴜɴᴛ ᴛᴏ ᴀᴅᴅ ᴠᴏᴛᴇ\nᴇxᴀᴍᴘʟᴇ 1234567890 10"
    else:
        prompt = "ꜱᴇɴᴅ ᴛʜᴇ ᴜꜱᴇʀ ɪᴅ ᴡɪᴛʜ ᴠᴏᴛᴇ ᴀᴍᴏᴜɴᴛ ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴠᴏᴛᴇ\nᴇxᴀᴍᴘʟᴇ 1234567890 10"

    await callback.message.edit_text(
        prompt,
        reply_markup=back_kb(ManageCB(action="select", code=giveaway["code"]).pack()),
    )
    await callback.answer()


@router.message(AdjustVote.waiting_input, ~F.text.startswith("/"))
async def process_vote_input(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    action = data.get("adjust_action")
    code = data.get("adjust_code")
    giveaway = await db.get_giveaway_by_code(code)

    if not giveaway or giveaway["owner_id"] != message.from_user.id:
        await state.clear()
        await message.answer("⛔ This giveaway is no longer available.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[0].lstrip("-").isdigit() or not parts[1].isdigit():
        await message.answer(
            "❌ ɪɴᴠᴀʟɪᴅ ꜰᴏʀᴍᴀᴛ. ꜱᴇɴᴅ: <code>user_id amount</code>\nᴇxᴀᴍᴘʟᴇ: <code>1234567890 10</code>"
        )
        return

    user_id, amount = int(parts[0]), int(parts[1])
    if amount <= 0:
        await message.answer("❌ Amount must be a positive number.")
        return

    participant = await db.get_participant_by_user(giveaway["_id"], user_id)
    if not participant:
        await message.answer(
            "❌ ᴛʜᴀᴛ ᴜꜱᴇʀ ɪꜱ ɴᴏᴛ ᴀ ᴘᴀʀᴛɪᴄɪᴘᴀɴᴛ ɪɴ ᴛʜɪꜱ ɢɪᴠᴇᴀᴡᴀʏ.\n\nSend another <code>user_id amount</code>, or go back."
        )
        return

    delta = amount if action == "addvote" else -amount
    await db.adjust_vote(giveaway["_id"], participant["_id"], delta)
    updated = await db.get_participant(participant["_id"])

    # keep the channel entry card's counter in sync
    if updated.get("entry_message_id"):
        new_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=vote_button_text(updated["votes"]),
                        callback_data=VoteCB(
                            code=giveaway["code"], pid=str(updated["_id"])
                        ).pack(),
                        style="success",
                    )
                ]
            ]
        )
        try:
            await bot.edit_message_reply_markup(
                chat_id=giveaway["channel_id"],
                message_id=updated["entry_message_id"],
                reply_markup=new_kb,
            )
        except TelegramBadRequest:
            pass

    verb = "Added" if action == "addvote" else "Removed"
    await state.clear()
    await message.answer(
        f"✅ {verb} {amount} vote(s) {'to' if action == 'addvote' else 'from'} "
        f"{updated['name']} (<code>{user_id}</code>). Total now: <b>{updated['votes']}</b>.",
        reply_markup=manage_panel_kb(giveaway["code"]),
    )


@router.callback_query(ManageCB.filter(F.action == "end"))
async def cb_end_confirm(callback: CallbackQuery, callback_data: ManageCB):
    giveaway = await require_owner_giveaway(callback, callback_data.code)
    if not giveaway:
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Yes, end it",
                    callback_data=ManageCB(
                        action="endconfirm", code=giveaway["code"]
                    ).pack(),
                    style="success",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=ManageCB(action="select", code=giveaway["code"]).pack(),
                    style="danger",
                ),
            ]
        ]
    )
    await callback.message.edit_text(
        "🛑 <b>End this giveaway?</b> This cannot be undone.", reply_markup=kb
    )
    await callback.answer()


@router.callback_query(ManageCB.filter(F.action == "endconfirm"))
async def cb_end_giveaway(callback: CallbackQuery, callback_data: ManageCB, bot: Bot):
    giveaway = await require_owner_giveaway(callback, callback_data.code)
    if not giveaway:
        return

    await db.end_giveaway(giveaway["_id"])
    winner = await db.get_top_participant(giveaway["_id"])
    total = await db.get_participant_count(giveaway["_id"])

    if winner and winner["votes"] > 0:
        result_text = (
            "🏆 <b>Giveaway Ended!</b>\n\n"
            f"🎉 Winner: {mention(winner['user_id'], winner['name'])}\n"
            f"🗳 Votes: {winner['votes']}\n"
            f"👥 Total participants: {total}\n\n"
            "Thanks to everyone who joined! 🍀"
        )
    else:
        result_text = (
            "🏆 <b>Giveaway Ended!</b>\n\n"
            f"👥 Total participants: {total}\n"
            "No votes were cast."
        )

    try:
        await bot.send_message(giveaway["channel_id"], result_text)
    except TelegramBadRequest:
        pass

    await callback.message.edit_text(
        "✅ Giveaway ended and results posted to the channel.",
        reply_markup=back_kb(MenuCB(action="manage").pack()),
    )
    await callback.answer()


# ══════════════════════════════════════════════════════════════════
# Owner-only commands
# ══════════════════════════════════════════════════════════════════
@router.message(Command("bot"))
async def cmd_bot_toggle(message: Message, command: CommandObject):
    if message.from_user.id != config.OWNER_ID:
        return
    arg = (command.args or "").strip().lower()
    if arg not in ("on", "off"):
        await message.answer("Usage: <code>/bot on</code> or <code>/bot off</code>")
        return
    await db.set_setting("bot_enabled", arg == "on")
    await message.answer(f"✅ Bot is now <b>{arg.upper()}</b>.")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != config.OWNER_ID:
        return
    s = await db.get_stats()
    await message.answer(
        "📊 <b>Bot Stats</b>\n\n"
        f"👥 Users: {s['users']}\n"
        f"🎉 Giveaways: {s['giveaways_total']} (active: {s['giveaways_active']})\n"
        f"🙋 Participants: {s['participants_total']}\n"
        f"🗳 Votes: {s['votes_total']}"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, bot: Bot):
    if message.from_user.id != config.OWNER_ID:
        return
    text = command.args
    if message.reply_to_message:
        source = message.reply_to_message
    elif text:
        source = None
    else:
        await message.answer(
            "Usage: reply to a message with <code>/broadcast</code>, "
            "or <code>/broadcast your text</code>"
        )
        return

    user_ids = await db.get_all_user_ids()
    sent, failed = 0, 0
    status = await message.answer(f"📢 Broadcasting to {len(user_ids)} users…")

    for uid in user_ids:
        try:
            if source:
                await source.copy_to(uid)
            else:
                await bot.send_message(uid, text)
            sent += 1
        except TelegramBadRequest:
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(config.BROADCAST_DELAY)

    await status.edit_text(f"✅ Broadcast done. Sent: {sent} | Failed: {failed}")


# ══════════════════════════════════════════════════════════════════
# Entrypoint
# ══════════════════════════════════════════════════════════════════
async def main():
    if not config.OWNER_ID:
        log.warning(
            "OWNER_ID is not set (or is 0) in config.py / env — /bot, /stats and "
            "/broadcast will be silently ignored for everyone until you set it "
            "to @RoyalEren's numeric Telegram user id."
        )
    await db.init_indexes()
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    log.info("Giveaway bot starting…")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())