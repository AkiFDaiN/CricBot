"""
🏏 Cricket Game Telegram Bot
====================================
Works in both private chats and group chats.

Modes:
  1v1 (with 5)     — numbers 1–6
  1v1 (without 5)  — numbers 0,1,2,3,4,6
  Team Mode        — host sets up teams, overs, timeout

Commands:
  /start         - Welcome message
  /gamecricket   - Start a game (choose mode)
  /profile       - View your Cricket stats
  /flip          - Flip a coin
  /help          - Show help

Setup:
  1. Get Telegram Bot Token from @BotFather
  2. pip install python-telegram-bot
  3. TELEGRAM_TOKEN=<token> python bot.py
"""

import logging
import random
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  PLAYER PROFILES
# ─────────────────────────────────────────────────────────────
player_stats: dict[int, dict] = {}


def get_profile(user_id: int, name: str) -> dict:
    if user_id not in player_stats:
        player_stats[user_id] = {"name\n": name, "wins\n": 0, "losses\n": 0, "draws\n": 0, "games": 0}
    else:
        player_stats[user_id]["name"] = name
        player_stats[user_id].setdefault("draws", 0)
    return player_stats[user_id]


def record_result(user_id: int, name: str, won: bool, draw: bool = False) -> None:
    p = get_profile(user_id, name)
    p["games"] += 1
    if draw:
        p["draws"] += 1
    elif won:
        p["wins"] += 1
    else:
        p["losses"] += 1


# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────
async def is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
#  GAME STORAGE
# ─────────────────────────────────────────────────────────────
duel_games: dict[int, dict] = {}   # 1v1 games keyed by message_id
team_games: dict[int, dict] = {}   # team games keyed by setup message_id


# ─────────────────────────────────────────────────────────────
#  BASIC COMMANDS
# ─────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🏏 *Welcome to CricBot!*\n\n"
        "• /gamecricket — Play Cricket!\n"
        "• /profile — Your stats\n"
        "• /flip — Flip a coin\n"
        "• /help — Help",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🏏 *CricBot Commands*\n\n"
        "/gamecricket — Start a game (1v1 or Team)\n"
        "/profile — Wins, losses & draws\n"
        "/flip — Flip a coin\n"
        "/start — Welcome\n"
        "/help — This message",
        parse_mode="Markdown",
    )


async def cmd_flip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(["🪙 Heads!", "🪙 Tails!"]))


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    p = get_profile(user.id, user.first_name)
    games, wins, losses, draws = p["games"], p["wins"], p["losses"], p.get("draws", 0)
    if games == 0:
        record_line = "No games played yet. Start one with /gamecricket!"
        extra = ""
    else:
        wr = round((wins / games) * 100, 1)
        record_line = (
            f"🏆 Wins: *{wins}*  |  💀 Losses: *{losses}*  |  "
            f"🤝 Draws: *{draws}*  |  🎮 Games: *{games}*"
        )
        badge = (
            "🥇 Legend" if wr >= 70 else
            "🥈 Pro" if wr >= 50 else
            "🥉 Amateur" if wr >= 30 else
            "📉 Rookie"
        )
        extra = f"📈 Win Rate: *{wr}%*  |  {badge}"
    await update.message.reply_text(
        f"👤 *{p['name']}'s Cricket Profile*\n"
        f"─────────────────────────\n"
        f"{record_line}\n{extra}",
        parse_mode="Markdown",
    )


async def cmd_gamecricket(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ 1v1", callback_data="mode:1v1")],
        [InlineKeyboardButton("🏟️ Team Mode", callback_data="mode:team")],
    ])
    await update.message.reply_text(
        "🏏 *Choose Game Mode:*",
        reply_markup=kb,
        parse_mode="Markdown",
    )


async def cb_mode_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]
    if choice == "1v1":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔢 With 5  (1–6)", callback_data="1v1v:with5")],
            [InlineKeyboardButton("0️⃣ Without 5  (0,1,2,3,4,6)", callback_data="1v1v:no5")],
        ])
        await query.edit_message_text(
            "⚔️ *1v1 — Choose variant:*", reply_markup=kb, parse_mode="Markdown"
        )
    else:
        await _team_setup_start(query, ctx)


# ═════════════════════════════════════════════════════════════
#  1v1 GAME
# ═════════════════════════════════════════════════════════════

def _duel_kb(game_id: int, variant: str) -> InlineKeyboardMarkup:
    if variant == "with5":
        r1 = [InlineKeyboardButton(str(n), callback_data=f"dp:{game_id}:{n}") for n in [1, 2, 3]]
        r2 = [InlineKeyboardButton(str(n), callback_data=f"dp:{game_id}:{n}") for n in [4, 5, 6]]
    else:
        r1 = [InlineKeyboardButton(str(n), callback_data=f"dp:{game_id}:{n}") for n in [0, 1, 2]]
        r2 = [InlineKeyboardButton(str(n), callback_data=f"dp:{game_id}:{n}") for n in [3, 4, 6]]
    return InlineKeyboardMarkup([r1, r2])


def _duel_text(g: dict) -> str:
    batter = g["batter"]["name"]
    bowler = g["bowler"]["name"]
    score = g["score"]
    ball = g["ball"]
    ov, b_in_ov = divmod(ball, 6)
    target = g.get("target")
    b_runs = g.get("batter_runs", 0)
    b_balls = g.get("batter_balls", 0)
    recent = "  ".join(g.get("history", [])[-6:]) or "-"
    last_bowl = g.get("last_bowl_num")
    bowl_line = f"🎳 Bowler's last: *{last_bowl}*" if last_bowl is not None else ""
    pick_phase = g.get("pick_phase", "batter")

    lines = [
        "🏏 *CRICKET — 1v1*", "",
        f"{'Innings 1' if g['innings'] == 1 else 'Innings 2'}  |  *{batter}* 🏏 vs 🎳 *{bowler}*", "",
        f"📊 Score: *{score}*  |  Over: *{ov}.{b_in_ov}*",
        f"🏏 *{batter}*: *{b_runs}* off *{b_balls}* balls",
    ]
    if target:
        lines.append(f"🎯 Target: {target}  |  Need: *{target - score}*")
    lines += ["", f"🕐 This over: {recent}", bowl_line, ""]
    if pick_phase == "batter":
        lines += [f"⏳ Waiting for *{batter}* to pick...", f"🔒 *{bowler}* picks after", "", f"👇 *{batter}*, pick:"]
    else:
        lines += [f"✅ *{batter}* has picked!", f"⏳ Waiting for *{bowler}*...", "", f"👇 *{bowler}*, pick:"]
    return "\n".join(lines)


def _duel_scorecard(g: dict, result_line: str, winner_name: str = None) -> str:
    sc1 = g.get("innings1_scorecard", {})
    sc2 = g.get("innings2_scorecard", {})

    def bat(name, runs, balls, out):
        sr = round((runs / balls) * 100, 1) if balls else 0.0
        return f"  *{name}*: *{runs}* ({balls}b) [{'out' if out else 'not out ✳️'}]  SR {sr}"

    def bowl(name, wkts, runs):
        return f"  *{name}*: {wkts}wkt / {runs}r"

    outcome = f"🏆 *{winner_name}* WINS!" if winner_name else "🤝 *MATCH DRAWN!*"
    return "\n".join([
        "🏏 *MATCH SCORECARD*", "━━━━━━━━━━━━━━━━━━━━━━", "",
        "*Innings 1*",
        bat(sc1.get("batter_name", "?"), sc1.get("runs", 0), sc1.get("balls", 0), out=True), "",
        "*Bowling (Inns 1)*", bowl(sc1.get("bowler_name", "?"), 1, sc1.get("runs", 0)), "",
        "━━━━━━━━━━━━━━━━━━━━━━", "",
        "*Innings 2*",
        bat(sc2.get("batter_name", "?"), sc2.get("runs", 0), sc2.get("balls", 0), out=sc2.get("out", False)), "",
        "*Bowling (Inns 2)*", bowl(sc2.get("bowler_name", "?"), sc2.get("wickets", 0), sc2.get("runs", 0)), "",
        "━━━━━━━━━━━━━━━━━━━━━━", "",
        result_line, outcome, "",
        "Play again with /gamecricket 🏏",
    ])


async def cb_1v1_variant(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    challenger = update.effective_user
    variant = query.data.split(":")[1]
    get_profile(challenger.id, challenger.first_name)

    g = {
        "variant": variant,
        "chat_id": update.effective_chat.id,
        "challenger": {"id": challenger.id, "name": challenger.first_name},
        "opponent": None,
        "batter": None, "bowler": None,
        "score": 0, "target": None,
        "innings": 1, "phase": "waiting",
        "pick_phase": "batter",
        "toss_winner": None,
        "batter_pick": None, "bowler_pick": None,
        "ball": 0, "batter_runs": 0, "batter_balls": 0,
        "first_score": None, "history": [],
        "last_bowl_num": None,
        "game_msg_id": None,
        "innings1_scorecard": {}, "innings2_scorecard": {},
    }

    label = "With 5 (1–6)" if variant == "with5" else "Without 5 (0,1,2,3,4,6)"
    msg = await query.edit_message_text(
        f"🏏 *{challenger.first_name}* wants to play Cricket! ({label})\n\nTap *Join Game* to play! 👇",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏏 Join Game!", callback_data="dj:0")]]),
        parse_mode="Markdown",
    )
    game_id = msg.message_id
    g["game_msg_id"] = game_id
    duel_games[game_id] = g
    await ctx.bot.edit_message_reply_markup(
        chat_id=g["chat_id"], message_id=game_id,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏏 Join Game!", callback_data=f"dj:{game_id}")]]),
    )


async def cb_duel_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    try:
        game_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Game not found.")
        return
    g = duel_games.get(game_id)
    if not g or g.get("phase") != "waiting":
        await query.answer("Game already started or finished!")
        return
    if g["challenger"]["id"] == user.id:
        await query.answer("You started this! Wait for someone else 😄", show_alert=True)
        return
    await query.answer()
    g["opponent"] = {"id": user.id, "name": user.first_name}
    get_profile(user.id, user.first_name)

    # Toss
    g["phase"] = "toss"
    coin = random.choice(["Heads", "Tails"])
    toss_winner = random.choice(["challenger", "opponent"])
    g["toss_winner"] = toss_winner
    winner_name = g["challenger"]["name"] if toss_winner == "challenger" else g["opponent"]["name"]
    await ctx.bot.edit_message_text(
        chat_id=g["chat_id"], message_id=game_id,
        text=(
            f"🏏 *{g['challenger']['name']}* vs *{g['opponent']['name']}*\n\n"
            f"🪙 Toss — *{coin}*\n🏆 *{winner_name}* wins the toss!\n\nChoose Bat or Bowl 👇"
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏏 Bat",  callback_data=f"dt:{game_id}:bat"),
            InlineKeyboardButton("🎳 Bowl", callback_data=f"dt:{game_id}:bowl"),
        ]]),
        parse_mode="Markdown",
    )


async def cb_duel_toss(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        game_id = int(parts[1])
        choice = parts[2]
    except (IndexError, ValueError):
        await query.answer()
        return
    g = duel_games.get(game_id)
    if not g or g["phase"] != "toss":
        await query.answer()
        return
    tw = g["toss_winner"]
    winner_id = g["challenger"]["id"] if tw == "challenger" else g["opponent"]["id"]
    if user.id != winner_id:
        await query.answer("You didn't win the toss!", show_alert=True)
        return
    await query.answer()
    c, o = g["challenger"], g["opponent"]
    batter, bowler = (c, o) if (tw == "challenger") == (choice == "bat") else (o, c)
    g.update(batter=batter, bowler=bowler, phase="playing", pick_phase="batter",
              score=0, ball=0, batter_runs=0, batter_balls=0,
              batter_pick=None, bowler_pick=None, history=[])
    await ctx.bot.edit_message_text(
        chat_id=g["chat_id"], message_id=game_id,
        text=_duel_text(g), reply_markup=_duel_kb(game_id, g["variant"]),
        parse_mode="Markdown",
    )


async def cb_duel_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        game_id = int(parts[1])
        num = int(parts[2])
    except (IndexError, ValueError):
        await query.answer()
        return
    g = duel_games.get(game_id)
    if not g or g["phase"] != "playing":
        await query.answer()
        return
    is_batter = user.id == g["batter"]["id"]
    is_bowler = user.id == g["bowler"]["id"]
    if not is_batter and not is_bowler:
        await query.answer("You're not in this game!", show_alert=True)
        return
    pp = g.get("pick_phase", "batter")
    if pp == "batter":
        if not is_batter:
            await query.answer("Batter picks first! Wait 🎳", show_alert=True)
            return
        if g["batter_pick"] is not None:
            await query.answer("Already picked! ✋")
            return
        g["batter_pick"] = num
        g["pick_phase"] = "bowler"
        await query.answer(f"Picked {num} 🤫 — bowler's turn!")
        try:
            await ctx.bot.edit_message_text(
                chat_id=g["chat_id"], message_id=game_id,
                text=_duel_text(g), reply_markup=_duel_kb(game_id, g["variant"]),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return
    if pp == "bowler":
        if not is_bowler:
            await query.answer("Bowler's turn! Wait 🏏", show_alert=True)
            return
        if g["bowler_pick"] is not None:
            await query.answer("Already picked! ✋")
            return
        g["bowler_pick"] = num
        await query.answer(f"Picked {num} 🤫")
    await _duel_resolve(ctx, game_id)


async def _duel_resolve(ctx, game_id: int) -> None:
    g = duel_games.get(game_id)
    if not g:
        return
    bat_n = g["batter_pick"]
    bowl_n = g["bowler_pick"]
    g.update(last_bowl_num=bowl_n, batter_pick=None, bowler_pick=None, pick_phase="batter")
    g["ball"] += 1
    g["batter_balls"] += 1
    ball = g["ball"]
    chat_id = g["chat_id"]
    ov, b_in = divmod(ball, 6)
    sr = round((g["batter_runs"] / g["batter_balls"]) * 100, 1) if g["batter_balls"] else 0.0

    if bat_n == bowl_n:
        # WICKET
        wkt_text = (
            f"💥 *WICKET!*\n\n"
            f"🏏 *{g['batter']['name']}* OUT for *{g['batter_runs']}* off *{g['batter_balls']}* balls  SR {sr}\n"
            f"🎳 Bowled by *{g['bowler']['name']}*\n"
            f"⚡ Ball *{ov}.{b_in}*  |  Score: *{g['score']}*"
        )
        if g["innings"] == 1:
            g["innings1_scorecard"] = {
                "batter_name": g["batter"]["name"], "bowler_name": g["bowler"]["name"],
                "runs": g["score"], "balls": g["batter_balls"],
            }
            g["first_score"] = g["score"]
            target = g["score"] + 1
            await ctx.bot.send_message(chat_id, wkt_text + "\n\n🔄 *End of Innings 1*", parse_mode="Markdown")
            old_b, old_bw = g["batter"], g["bowler"]
            g.update(innings=2, target=target, batter=old_bw, bowler=old_b,
                     score=0, ball=0, batter_runs=0, batter_balls=0,
                     history=[], batter_pick=None, bowler_pick=None,
                     pick_phase="batter", last_bowl_num=None)
            await ctx.bot.edit_message_text(
                chat_id=chat_id, message_id=game_id,
                text=f"🔄 *Innings 2!*\n🎯 Target: *{target}*\n\n" + _duel_text(g),
                reply_markup=_duel_kb(game_id, g["variant"]), parse_mode="Markdown",
            )
        else:
            score2, score1 = g["score"], g["first_score"]
            g["innings2_scorecard"] = {
                "batter_name": g["batter"]["name"], "bowler_name": g["bowler"]["name"],
                "runs": score2, "balls": g["batter_balls"], "wickets": 1, "out": True,
            }
            await ctx.bot.send_message(chat_id, wkt_text + "\n\n🔄 *End of Innings 2*", parse_mode="Markdown")
            if score2 == score1:
                result_line = f"📊 Both innings: *{score1}* — incredible game!"
                record_result(g["batter"]["id"], g["batter"]["name"], won=False, draw=True)
                record_result(g["bowler"]["id"], g["bowler"]["name"], won=False, draw=True)
                await ctx.bot.edit_message_text(
                    chat_id=chat_id, message_id=game_id,
                    text=_duel_scorecard(g, result_line), parse_mode="Markdown",
                )
            else:
                if score2 >= g["target"]:
                    winner, loser = g["batter"], g["bowler"]
                    result_line = f"🎯 Target of *{g['target']}* chased!"
                else:
                    margin = g["target"] - 1 - score2
                    winner, loser = g["bowler"], g["batter"]
                    result_line = f"🛡️ Defended by *{margin}* run{'s' if margin != 1 else ''}!"
                record_result(winner["id"], winner["name"], won=True)
                record_result(loser["id"], loser["name"], won=False)
                await ctx.bot.edit_message_text(
                    chat_id=chat_id, message_id=game_id,
                    text=_duel_scorecard(g, result_line, winner_name=winner["name"]),
                    parse_mode="Markdown",
                )
            del duel_games[game_id]
    else:
        # RUNS
        g["score"] += bat_n
        g["batter_runs"] += bat_n
        g["history"].append(str(bat_n))
        if g["innings"] == 2 and g["score"] >= g["target"]:
            g["innings2_scorecard"] = {
                "batter_name": g["batter"]["name"], "bowler_name": g["bowler"]["name"],
                "runs": g["score"], "balls": g["batter_balls"], "wickets": 0, "out": False,
            }
            winner, loser = g["batter"], g["bowler"]
            result_line = f"🎯 Target of *{g['target']}* chased!"
            record_result(winner["id"], winner["name"], won=True)
            record_result(loser["id"], loser["name"], won=False)
            await ctx.bot.edit_message_text(
                chat_id=chat_id, message_id=game_id,
                text=_duel_scorecard(g, result_line, winner_name=winner["name"]),
                parse_mode="Markdown",
            )
            del duel_games[game_id]
            return
        if ball % 6 == 0:
            ov_balls = g["history"][-6:]
            runs_ov = sum(int(b) for b in ov_balls if b.lstrip("-").isdigit())
            extra = f"  |  Need: *{g['target'] - g['score']}*" if g.get("target") else ""
            await ctx.bot.send_message(
                chat_id,
                f"📋 *End of Over {ball // 6}*\n"
                f"Balls: {' | '.join(ov_balls)}\nRuns: *{runs_ov}*\n\n"
                f"🏏 *{g['batter']['name']}*: *{g['batter_runs']}* off *{g['batter_balls']}* balls\n"
                f"📊 Total: *{g['score']}*{extra}",
                parse_mode="Markdown",
            )
        await ctx.bot.edit_message_text(
            chat_id=chat_id, message_id=game_id,
            text=_duel_text(g), reply_markup=_duel_kb(game_id, g["variant"]),
            parse_mode="Markdown",
        )


# ═════════════════════════════════════════════════════════════
#  TEAM MODE
# ═════════════════════════════════════════════════════════════
#
#  team_game keys:
#    chat_id, host_id, host_name
#    phase: setup | toss | innings1 | innings2 | finished
#    overs: int            (1–20)
#    timeout_mins: int
#    variant: "with5" | "no5"
#    team_a / team_b:
#      name, captain_id, captain_name, members: {uid: name}
#    toss_winner: "a" | "b"
#    batting_team: "a" | "b"
#    innings1_batting_team / innings2_batting_team
#    current_innings: 1 | 2
#    target: int | None
#    innings_data: {
#      1: {score, wickets, balls, history, batter_runs{uid:int}, batter_balls{uid:int}},
#      2: {...}
#    }
#    balls_on_field: None | {
#      msg_id, batter_id, batter_name,
#      bowler_id, bowler_name,
#      batter_pick, bowler_pick,
#      pick_phase, last_bowl_num,
#      over_history
#    }
#    prev_bowlers: set of bowler_ids who bowled in the current over period
#    game_msg_id: int
# ═════════════════════════════════════════════════════════════

def _tname(tgame: dict, key: str) -> str:
    return tgame[f"team_{key}"]["name"]


def _bowl_key(tgame: dict) -> str:
    return "b" if tgame["batting_team"] == "a" else "a"


def _is_authority(tgame: dict, user_id: int, admin: bool = False) -> bool:
    if user_id == tgame["host_id"]:
        return True
    for t in ["a", "b"]:
        if user_id == tgame[f"team_{t}"]["captain_id"]:
            return True
    return admin


def _setup_text(tgame: dict) -> str:
    def mlist(t):
        m = tgame[f"team_{t}"]["members"]
        if not m:
            return "  _(none)_"
        return "\n".join(
            f"  • {n}{' 👑' if uid == tgame[f'team_{t}']['captain_id'] else ''}"
            for uid, n in m.items()
        )
    ov = str(tgame["overs"]) if tgame["overs"] else "_(not set)_"
    to = f"{tgame['timeout_mins']} min" if tgame["timeout_mins"] else "_(not set)_"
    vt = "With 5 (1–6)" if tgame["variant"] == "with5" else "Without 5 (0,1,2,3,4,6)"
    ta, tb = tgame["team_a"], tgame["team_b"]
    return (
        f"🏟️ *TEAM CRICKET SETUP*\n"
        f"Host: *{tgame['host_name']}*\n"
        f"─────────────────────────\n"
        f"🅰️ *{ta['name']}*  Cap: {ta['captain_name'] or '_none_'}\n{mlist('a')}\n\n"
        f"🅱️ *{tb['name']}*  Cap: {tb['captain_name'] or '_none_'}\n{mlist('b')}\n\n"
        f"⚙️ Overs: *{ov}*  |  Timeout: *{to}*  |  Variant: *{vt}*\n"
        f"─────────────────────────\n"
        f"_Host/admins: use the buttons to manage_"
    )


def _setup_kb(tgame_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Join Team A", callback_data=f"tj:{tgame_id}:a"),
         InlineKeyboardButton("➕ Join Team B", callback_data=f"tj:{tgame_id}:b")],
        [InlineKeyboardButton("👑 Set Captains",      callback_data=f"tsetcap:{tgame_id}")],
        [InlineKeyboardButton("🎯 Set Overs",         callback_data=f"tsetovers:{tgame_id}")],
        [InlineKeyboardButton("⏱️ Set Timeout",       callback_data=f"tsettout:{tgame_id}")],
        [InlineKeyboardButton("🔢 Toggle Variant",    callback_data=f"tvariant:{tgame_id}")],
        [InlineKeyboardButton("✅ Start Game (Toss)", callback_data=f"tstart:{tgame_id}")],
    ])


async def _refresh_setup(ctx, tgame: dict) -> None:
    try:
        await ctx.bot.edit_message_text(
            chat_id=tgame["chat_id"], message_id=tgame["game_msg_id"],
            text=_setup_text(tgame), reply_markup=_setup_kb(tgame["game_msg_id"]),
            parse_mode="Markdown",
        )
    except Exception:
        pass


async def _team_setup_start(query, ctx) -> None:
    host = query.from_user
    tgame = {
        "chat_id": query.message.chat.id,
        "host_id": host.id, "host_name": host.first_name,
        "phase": "setup",
        "overs": 5, "timeout_mins": 5, "variant": "with5",
        "team_a": {"name": "Team A", "captain_id": None, "captain_name": None, "members": {}},
        "team_b": {"name": "Team B", "captain_id": None, "captain_name": None, "members": {}},
        "toss_winner": None, "batting_team": None,
        "innings1_batting_team": None, "innings2_batting_team": None,
        "current_innings": 1, "target": None,
        "innings_data": {
            1: {"score": 0, "wickets": 0, "balls": 0, "history": [], "batter_runs": {}, "batter_balls": {}},
            2: {"score": 0, "wickets": 0, "balls": 0, "history": [], "batter_runs": {}, "batter_balls": {}},
        },
        "balls_on_field": None,
        "prev_bowlers": set(),
        "game_msg_id": None,
    }
    msg = await ctx.bot.send_message(
        chat_id=query.message.chat.id,
        text=_setup_text(tgame),
        reply_markup=_setup_kb(0),
        parse_mode="Markdown",
    )
    tgame["game_msg_id"] = msg.message_id
    team_games[msg.message_id] = tgame
    await ctx.bot.edit_message_reply_markup(
        chat_id=tgame["chat_id"], message_id=msg.message_id,
        reply_markup=_setup_kb(msg.message_id),
    )


# ── Join team ──────────────────────────────────────────────────────────────

async def cb_team_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        team = parts[2]
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer("Game not found.")
        return
    other = "b" if team == "a" else "a"
    tgame[f"team_{other}"]["members"].pop(user.id, None)
    if tgame[f"team_{other}"]["captain_id"] == user.id:
        tgame[f"team_{other}"]["captain_id"] = None
        tgame[f"team_{other}"]["captain_name"] = None
    tgame[f"team_{team}"]["members"][user.id] = user.first_name
    await query.answer(f"Joined {_tname(tgame, team)}!")
    await _refresh_setup(ctx, tgame)


# ── Set captains ───────────────────────────────────────────────────────────

async def cb_set_captains(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer("Game not found.")
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if not _is_authority(tgame, user.id, admin):
        await query.answer("Only host/captains/admins.", show_alert=True)
        return
    await query.answer()
    buttons = []
    for t in ["a", "b"]:
        for uid, uname in tgame[f"team_{t}"]["members"].items():
            mark = " 👑" if uid == tgame[f"team_{t}"]["captain_id"] else ""
            buttons.append([InlineKeyboardButton(
                f"{uname}{mark} ({_tname(tgame, t)})",
                callback_data=f"tcap:{tgame_id}:{t}:{uid}"
            )])
    if not buttons:
        await ctx.bot.send_message(tgame["chat_id"], "No players in teams yet!")
        return
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"tcapcancel:{tgame_id}")])
    await ctx.bot.send_message(
        tgame["chat_id"], "👑 *Select captain:*",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown",
    )


async def cb_assign_captain(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        team = parts[2]
        target_uid = int(parts[3])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if not _is_authority(tgame, user.id, admin):
        await query.answer("Only host/captains/admins.", show_alert=True)
        return
    name = tgame[f"team_{team}"]["members"].get(target_uid, "?")
    tgame[f"team_{team}"]["captain_id"] = target_uid
    tgame[f"team_{team}"]["captain_name"] = name
    await query.answer(f"{name} is captain of {_tname(tgame, team)}!")
    await query.edit_message_reply_markup(reply_markup=None)
    await _refresh_setup(ctx, tgame)


async def cb_cap_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)


# ── Set overs ──────────────────────────────────────────────────────────────

async def cb_set_overs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if not _is_authority(tgame, user.id, admin):
        await query.answer("Only host/admins.", show_alert=True)
        return
    await query.answer()
    rows = []
    row = []
    for ov in range(1, 21):
        row.append(InlineKeyboardButton(str(ov), callback_data=f"tov:{tgame_id}:{ov}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    await ctx.bot.send_message(
        tgame["chat_id"], "🎯 *Select overs (1–20):*",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown",
    )


async def cb_overs_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        overs = int(parts[2])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if not _is_authority(tgame, user.id, admin):
        await query.answer("Only host/admins.", show_alert=True)
        return
    tgame["overs"] = overs
    await query.answer(f"Overs set to {overs}!")
    await query.edit_message_reply_markup(reply_markup=None)
    await _refresh_setup(ctx, tgame)


# ── Set timeout ────────────────────────────────────────────────────────────

async def cb_set_timeout(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if not _is_authority(tgame, user.id, admin):
        await query.answer("Only host/admins.", show_alert=True)
        return
    await query.answer()
    opts = [1, 2, 3, 5, 10, 15]
    await ctx.bot.send_message(
        tgame["chat_id"], "⏱️ *Select timeout:*",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"{t} min", callback_data=f"ttout:{tgame_id}:{t}")
            for t in opts
        ]]),
        parse_mode="Markdown",
    )


async def cb_timeout_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        mins = int(parts[2])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if not _is_authority(tgame, user.id, admin):
        await query.answer("Only host/admins.", show_alert=True)
        return
    tgame["timeout_mins"] = mins
    await query.answer(f"Timeout: {mins} min!")
    await query.edit_message_reply_markup(reply_markup=None)
    await _refresh_setup(ctx, tgame)


# ── Toggle variant ──────────────────────────────────────────────────────────

async def cb_toggle_variant(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if not _is_authority(tgame, user.id, admin):
        await query.answer("Only host/admins.", show_alert=True)
        return
    tgame["variant"] = "no5" if tgame["variant"] == "with5" else "with5"
    label = "With 5 (1–6)" if tgame["variant"] == "with5" else "Without 5 (0,1,2,3,4,6)"
    await query.answer(f"Variant: {label}")
    await _refresh_setup(ctx, tgame)


# ── Start / Toss ───────────────────────────────────────────────────────────

async def cb_team_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if user.id != tgame["host_id"] and not admin:
        await query.answer("Only the host or group admins can start.", show_alert=True)
        return
    ta, tb = tgame["team_a"], tgame["team_b"]
    if not ta["members"] or not tb["members"]:
        await query.answer("Both teams need at least 1 player!", show_alert=True)
        return
    if not ta["captain_id"] or not tb["captain_id"]:
        await query.answer("Both teams need a captain!", show_alert=True)
        return
    await query.answer()
    tgame["phase"] = "toss"
    coin = random.choice(["Heads", "Tails"])
    tw = random.choice(["a", "b"])
    tgame["toss_winner"] = tw
    winner_cap = tgame[f"team_{tw}"]["captain_name"]
    await ctx.bot.edit_message_text(
        chat_id=tgame["chat_id"], message_id=tgame["game_msg_id"],
        text=(
            f"🏟️ *TEAM CRICKET — TOSS*\n\n"
            f"🪙 *{coin}* — *{_tname(tgame, tw)}* wins the toss!\n\n"
            f"👑 *{winner_cap}*, choose Bat or Bowl:"
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏏 Bat",  callback_data=f"ttoss:{tgame_id}:bat"),
            InlineKeyboardButton("🎳 Bowl", callback_data=f"ttoss:{tgame_id}:bowl"),
        ]]),
        parse_mode="Markdown",
    )


async def cb_team_toss(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        choice = parts[2]
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame or tgame["phase"] != "toss":
        await query.answer()
        return
    tw = tgame["toss_winner"]
    cap_id = tgame[f"team_{tw}"]["captain_id"]
    if user.id != cap_id:
        await query.answer("Only the toss-winning captain can choose!", show_alert=True)
        return
    await query.answer()
    tgame["batting_team"] = tw if choice == "bat" else ("b" if tw == "a" else "a")
    tgame["innings1_batting_team"] = tgame["batting_team"]
    tgame["phase"] = "innings1"
    tgame["current_innings"] = 1

    bk = tgame["batting_team"]
    wk = _bowl_key(tgame)
    await ctx.bot.edit_message_text(
        chat_id=tgame["chat_id"], message_id=tgame["game_msg_id"],
        text=(
            f"🏟️ *TEAM CRICKET — INNINGS 1*\n\n"
            f"🏏 *{_tname(tgame, bk)}* bat first\n"
            f"🎳 *{_tname(tgame, wk)}* bowl\n\n"
            f"Overs: *{tgame['overs']}*  |  Timeout: *{tgame['timeout_mins']} min*\n\n"
            f"👑 *{tgame[f'team_{bk}']['captain_name']}* — send your opening batter\n"
            f"👑 *{tgame[f'team_{wk}']['captain_name']}* — send your opening bowler"
        ),
        reply_markup=_assign_kb(tgame_id),
        parse_mode="Markdown",
    )
    await _schedule_timeout(ctx, tgame_id)


def _assign_kb(tgame_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏏 Send Batter", callback_data=f"tassign:{tgame_id}:batter")],
        [InlineKeyboardButton("🎳 Send Bowler", callback_data=f"tassign:{tgame_id}:bowler")],
    ])


# ── Timeout scheduler ──────────────────────────────────────────────────────

async def _schedule_timeout(ctx, tgame_id: int) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    secs = tgame["timeout_mins"] * 60

    async def _run():
        await asyncio.sleep(secs)
        tgame = team_games.get(tgame_id)
        if not tgame or tgame["phase"] == "finished":
            return
        bof = tgame.get("balls_on_field")
        if bof and bof.get("batter_id") and bof.get("bowler_id"):
            return  # both assigned, no alert needed
        bk = tgame["batting_team"]
        wk = _bowl_key(tgame)
        bc = tgame[f"team_{bk}"]["captain_name"]
        wc = tgame[f"team_{wk}"]["captain_name"]
        missing_parts = []
        if not bof or not bof.get("batter_id"):
            missing_parts.append(f"👑 *{bc}* — assign a batter!")
        if not bof or not bof.get("bowler_id"):
            missing_parts.append(f"👑 *{wc}* — assign a bowler!")
        try:
            await ctx.bot.send_message(
                tgame["chat_id"],
                f"⏰ *Timeout!* Waiting for assignment:\n\n" + "\n".join(missing_parts),
                reply_markup=_assign_kb(tgame_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass

    asyncio.ensure_future(_run())


# ── Assign batter / bowler ─────────────────────────────────────────────────

async def cb_team_assign(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        role = parts[2]
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer("Game not found.")
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if not _is_authority(tgame, user.id, admin):
        await query.answer("Only captains/host/admins.", show_alert=True)
        return
    await query.answer()

    team_key = tgame["batting_team"] if role == "batter" else _bowl_key(tgame)
    members = tgame[f"team_{team_key}"]["members"]
    if not members:
        await ctx.bot.send_message(tgame["chat_id"], f"No members in {_tname(tgame, team_key)}!")
        return

    prev_bowlers = tgame.get("prev_bowlers", set())
    buttons = []
    for uid, uname in members.items():
        if role == "bowler" and uid in prev_bowlers:
            continue  # can't bowl consecutive overs
        buttons.append([InlineKeyboardButton(uname, callback_data=f"tsetp:{tgame_id}:{role}:{uid}")])

    if not buttons:
        await ctx.bot.send_message(
            tgame["chat_id"],
            f"⚠️ No eligible bowlers! All bowled last over.\n"
            f"_The same bowler cannot bowl consecutive overs._",
            parse_mode="Markdown",
        )
        return

    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"tcapcancel:{tgame_id}")])
    await ctx.bot.send_message(
        tgame["chat_id"], f"👇 *Select {role}:*",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown",
    )


async def cb_set_player(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        role = parts[2]
        target_uid = int(parts[3])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    admin = await is_admin(ctx.bot, tgame["chat_id"], user.id)
    if not _is_authority(tgame, user.id, admin):
        await query.answer("Only captains/host/admins.", show_alert=True)
        return

    team_key = tgame["batting_team"] if role == "batter" else _bowl_key(tgame)
    target_name = tgame[f"team_{team_key}"]["members"].get(target_uid, "?")

    if tgame.get("balls_on_field") is None:
        tgame["balls_on_field"] = {
            "msg_id": None,
            "batter_id": None, "batter_name": None,
            "bowler_id": None, "bowler_name": None,
            "batter_pick": None, "bowler_pick": None,
            "pick_phase": "batter",
            "last_bowl_num": None,
            "over_history": [],
        }

    bof = tgame["balls_on_field"]
    if role == "batter":
        bof["batter_id"] = target_uid
        bof["batter_name"] = target_name
    else:
        bof["bowler_id"] = target_uid
        bof["bowler_name"] = target_name

    await query.answer(f"{target_name} → {role}!")
    await query.edit_message_reply_markup(reply_markup=None)

    if bof["batter_id"] and bof["bowler_id"]:
        await _launch_ball_game(ctx, tgame_id)
    else:
        missing = "bowler" if bof["batter_id"] else "batter"
        await ctx.bot.send_message(
            tgame["chat_id"],
            f"✅ *{target_name}* set as {role}. Waiting for *{missing}*...",
            reply_markup=_assign_kb(tgame_id),
            parse_mode="Markdown",
        )


async def _launch_ball_game(ctx, tgame_id: int) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    bof = tgame["balls_on_field"]
    msg = await ctx.bot.send_message(
        tgame["chat_id"],
        _team_field_text(tgame),
        reply_markup=_team_kb(tgame_id, tgame["variant"]),
        parse_mode="Markdown",
    )
    bof["msg_id"] = msg.message_id


def _team_kb(tgame_id: int, variant: str) -> InlineKeyboardMarkup:
    if variant == "with5":
        r1 = [InlineKeyboardButton(str(n), callback_data=f"tp:{tgame_id}:{n}") for n in [1, 2, 3]]
        r2 = [InlineKeyboardButton(str(n), callback_data=f"tp:{tgame_id}:{n}") for n in [4, 5, 6]]
    else:
        r1 = [InlineKeyboardButton(str(n), callback_data=f"tp:{tgame_id}:{n}") for n in [0, 1, 2]]
        r2 = [InlineKeyboardButton(str(n), callback_data=f"tp:{tgame_id}:{n}") for n in [3, 4, 6]]
    return InlineKeyboardMarkup([r1, r2])


def _team_field_text(tgame: dict) -> str:
    inn = tgame["current_innings"]
    bof = tgame["balls_on_field"]
    d = tgame["innings_data"][inn]
    score = d["score"]
    wickets = d["wickets"]
    balls = d["balls"]
    ov, b_in = divmod(balls, 6)
    target = tgame.get("target")
    batter_name = bof["batter_name"]
    bowler_name = bof["bowler_name"]
    recent = "  ".join(bof["over_history"][-6:]) or "-"
    last_bowl = bof.get("last_bowl_num")
    bowl_line = f"🎳 Bowler's last: *{last_bowl}*" if last_bowl is not None else ""
    pick_phase = bof.get("pick_phase", "batter")
    bk = tgame["batting_team"]
    wk = _bowl_key(tgame)

    lines = [
        f"🏟️ *TEAM CRICKET — Innings {inn}*", "",
        f"*{_tname(tgame, bk)}* 🏏  vs  🎳 *{_tname(tgame, wk)}*", "",
        f"📊 *{score}/{wickets}*  |  Over: *{ov}.{b_in}*  |  Remaining: *{tgame['overs'] - ov}*",
    ]
    if target:
        need = target - score
        balls_left = tgame["overs"] * 6 - balls
        lines.append(f"🎯 Target: {target}  |  Need: *{need}* in *{balls_left}* balls")
    lines += [
        "", f"🏏 Batter: *{batter_name}*", f"🎳 Bowler: *{bowler_name}*",
        "", f"🕐 This over: {recent}", bowl_line, "",
    ]
    if pick_phase == "batter":
        lines += [f"⏳ Waiting for *{batter_name}* to pick...", f"🔒 *{bowler_name}* picks after", "", f"👇 *{batter_name}*, pick:"]
    else:
        lines += [f"✅ *{batter_name}* has picked!", f"⏳ Waiting for *{bowler_name}*...", "", f"👇 *{bowler_name}*, pick:"]
    return "\n".join(l for l in lines if l is not None)


# ── Team pick ──────────────────────────────────────────────────────────────

async def cb_team_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        num = int(parts[2])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    bof = tgame.get("balls_on_field")
    if not bof or not bof.get("msg_id"):
        await query.answer()
        return

    is_batter = user.id == bof["batter_id"]
    is_bowler = user.id == bof["bowler_id"]
    if not is_batter and not is_bowler:
        await query.answer("You're not batting or bowling right now!", show_alert=True)
        return

    pp = bof.get("pick_phase", "batter")
    if pp == "batter":
        if not is_batter:
            await query.answer("Batter picks first! Wait 🎳", show_alert=True)
            return
        if bof["batter_pick"] is not None:
            await query.answer("Already picked! ✋")
            return
        bof["batter_pick"] = num
        bof["pick_phase"] = "bowler"
        await query.answer(f"Picked {num} 🤫 — bowler's turn!")
        try:
            await ctx.bot.edit_message_text(
                chat_id=tgame["chat_id"], message_id=bof["msg_id"],
                text=_team_field_text(tgame), reply_markup=_team_kb(tgame_id, tgame["variant"]),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return
    if pp == "bowler":
        if not is_bowler:
            await query.answer("Bowler's turn! Wait 🏏", show_alert=True)
            return
        if bof["bowler_pick"] is not None:
            await query.answer("Already picked! ✋")
            return
        bof["bowler_pick"] = num
        await query.answer(f"Picked {num} 🤫")
    await _team_resolve(ctx, tgame_id)


async def _team_resolve(ctx, tgame_id: int) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    bof = tgame["balls_on_field"]
    bat_n = bof["batter_pick"]
    bowl_n = bof["bowler_pick"]
    bof.update(last_bowl_num=bowl_n, batter_pick=None, bowler_pick=None, pick_phase="batter")

    inn = tgame["current_innings"]
    d = tgame["innings_data"][inn]
    d["balls"] += 1
    balls = d["balls"]
    chat_id = tgame["chat_id"]
    batter_id = bof["batter_id"]
    batter_name = bof["batter_name"]
    bowler_name = bof["bowler_name"]
    bowler_id = bof["bowler_id"]

    d["batter_balls"][batter_id] = d["batter_balls"].get(batter_id, 0) + 1

    ov, b_in = divmod(balls, 6)
    sr_b = d["batter_runs"].get(batter_id, 0)
    sr_bb = d["batter_balls"].get(batter_id, 0)
    sr = round((sr_b / sr_bb) * 100, 1) if sr_bb else 0.0

    if bat_n == bowl_n:
        # WICKET
        d["wickets"] += 1
        bof["over_history"].append("W")
        d["history"].append("W")

        wkt_text = (
            f"💥 *WICKET!*\n\n"
            f"🏏 *{batter_name}* OUT for *{sr_b}* off *{sr_bb}* balls  SR {sr}\n"
            f"🎳 Bowled by *{bowler_name}*\n"
            f"⚡ Over *{ov}.{b_in}*  |  Score: *{d['score']}/{d['wickets']}*"
        )
        await ctx.bot.send_message(chat_id, wkt_text, parse_mode="Markdown")

        # Check all-out
        bk = tgame["batting_team"]
        total = len(tgame[f"team_{bk}"]["members"])
        all_out = d["wickets"] >= total

        if all_out:
            if inn == 1:
                await _end_innings1(ctx, tgame_id)
            else:
                await _end_match(ctx, tgame_id)
        else:
            # New batter, same bowler
            await _need_new_batter(ctx, tgame_id, bowler_id, bowler_name)

    else:
        # RUNS
        d["score"] += bat_n
        d["batter_runs"][batter_id] = d["batter_runs"].get(batter_id, 0) + bat_n
        bof["over_history"].append(str(bat_n))
        d["history"].append(str(bat_n))

        # Innings 2: check if target reached
        if inn == 2 and d["score"] >= tgame["target"]:
            await ctx.bot.send_message(
                chat_id, f"🎯 *Target chased!* *{batter_name}* hits the winning runs!\n📊 *{d['score']}/{d['wickets']}*",
                parse_mode="Markdown",
            )
            await _end_match(ctx, tgame_id)
            return

        # End of overs
        if balls >= tgame["overs"] * 6:
            if inn == 1:
                await _end_innings1(ctx, tgame_id)
            else:
                await _end_match(ctx, tgame_id)
            return

        # End of over (not end of innings)
        if balls % 6 == 0:
            ov_num = balls // 6
            ov_balls = bof["over_history"][-6:]
            runs_ov = sum(int(b) for b in ov_balls if b.isdigit())
            extra = f"  |  Need: *{tgame['target'] - d['score']}*" if tgame.get("target") else ""
            await ctx.bot.send_message(
                chat_id,
                f"📋 *End of Over {ov_num}*\n"
                f"Balls: {' | '.join(ov_balls)}\nRuns: *{runs_ov}*\n\n"
                f"📊 *{_tname(tgame, tgame['batting_team'])}*: *{d['score']}/{d['wickets']}*{extra}",
                parse_mode="Markdown",
            )
            # Same batter, new bowler
            tgame["prev_bowlers"] = {bowler_id}
            await _need_new_bowler(ctx, tgame_id, batter_id, batter_name)
            return

        # Normal — update field message
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id, message_id=bof["msg_id"],
                text=_team_field_text(tgame), reply_markup=_team_kb(tgame_id, tgame["variant"]),
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def _need_new_batter(ctx, tgame_id: int, bowler_id: int, bowler_name: str) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    bk = tgame["batting_team"]
    bat_cap = tgame[f"team_{bk}"]["captain_name"]
    over_hist = tgame["balls_on_field"].get("over_history", [])

    tgame["balls_on_field"] = {
        "msg_id": None,
        "batter_id": None, "batter_name": None,
        "bowler_id": bowler_id, "bowler_name": bowler_name,
        "batter_pick": None, "bowler_pick": None,
        "pick_phase": "batter",
        "last_bowl_num": None,
        "over_history": over_hist,
    }
    await ctx.bot.send_message(
        tgame["chat_id"],
        f"🏏 *New batter needed!*\n\n"
        f"👑 *{bat_cap}*, send your next batter.\n"
        f"🎳 *{bowler_name}* continues bowling.\n\n"
        f"_Timeout: {tgame['timeout_mins']} min_",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏏 Send Batter", callback_data=f"tassign:{tgame_id}:batter"),
        ]]),
        parse_mode="Markdown",
    )
    await _schedule_timeout(ctx, tgame_id)


async def _need_new_bowler(ctx, tgame_id: int, batter_id: int, batter_name: str) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    wk = _bowl_key(tgame)
    bowl_cap = tgame[f"team_{wk}"]["captain_name"]

    tgame["balls_on_field"] = {
        "msg_id": None,
        "batter_id": batter_id, "batter_name": batter_name,
        "bowler_id": None, "bowler_name": None,
        "batter_pick": None, "bowler_pick": None,
        "pick_phase": "batter",
        "last_bowl_num": None,
        "over_history": [],
    }
    await ctx.bot.send_message(
        tgame["chat_id"],
        f"🎳 *New bowler needed!*\n\n"
        f"👑 *{bowl_cap}*, send your next bowler.\n"
        f"🏏 *{batter_name}* continues batting.\n\n"
        f"_Timeout: {tgame['timeout_mins']} min_\n"
        f"_(Same bowler cannot bowl consecutive overs)_",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎳 Send Bowler", callback_data=f"tassign:{tgame_id}:bowler"),
        ]]),
        parse_mode="Markdown",
    )
    await _schedule_timeout(ctx, tgame_id)


async def _end_innings1(ctx, tgame_id: int) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    d1 = tgame["innings_data"][1]
    bk = tgame["batting_team"]
    wk = _bowl_key(tgame)

    await ctx.bot.send_message(
        tgame["chat_id"],
        f"🏁 *End of Innings 1*\n\n"
        f"🏏 *{_tname(tgame, bk)}*: *{d1['score']}/{d1['wickets']}*\n\n"
        f"🎯 *{_tname(tgame, wk)}* need *{d1['score'] + 1}* to win from {tgame['overs']} overs!",
        parse_mode="Markdown",
    )

    tgame["target"] = d1["score"] + 1
    tgame["batting_team"] = wk
    tgame["innings2_batting_team"] = wk
    tgame["current_innings"] = 2
    tgame["phase"] = "innings2"
    tgame["prev_bowlers"] = set()
    tgame["balls_on_field"] = None

    new_bk = tgame["batting_team"]
    new_wk = _bowl_key(tgame)
    await ctx.bot.send_message(
        tgame["chat_id"],
        f"🔄 *Innings 2 begins!*\n\n"
        f"🏏 *{_tname(tgame, new_bk)}* bat\n"
        f"🎳 *{_tname(tgame, new_wk)}* bowl\n\n"
        f"👑 *{tgame[f'team_{new_bk}']['captain_name']}* — send your opening batter\n"
        f"👑 *{tgame[f'team_{new_wk}']['captain_name']}* — send your opening bowler",
        reply_markup=_assign_kb(tgame_id),
        parse_mode="Markdown",
    )
    await _schedule_timeout(ctx, tgame_id)


async def _end_match(ctx, tgame_id: int) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return

    d1 = tgame["innings_data"][1]
    d2 = tgame["innings_data"][2]
    bat1 = tgame.get("innings1_batting_team", "a")
    bat2 = tgame.get("innings2_batting_team", "b")
    s1, s2 = d1["score"], d2["score"]

    if s2 > s1:
        winner_key = bat2
        result_line = f"🎯 *{_tname(tgame, bat2)}* chased the target and won!"
    elif s2 == s1:
        winner_key = None
        result_line = f"🤝 Match tied at *{s1}* runs!"
    else:
        winner_key = bat1
        margin = s1 - s2
        result_line = f"🛡️ *{_tname(tgame, bat1)}* defended — won by *{margin}* run{'s' if margin != 1 else ''}!"

    for t in ["a", "b"]:
        won = (winner_key == t) if winner_key else False
        draw = (winner_key is None)
        for uid, uname in tgame[f"team_{t}"]["members"].items():
            record_result(uid, uname, won=won, draw=draw)

    def bat_summary(d, bat_key):
        lines = []
        for uid, uname in tgame[f"team_{bat_key}"]["members"].items():
            runs = d["batter_runs"].get(uid)
            balls_b = d["batter_balls"].get(uid, 0)
            if runs is None:
                lines.append(f"  {uname}: DNB")
            else:
                sr = round((runs / balls_b) * 100, 1) if balls_b else 0.0
                lines.append(f"  *{uname}*: *{runs}* ({balls_b}b)  SR {sr}")
        return "\n".join(lines) or "  (none)"

    outcome = f"🏆 *{_tname(tgame, winner_key)}* WINS!" if winner_key else "🤝 *MATCH TIED!*"
    scorecard = (
        f"🏆 *MATCH OVER*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Innings 1 — {_tname(tgame, bat1)}*\n{bat_summary(d1, bat1)}\n"
        f"Total: *{s1}/{d1['wickets']}*\n\n"
        f"*Innings 2 — {_tname(tgame, bat2)}*\n{bat_summary(d2, bat2)}\n"
        f"Total: *{s2}/{d2['wickets']}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n{result_line}\n{outcome}\n\n"
        f"Play again with /gamecricket 🏏"
    )
    await ctx.bot.send_message(tgame["chat_id"], scorecard, parse_mode="Markdown")
    tgame["phase"] = "finished"
    del team_games[tgame_id]


# ═════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════
def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("flip",        cmd_flip))
    app.add_handler(CommandHandler("profile",     cmd_profile))
    app.add_handler(CommandHandler("gamecricket", cmd_gamecricket))

    # Mode selector
    app.add_handler(CallbackQueryHandler(cb_mode_select,    pattern=r"^mode:"))

    # 1v1
    app.add_handler(CallbackQueryHandler(cb_1v1_variant,    pattern=r"^1v1v:"))
    app.add_handler(CallbackQueryHandler(cb_duel_join,      pattern=r"^dj:"))
    app.add_handler(CallbackQueryHandler(cb_duel_toss,      pattern=r"^dt:"))
    app.add_handler(CallbackQueryHandler(cb_duel_pick,      pattern=r"^dp:"))

    # Team setup
    app.add_handler(CallbackQueryHandler(cb_team_join,      pattern=r"^tj:"))
    app.add_handler(CallbackQueryHandler(cb_set_captains,   pattern=r"^tsetcap:"))
    app.add_handler(CallbackQueryHandler(cb_assign_captain, pattern=r"^tcap:"))
    app.add_handler(CallbackQueryHandler(cb_cap_cancel,     pattern=r"^tcapcancel:"))
    app.add_handler(CallbackQueryHandler(cb_set_overs,      pattern=r"^tsetovers:"))
    app.add_handler(CallbackQueryHandler(cb_overs_pick,     pattern=r"^tov:"))
    app.add_handler(CallbackQueryHandler(cb_set_timeout,    pattern=r"^tsettout:"))
    app.add_handler(CallbackQueryHandler(cb_timeout_pick,   pattern=r"^ttout:"))
    app.add_handler(CallbackQueryHandler(cb_toggle_variant, pattern=r"^tvariant:"))
    app.add_handler(CallbackQueryHandler(cb_team_start,     pattern=r"^tstart:"))

    # Team game
    app.add_handler(CallbackQueryHandler(cb_team_toss,      pattern=r"^ttoss:"))
    app.add_handler(CallbackQueryHandler(cb_team_assign,    pattern=r"^tassign:"))
    app.add_handler(CallbackQueryHandler(cb_set_player,     pattern=r"^tsetp:"))
    app.add_handler(CallbackQueryHandler(cb_team_pick,      pattern=r"^tp:"))

    logger.info("CricBot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()