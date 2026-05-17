"""
🏏 Cricket Game Telegram Bot
====================================
Works in both private chats and group chats.

Commands:
  /start        - Welcome message
  /gamecricket  - Start a Cricket game (multiple can run at once!)
  /profile      - View your Cricket stats
  /flip         - Flip a coin
  /help         - Show help

Setup:
  1. Get Telegram Bot Token from @BotFather
  2. pip install python-telegram-bot
  3. python bot.py
"""

import logging
import random
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


# ─────────────────────────────────────────────
#  👤  PLAYER PROFILES  (in-memory)
# ─────────────────────────────────────────────
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


# ──────────────────────────────────────────────────────────────────────────────
#  BASIC COMMAND HANDLERS
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🏏 *Welcome to CricBot!*\n\n"
        "Commands:\n"
        "• /gamecricket — Play Cricket!\n"
        "• /profile — Your Cricket stats\n"
        "• /flip — Flip a coin\n"
        "• /help — This message\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🏏 *CricBot Commands*\n\n"
        "/gamecricket — Start a Cricket game\n"
        "/profile — View your wins, losses & draws\n"
        "/flip — Flip a coin\n"
        "/start — Welcome message\n"
        "/help — This message\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_flip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    result = random.choice(["🪙 Heads!", "🪙 Tails!"])
    await update.message.reply_text(result)


# ──────────────────────────────────────────────────────────────────────────────
#  👤  PROFILE COMMAND
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    p    = get_profile(user.id, user.first_name)
    games, wins, losses, draws = p["games"], p["wins"], p["losses"], p.get("draws", 0)

    if games == 0:
        record_line = "No games played yet. Start one with /gamecricket!"
        extra       = ""
    else:
        wr          = round((wins / games) * 100, 1)
        record_line = (
            f"🏆 Wins: *{wins}*  |  💀 Losses: *{losses}*  |  "
            f"🤝 Draws: *{draws}*  |  🎮 Games: *{games}*"
        )
        badge = "🥇 Legend" if wr >= 70 else "🥈 Pro" if wr >= 50 else "🥉 Amateur" if wr >= 30 else "📉 Rookie"
        extra = f"📈 Win Rate: *{wr}%*  |  {badge}"

    await update.message.reply_text(
        f"👤 *{p['name']}'s Cricket Profile*\n"
        f"─────────────────────────\n"
        f"{record_line}\n"
        f"{extra}",
        parse_mode="Markdown",
    )


# ──────────────────────────────────────────────────────────────────────────────
#  🏏 CRICKET GAME
# ──────────────────────────────────────────────────────────────────────────────

hand_cricket_games: dict[int, dict] = {}


def number_keyboard(game_id: int) -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(str(n), callback_data=f"hc_pick:{game_id}:{n}") for n in range(1, 4)]
    row2 = [InlineKeyboardButton(str(n), callback_data=f"hc_pick:{game_id}:{n}") for n in range(4, 7)]
    return InlineKeyboardMarkup([row1, row2])


def build_game_text(game: dict) -> str:
    batter       = game["batter"]["name"] if game.get("batter") else "?"
    bowler       = game["bowler"]["name"] if game.get("bowler") else "?"
    score        = game["score"]
    ball         = game["ball"]
    over         = ball // 6
    ball_in_over = ball % 6
    target       = game.get("target")
    batter_runs  = game.get("batter_runs", 0)
    batter_balls = game.get("batter_balls", 0)
    history      = game.get("history", [])
    recent       = history[-6:] if history else []
    dots_str     = "  ".join(recent) if recent else "-"
    pick_phase   = game.get("pick_phase", "batter")
    last_bowl    = game.get("last_bowl_num")
    bowl_line    = f"🎳 Bowler's last: *{last_bowl}*" if last_bowl is not None else ""

    lines = [
        "🏏 *CRICKET*",
        "",
        f"{'Innings 1' if game['innings'] == 1 else 'Innings 2'}  |  *{batter}* 🏏 vs 🎳 *{bowler}*",
        "",
        f"📊 Score: *{score}*  |  Over: *{over}.{ball_in_over}*",
        f"🏏 *{batter}*: *{batter_runs}* runs off *{batter_balls}* balls",
    ]
    if target:
        need = target - score
        lines.append(f"🎯 Target: {target}  |  Need: *{need}*")

    lines += ["", f"🕐 This over: {dots_str}", bowl_line, ""]

    if pick_phase == "batter":
        lines += [
            f"⏳ Waiting for *{batter}* (batter) to pick...",
            f"🔒 *{bowler}* (bowler) picks after batter",
            "",
            f"👇 *{batter}*, pick your number:",
        ]
    else:
        lines += [
            f"✅ *{batter}* (batter) has picked!",
            f"⏳ Now waiting for *{bowler}* (bowler)...",
            "",
            f"👇 *{bowler}*, pick your number:",
        ]

    return "\n".join(lines)


def build_over_summary(game: dict, over_num: int) -> str:
    history        = game.get("history", [])
    over_balls     = history[-6:]
    runs_this_over = sum(int(b) for b in over_balls if b.isdigit())
    batter         = game["batter"]["name"] if game.get("batter") else "?"
    bowler         = game["bowler"]["name"] if game.get("bowler") else "?"
    batter_runs    = game.get("batter_runs", 0)
    batter_balls   = game.get("batter_balls", 0)
    extra          = f"  |  Need: *{game['target'] - game['score']}* more" if game.get("target") else ""
    return (
        f"📋 *End of Over {over_num}*\n"
        f"Balls: {' | '.join(over_balls)}\n"
        f"Runs this over: *{runs_this_over}*\n"
        f"\n"
        f"🏏 *{batter}* — *{batter_runs}* runs off *{batter_balls}* balls\n"
        f"🎳 *{bowler}* bowling\n"
        f"📊 Total: *{game['score']}*{extra}"
    )


def build_wicket_msg(game: dict, is_innings_end: bool = False) -> str:
    batter       = game["batter"]["name"] if game.get("batter") else "?"
    bowler       = game["bowler"]["name"] if game.get("bowler") else "?"
    batter_runs  = game.get("batter_runs", 0)
    batter_balls = game.get("batter_balls", 0)
    ball         = game["ball"]
    over         = ball // 6
    ball_in_over = ball % 6
    sr           = round((batter_runs / batter_balls) * 100, 1) if batter_balls else 0.0

    lines = [
        "💥 *WICKET!*",
        "",
        f"🏏 *{batter}* is *OUT* for *{batter_runs}* runs off *{batter_balls}* balls",
        f"   Strike Rate: *{sr}*",
        f"🎳 Bowled by *{bowler}*",
        f"⚡ Ball *{over}.{ball_in_over}*  |  Score at fall: *{game['score']}*",
    ]
    if is_innings_end:
        lines.append(f"\n🔄 *End of Innings {game['innings']}*")
    return "\n".join(lines)


def build_final_scorecard(game: dict, result_line: str, winner_name: str = None) -> str:
    sc1 = game.get("innings1_scorecard", {})
    sc2 = game.get("innings2_scorecard", {})

    def batting_row(name: str, runs: int, balls: int, out: bool) -> str:
        status = "out" if out else "not out ✳️"
        sr     = round((runs / balls) * 100, 1) if balls else 0.0
        return f"  *{name}*: *{runs}* ({balls}b) [{status}]  SR {sr}"

    def bowling_row(name: str, wickets: int, runs_given: int) -> str:
        return f"  *{name}*: {wickets}wkt / {runs_given}r"

    outcome_line = f"🏆 *{winner_name}* WINS!" if winner_name else "🤝 *MATCH DRAWN!*"

    return "\n".join([
        "🏏 *MATCH SCORECARD*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "*Innings 1*",
        batting_row(sc1.get("batter_name", "?"), sc1.get("runs", 0), sc1.get("balls", 0), out=True),
        "",
        "*Bowling (Inns 1)*",
        bowling_row(sc1.get("bowler_name", "?"), 1, sc1.get("runs", 0)),
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "*Innings 2*",
        batting_row(
            sc2.get("batter_name", "?"),
            sc2.get("runs", 0),
            sc2.get("balls", 0),
            out=sc2.get("out", False),
        ),
        "",
        "*Bowling (Inns 2)*",
        bowling_row(sc2.get("bowler_name", "?"), sc2.get("wickets", 0), sc2.get("runs", 0)),
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        result_line,
        outcome_line,
        "",
        "Play again with /gamecricket 🏏",
    ])


# ── /gamecricket ──────────────────────────────────────────────────────────────

async def cmd_gamecricket(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    challenger = update.effective_user
    get_profile(challenger.id, challenger.first_name)

    game: dict = {
        "chat_id":            update.effective_chat.id,
        "challenger":         {"id": challenger.id, "name": challenger.first_name},
        "opponent":           None,
        "batter":             None,
        "bowler":             None,
        "score":              0,
        "target":             None,
        "innings":            1,
        "phase":              "waiting",
        "pick_phase":         "batter",
        "toss_winner":        None,
        "batter_pick":        None,
        "bowler_pick":        None,
        "ball":               0,
        "batter_runs":        0,
        "batter_balls":       0,
        "first_score":        None,
        "history":            [],
        "game_msg_id":        None,
        "innings1_scorecard": {},
        "innings2_scorecard": {},
    }

    msg = await update.message.reply_text(
        f"🏏 *{challenger.first_name}* wants to play Cricket!\n\n"
        "Anyone — tap *Join Game* to play! 👇",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🏏 Join Game!", callback_data="hc_join:0")
        ]]),
        parse_mode="Markdown",
    )

    game_id             = msg.message_id
    game["game_msg_id"] = game_id
    hand_cricket_games[game_id] = game

    await msg.edit_reply_markup(reply_markup=InlineKeyboardMarkup([[
        InlineKeyboardButton("🏏 Join Game!", callback_data=f"hc_join:{game_id}")
    ]]))


# ── Join ──────────────────────────────────────────────────────────────────────

async def cb_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user

    try:
        game_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Game not found.")
        return

    game = hand_cricket_games.get(game_id)
    if not game or game.get("phase") != "waiting":
        await query.answer("Game already started or finished!")
        return
    if game["challenger"]["id"] == user.id:
        await query.answer("You started this! Wait for someone else 😄", show_alert=True)
        return

    await query.answer()
    game["opponent"] = {"id": user.id, "name": user.first_name}
    get_profile(user.id, user.first_name)
    await _start_toss(ctx, game_id, game)


async def _start_toss(ctx, game_id: int, game: dict) -> None:
    game["phase"]       = "toss"
    coin                = random.choice(["Heads", "Tails"])
    toss_winner         = random.choice(["challenger", "opponent"])
    game["toss_winner"] = toss_winner
    winner_name = (
        game["challenger"]["name"] if toss_winner == "challenger"
        else game["opponent"]["name"]
    )
    toss_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏏 Bat",  callback_data=f"hc_toss:{game_id}:bat"),
        InlineKeyboardButton("🎳 Bowl", callback_data=f"hc_toss:{game_id}:bowl"),
    ]])
    await ctx.bot.edit_message_text(
        chat_id=game["chat_id"],
        message_id=game_id,
        text=(
            f"🏏 *{game['challenger']['name']}* vs *{game['opponent']['name']}*\n\n"
            f"🪙 Toss — *{coin}*\n"
            f"🏆 *{winner_name}* wins the toss!\n\n"
            "Choose Bat or Bowl 👇"
        ),
        reply_markup=toss_kb,
        parse_mode="Markdown",
    )


# ── Toss choice ───────────────────────────────────────────────────────────────

async def cb_toss(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    parts = query.data.split(":")

    try:
        game_id = int(parts[1])
        choice  = parts[2]
    except (IndexError, ValueError):
        await query.answer("Invalid toss data.")
        return

    game = hand_cricket_games.get(game_id)
    if not game or game["phase"] != "toss":
        await query.answer()
        return

    toss_winner = game["toss_winner"]
    winner_id   = (
        game["challenger"]["id"] if toss_winner == "challenger"
        else game["opponent"]["id"]
    )
    if user.id != winner_id:
        await query.answer("You didn't win the toss!", show_alert=True)
        return

    await query.answer()
    c, o = game["challenger"], game["opponent"]
    if toss_winner == "challenger":
        batter, bowler = (c, o) if choice == "bat" else (o, c)
    else:
        batter, bowler = (o, c) if choice == "bat" else (c, o)

    game.update(
        batter=batter, bowler=bowler, phase="playing",
        pick_phase="batter",
        score=0, ball=0, batter_runs=0, batter_balls=0,
        batter_pick=None, bowler_pick=None, history=[],
    )
    await ctx.bot.edit_message_text(
        chat_id=game["chat_id"],
        message_id=game_id,
        text=build_game_text(game),
        reply_markup=number_keyboard(game_id),
        parse_mode="Markdown",
    )


# ── Number pick ───────────────────────────────────────────────────────────────

async def cb_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user  = update.effective_user
    parts = query.data.split(":")

    try:
        game_id = int(parts[1])
        num     = int(parts[2])
    except (IndexError, ValueError):
        await query.answer("Invalid pick data.")
        return

    game = hand_cricket_games.get(game_id)
    if not game or game["phase"] != "playing":
        await query.answer()
        return

    is_batter = user.id == game["batter"]["id"]
    is_bowler = user.id == game["bowler"]["id"]

    if not is_batter and not is_bowler:
        await query.answer("You're not in this game!", show_alert=True)
        return

    pick_phase = game.get("pick_phase", "batter")

    # ── Batter's turn ─────────────────────────────────────────────────────────
    if pick_phase == "batter":
        if not is_batter:
            await query.answer("Batter picks first! Wait for your turn 🎳", show_alert=True)
            return
        if game["batter_pick"] is not None:
            await query.answer("You already picked! ✋")
            return
        game["batter_pick"] = num
        game["pick_phase"]  = "bowler"
        await query.answer(f"You picked {num} 🤫 — bowler's turn now!")
        try:
            await ctx.bot.edit_message_text(
                chat_id=game["chat_id"],
                message_id=game_id,
                text=build_game_text(game),
                reply_markup=number_keyboard(game_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return

    # ── Bowler's turn ─────────────────────────────────────────────────────────
    if pick_phase == "bowler":
        if not is_bowler:
            await query.answer("It's the bowler's turn now! Wait 🏏", show_alert=True)
            return
        if game["bowler_pick"] is not None:
            await query.answer("You already picked! ✋")
            return
        game["bowler_pick"] = num
        await query.answer(f"You picked {num} 🤫")

    await _resolve_ball(ctx, game_id)


# ── Resolve ball ──────────────────────────────────────────────────────────────

async def _resolve_ball(ctx, game_id: int) -> None:
    game     = hand_cricket_games.get(game_id)
    bat_num  = game["batter_pick"]
    bowl_num = game["bowler_pick"]

    game["last_bowl_num"] = bowl_num
    game["batter_pick"]   = None
    game["bowler_pick"]   = None
    game["pick_phase"]    = "batter"

    game["ball"]         += 1
    game["batter_balls"] += 1
    ball    = game["ball"]
    chat_id = game["chat_id"]

    if bat_num == bowl_num:
        # ── WICKET ────────────────────────────────────────────────────────────

        if game["innings"] == 1:
            game["innings1_scorecard"] = {
                "batter_name": game["batter"]["name"],
                "bowler_name": game["bowler"]["name"],
                "runs":        game["score"],
                "balls":       game["batter_balls"],
            }
            game["first_score"] = game["score"]
            target = game["score"] + 1

            await ctx.bot.send_message(
                chat_id,
                build_wicket_msg(game, is_innings_end=True),
                parse_mode="Markdown",
            )

            old_batter = game["batter"]
            old_bowler = game["bowler"]
            game.update(
                innings=2, target=target,
                batter=old_bowler, bowler=old_batter,
                score=0, ball=0,
                batter_runs=0, batter_balls=0,
                history=[],
                batter_pick=None, bowler_pick=None,
                pick_phase="batter",
                last_bowl_num=None,
            )
            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=game_id,
                text=(
                    f"🔄 *Innings 2 begins!*\n"
                    f"🎯 Target: *{target}*\n\n"
                    + build_game_text(game)
                ),
                reply_markup=number_keyboard(game_id),
                parse_mode="Markdown",
            )

        else:
            # ── GAME OVER ─────────────────────────────────────────────────────
            score2 = game["score"]
            target = game["target"]
            score1 = game["first_score"]

            game["innings2_scorecard"] = {
                "batter_name": game["batter"]["name"],
                "bowler_name": game["bowler"]["name"],
                "runs":        score2,
                "balls":       game["batter_balls"],
                "wickets":     1,
                "out":         True,
            }

            await ctx.bot.send_message(
                chat_id,
                build_wicket_msg(game, is_innings_end=True),
                parse_mode="Markdown",
            )

            if score2 == score1:
                result_line = f"📊 Both innings ended at *{score1}* runs — incredible game!"
                record_result(game["batter"]["id"], game["batter"]["name"], won=False, draw=True)
                record_result(game["bowler"]["id"], game["bowler"]["name"], won=False, draw=True)
                await ctx.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=game_id,
                    text=build_final_scorecard(game, result_line, winner_name=None),
                    parse_mode="Markdown",
                )
            else:
                if score2 >= target:
                    winner      = game["batter"]
                    loser       = game["bowler"]
                    result_line = f"🎯 Target of *{target}* chased!"
                else:
                    margin      = target - 1 - score2
                    winner      = game["bowler"]
                    loser       = game["batter"]
                    result_line = f"🛡️ Defended by *{margin}* run{'s' if margin != 1 else ''}!"

                record_result(winner["id"], winner["name"], won=True)
                record_result(loser["id"],  loser["name"],  won=False)
                await ctx.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=game_id,
                    text=build_final_scorecard(game, result_line, winner_name=winner["name"]),
                    parse_mode="Markdown",
                )

            del hand_cricket_games[game_id]

    else:
        # ── RUNS ──────────────────────────────────────────────────────────────
        game["score"]       += bat_num
        game["batter_runs"] += bat_num
        game["history"].append(str(bat_num))

        if game["innings"] == 2 and game["score"] >= game["target"]:
            game["innings2_scorecard"] = {
                "batter_name": game["batter"]["name"],
                "bowler_name": game["bowler"]["name"],
                "runs":        game["score"],
                "balls":       game["batter_balls"],
                "wickets":     0,
                "out":         False,
            }
            winner      = game["batter"]
            loser       = game["bowler"]
            result_line = f"🎯 Target of *{game['target']}* chased!"

            record_result(winner["id"], winner["name"], won=True)
            record_result(loser["id"],  loser["name"],  won=False)

            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=game_id,
                text=build_final_scorecard(game, result_line, winner_name=winner["name"]),
                parse_mode="Markdown",
            )
            del hand_cricket_games[game_id]
            return

        if ball % 6 == 0:
            await ctx.bot.send_message(
                chat_id,
                build_over_summary(game, ball // 6),
                parse_mode="Markdown",
            )

        await ctx.bot.edit_message_text(
            chat_id=chat_id,
            message_id=game_id,
            text=build_game_text(game),
            reply_markup=number_keyboard(game_id),
            parse_mode="Markdown",
        )


# ──────────────────────────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("flip",        cmd_flip))
    app.add_handler(CommandHandler("gamecricket", cmd_gamecricket))
    app.add_handler(CommandHandler("profile",     cmd_profile))

    app.add_handler(CallbackQueryHandler(cb_join, pattern=r"^hc_join:"))
    app.add_handler(CallbackQueryHandler(cb_toss, pattern=r"^hc_toss:"))
    app.add_handler(CallbackQueryHandler(cb_pick, pattern=r"^hc_pick:"))

    logger.info("CricBot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()