"""
Specific requirements handled:
  1. Menu protection limits mode configuration access to the /gamecricket commander.
  2. Captain claims collapse automatically via inline message editing.
  3. Performance weights track and calculate match MVP at final whistle.
  4. Bowler names explicitly mapped into over summaries.
"""
import logging
import random
import asyncio
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
#  USER CACHE
# ─────────────────────────────────────────────────────────────
_user_cache: dict[int, dict] = {}
_username_to_id: dict[str, int] = {}


def _cache_user(user) -> None:
    _user_cache[user.id] = {"username": user.username, "first_name": user.first_name}
    if user.username:
        _username_to_id[user.username.lower()] = user.id


def _resolve_mention(message) -> tuple[Optional[int], Optional[str]]:
    for entity in (message.entities or []):
        if entity.type == "text_mention":
            u = entity.user
            _cache_user(u)
            return u.id, u.first_name
        elif entity.type == "mention":
            username = message.text[entity.offset + 1: entity.offset + entity.length]
            uid = _username_to_id.get(username.lower())
            if uid:
                name = _user_cache.get(uid, {}).get("first_name", username)
                return uid, name
    return None, None


# ─────────────────────────────────────────────────────────────
#  PLAYER PROFILES
# ─────────────────────────────────────────────────────────────
player_stats: dict[int, dict] = {}


def get_profile(user_id: int, name: str) -> dict:
    if user_id not in player_stats:
        player_stats[user_id] = {"name": name, "wins": 0, "losses": 0, "draws": 0, "games": 0}
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
#  GAME STORAGE & TIMEOUT MANAGEMENT
# ─────────────────────────────────────────────────────────────
duel_games: dict[int, dict] = {}
team_games: dict[int, dict] = {}
group_team_game: dict[int, int] = {}
active_timers: dict[int, asyncio.Task] = {}


def _cancel_timer(tgame_id: int) -> None:
    if tgame_id in active_timers:
        active_timers[tgame_id].cancel()
        del active_timers[tgame_id]


def _reset_timer(ctx: ContextTypes.DEFAULT_TYPE, tgame_id: int) -> None:
    _cancel_timer(tgame_id)
    tgame = team_games.get(tgame_id)
    if not tgame or tgame["phase"] not in ("innings1", "innings2"):
        return
    if tgame["timeout_secs"] <= 0:
        return

    async def timeout_countdown():
        try:
            warning_delay = tgame["timeout_secs"] - 30
            if warning_delay > 0:
                await asyncio.sleep(warning_delay)
                bof = tgame.get("balls_on_field")
                target_team = tgame["batting_team"] if (not bof or not bof["batter_id"] or bof["pick_phase"] == "batter") else _bowl_key(tgame)
                await ctx.bot.send_message(
                    chat_id=tgame["chat_id"],
                    text=f"⏳ *WARNING:* 30 seconds left before timeout! *{_tname(tgame, target_team)}* will be penalized *{tgame['penalty_runs']} runs*!",
                    parse_mode="Markdown"
                )
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(tgame["timeout_secs"])

            bof = tgame.get("balls_on_field")
            inn = tgame["current_innings"]
            d = tgame["innings_data"][inn]
            penalty = tgame["penalty_runs"]

            if not bof or not bof["batter_id"] or bof["pick_phase"] == "batter":
                fault_team = tgame["batting_team"]
                d["score"] -= penalty
                fault_reason = "assigning/picking their batter inside the deadline"
            else:
                fault_team = _bowl_key(tgame)
                d["score"] += penalty
                fault_reason = "assigning/picking their bowler inside the deadline"

            await ctx.bot.send_message(
                chat_id=tgame["chat_id"],
                text=f"🚨 *TIMEOUT!* *{_tname(tgame, fault_team)}* failed at {fault_reason}.\n⚠️ Penalty of *{penalty} runs* has been applied!",
                parse_mode="Markdown"
            )

            if inn == 2 and d["score"] >= tgame["target"]:
                await _end_match(ctx, tgame_id)
                return

            if bof and bof["msg_id"]:
                try:
                    await ctx.bot.edit_message_reply_markup(chat_id=tgame["chat_id"], message_id=bof["msg_id"], reply_markup=None)
                except Exception:
                    pass
                bof["batter_pick"] = None
                bof["bowler_pick"] = None
                bof["pick_phase"] = "batter"

            await _launch_ball_game(ctx, tgame_id)

        except asyncio.CancelledError:
            pass

    active_timers[tgame_id] = asyncio.create_task(timeout_countdown())


# ─────────────────────────────────────────────────────────────
#  BASIC COMMANDS
# ─────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _cache_user(update.effective_user)
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
        "/declare — Forfeit current batting innings _(captain/host)_\n"
        "/endgame — End active team game _(host only)_\n"
        "/add @user A|B — Add player to a team _(host only)_\n"
        "/remove @user — Remove player from team _(host only)_\n"
        "/batting @user|me — Assign batter _(captain / host / self)_\n"
        "/bowling @user|me — Assign bowler _(captain / host / self)_\n",
        parse_mode="Markdown",
    )


async def cmd_flip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(random.choice(["🪙 Heads!", "🪙 Tails!"]))


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    _cache_user(user)
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
            "🥈 Pro"    if wr >= 50 else
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
    user = update.effective_user
    _cache_user(user)
    # Inject Commander ID to restrict initialization configurations
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ 1v1",        callback_data=f"mode:1v1:{user.id}")],
        [InlineKeyboardButton("🏟️ Team Mode", callback_data=f"mode:team:{user.id}")],
    ])
    await update.message.reply_text(
        "🏏 *Choose Game Mode:*",
        reply_markup=kb,
        parse_mode="Markdown",
    )


async def cb_mode_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    _cache_user(user)
    await query.answer()
    
    parts = query.data.split(":")
    choice = parts[1]
    creator_id = int(parts[2])
    
    if user.id != creator_id:
        await query.answer("🛑 You aren't the commander who initiated this game call setup!", show_alert=True)
        return

    if choice == "1v1":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔢 With 5  (1–6)",             callback_data=f"1v1v:with5:{creator_id}")],
            [InlineKeyboardButton("0️⃣ Without 5  (0,1,2,3,4,6)", callback_data=f"1v1v:no5:{creator_id}")],
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
    batter   = g["batter"]["name"]
    bowler   = g["bowler"]["name"]
    score    = g["score"]
    ball     = g["ball"]
    ov, b_in = divmod(ball, 6)
    target   = g.get("target")
    b_runs   = g.get("batter_runs", 0)
    b_balls  = g.get("batter_balls", 0)
    recent   = "  ".join(g.get("history", [])[-6:]) or "-"
    last_bowl = g.get("last_bowl_num")
    bowl_line = f"🎳 Bowler's last: *{last_bowl}*" if last_bowl is not None else ""
    pick_phase = g.get("pick_phase", "batter")
    lines = [
        "🏏 *CRICKET — 1v1*", "",
        f"{'Innings 1' if g['innings'] == 1 else 'Innings 2'}  |  *{batter}* 🏏 vs 🎳 *{bowler}*", "",
        f"📊 Score: *{score}*  |  Over: *{ov}.{b_in}*",
        f"🏏 *{batter}*: *{b_runs}* off *{b_balls}* balls",
    ]
    if target:
        lines.append(f"🎯 Target: {target}  |  Need: *{target - score}*")
    lines += ["", f"🕐 This over: {recent}", bowl_line, ""]
    if pick_phase == "batter":
        lines += [
            f"⏳ Waiting for *{batter}* to pick...",
            f"🔒 *{bowler}* picks after", "",
            f"👇 *{batter}*, pick:",
        ]
    else:
        lines += [
            f"✅ *{batter}* has picked!",
            f"⏳ Waiting for *{bowler}*...", "",
            f"👇 *{bowler}*, pick:",
        ]
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
    query      = update.callback_query
    challenger = update.effective_user
    _cache_user(challenger)
    await query.answer()
    
    parts = query.data.split(":")
    variant = parts[1]
    creator_id = int(parts[2])
    
    if challenger.id != creator_id:
        await query.answer("🛑 Setup configuration access locked to match commander!", show_alert=True)
        return
        
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
    user  = update.effective_user
    _cache_user(user)
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
    g["phase"]       = "toss"
    coin             = random.choice(["Heads", "Tails"])
    toss_winner      = random.choice(["challenger", "opponent"])
    g["toss_winner"] = toss_winner
    winner_name      = g["challenger"]["name"] if toss_winner == "challenger" else g["opponent"]["name"]
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
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    try:
        game_id = int(parts[1])
        choice  = parts[2]
    except (IndexError, ValueError):
        await query.answer()
        return
    g = duel_games.get(game_id)
    if not g or g["phase"] != "toss":
        await query.answer()
        return
    tw        = g["toss_winner"]
    winner_id = g["challenger"]["id"] if tw == "challenger" else g["opponent"]["id"]
    if user.id != winner_id:
        await query.answer("You didn't win the toss!", show_alert=True)
        return
    await query.answer()
    c, o             = g["challenger"], g["opponent"]
    batter, bowler   = (c, o) if (tw == "challenger") == (choice == "bat") else (o, c)
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
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    try:
        game_id = int(parts[1])
        num     = int(parts[2])
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
        g["batter_pick"]  = num
        g["pick_phase"]   = "bowler"
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
    bat_n  = g["batter_pick"]
    bowl_n = g["bowler_pick"]
    g.update(last_bowl_num=bowl_n, batter_pick=None, bowler_pick=None, pick_phase="batter")
    g["ball"]         += 1
    g["batter_balls"] += 1
    ball    = g["ball"]
    chat_id = g["chat_id"]
    ov, b_in = divmod(ball, 6)
    sr = round((g["batter_runs"] / g["batter_balls"]) * 100, 1) if g["batter_balls"] else 0.0

    if bat_n == bowl_n:
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
                    result_line   = f"🎯 Target of *{g['target']}* chased!"
                else:
                    margin        = g["target"] - 1 - score2
                    winner, loser = g["bowler"], g["batter"]
                    result_line   = f"🛡️ Defended by *{margin}* run{'s' if margin != 1 else ''}!"
                record_result(winner["id"], winner["name"], won=True)
                record_result(loser["id"],  loser["name"],  won=False)
                await ctx.bot.edit_message_text(
                    chat_id=chat_id, message_id=game_id,
                    text=_duel_scorecard(g, result_line, winner_name=winner["name"]),
                    parse_mode="Markdown",
                )
            del duel_games[game_id]
    else:
        g["score"]       += bat_n
        g["batter_runs"] += bat_n
        g["history"].append(str(bat_n))
        if g["innings"] == 2 and g["score"] >= g["target"]:
            g["innings2_scorecard"] = {
                "batter_name": g["batter"]["name"], "bowler_name": g["bowler"]["name"],
                "runs": g["score"], "balls": g["batter_balls"], "wickets": 0, "out": False,
            }
            winner, loser = g["batter"], g["bowler"]
            result_line   = f"🎯 Target of *{g['target']}* chased!"
            record_result(winner["id"], winner["name"], won=True)
            record_result(loser["id"],  loser["name"],  won=False)
            await ctx.bot.edit_message_text(
                chat_id=chat_id, message_id=game_id,
                text=_duel_scorecard(g, result_line, winner_name=winner["name"]),
                parse_mode="Markdown",
            )
            del duel_games[game_id]
            return
        if ball % 6 == 0:
            ov_balls  = g["history"][-6:]
            runs_ov   = sum(int(b) for b in ov_balls if b.lstrip("-").isdigit())
            extra     = f"  |  Need: *{g['target'] - g['score']}*" if g.get("target") else ""
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
#  TEAM MODE — HELPERS
# ═════════════════════════════════════════════════════════════
def _tname(tgame: dict, key: str) -> str:
    return tgame[f"team_{key}"]["name"]


def _bowl_key(tgame: dict) -> str:
    return "b" if tgame["batting_team"] == "a" else "a"


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
    vt = "With 5 (1–6)" if tgame["variant"] == "with5" else "Without 5 (0,1,2,3,4,6)"
    to = f"{tgame['timeout_secs']}s" if tgame["timeout_secs"] > 0 else "Off"
    tp = f"{tgame['penalty_runs']} runs"
    ta, tb = tgame["team_a"], tgame["team_b"]
    return (
        f"🏟️ *TEAM CRICKET SETUP*\n"
        f"Host: *{tgame['host_name']}*\n"
        f"─────────────────────────\n"
        f"🅰️ *{ta['name']}*  Cap: {ta['captain_name'] or '_none_'}\n{mlist('a')}\n\n"
        f"🅱️ *{tb['name']}*  Cap: {tb['captain_name'] or '_none_'}\n{mlist('b')}\n\n"
        f"⚙️ Overs: *{ov}*  |  Variant: *{vt}*\n"
        f"⏳ Timeout: *{to}*  |  Penalty: *{tp}*\n"
        f"─────────────────────────\n"
        f"_Tap a button to join. Host can /add @user A|B or /remove @user._"
    )


def _setup_kb(tgame_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Join Team A", callback_data=f"tj:{tgame_id}:a"),
         InlineKeyboardButton("➕ Join Team B", callback_data=f"tj:{tgame_id}:b")],
        [InlineKeyboardButton("🎯 Set Overs",         callback_data=f"tsetovers:{tgame_id}")],
        [InlineKeyboardButton("🔢 Toggle Variant",    callback_data=f"tvariant:{tgame_id}")],
        [InlineKeyboardButton("⏳ Config Timeout",    callback_data=f"tmenu_to_sec:{tgame_id}")],
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


# ═════════════════════════════════════════════════════════════
#  TEAM MODE — SETUP START
# ═════════════════════════════════════════════════════════════
async def _team_setup_start(query, ctx) -> None:
    host    = query.from_user
    chat_id = query.message.chat.id

    if chat_id in group_team_game:
        await query.edit_message_text(
            "⚠️ There is already an active team game in this group.\n"
            "The host must use /endgame to end it first.",
        )
        return

    tgame = {
        "chat_id":   chat_id,
        "host_id":   host.id,
        "host_name": host.first_name,
        "phase":     "setup",
        "overs":     5,
        "variant":   "with5",
        "timeout_secs": 60,   
        "penalty_runs": 5,    
        "team_a": {"name": "Team A", "captain_id": None, "captain_name": None, "members": {}, "claim_msg_id": None},
        "team_b": {"name": "Team B", "captain_id": None, "captain_name": None, "members": {}, "claim_msg_id": None},
        "toss_caller_team":  None,
        "toss_flipper_team": None,
        "toss_call":         None,
        "toss_winner":       None,
        "batting_team":          None,
        "innings1_batting_team": None,
        "innings2_batting_team": None,
        "current_innings":       1,
        "target":                None,
        "innings_data": {
            1: {"score": 0, "wickets": 0, "balls": 0, "history": [], "batter_runs": {}, "batter_balls": {}, "bowler_wickets": {}},
            2: {"score": 0, "wickets": 0, "balls": 0, "history": [], "batter_runs": {}, "batter_balls": {}, "bowler_wickets": {}},
        },
        "balls_on_field": None,
        "prev_bowlers":   set(),
        "game_msg_id":    None,
    }

    msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text=_setup_text(tgame),
        reply_markup=_setup_kb(0),
        parse_mode="Markdown",
    )
    tgame["game_msg_id"]         = msg.message_id
    team_games[msg.message_id]   = tgame
    group_team_game[chat_id]     = msg.message_id

    await ctx.bot.edit_message_reply_markup(
        chat_id=chat_id, message_id=msg.message_id,
        reply_markup=_setup_kb(msg.message_id),
    )


# ─────────────────────────────────────────────────────────────
#  /declare
# ─────────────────────────────────────────────────────────────
async def cmd_declare(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user
    _cache_user(user)
    tgame_id = group_team_game.get(chat_id)
    if not tgame_id:
        return
    tgame = team_games.get(tgame_id)
    if not tgame or tgame["phase"] not in ("innings1", "innings2"):
        return
        
    bk = tgame["batting_team"]
    if user.id != tgame[f"team_{bk}"]["captain_id"] and user.id != tgame["host_id"]:
        await update.message.reply_text("🛑 Only your Captain or Host can declare the innings profile.")
        return

    _cancel_timer(tgame_id)
    await update.message.reply_text(f"📢 *Innings Declared!* *{user.first_name}* closed the innings profile.", parse_mode="Markdown")
    
    if tgame["current_innings"] == 1:
        await _end_innings1(ctx, tgame_id)
    else:
        await _end_match(ctx, tgame_id)


# ─────────────────────────────────────────────────────────────
#  /endgame
# ─────────────────────────────────────────────────────────────
async def cmd_endgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id  = update.effective_chat.id
    user     = update.effective_user
    _cache_user(user)
    tgame_id = group_team_game.get(chat_id)
    if not tgame_id:
        await update.message.reply_text("No active team game in this chat.")
        return
    tgame = team_games.get(tgame_id)
    if tgame and user.id != tgame["host_id"]:
        await update.message.reply_text("🛑 Only the host can terminate active matches.")
        return
    if tgame:
        _cancel_timer(tgame_id)
        tgame["phase"] = "finished"
        del team_games[tgame_id]
    del group_team_game[chat_id]
    await update.message.reply_text(
        f"🏏 Game ended by *{user.first_name}*.", parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────
#  /add @user A|B
# ─────────────────────────────────────────────────────────────
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id  = update.effective_chat.id
    user     = update.effective_user
    _cache_user(user)
    tgame_id = group_team_game.get(chat_id)
    if not tgame_id:
        await update.message.reply_text("No active team game in this chat.")
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    if user.id != tgame["host_id"]:
        await update.message.reply_text("🛑 Only the host can add players.")
        return
    target_id, target_name = _resolve_mention(update.message)
    if not target_id:
        await update.message.reply_text("Please mention a user: /add @username A  or  /add @username B")
        return
    team_letter: Optional[str] = None
    for arg in (ctx.args or []):
        if arg.upper() in ("A", "B"):
            team_letter = arg.lower()
            break
    if not team_letter:
        await update.message.reply_text("Specify the team: /add @username A  or  /add @username B")
        return
    other = "b" if team_letter == "a" else "a"
    tgame[f"team_{other}"]["members"].pop(target_id, None)
    if tgame[f"team_{other}"]["captain_id"] == target_id:
        tgame[f"team_{other}"]["captain_id"]   = None
        tgame[f"team_{other}"]["captain_name"] = None
    display = target_name or "Player"
    tgame[f"team_{team_letter}"]["members"][target_id] = display
    await update.message.reply_text(
        f"✅ *{display}* added to *{_tname(tgame, team_letter)}*!", parse_mode="Markdown"
    )
    if tgame["phase"] == "setup":
        await _refresh_setup(ctx, tgame)


# ─────────────────────────────────────────────────────────────
#  /remove @user
# ─────────────────────────────────────────────────────────────
async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id  = update.effective_chat.id
    user     = update.effective_user
    _cache_user(user)
    tgame_id = group_team_game.get(chat_id)
    if not tgame_id:
        await update.message.reply_text("No active team game in this chat.")
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    if user.id != tgame["host_id"]:
        await update.message.reply_text("🛑 Only the host can remove players.")
        return
    target_id, _ = _resolve_mention(update.message)
    if not target_id:
        await update.message.reply_text("Please mention a user: /remove @username")
        return
    removed = False
    for t in ["a", "b"]:
        if target_id in tgame[f"team_{t}"]["members"]:
            name = tgame[f"team_{t}"]["members"].pop(target_id)
            if tgame[f"team_{t}"]["captain_id"] == target_id:
                tgame[f"team_{t}"]["captain_id"]   = None
                tgame[f"team_{t}"]["captain_name"] = None
            await update.message.reply_text(
                f"✅ *{name}* removed from *{_tname(tgame, t)}*!", parse_mode="Markdown"
            )
            removed = True
            break
    if not removed:
        await update.message.reply_text("That user is not in any team.")
    elif tgame["phase"] == "setup":
        await _refresh_setup(ctx, tgame)


# ─────────────────────────────────────────────────────────────
#  Join Team / Captain Dashboard Message Strategy Collapse
# ─────────────────────────────────────────────────────────────
async def cb_team_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        team     = parts[2]
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer("Game not found.")
        return
    if tgame["phase"] != "setup":
        await query.answer("The game has already started!", show_alert=True)
        return
    for t in ["a", "b"]:
        if user.id in tgame[f"team_{t}"]["members"]:
            await query.answer("You are already in a team! No manual switching.", show_alert=True)
            return
            
    tgame[f"team_{team}"]["members"][user.id] = user.first_name
    await query.answer(f"Joined {_tname(tgame, team)}!")
    await _refresh_setup(ctx, tgame)
    
    # Send a single claim message per team (instead of spawning updates continuously)
    await _send_team_claim_msg(ctx, tgame, team)


async def _send_team_claim_msg(ctx, tgame, team):
    tdata = tgame[f"team_{team}"]
    # Block processing if captain exists or a claim block is already live
    if tdata["captain_id"] or tdata.get("claim_msg_id") is not None:
        return
        
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"👑 I am the Captain of {_tname(tgame, team)}", callback_data=f"tclaim_cap:{tgame['game_msg_id']}:{team}")
    ]])
    
    msg = await ctx.bot.send_message(
        chat_id=tgame["chat_id"],
        text=f"🏟️ *{_tname(tgame, team)} Captaincy Declaration*\nIf you joined this side, click below to take charge!",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    tdata["claim_msg_id"] = msg.message_id


async def cb_team_claim_cap(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    parts = query.data.split(":")
    tgame_id, team = int(parts[1]), parts[2]
    
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
        
    tdata = tgame[f"team_{team}"]
    if tdata["captain_id"]:
        await query.answer("Captain already assigned!")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
        
    if user.id not in tdata["members"]:
        await query.answer(f"🛑 Access Denied! You must join {_tname(tgame, team)} first to declare yourself captain.", show_alert=True)
        return
        
    tdata["captain_id"] = user.id
    tdata["captain_name"] = user.first_name
    
    await query.answer("Captain status successfully updated!")
    # Collapses the message block inline smoothly as specified
    await query.edit_message_text(
        text=f"✅ *{user.first_name}* is the captain of *{_tname(tgame, team)}*!",
        reply_markup=None,
        parse_mode="Markdown"
    )
    await _refresh_setup(ctx, tgame)


# ─────────────────────────────────────────────────────────────
#  Set Overs (Host Only)
# ─────────────────────────────────────────────────────────────
async def cb_set_overs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    tgame_id = int(parts[1])
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    if user.id != tgame["host_id"]:
        await query.answer("🛑 Configuration locked. Only the host can edit overs.", show_alert=True)
        return
    await query.answer()
    rows, row = [], []
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
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    tgame_id = int(parts[1])
    overs    = int(parts[2])
    tgame = team_games.get(tgame_id)
    if not tgame or user.id != tgame["host_id"]:
        await query.answer("Unauthorized parameter command mapping.", show_alert=True)
        return
    tgame["overs"] = overs
    await query.answer(f"Overs set to {overs}!")
    await query.edit_message_reply_markup(reply_markup=None)
    await _refresh_setup(ctx, tgame)


# ─────────────────────────────────────────────────────────────
#  Configure Timeout System (Host Only)
# ─────────────────────────────────────────────────────────────
async def cb_menu_timeout_secs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    _cache_user(user)
    tgame_id = int(query.data.split(":")[1])
    tgame = team_games.get(tgame_id)
    if not tgame or user.id != tgame["host_id"]:
        await query.answer("🛑 Configuration locked. Only the host can configure timeouts.", show_alert=True)
        return
    await query.answer()
    
    rows, row = [], []
    for sec in range(30, 301, 30):
        row.append(InlineKeyboardButton(f"{sec}s", callback_data=f"tselect_sec:{tgame_id}:{sec}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Turn Timeout OFF", callback_data=f"tselect_sec:{tgame_id}:0")])
    
    await ctx.bot.send_message(
        tgame["chat_id"], "⏳ *Step 1: Choose Timeout Duration*", reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown"
    )


async def cb_select_timeout_secs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    tgame_id = int(parts[1])
    secs = int(parts[2])
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    await query.answer()
    
    if secs == 0:
        tgame["timeout_secs"] = 0
        tgame["penalty_runs"] = 0
        await query.edit_message_text("✅ Timeout system deactivated.")
        await _refresh_setup(ctx, tgame)
        return

    tgame["_temp_secs"] = secs
    row = [InlineKeyboardButton(f"{r} Run{'s' if r > 1 else ''}", callback_data=f"tfinish_to:{tgame_id}:{r}") for r in range(1, 7)]
    kb = InlineKeyboardMarkup([row[0:3], row[3:6]])
    
    await query.edit_message_text(
        text=f"⏳ *Step 2: Choose Run Penalty*\nDuration selected: *{secs}s*\n\nSelect runs to deduct on timeout:",
        reply_markup=kb, parse_mode="Markdown"
    )


async def cb_finish_timeout_config(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    tgame_id = int(parts[1])
    runs = int(parts[2])
    tgame = team_games.get(tgame_id)
    if tgame and "_temp_secs" in tgame:
        tgame["timeout_secs"] = tgame["_temp_secs"]
        tgame["penalty_runs"] = runs
        del tgame["_temp_secs"]
        
    await query.answer("Configuration applied!")
    await query.edit_message_text(f"✅ Timeout Configured: *{tgame['timeout_secs']}s* with a *{runs} run* penalty.")
    await _refresh_setup(ctx, tgame)


# ─────────────────────────────────────────────────────────────
#  Toggle Variant (Host Only)
# ─────────────────────────────────────────────────────────────
async def cb_toggle_variant(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    tgame_id = int(parts[1])
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    if user.id != tgame["host_id"]:
        await query.answer("🛑 Configuration locked. Only the host can toggle variants.", show_alert=True)
        return
    tgame["variant"] = "no5" if tgame["variant"] == "with5" else "with5"
    label = "With 5 (1–6)" if tgame["variant"] == "with5" else "Without 5 (0,1,2,3,4,6)"
    await query.answer(f"Variant: {label}")
    await _refresh_setup(ctx, tgame)


# ═════════════════════════════════════════════════════════════
#  TEAM TOSS (Host Starts)
# ═════════════════════════════════════════════════════════════
async def cb_team_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    tgame_id = int(parts[1])
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    if user.id != tgame["host_id"]:
        await query.answer("🛑 Only the game host can start the match structure.", show_alert=True)
        return
    ta, tb = tgame["team_a"], tgame["team_b"]
    if not ta["members"] or not tb["members"]:
        await query.answer("Both teams need at least 1 player!", show_alert=True)
        return
    if not ta["captain_id"] or not tb["captain_id"]:
        await query.answer("Both teams need a captain assigned first!", show_alert=True)
        return
    await query.answer()

    teams = ["a", "b"]
    random.shuffle(teams)
    tgame["toss_caller_team"]  = teams[0]
    tgame["toss_flipper_team"] = teams[1]
    tgame["toss_call"]         = None
    tgame["phase"]             = "toss"

    caller_cap   = tgame[f"team_{teams[0]}"]["captain_name"]
    caller_tname = _tname(tgame, teams[0])
    flipper_cap  = tgame[f"team_{teams[1]}"]["captain_name"]
    flipper_tname = _tname(tgame, teams[1])

    await ctx.bot.edit_message_text(
        chat_id=tgame["chat_id"], message_id=tgame["game_msg_id"],
        text=(
            f"🏟️ *TEAM CRICKET — TOSS*\n\n"
            f"👑 *{caller_cap}* ({caller_tname}) — call it!\n"
            f"👑 *{flipper_cap}* ({flipper_tname}) — will flip the coin\n\n"
            f"*{caller_cap}*, choose:"
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🪙 Heads", callback_data=f"ttoss_call:{tgame_id}:heads"),
            InlineKeyboardButton("🪙 Tails", callback_data=f"ttoss_call:{tgame_id}:tails"),
        ]]),
        parse_mode="Markdown",
    )


async def cb_team_toss_call(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    tgame_id = int(parts[1])
    call     = parts[2]
    tgame = team_games.get(tgame_id)
    if not tgame or tgame["phase"] != "toss":
        await query.answer()
        return
    caller_team   = tgame["toss_caller_team"]
    caller_cap_id = tgame[f"team_{caller_team}"]["captain_id"]
    if user.id != caller_cap_id:
        await query.answer("Only the calling captain can make the call!", show_alert=True)
        return
    if tgame["toss_call"] is not None:
        await query.answer("Already called!")
        return
    tgame["toss_call"] = call
    await query.answer(f"Called {call.capitalize()}!")

    caller_cap    = tgame[f"team_{caller_team}"]["captain_name"]
    caller_tname  = _tname(tgame, caller_team)
    flipper_team  = tgame["toss_flipper_team"]
    flipper_cap   = tgame[f"team_{flipper_team}"]["captain_name"]

    await ctx.bot.edit_message_text(
        chat_id=tgame["chat_id"], message_id=tgame["game_msg_id"],
        text=(
            f"🏟️ *TEAM CRICKET — TOSS*\n\n"
            f"👑 *{caller_cap}* ({caller_tname}) called: *{call.capitalize()}*\n\n"
            f"👑 *{flipper_cap}*, flip the coin!"
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🪙 Flip!", callback_data=f"ttoss_flip:{tgame_id}"),
        ]]),
        parse_mode="Markdown",
    )


async def cb_team_toss_flip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    tgame_id = int(parts[1])
    tgame = team_games.get(tgame_id)
    if not tgame or tgame["phase"] != "toss":
        await query.answer()
        return
    flipper_team   = tgame["toss_flipper_team"]
    flipper_cap_id = tgame[f"team_{flipper_team}"]["captain_id"]
    if user.id != flipper_cap_id:
        await query.answer("Only the flipping captain can execute the action!", show_alert=True)
        return
    await query.answer("Flipping… 🪙")

    result       = random.choice(["heads", "tails"])
    call         = tgame["toss_call"]
    caller_team  = tgame["toss_caller_team"]
    winner_team  = caller_team if call == result else flipper_team
    tgame["toss_winner"] = winner_team

    caller_cap  = tgame[f"team_{caller_team}"]["captain_name"]
    winner_cap  = tgame[f"team_{winner_team}"]["captain_name"]
    winner_tname = _tname(tgame, winner_team)
    correct_txt  = "✅ Correct!" if call == result else "❌ Wrong!"

    await ctx.bot.edit_message_text(
        chat_id=tgame["chat_id"], message_id=tgame["game_msg_id"],
        text=(
            f"🏟️ *TEAM CRICKET — TOSS*\n\n"
            f"🪙 Result: *{result.capitalize()}*!\n"
            f"👑 *{caller_cap}* called *{call.capitalize()}* — {correct_txt}\n\n"
            f"🏆 *{winner_cap}* ({winner_tname}) wins the toss!\n\n"
            f"*{winner_cap}*, choose:"
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏏 Bat",  callback_data=f"ttoss:{tgame_id}:bat"),
            InlineKeyboardButton("🎳 Bowl", callback_data=f"ttoss:{tgame_id}:bowl"),
        ]]),
        parse_mode="Markdown",
    )


async def cb_team_toss(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    tgame_id = int(parts[1])
    choice   = parts[2]
    tgame = team_games.get(tgame_id)
    if not tgame or tgame["phase"] != "toss":
        await query.answer()
        return
    tw     = tgame["toss_winner"]
    cap_id = tgame[f"team_{tw}"]["captain_id"]
    if user.id != cap_id:
        await query.answer("Only the toss-winning captain can settle choices!", show_alert=True)
        return
    await query.answer()

    tgame["batting_team"]          = tw if choice == "bat" else ("b" if tw == "a" else "a")
    tgame["innings1_batting_team"] = tgame["batting_team"]
    tgame["phase"]                 = "innings1"
    tgame["current_innings"]       = 1
    bk = tgame["batting_team"]
    wk = _bowl_key(tgame)

    await ctx.bot.edit_message_text(
        chat_id=tgame["chat_id"], message_id=tgame["game_msg_id"],
        text=(
            f"🏟️ *TEAM CRICKET — INNINGS 1*\n\n"
            f"🏏 *{_tname(tgame, bk)}* bat first\n"
            f"🎳 *{_tname(tgame, wk)}* bowl\n\n"
            f"Overs: *{tgame['overs']}*\n\n"
            f"👑 *{tgame[f'team_{bk}']['captain_name']}* — `/batting @username` or `/batting me` to send opener\n"
            f"👑 *{tgame[f'team_{wk}']['captain_name']}* — `/bowling @username` or `/bowling me` to send opener"
        ),
        reply_markup=None,
        parse_mode="Markdown",
    )
    _reset_timer(ctx, tgame_id)


# ═════════════════════════════════════════════════════════════
#  /batting AND /bowling COMMANDS (CO-MANAGED BY HOST & CAPTAIN)
# ═════════════════════════════════════════════════════════════
def _ensure_bof(tgame: dict) -> dict:
    if tgame.get("balls_on_field") is None:
        tgame["balls_on_field"] = {
            "msg_id":      None,
            "batter_id":   None, "batter_name": None,
            "bowler_id":   None, "bowler_name": None,
            "batter_pick": None, "bowler_pick": None,
            "pick_phase":  "batter",
            "last_bowl_num": None,
            "over_history":  [],
        }
    return tgame["balls_on_field"]


async def cmd_batting(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id  = update.effective_chat.id
    user     = update.effective_user
    _cache_user(user)
    tgame_id = group_team_game.get(chat_id)
    if not tgame_id:
        return
    tgame = team_games.get(tgame_id)
    if not tgame or tgame["phase"] not in ("innings1", "innings2"):
        return
    bk = tgame["batting_team"]

    if ctx.args and ctx.args[0].lower() == "me":
        target_id, target_name = user.id, user.first_name
    else:
        if user.id != tgame[f"team_{bk}"]["captain_id"] and user.id != tgame["host_id"]:
            await update.message.reply_text("🛑 Lineups can only be mapped by your Team Captain or the Game Host.")
            return
        target_id, target_name = _resolve_mention(update.message)

    if not target_id:
        await update.message.reply_text("Please mention a player or use 'me': /batting @username or /batting me")
        return
    if target_id not in tgame[f"team_{bk}"]["members"]:
        await update.message.reply_text(f"*{target_name}* is not in *{_tname(tgame, bk)}*!", parse_mode="Markdown")
        return

    display = tgame[f"team_{bk}"]["members"][target_id]
    bof = _ensure_bof(tgame)
    bof["batter_id"]   = target_id
    bof["batter_name"] = display
    await update.message.reply_text(f"✅ *{display}* is now batting!", parse_mode="Markdown")
    
    if bof["batter_id"] and bof["bowler_id"]:
        await _launch_ball_game(ctx, tgame_id)


async def cmd_bowling(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id  = update.effective_chat.id
    user     = update.effective_user
    _cache_user(user)
    tgame_id = group_team_game.get(chat_id)
    if not tgame_id:
        return
    tgame = team_games.get(tgame_id)
    if not tgame or tgame["phase"] not in ("innings1", "innings2"):
        return
    wk = _bowl_key(tgame)

    if ctx.args and ctx.args[0].lower() == "me":
        target_id, target_name = user.id, user.first_name
    else:
        if user.id != tgame[f"team_{wk}"]["captain_id"] and user.id != tgame["host_id"]:
            await update.message.reply_text("🛑 Lineups can only be mapped by your Team Captain or the Game Host.")
            return
        target_id, target_name = _resolve_mention(update.message)

    if not target_id:
        await update.message.reply_text("Please mention a player or use 'me': /bowling @username or /bowling me")
        return
    if target_id not in tgame[f"team_{wk}"]["members"]:
        await update.message.reply_text(f"*{target_name}* is not in *{_tname(tgame, wk)}*!", parse_mode="Markdown")
        return
    if target_id in tgame.get("prev_bowlers", set()):
        display = tgame[f"team_{wk}"]["members"][target_id]
        await update.message.reply_text(f"⚠️ *{display}* bowled the last over. Choose a different bowler.", parse_mode="Markdown")
        return

    display = tgame[f"team_{wk}"]["members"][target_id]
    bof = _ensure_bof(tgame)
    bof["bowler_id"]   = target_id
    bof["bowler_name"] = display
    await update.message.reply_text(f"✅ *{display}* is now bowling!", parse_mode="Markdown")
    
    if bof["batter_id"] and bof["bowler_id"]:
        await _launch_ball_game(ctx, tgame_id)


# ═════════════════════════════════════════════════════════════
#  TEAM FIELD — BALL-BY-BALL GAME
# ═════════════════════════════════════════════════════════════
def _team_kb(tgame_id: int, variant: str) -> InlineKeyboardMarkup:
    if variant == "with5":
        r1 = [InlineKeyboardButton(str(n), callback_data=f"tp:{tgame_id}:{n}") for n in [1, 2, 3]]
        r2 = [InlineKeyboardButton(str(n), callback_data=f"tp:{tgame_id}:{n}") for n in [4, 5, 6]]
    else:
        r1 = [InlineKeyboardButton(str(n), callback_data=f"tp:{tgame_id}:{n}") for n in [0, 1, 2]]
        r2 = [InlineKeyboardButton(str(n), callback_data=f"tp:{tgame_id}:{n}") for n in [3, 4, 6]]
    return InlineKeyboardMarkup([r1, r2])


def _team_field_text(tgame: dict) -> str:
    inn        = tgame["current_innings"]
    bof        = tgame["balls_on_field"]
    d          = tgame["innings_data"][inn]
    score      = d["score"]
    wickets    = d["wickets"]
    balls      = d["balls"]
    ov, b_in   = divmod(balls, 6)
    target     = tgame.get("target")
    batter_name = bof["batter_name"]
    bowler_name = bof["bowler_name"]
    recent      = "  ".join(bof["over_history"][-6:]) or "-"
    last_bowl   = bof.get("last_bowl_num")
    bowl_line   = f"🎳 Bowler's last: *{last_bowl}*" if last_bowl is not None else ""
    pick_phase  = bof.get("pick_phase", "batter")
    bk          = tgame["batting_team"]
    wk          = _bowl_key(tgame)
    b_runs      = d["batter_runs"].get(bof["batter_id"], 0)
    b_balls_c   = d["batter_balls"].get(bof["batter_id"], 0)

    lines = [
        f"🏟️ *TEAM CRICKET — Innings {inn}*", "",
        f"*{_tname(tgame, bk)}* 🏏  vs  🎳 *{_tname(tgame, wk)}*", "",
        f"📊 *{score}/{wickets}*  |  Over: *{ov}.{b_in}*  |  Remaining: *{tgame['overs'] - ov}*",
    ]
    if target:
        need       = target - score
        balls_left = tgame["overs"] * 6 - balls
        lines.append(f"🎯 Target: {target}  |  Need: *{need}* in *{balls_left}* balls")
    lines += [
        "",
        f"🏏 Batter: *{batter_name}*: *{b_runs}* off *{b_balls_c}* balls",
        f"🎳 Bowler: *{bowler_name}*",
        "", f"🕐 This over: {recent}", bowl_line, "",
    ]
    if pick_phase == "batter":
        lines += [
            f"⏳ Waiting for *{batter_name}* to pick...",
            f"🔒 *{bowler_name}* picks after", "",
            f"👇 *{batter_name}*, pick:",
        ]
    else:
        lines += [
            f"✅ *{batter_name}* has picked!",
            f"⏳ Waiting for *{bowler_name}*...", "",
            f"👇 *{bowler_name}*, pick:",
        ]
    return "\n".join(l for l in lines if l is not None)


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
    _reset_timer(ctx, tgame_id)


async def cb_team_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    _cache_user(user)
    parts = query.data.split(":")
    try:
        tgame_id = int(parts[1])
        num      = int(parts[2])
    except (IndexError, ValueError):
        await query.answer()
        return
    tgame = team_games.get(tgame_id)
    if not tgame:
        await query.answer()
        return
    bof = tgame.get("balls_on_field")
    if not bof or not bof.get("msg_id") or query.message.message_id != bof["msg_id"]:
        await query.answer("This play sequence has expired or been replaced.")
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
        bof["pick_phase"]  = "bowler"
        await query.answer(f"Picked {num} 🤫 — bowler's turn!")
        _reset_timer(ctx, tgame_id)
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
        _cancel_timer(tgame_id)
        await _team_resolve(ctx, tgame_id)


async def _team_resolve(ctx, tgame_id: int) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    bof        = tgame["balls_on_field"]
    bat_n      = bof["batter_pick"]
    bowl_n     = bof["bowler_pick"]
    bof.update(last_bowl_num=bowl_n, batter_pick=None, bowler_pick=None, pick_phase="batter")
    inn        = tgame["current_innings"]
    d          = tgame["innings_data"][inn]
    d["balls"] += 1
    balls      = d["balls"]
    chat_id    = tgame["chat_id"]
    batter_id  = bof["batter_id"]
    batter_name = bof["batter_name"]
    bowler_name = bof["bowler_name"]
    bowler_id   = bof["bowler_id"]
    d["batter_balls"][batter_id] = d["batter_balls"].get(batter_id, 0) + 1
    ov, b_in   = divmod(balls, 6)
    sr_b       = d["batter_runs"].get(batter_id, 0)
    sr_bb      = d["batter_balls"].get(batter_id, 0)
    sr         = round((sr_b / sr_bb) * 100, 1) if sr_bb else 0.0

    if bat_n == bowl_n:
        d["wickets"] += 1
        d["bowler_wickets"][bowler_id] = d["bowler_wickets"].get(bowler_id, 0) + 1
        bof["over_history"].append("W")
        d["history"].append("W")
        wkt_text = (
            f"💥 *WICKET!*\n\n"
            f"🏏 *{batter_name}* OUT for *{sr_b}* off *{sr_bb}* balls  SR {sr}\n"
            f"🎳 Bowled by *{bowler_name}*\n"
            f"⚡ Over *{ov}.{b_in}*  |  Score: *{d['score']}/{d['wickets']}*"
        )
        await ctx.bot.send_message(chat_id, wkt_text, parse_mode="Markdown")
        bk      = tgame["batting_team"]
        total   = len(tgame[f"team_{bk}"]["members"])
        all_out = d["wickets"] >= total
        if all_out:
            if inn == 1:
                await _end_innings1(ctx, tgame_id)
            else:
                await _end_match(ctx, tgame_id)
        else:
            await _need_new_batter(ctx, tgame_id, bowler_id, bowler_name)
    else:
        d["score"]  += bat_n
        d["batter_runs"][batter_id] = d["batter_runs"].get(batter_id, 0) + bat_n
        bof["over_history"].append(str(bat_n))
        d["history"].append(str(bat_n))
        if inn == 2 and d["score"] >= tgame["target"]:
            await ctx.bot.send_message(
                chat_id,
                f"🎯 *Target chased!* *{batter_name}* hits the winning runs!\n📊 *{d['score']}/{d['wickets']}*",
                parse_mode="Markdown",
            )
            await _end_match(ctx, tgame_id)
            return
        if balls >= tgame["overs"] * 6:
            if inn == 1:
                await _end_innings1(ctx, tgame_id)
            else:
                await _end_match(ctx, tgame_id)
            return
        if balls % 6 == 0:
            ov_num   = balls // 6
            ov_balls = bof["over_history"][-6:]
            runs_ov  = sum(int(b) for b in ov_balls if b.isdigit())
            extra    = f"  |  Need: *{tgame['target'] - d['score']}*" if tgame.get("target") else ""
            # Includes Bowler Name as requested
            await ctx.bot.send_message(
                chat_id,
                f"📋 *End of Over {ov_num}*\n"
                f"🎳 Bowler: *{bowler_name}*\n"
                f"Balls: {' | '.join(ov_balls)}\nRuns: *{runs_ov}*\n\n"
                f"📊 *{_tname(tgame, tgame['batting_team'])}*: *{d['score']}/{d['wickets']}*{extra}",
                parse_mode="Markdown",
            )
            tgame["prev_bowlers"] = {bowler_id}
            await _need_new_bowler(ctx, tgame_id, batter_id, batter_name)
            return
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id, message_id=bof["msg_id"],
                text=_team_field_text(tgame), reply_markup=_team_kb(tgame_id, tgame["variant"]),
                parse_mode="Markdown",
            )
            _reset_timer(ctx, tgame_id)
        except Exception:
            pass


async def _need_new_batter(ctx, tgame_id: int, bowler_id: int, bowler_name: str) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    bk       = tgame["batting_team"]
    bat_cap  = tgame[f"team_{bk}"]["captain_name"]
    over_hist = tgame["balls_on_field"].get("over_history", [])
    tgame["balls_on_field"] = {
        "msg_id":      None,
        "batter_id":   None, "batter_name": None,
        "bowler_id":   bowler_id, "bowler_name": bowler_name,
        "batter_pick": None, "bowler_pick": None,
        "pick_phase":  "batter",
        "last_bowl_num": None,
        "over_history":  over_hist,
    }
    await ctx.bot.send_message(
        tgame["chat_id"],
        f"🏏 *New batter needed!*\n\n"
        f"👑 *{bat_cap}*, use `/batting @username` or `/batting me` to send your next batter.\n"
        f"🎳 *{bowler_name}* continues bowling.",
        parse_mode="Markdown",
    )
    _reset_timer(ctx, tgame_id)


async def _need_new_bowler(ctx, tgame_id: int, batter_id: int, batter_name: str) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    wk        = _bowl_key(tgame)
    bowl_cap  = tgame[f"team_{wk}"]["captain_name"]
    tgame["balls_on_field"] = {
        "msg_id":      None,
        "batter_id":   batter_id, "batter_name": batter_name,
        "bowler_id":   None, "bowler_name": None,
        "batter_pick": None, "bowler_pick": None,
        "pick_phase":  "batter",
        "last_bowl_num": None,
        "over_history":  [],
    }
    await ctx.bot.send_message(
        tgame["chat_id"],
        f"🎳 *New bowler needed!*\n\n"
        f"👑 *{bowl_cap}*, use `/bowling @username` or `/bowling me` to send your next bowler.\n"
        f"🏏 *{batter_name}* continues batting.\n\n"
        f"_(Same bowler cannot bowl consecutive overs)_",
        parse_mode="Markdown",
    )
    _reset_timer(ctx, tgame_id)


async def _end_innings1(ctx, tgame_id: int) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    d1  = tgame["innings_data"][1]
    bk  = tgame["batting_team"]
    wk  = _bowl_key(tgame)
    await ctx.bot.send_message(
        tgame["chat_id"],
        f"🏁 *End of Innings 1*\n\n"
        f"🏏 *{_tname(tgame, bk)}*: *{d1['score']}/{d1['wickets']}*\n\n"
        f"🎯 *{_tname(tgame, wk)}* need *{d1['score'] + 1}* to win from {tgame['overs']} overs!",
        parse_mode="Markdown",
    )
    tgame["target"]                = d1["score"] + 1
    tgame["batting_team"]          = wk
    tgame["innings2_batting_team"] = wk
    tgame["current_innings"]       = 2
    tgame["phase"]                 = "innings2"
    tgame["prev_bowlers"]          = set()
    tgame["balls_on_field"]        = None
    new_bk = tgame["batting_team"]
    new_wk = _bowl_key(tgame)
    await ctx.bot.send_message(
        tgame["chat_id"],
        f"🔄 *Innings 2 begins!*\n\n"
        f"🏏 *{_tname(tgame, new_bk)}* bat\n"
        f"🎳 *{_tname(tgame, new_wk)}* bowl\n\n"
        f"👑 *{tgame[f'team_{new_bk}']['captain_name']}* — `/batting @username` or `/batting me` for opener\n"
        f"👑 *{tgame[f'team_{new_wk}']['captain_name']}* — `/bowling @username` or `/bowling me` for opener",
        parse_mode="Markdown",
    )
    _reset_timer(ctx, tgame_id)


async def _end_match(ctx, tgame_id: int) -> None:
    tgame = team_games.get(tgame_id)
    if not tgame:
        return
    _cancel_timer(tgame_id)
    chat_id = tgame["chat_id"]
    d1      = tgame["innings_data"][1]
    d2      = tgame["innings_data"][2]
    bat1    = tgame.get("innings1_batting_team", "a")
    bat2    = tgame.get("innings2_batting_team", "b")
    s1, s2  = d1["score"], d2["score"]

    if s2 > s1:
        winner_key  = bat2
        result_line = f"🎯 *{_tname(tgame, bat2)}* chased the target and won!"
    elif s2 == s1:
        winner_key  = None
        result_line = f"🤝 Match tied at *{s1}* runs!"
    else:
        winner_key  = bat1
        margin      = s1 - s2
        result_line = f"🛡️ *{_tname(tgame, bat1)}* defended — won by *{margin}* run{'s' if margin != 1 else ''}!"

    for t in ["a", "b"]:
        won  = (winner_key == t) if winner_key else False
        draw = (winner_key is None)
        for uid, uname in tgame[f"team_{t}"]["members"].items():
            record_result(uid, uname, won=won, draw=draw)

    # ── MVP ENGINE COMPUTATION MATRIX ─────────────────────────
    player_perf = {}
    def process_mvp(d, team_key):
        for uid, uname in tgame[f"team_{team_key}"]["members"].items():
            if uid not in player_perf:
                player_perf[uid] = {"name": uname, "runs": 0, "wickets": 0}
            player_perf[uid]["runs"] += d["batter_runs"].get(uid, 0)
            player_perf[uid]["wickets"] += d["bowler_wickets"].get(uid, 0)

    process_mvp(d1, "a")
    process_mvp(d1, "b")
    process_mvp(d2, "a")
    process_mvp(d2, "b")

    mvp_name, max_weight = None, -1
    for uid, stats in player_perf.items():
        weight = stats["runs"] + (stats["wickets"] * 20)
        if weight > max_weight and (stats["runs"] > 0 or stats["wickets"] > 0):
            max_weight = weight
            mvp_name = f"🏅 *MVP:* *{stats['name']}* ({stats['runs']} runs | {stats['wickets']} wickets)"

    mvp_line = mvp_name if mvp_name else "🏅 *MVP:* None (No performance records)"
    # ──────────────────────────────────────────────────────────

    def bat_summary(d, bat_key):
        lines = []
        for uid, uname in tgame[f"team_{bat_key}"]["members"].items():
            runs   = d["batter_runs"].get(uid)
            balls_b = d["batter_balls"].get(uid, 0)
            if runs is None:
                lines.append(f"  {uname}: DNB")
            else:
                sr = round((runs / balls_b) * 100, 1) if balls_b else 0.0
                lines.append(f"  *{uname}*: *{runs}* ({balls_b}b)  SR {sr}")
        return "\n".join(lines) or "  (none)"

    outcome   = f"🏆 *{_tname(tgame, winner_key)}* WINS!" if winner_key else "🤝 *MATCH TIED!*"
    scorecard = (
        f"🏆 *MATCH OVER*\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Innings 1 — {_tname(tgame, bat1)}*\n{bat_summary(d1, bat1)}\n"
        f"Total: *{s1}/{d1['wickets']}*\n\n"
        f"*Innings 2 — {_tname(tgame, bat2)}*\n{bat_summary(d2, bat2)}\n"
        f"Total: *{s2}/{d2['wickets']}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n{result_line}\n{outcome}\n\n"
        f"{mvp_line}\n\n"
        f"Play again with /gamecricket 🏏"
    )
    await ctx.bot.send_message(chat_id, scorecard, parse_mode="Markdown")
    tgame["phase"] = "finished"
    del team_games[tgame_id]
    group_team_game.pop(chat_id, None)


# ─────────────────────────────────────────────────────────────
#  MAIN ENTRY
# ─────────────────────────────────────────────────────────────
def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("flip",        cmd_flip))
    app.add_handler(CommandHandler("profile",     cmd_profile))
    app.add_handler(CommandHandler("gamecricket", cmd_gamecricket))
    
    app.add_handler(CommandHandler("endgame",     cmd_endgame))
    app.add_handler(CommandHandler("add",         cmd_add))
    app.add_handler(CommandHandler("remove",      cmd_remove))
    app.add_handler(CommandHandler("declare",     cmd_declare))
    
    app.add_handler(CommandHandler("batting",     cmd_batting))
    app.add_handler(CommandHandler("bowling",     cmd_bowling))

    app.add_handler(CallbackQueryHandler(cb_mode_select,      pattern=r"^mode:"))
    app.add_handler(CallbackQueryHandler(cb_1v1_variant,      pattern=r"^1v1v:"))
    app.add_handler(CallbackQueryHandler(cb_duel_join,        pattern=r"^dj:"))
    app.add_handler(CallbackQueryHandler(cb_duel_toss,        pattern=r"^dt:"))
    app.add_handler(CallbackQueryHandler(cb_duel_pick,        pattern=r"^dp:"))
    
    app.add_handler(CallbackQueryHandler(cb_team_join,        pattern=r"^tj:"))
    app.add_handler(CallbackQueryHandler(cb_team_claim_cap,   pattern=r"^tclaim_cap:"))
    app.add_handler(CallbackQueryHandler(cb_set_overs,        pattern=r"^tsetovers:"))
    app.add_handler(CallbackQueryHandler(cb_overs_pick,       pattern=r"^tov:"))
    
    app.add_handler(CallbackQueryHandler(cb_menu_timeout_secs,  pattern=r"^tmenu_to_sec:"))
    app.add_handler(CallbackQueryHandler(cb_select_timeout_secs, pattern=r"^tselect_sec:"))
    app.add_handler(CallbackQueryHandler(cb_finish_timeout_config, pattern=r"^tfinish_to:"))
    
    app.add_handler(CallbackQueryHandler(cb_toggle_variant,   pattern=r"^tvariant:"))
    app.add_handler(CallbackQueryHandler(cb_team_start,       pattern=r"^tstart:"))
    
    app.add_handler(CallbackQueryHandler(cb_team_toss_call,   pattern=r"^ttoss_call:"))
    app.add_handler(CallbackQueryHandler(cb_team_toss_flip,   pattern=r"^ttoss_flip:"))
    app.add_handler(CallbackQueryHandler(cb_team_toss,        pattern=r"^ttoss:"))
    app.add_handler(CallbackQueryHandler(cb_team_pick,        pattern=r"^tp:"))

    logger.info("CricBot is active with direct captain claim actions…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()