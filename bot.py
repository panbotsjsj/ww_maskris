"""
Werewolf Discord Bot  (v2 — Tim Werewolf, Warga, dan Netral)
-----------------------------------------------------------
- Lobby dengan tombol Join / Leave / Start
- Host bisa pilih ikut main atau spectate (tombol 👁️ Spectate)
- Role bisa ditambah / dihapus (/ww role add|remove|auto|list) untuk balancing
- Host bisa atur Werewolf boleh membunuh mulai malam ke-berapa (/ww setting malam_bunuh:N)
- Role TIDAK diumumkan saat pemain mati (tetap rahasia sampai game selesai)
- Channel private khusus Werewolf dengan select menu korban
- Semua role dengan aksi malam memakai UI interaktif yang hanya terlihat oleh pemainnya (ephemeral)
- Malam -> Siang (diskusi + pemerasan/bungkam) -> Voting, otomatis sampai ada pemenang
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger("werewolf")
logging.basicConfig(level=logging.INFO)

MIN_PLAYERS = int(os.getenv("MIN_PLAYERS", "4"))
MAX_PLAYERS = 24  # select menu Discord maks 25 opsi (24 pemain + opsi "skip")
LOCK_CHAT_AT_NIGHT = os.getenv("LOCK_CHAT_AT_NIGHT", "1") == "1"
WOLF_KILL_NIGHT = max(1, int(os.getenv("WOLF_KILL_NIGHT", "1")))  # Werewolf boleh membunuh mulai malam ke-N
HUNTER_TIME = 30
WITCH_TIME = 30


# ════════════════════════════════════════════════════════════════════
#  DEFINISI ROLE
# ════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class RoleInfo:
    key: str
    emoji: str
    name: str
    team: str  # "village" / "wolf" / "neutral"
    desc: str


TEAMS = {
    "village": ("🏘️", "Tim Warga"),
    "wolf": ("🐺", "Tim Werewolf"),
    "neutral": ("⚖️", "Netral"),
}

ROLES: dict[str, RoleInfo] = {r.key: r for r in [
    # ───── Tim Werewolf ─────
    RoleInfo("werewolf", "🐺", "Werewolf", "wolf",
             "Berkelompok dengan sesama Werewolf di channel private. Tiap malam memilih **1 korban** untuk dimakan."),
    RoleInfo("werewolf_kid", "🐺", "Werewolf Kid", "wolf",
             "Sama seperti Werewolf biasa. Jika dia **dibakar (di-lynch lewat voting)**, malam berikutnya "
             "Werewolf bisa memakan **2 orang**."),
    RoleInfo("sorcerer", "🧿", "Sorcerer", "wolf",
             "Kamu **tahu siapa saja Werewolf** (mereka tidak tahu kamu). Tiap malam pilih 1 pemain untuk dicek "
             "apakah dia **Seer**. Tidak ikut memilih korban."),
    RoleInfo("blackmailer", "😈", "Blackmailer", "wolf",
             "Tiap malam pilih 1 target **dan role yang harus dia akui**. Target wajib mengaku di depan umum "
             "(lewat tombol) sebelum fase siang berakhir, kalau tidak dia **dibunuh**."),
    RoleInfo("dpr", "🤐", "DPR", "wolf",
             "Tiap malam **membungkam 1 orang** (tidak bisa chat sepanjang siang berikutnya). "
             "Malam selanjutnya tidak boleh membungkam orang yang sama."),
    # ───── Tim Warga ─────
    RoleInfo("villager", "👤", "Villager", "village",
             "Warga biasa tanpa kekuatan khusus. Andalkan diskusi dan voting."),
    RoleInfo("seer", "🔮", "Seer", "village",
             "Tiap malam cek **role asli** satu pemain. Hasilnya hanya kamu yang lihat."),
    RoleInfo("doctor", "💉", "Doctor", "village",
             "Tiap malam lindungi 1 pemain (boleh diri sendiri) dari serangan malam (Werewolf & Serial Killer). "
             "Tidak boleh orang yang sama 2 malam berturut-turut."),
    RoleInfo("witch", "🧙", "Witch", "village",
             "1 ramuan penyelamat + 1 ramuan racun (sekali pakai). Kamu diberi tahu korban Werewolf malam itu. "
             "Satu aksi per malam."),
    RoleInfo("hunter", "🏹", "Hunter", "village",
             "Saat mati (dibunuh/di-lynch/diracun) kamu boleh menembak 1 pemain untuk ikut mati."),
    # ───── Netral ─────
    RoleInfo("tanner", "🤡", "Tanner", "neutral",
             "Menang **sendirian** jika kamu mati karena **voting (di-lynch)**. Game langsung selesai."),
    RoleInfo("blamer", "👉", "Blamer", "neutral",
             "Di **malam pertama** pilih **2 orang** sebagai target. Jika keduanya mati dan kamu masih hidup, kamu menang."),
    RoleInfo("serial_killer", "🔪", "Serial Killer", "neutral",
             "Tiap malam membunuh **1 orang**. Menang jika kamu adalah **orang terakhir yang bertahan**."),
]}

PACK = {"werewolf", "werewolf_kid"}         # anggota channel Werewolf & pemilih korban
KNOWS_WOLVES = {"sorcerer"}                 # role yang tahu siapa Werewolf (tambah "blackmailer", "dpr" jika mau)
NIGHT_REQUIRED = {"seer", "doctor", "sorcerer", "blackmailer", "dpr", "serial_killer"}


def auto_roles(n: int) -> dict[str, int]:
    """Komposisi otomatis (villager mengisi sisa)."""
    total_wolf = max(1, (n + 2) // 4)
    extras: list[str] = []
    for min_n, key in [(9, "sorcerer"), (10, "werewolf_kid"), (12, "blackmailer"), (14, "dpr")]:
        if n >= min_n and len(extras) < total_wolf - 1:
            extras.append(key)
    cfg: dict[str, int] = {"werewolf": total_wolf - len(extras)}
    for k in extras:
        cfg[k] = 1
    for min_n, key in [(5, "seer"), (6, "doctor"), (7, "hunter"), (8, "witch"),
                       (10, "tanner"), (12, "serial_killer"), (14, "blamer")]:
        if n >= min_n:
            cfg[key] = 1
    return cfg


def fmt_time(seconds: int) -> str:
    return discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=seconds), "R")


class Player:
    def __init__(self, member: discord.Member):
        self.member = member
        self.id = member.id
        self.role = "villager"
        self.alive = True
        self.heal = 1                  # Witch
        self.poison = 1
        self.last_protected: Optional[int] = None   # Doctor
        self.last_silenced: Optional[int] = None    # DPR
        self.blame_targets: list[int] = []          # Blamer

    @property
    def name(self) -> str:
        return self.member.display_name

    @property
    def info(self) -> RoleInfo:
        return ROLES[self.role]

    @property
    def team(self) -> str:
        return ROLES[self.role].team


# ════════════════════════════════════════════════════════════════════
#  UI COMPONENTS
# ════════════════════════════════════════════════════════════════════
class TargetSelect(discord.ui.Select):
    """Select menu generik berisi daftar pemain. Logika ada di Game.handle_select."""

    def __init__(self, game: "Game", kind: str, candidates: list[Player], placeholder: str,
                 allow_skip: bool = False, min_values: int = 1, max_values: int = 1):
        options = [discord.SelectOption(label=p.name[:100], value=str(p.id)) for p in candidates][:25]
        if allow_skip:
            options = options[:24] + [discord.SelectOption(label="⏭️ Skip / tidak memilih", value="skip")]
        n = len(options)
        super().__init__(placeholder=placeholder, min_values=min(min_values, n),
                         max_values=min(max_values, n), options=options)
        self.game = game
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        await self.game.handle_select(interaction, self.kind, list(self.values))


class RoleClaimSelect(discord.ui.Select):
    """Dipakai Blackmailer untuk memilih role yang harus diakui target."""

    def __init__(self, game: "Game"):
        opts = [discord.SelectOption(label=r.name, value=r.key, emoji=r.emoji) for r in ROLES.values()]
        super().__init__(placeholder="Role yang harus diakui target", options=opts)
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        await self.game.handle_bm_role(interaction, self.values[0])


class WolfVoteView(discord.ui.View):
    def __init__(self, game: "Game", targets: list[Player], kills: int):
        super().__init__(timeout=None)
        ph = "🐺 Pilih korban malam ini" if kills == 1 else f"🐺 Pilih sampai {kills} korban malam ini"
        self.add_item(TargetSelect(game, "wolf", targets, ph, max_values=kills))


class VoteView(discord.ui.View):
    def __init__(self, game: "Game"):
        super().__init__(timeout=None)
        self.add_item(TargetSelect(game, "vote", game.alive(), "🗳️ Pilih pemain yang mau di-lynch", allow_skip=True))


class NightPanelView(discord.ui.View):
    def __init__(self, game: "Game"):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Aksi Malam", emoji="🌙", style=discord.ButtonStyle.primary)
    async def act(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.game.open_night_action(interaction)


class DayPanelView(discord.ui.View):
    """Tombol publik siang hari: semua pemain bisa klik sehingga target Blackmailer tidak ketahuan."""

    def __init__(self, game: "Game"):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Cek Pesan Rahasia", emoji="📩", style=discord.ButtonStyle.secondary)
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.game.open_day_message(interaction)


class ConfessView(discord.ui.View):
    def __init__(self, game: "Game", entry: dict):
        super().__init__(timeout=None)
        self.game = game
        self.entry = entry

    @discord.ui.button(label="Aku Mengaku (umumkan di channel)", emoji="🗣️", style=discord.ButtonStyle.danger)
    async def confess(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = self.game
        if g.stage not in ("discuss", "vote"):
            return await interaction.response.edit_message(content="⏰ Fase siang sudah berakhir.", view=None)
        if self.entry["complied"]:
            return await interaction.response.edit_message(content="Kamu sudah mengaku.", view=None)
        self.entry["complied"] = True
        role = ROLES[self.entry["role"]]
        await interaction.response.edit_message(content="✅ Pengakuanmu diumumkan. Kamu aman.", view=None)
        await g.say(f"🗣️ {interaction.user.mention} mengaku: *\"Aku adalah {role.emoji} **{role.name}**\"*")


class RoleView(discord.ui.View):
    def __init__(self, game: "Game"):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Lihat Role Saya", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def show(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = self.game
        p = g.players.get(interaction.user.id)
        if not p:
            return await interaction.response.send_message("Kamu tidak ikut game ini.", ephemeral=True)
        te, tn = TEAMS[p.team]
        color = {"wolf": discord.Color.red(), "village": discord.Color.green(), "neutral": discord.Color.gold()}[p.team]
        e = discord.Embed(title=f"{p.info.emoji} Kamu adalah {p.info.name}", description=p.info.desc, color=color)
        e.add_field(name="Tim", value=f"{te} {tn}")
        e.add_field(name="Status", value="❤️ Hidup" if p.alive else "💀 Mati")
        if p.role in PACK:
            mates = ", ".join(w.member.mention for w in g.players.values() if w.role in PACK)
            e.add_field(name="Anggota pack", value=mates, inline=False)
        elif p.role in KNOWS_WOLVES:
            wolves = ", ".join(w.member.mention for w in g.players.values() if w.role in PACK)
            e.add_field(name="🐺 Werewolf yang kamu ketahui", value=wolves or "-", inline=False)
        if p.role == "blamer" and p.blame_targets:
            e.add_field(name="🎯 Targetmu", value=", ".join(g.players[t].member.mention for t in p.blame_targets), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)


class WitchView(discord.ui.View):
    def __init__(self, game: "Game", witch: Player):
        super().__init__(timeout=120)
        self.game = game
        self.witch = witch
        self.save_btn.disabled = not (game.night_victims and witch.heal > 0)
        self.poison_btn.disabled = witch.poison <= 0

    async def _expired(self, interaction: discord.Interaction) -> bool:
        if self.game.stage != "witch":
            await interaction.response.edit_message(content="⏰ Waktu aksi sudah habis.", view=None)
            return True
        return False

    @discord.ui.button(label="Selamatkan korban", emoji="💚", style=discord.ButtonStyle.success)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._expired(interaction):
            return
        g = self.game
        if len(g.night_victims) == 1:
            self.witch.heal -= 1
            g.witch_saved.append(g.night_victims[0])
            g.acted.add(self.witch.id)
            await interaction.response.edit_message(content="💚 Kamu menggunakan ramuan penyelamat.", view=None)
            g.check_done()
        else:
            v = discord.ui.View(timeout=120)
            v.add_item(TargetSelect(g, "witch_save", [g.players[x] for x in g.night_victims], "💚 Pilih korban yang diselamatkan"))
            await interaction.response.edit_message(content="💚 Siapa yang ingin kamu selamatkan?", view=v)

    @discord.ui.button(label="Racuni seseorang", emoji="☠️", style=discord.ButtonStyle.danger)
    async def poison_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._expired(interaction):
            return
        cands = [p for p in self.game.alive() if p.id != self.witch.id]
        v = discord.ui.View(timeout=120)
        v.add_item(TargetSelect(self.game, "poison", cands, "☠️ Pilih target racun"))
        await interaction.response.edit_message(content="☠️ Siapa yang mau kamu racuni?", view=v)

    @discord.ui.button(label="Lewati", emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._expired(interaction):
            return
        self.game.acted.add(self.witch.id)
        await interaction.response.edit_message(content="⏭️ Kamu tidak memakai ramuan malam ini.", view=None)
        self.game.check_done()


class LobbyView(discord.ui.View):
    def __init__(self, game: "Game"):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Join", emoji="✅", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = self.game
        if g.state != "lobby":
            return await interaction.response.send_message("Game sudah berjalan.", ephemeral=True)
        if interaction.user.id in g.players:
            return await interaction.response.send_message("Kamu sudah terdaftar.", ephemeral=True)
        if len(g.players) >= MAX_PLAYERS:
            return await interaction.response.send_message("Lobby penuh.", ephemeral=True)
        g.players[interaction.user.id] = Player(interaction.user)  # type: ignore[arg-type]
        await interaction.response.edit_message(embed=g.lobby_embed(), view=self)

    @discord.ui.button(label="Leave", emoji="🚪", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = self.game
        if g.state != "lobby" or interaction.user.id not in g.players:
            return await interaction.response.send_message("Kamu tidak ada di lobby.", ephemeral=True)
        if interaction.user.id == g.host.id:
            return await interaction.response.send_message(
                "Host tidak bisa keluar. Pakai tombol 👁️ **Spectate** atau `/ww cancel`.", ephemeral=True)
        del g.players[interaction.user.id]
        await interaction.response.edit_message(embed=g.lobby_embed(), view=self)

    @discord.ui.button(label="Spectate", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def spectate(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Host bisa pilih ikut main atau hanya spectate."""
        g = self.game
        if g.state != "lobby":
            return await interaction.response.send_message("Game sudah berjalan.", ephemeral=True)
        if interaction.user.id != g.host.id:
            return await interaction.response.send_message("Hanya host yang bisa mengubah ini.", ephemeral=True)
        if g.host_playing:
            g.host_playing = False
            g.players.pop(g.host.id, None)
            button.label = "Ikut Main"
            button.emoji = "🎮"
        else:
            if len(g.players) >= MAX_PLAYERS:
                return await interaction.response.send_message("Lobby penuh.", ephemeral=True)
            g.host_playing = True
            g.players[g.host.id] = Player(g.host)
            button.label = "Spectate"
            button.emoji = "👁️"
        await interaction.response.edit_message(embed=g.lobby_embed(), view=self)

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.host.id:
            return await interaction.response.send_message("Hanya host yang bisa memulai.", ephemeral=True)
        await self.game.begin(interaction)


# ════════════════════════════════════════════════════════════════════
#  GAME
# ════════════════════════════════════════════════════════════════════
class Game:
    def __init__(self, bot: "WerewolfBot", channel: discord.TextChannel, host: discord.Member):
        self.bot = bot
        self.channel = channel
        self.guild = channel.guild
        self.host = host
        self.players: dict[int, Player] = {host.id: Player(host)}
        self.state = "lobby"            # lobby / running / ended
        self.custom_roles: Optional[dict[str, int]] = None
        self.night_time, self.day_time, self.vote_time = 60, 90, 45
        self.host_playing = True        # host ikut main / spectate
        self.wolf_kill_night = WOLF_KILL_NIGHT  # Werewolf boleh membunuh mulai malam ke-N
        self.wolf_active_night = False  # di-set tiap malam di night_phase
        self.lobby_message: Optional[discord.Message] = None
        self.wolf_channel: Optional[discord.TextChannel] = None
        self.task: Optional[asyncio.Task] = None
        self.round = 0
        self._orig_send: Optional[bool] = None
        self._locked = False

        self.stage: Optional[str] = None  # night / witch / discuss / vote / hunter
        self.event = asyncio.Event()

        # state malam
        self.kills = 1
        self.kid_revenge = False
        self.wolf_votes: dict[int, list[int]] = {}
        self.acted: set[int] = set()
        self.doctor_targets: list[int] = []
        self.night_victims: list[int] = []
        self.sk_targets: list[int] = []
        self.silence_targets: list[int] = []
        self.silenced: list[int] = []
        self.witch_saved: list[int] = []
        self.witch_poisons: list[int] = []
        self.bm_pending: dict[int, int] = {}
        self.blackmails: list[dict] = []
        # voting & hunter
        self.votes: dict[int, Optional[int]] = {}
        self.hunter_id: Optional[int] = None
        self.hunter_choice: Optional[int] = None

    # ─────────────── helper ───────────────
    def alive(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    def alive_role(self, role: str) -> list[Player]:
        return [p for p in self.alive() if p.role == role]

    def alive_pack(self) -> list[Player]:
        return [p for p in self.alive() if p.role in PACK]

    async def say(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)

    def role_plan(self) -> dict[str, int]:
        n = len(self.players)
        base = dict(self.custom_roles) if self.custom_roles is not None else auto_roles(n)
        plan = {k: v for k, v in base.items() if v > 0}
        plan["villager"] = max(0, n - sum(plan.values()))
        return plan

    def plan_text(self, plan: Optional[dict[str, int]] = None) -> str:
        plan = plan or self.role_plan()
        return "\n".join(f"{ROLES[k].emoji} **{ROLES[k].name}** × {plan[k]}" for k in ROLES if plan.get(k))

    def validate(self, plan: dict[str, int]) -> Optional[str]:
        n = len(self.players)
        if n < MIN_PLAYERS:
            return f"Minimal {MIN_PLAYERS} pemain untuk mulai (sekarang {n})."
        special = sum(v for k, v in plan.items() if k != "villager")
        if special > n:
            return f"Total role ({special}) lebih banyak dari jumlah pemain ({n}). Kurangi dengan `/ww role remove`."
        if plan.get("werewolf", 0) + plan.get("werewolf_kid", 0) < 1:
            return "Minimal harus ada 1 Werewolf atau Werewolf Kid (yang bisa memilih korban)."
        wolf_team = sum(v for k, v in plan.items() if ROLES[k].team == "wolf")
        if wolf_team * 2 >= n:
            return "Tim Werewolf terlalu besar (harus kurang dari separuh pemain). Kurangi role Werewolf."
        return None

    def lobby_embed(self) -> discord.Embed:
        e = discord.Embed(title="🐺 Werewolf — Lobby", color=discord.Color.dark_red())
        host_mode = "🎮 ikut main" if self.host_playing else "👁️ spectate"
        e.description = (f"Host: {self.host.mention} ({host_mode})\n"
                         "Klik **Join** untuk ikut. Host menekan **Start** jika sudah siap.\n"
                         "Host bisa pakai tombol **👁️ Spectate** untuk tidak ikut main.")
        plist = "\n".join(f"{i}. {p.member.mention}" for i, p in enumerate(self.players.values(), 1))
        e.add_field(name=f"Pemain ({len(self.players)}/{MAX_PLAYERS})", value=plist or "-", inline=False)
        mode = "otomatis" if self.custom_roles is None else "kustom"
        e.add_field(name=f"Komposisi role ({mode})", value=self.plan_text() or "-", inline=False)
        err = self.validate(self.role_plan()) if len(self.players) >= MIN_PLAYERS else None
        if err:
            e.add_field(name="⚠️ Perhatian", value=err, inline=False)
        e.set_footer(text=f"Malam {self.night_time}s • Diskusi {self.day_time}s • Voting {self.vote_time}s • "
                          f"🐺 bunuh mulai malam ke-{self.wolf_kill_night}  |  /ww role add|remove|auto")
        return e

    async def refresh_lobby(self):
        if self.lobby_message and self.state == "lobby":
            try:
                await self.lobby_message.edit(embed=self.lobby_embed())
            except discord.HTTPException:
                pass

    # ─────────────── permission helpers ───────────────
    async def set_lock(self, locked: bool):
        if not LOCK_CHAT_AT_NIGHT or locked == self._locked:
            return
        try:
            ow = self.channel.overwrites_for(self.guild.default_role)
            if locked:
                self._orig_send = ow.send_messages
                ow.send_messages = False
            else:
                ow.send_messages = self._orig_send
            await self.channel.set_permissions(self.guild.default_role, overwrite=ow)
            self._locked = locked
        except discord.HTTPException:
            log.warning("Tidak bisa mengubah permission channel utama")

    async def mute(self, p: Player):
        try:
            await self.channel.set_permissions(p.member, send_messages=False, add_reactions=False)
        except discord.HTTPException:
            pass
        if p.role in PACK and self.wolf_channel:
            try:
                await self.wolf_channel.set_permissions(p.member, view_channel=True, send_messages=False)
            except discord.HTTPException:
                pass

    async def create_wolf_channel(self):
        ow: dict = {
            self.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            self.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        for p in self.players.values():
            if p.role in PACK:
                ow[p.member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        self.wolf_channel = await self.guild.create_text_channel(
            "werewolf-pack", category=self.channel.category, overwrites=ow,
            topic="Channel rahasia Werewolf. Pilih korban tiap malam lewat select menu.")

    # ─────────────── mulai game ───────────────
    async def begin(self, interaction: discord.Interaction):
        if self.state != "lobby":
            return await interaction.response.send_message("Game sudah berjalan.", ephemeral=True)
        plan = self.role_plan()
        err = self.validate(plan)
        if err:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)
        await interaction.response.send_message("✅ Membagikan role...", ephemeral=True)

        roles: list[str] = []
        for k, c in plan.items():
            roles += [k] * c
        random.shuffle(roles)
        for p, r in zip(self.players.values(), roles):
            p.role = r

        try:
            await self.create_wolf_channel()
        except discord.HTTPException as exc:
            for p in self.players.values():
                p.role = "villager"
            return await interaction.followup.send(
                f"❌ Gagal membuat channel Werewolf: {exc}\nPastikan bot punya izin **Manage Channels** & **Manage Roles**.",
                ephemeral=True)

        self.state = "running"
        if self.lobby_message:
            try:
                await self.lobby_message.edit(view=None)
            except discord.HTTPException:
                pass

        e = discord.Embed(title="🌕 Game Werewolf Dimulai!", color=discord.Color.dark_purple(),
                          description="Role sudah dibagikan. Klik tombol di bawah untuk melihat role kamu (hanya kamu yang bisa lihat).")
        e.add_field(name="Komposisi", value=self.plan_text(plan))
        e.add_field(name="Pemain", value=", ".join(p.member.mention for p in self.players.values()), inline=False)
        await self.say(embed=e, view=RoleView(self))

        pack = [p for p in self.players.values() if p.role in PACK]
        await self.wolf_channel.send(
            "🐺 **Selamat datang di sarang Werewolf!**\n"
            f"Pack kamu: {', '.join(w.member.mention for w in pack)}\n"
            "Setiap malam, pilih korban lewat select menu. Diskusikan strategi di sini — jangan ketahuan!")
        self.task = asyncio.create_task(self.run())

    # ─────────────── loop utama ───────────────
    async def run(self):
        try:
            await asyncio.sleep(8)
            while True:
                self.round += 1
                if await self.night_phase():
                    break
                if await self.day_phase():
                    break
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error di game loop")
            try:
                await self.say("⚠️ Terjadi error internal, game dihentikan.")
            except discord.HTTPException:
                pass
        finally:
            await self.cleanup()

    async def cleanup(self):
        if self.state == "ended":
            return
        self.state = "ended"
        self.bot.games.pop(self.channel.id, None)
        self.stage = None
        await self.set_lock(False)
        for p in self.players.values():
            try:
                await self.channel.set_permissions(p.member, overwrite=None)
            except discord.HTTPException:
                pass
        if self.wolf_channel:
            try:
                await self.wolf_channel.delete(reason="Werewolf game selesai")
            except discord.HTTPException:
                pass

    async def wait(self, timeout: float):
        try:
            await asyncio.wait_for(self.event.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    def witches_needed(self) -> list[Player]:
        return [w for w in self.alive_role("witch") if w.poison > 0 or (w.heal > 0 and self.night_victims)]

    def check_done(self):
        """Set event jika semua yang wajib bertindak pada stage ini sudah selesai."""
        done = False
        if self.stage == "night":
            need = [p for p in self.alive() if p.role in NIGHT_REQUIRED]
            blamers = [p for p in self.alive_role("blamer") if not p.blame_targets] if self.round == 1 else []
            # di malam saat Werewolf belum boleh membunuh, vote korban tidak ditunggu
            wolves_ok = True if not self.wolf_active_night else all(w.id in self.wolf_votes for w in self.alive_pack())
            done = (wolves_ok
                    and all(p.id in self.acted for p in need) and not blamers)
        elif self.stage == "witch":
            done = all(w.id in self.acted for w in self.witches_needed())
        elif self.stage == "vote":
            done = all(p.id in self.votes for p in self.alive())
        elif self.stage == "hunter":
            done = self.hunter_choice is not None
        if done:
            self.event.set()

    # ─────────────── kondisi menang ───────────────
    def check_end(self) -> Optional[tuple[str, Optional[Player]]]:
        alive = self.alive()
        if not alive:
            return ("draw", None)
        for b in self.alive_role("blamer"):
            if b.blame_targets and all(not self.players[t].alive for t in b.blame_targets):
                return ("blamer", b)
        sk = self.alive_role("serial_killer")
        if sk and len(alive) == 1:
            return ("sk", sk[0])
        wolves = sum(1 for p in alive if p.team == "wolf")
        if wolves == 0 and not sk:
            return ("village", None)
        if not sk and wolves >= len(alive) - wolves:
            return ("wolf", None)
        return None

    async def announce_end(self, kind: str, player: Optional[Player]):
        if kind == "village":
            e = discord.Embed(title="🏘️ TIM WARGA MENANG!", description="Semua Werewolf telah dibasmi.", color=discord.Color.green())
        elif kind == "wolf":
            e = discord.Embed(title="🐺 TIM WEREWOLF MENANG!", description="Werewolf menguasai desa.", color=discord.Color.red())
        elif kind == "sk":
            e = discord.Embed(title="🔪 SERIAL KILLER MENANG!", description=f"{player.member.mention} adalah orang terakhir yang bertahan.", color=discord.Color.dark_gold())
        elif kind == "tanner":
            e = discord.Embed(title="🤡 TANNER MENANG!", description=f"{player.member.mention} berhasil membuat desa me-lynch dirinya.", color=discord.Color.gold())
        elif kind == "blamer":
            e = discord.Embed(title="👉 BLAMER MENANG!", description=f"{player.member.mention} sukses — kedua targetnya mati dan dia masih hidup.", color=discord.Color.gold())
        else:
            e = discord.Embed(title="☠️ Tidak ada pemenang", description="Semua pemain tewas.", color=discord.Color.dark_grey())
        lines = [f"{p.info.emoji} {p.member.mention} — **{p.info.name}** ({TEAMS[p.team][1]}) {'❤️' if p.alive else '💀'}"
                 for p in self.players.values()]
        e.add_field(name="Role semua pemain", value="\n".join(lines), inline=False)
        await self.say(embed=e)

    async def finish_if_ended(self) -> bool:
        res = self.check_end()
        if res:
            await self.announce_end(*res)
            return True
        return False

    # ─────────────── FASE MALAM ───────────────
    async def night_phase(self) -> bool:
        self.wolf_votes.clear()
        self.acted.clear()
        self.doctor_targets.clear()
        self.night_victims = []
        self.sk_targets.clear()
        self.silence_targets.clear()
        self.witch_saved.clear()
        self.witch_poisons.clear()
        self.bm_pending.clear()
        self.blackmails.clear()
        # Werewolf hanya boleh membunuh mulai malam ke-wolf_kill_night
        self.wolf_active_night = self.round >= self.wolf_kill_night
        if self.wolf_active_night:
            self.kills = 2 if self.kid_revenge else 1
            revenge = self.kid_revenge
            self.kid_revenge = False
        else:
            self.kills = 0
            revenge = False  # balas dendam Werewolf Kid disimpan sampai malam boleh membunuh

        await self.set_lock(True)
        e = discord.Embed(title=f"🌙 Malam ke-{self.round}", color=discord.Color.dark_blue(),
                          description=f"Desa tertidur... Pemain dengan aksi malam, klik **Aksi Malam**.\n"
                                      f"Warga biasa cukup menunggu. Berakhir {fmt_time(self.night_time)}")
        panel = await self.say(embed=e, view=NightPanelView(self))

        wolf_msg = None
        pack = self.alive_pack()
        if pack and self.wolf_active_night:
            targets = [p for p in self.alive() if p.role not in PACK]
            txt = f"🌙 **Malam ke-{self.round}** — pilih korban {fmt_time(self.night_time)}.\nSuara terbanyak dipakai (seri = acak)."
            if revenge:
                txt += "\n🩸 **Werewolf Kid telah dibakar!** Malam ini kalian boleh memakan **2 korban**."
            if targets:
                wolf_msg = await self.wolf_channel.send(txt, view=WolfVoteView(self, targets, self.kills))
        elif pack:
            await self.wolf_channel.send(
                f"🌙 **Malam ke-{self.round}** — Werewolf belum boleh membunuh "
                f"(baru boleh mulai malam ke-{self.wolf_kill_night}). Manfaatkan waktu untuk menyusun strategi! 😴")

        self.stage = "night"
        self.event.clear()
        self.check_done()
        await self.wait(self.night_time)

        # tentukan korban Werewolf
        pool = dict(Counter(t for ts in self.wolf_votes.values() for t in ts))
        victims: list[int] = []
        while pool and len(victims) < self.kills:
            top = max(pool.values())
            pick = random.choice([k for k, v in pool.items() if v == top])
            victims.append(pick)
            pool.pop(pick)
        self.night_victims = victims
        if wolf_msg:
            try:
                await wolf_msg.edit(view=None)
            except discord.HTTPException:
                pass

        # tahap Witch
        if self.witches_needed():
            self.stage = "witch"
            self.event.clear()
            await self.wait(WITCH_TIME)
        self.stage = None
        try:
            await panel.edit(view=None)
        except discord.HTTPException:
            pass

        # resolusi
        protected, saved = set(self.doctor_targets), set(self.witch_saved)
        deaths: list[int] = []
        for v in self.night_victims:
            if v not in protected and v not in saved and v not in deaths:
                deaths.append(v)
        for t in self.sk_targets:
            if t not in protected and t not in deaths:
                deaths.append(t)
        for t in self.witch_poisons:
            if t not in deaths:
                deaths.append(t)

        await self.set_lock(False)
        e = discord.Embed(title=f"🌅 Pagi hari ke-{self.round}", color=discord.Color.gold())
        lines = []
        for pid in deaths:
            p = self.players[pid]
            p.alive = False
            await self.mute(p)
            # role TIDAK diumumkan — tetap rahasia sampai game selesai
            lines.append(f"💀 {p.member.mention} ditemukan tewas.")
        e.description = "\n".join(lines) if lines else "✨ Malam ini tidak ada yang tewas."

        # DPR membungkam
        self.silenced = [t for t in dict.fromkeys(self.silence_targets) if self.players[t].alive]
        if self.silenced:
            for pid in self.silenced:
                try:
                    await self.channel.set_permissions(self.players[pid].member, send_messages=False)
                except discord.HTTPException:
                    pass
            e.add_field(name="🤐 Dibungkam DPR",
                        value="\n".join(f"{self.players[t].member.mention} tidak boleh bicara sepanjang hari ini." for t in self.silenced),
                        inline=False)
        await self.say(embed=e)

        await self.hunter_chain(deaths)
        return await self.finish_if_ended()

    # ─────────────── FASE SIANG ───────────────
    async def day_phase(self) -> bool:
        alive_txt = ", ".join(p.member.mention for p in self.alive())
        await self.say(f"💬 **Diskusi** dimulai! Berakhir {fmt_time(self.day_time)}\n"
                       f"Pemain hidup ({len(self.alive())}): {alive_txt}\n"
                       f"*(Host bisa `/ww skip` untuk langsung ke voting)*")
        panel = None
        if self.blackmails or self.alive_role("blackmailer"):
            panel = await self.say("📩 Ada pesan rahasia untukmu? Klik tombol ini (semua pemain bisa klik).", view=DayPanelView(self))

        self.stage = "discuss"
        self.event.clear()
        await self.wait(self.day_time)

        # voting
        self.votes.clear()
        msg = await self.say(f"🗳️ **Voting!** Pilih siapa yang di-lynch. Berakhir {fmt_time(self.vote_time)}\n"
                             "Kamu bisa mengganti pilihan selama waktu belum habis.", view=VoteView(self))
        self.stage = "vote"
        self.event.clear()
        await self.wait(self.vote_time)
        self.stage = None
        for m in (msg, panel):
            if m:
                try:
                    await m.edit(view=None)
                except discord.HTTPException:
                    pass

        detail = "\n".join(
            f"• {self.players[v].name} → {self.players[t].name if t else 'skip'}" for v, t in self.votes.items()) or "Tidak ada yang memilih."
        counts = Counter(t for t in self.votes.values() if t)
        skip = sum(1 for t in self.votes.values() if not t)
        lynched: Optional[int] = None
        if counts:
            top = max(counts.values())
            leaders = [k for k, v in counts.items() if v == top]
            if len(leaders) == 1 and top > skip:
                lynched = leaders[0]

        e = discord.Embed(title="📊 Hasil Voting", description=detail, color=discord.Color.orange())
        if lynched is None:
            e.add_field(name="Hasil", value="Seri / mayoritas skip — tidak ada yang di-lynch.")
            await self.say(embed=e)
        else:
            p = self.players[lynched]
            p.alive = False
            await self.mute(p)
            # role TIDAK diumumkan — tetap rahasia sampai game selesai
            e.add_field(name="Hasil", value=f"🔥 {p.member.mention} di-lynch!")
            await self.say(embed=e)

            if p.role == "tanner":
                await self.announce_end("tanner", p)
                return True
            if p.role == "werewolf_kid":
                self.kid_revenge = True
                await self.say("🩸 *Werewolf Kid mati terbakar... para serigala akan lebih buas malam nanti.*")
            await self.hunter_chain([lynched])
            if await self.finish_if_ended():
                return True

        # eksekusi Blackmailer di akhir fase siang
        killed: list[int] = []
        lines = []
        for b in self.blackmails:
            t = self.players[b["target"]]
            if t.alive and not b["complied"]:
                t.alive = False
                await self.mute(t)
                killed.append(t.id)
                # role TIDAK diumumkan — tetap rahasia sampai game selesai
                lines.append(f"😈 {t.member.mention} tidak mematuhi perintah pemeras dan **dibunuh**!")
        self.blackmails.clear()
        if lines:
            await self.say(embed=discord.Embed(title="🩸 Korban Pemerasan", description="\n".join(lines), color=discord.Color.dark_red()))
            await self.hunter_chain(killed)
            if await self.finish_if_ended():
                return True

        # buka bungkam DPR
        for pid in self.silenced:
            p = self.players[pid]
            if p.alive:
                try:
                    await self.channel.set_permissions(p.member, overwrite=None)
                except discord.HTTPException:
                    pass
        self.silenced = []
        return False

    # ─────────────── HUNTER ───────────────
    async def hunter_chain(self, dead_ids: list[int]):
        queue = [pid for pid in dead_ids if self.players[pid].role == "hunter"]
        while queue:
            h = self.players[queue.pop(0)]
            cands = self.alive()
            if not cands:
                return
            self.stage = "hunter"
            self.hunter_id = h.id
            self.hunter_choice = None
            self.event.clear()
            v = discord.ui.View(timeout=None)
            v.add_item(TargetSelect(self, "hunter", cands, "🏹 Pilih target tembakan"))
            msg = await self.say(f"🏹 {h.member.mention} adalah **Hunter**! Pilih satu pemain untuk ditembak {fmt_time(HUNTER_TIME)}", view=v)
            await self.wait(HUNTER_TIME)
            self.stage = None
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
            if self.hunter_choice is None:
                await self.say(f"🏹 {h.member.mention} tidak sempat menembak.")
                continue
            t = self.players[self.hunter_choice]
            t.alive = False
            await self.mute(t)
            # role TIDAK diumumkan — tetap rahasia sampai game selesai
            await self.say(f"💥 {h.member.mention} menembak {t.member.mention} hingga tewas!")
            if t.role == "hunter":
                queue.append(t.id)

    # ─────────────── AKSI MALAM (tombol publik) ───────────────
    async def open_night_action(self, interaction: discord.Interaction):
        send = interaction.response.send_message
        p = self.players.get(interaction.user.id)
        if not p:
            return await send("Kamu tidak ikut game ini.", ephemeral=True)
        if not p.alive:
            return await send("💀 Kamu sudah mati.", ephemeral=True)
        if self.stage not in ("night", "witch"):
            return await send("Bukan waktunya beraksi.", ephemeral=True)
        role = p.role

        if role in PACK:
            return await send(f"🐺 Gunakan channel {self.wolf_channel.mention} untuk memilih korban.", ephemeral=True)

        if role == "witch":
            if self.stage != "witch":
                return await send("⏳ Tunggu Werewolf menentukan korban, lalu klik tombol ini lagi.", ephemeral=True)
            if p.id in self.acted:
                return await send("🧙 Kamu sudah bertindak malam ini.", ephemeral=True)
            if p.heal <= 0 and p.poison <= 0:
                return await send("🧙 Ramuanmu sudah habis.", ephemeral=True)
            victims = ", ".join(self.players[v].member.mention for v in self.night_victims) or "*tidak ada*"
            txt = (f"🧙 Korban Werewolf malam ini: {victims}\n"
                   f"Ramuan penyelamat: **{p.heal}** • Ramuan racun: **{p.poison}**\nPilih satu aksi:")
            return await send(txt, view=WitchView(self, p), ephemeral=True)

        # semua role di bawah hanya aktif pada stage "night"
        others = [x for x in self.alive() if x.id != p.id]
        spec = None  # (kind, kandidat, placeholder, prompt, min, max)
        if role == "seer":
            spec = ("seer", others, "🔮 Pilih pemain yang dicek", "🔮 Siapa yang ingin kamu intip role-nya?", 1, 1)
        elif role == "sorcerer":
            spec = ("sorcerer", [x for x in others if x.role not in PACK], "🧿 Pilih pemain yang dicek",
                    "🧿 Siapa yang kamu curigai sebagai **Seer**?", 1, 1)
        elif role == "doctor":
            note = "\n*(Tidak boleh melindungi orang yang sama 2 malam berturut-turut)*" if p.last_protected else ""
            spec = ("doctor", [x for x in self.alive() if x.id != p.last_protected], "💉 Pilih pemain yang dilindungi",
                    f"💉 Siapa yang ingin kamu lindungi malam ini?{note}", 1, 1)
        elif role == "dpr":
            note = "\n*(Tidak boleh membungkam orang yang sama 2 malam berturut-turut)*" if p.last_silenced else ""
            spec = ("dpr", [x for x in others if x.id != p.last_silenced], "🤐 Pilih pemain yang dibungkam",
                    f"🤐 Siapa yang ingin kamu bungkam besok?{note}", 1, 1)
        elif role == "serial_killer":
            spec = ("sk", others, "🔪 Pilih korban", "🔪 Siapa yang akan kamu bunuh malam ini?", 1, 1)
        elif role == "blackmailer":
            spec = ("bm_target", [x for x in others if x.role not in PACK], "😈 Pilih target pemerasan",
                    "😈 Siapa yang ingin kamu peras? (setelah itu pilih role yang harus dia akui)", 1, 1)
        elif role == "blamer":
            if self.round != 1 or p.blame_targets:
                return await send("👉 Kamu sudah menentukan targetmu. Tunggu mereka mati...", ephemeral=True)
            spec = ("blamer", others, "👉 Pilih 2 target", "👉 Pilih **2 orang** yang harus mati agar kamu menang.", 2, 2)
        elif role == "hunter":
            return await send("🏹 Hunter tidak punya aksi malam. Kekuatanmu aktif saat kamu mati.", ephemeral=True)
        elif role == "tanner":
            return await send("🤡 Tanner tidak punya aksi malam. Buat desa me-lynch dirimu!", ephemeral=True)
        else:
            return await send("👤 Kamu warga biasa. Tidur nyenyak... 😴", ephemeral=True)

        kind, cands, ph, prompt, mn, mx = spec
        if self.stage != "night":
            return await send("Fase aksi malam sudah lewat.", ephemeral=True)
        if p.id in self.acted:
            return await send("✅ Kamu sudah beraksi malam ini.", ephemeral=True)
        if not cands:
            return await send("Tidak ada target yang tersedia.", ephemeral=True)
        v = discord.ui.View(timeout=120)
        v.add_item(TargetSelect(self, kind, cands, ph, min_values=mn, max_values=mx))
        await send(prompt, view=v, ephemeral=True)

    # ─────────────── PESAN RAHASIA SIANG (Blackmailer) ───────────────
    async def open_day_message(self, interaction: discord.Interaction):
        send = interaction.response.send_message
        p = self.players.get(interaction.user.id)
        if not p:
            return await send("Kamu tidak ikut game ini.", ephemeral=True)
        if self.stage not in ("discuss", "vote"):
            return await send("Fase ini sudah lewat.", ephemeral=True)
        if not p.alive:
            return await send("💀 Kamu sudah mati.", ephemeral=True)
        for b in self.blackmails:
            if b["target"] == p.id:
                if b["complied"]:
                    return await send("✅ Kamu sudah mengaku. Kamu aman.", ephemeral=True)
                role = ROLES[b["role"]]
                return await send(
                    f"😈 **Kamu sedang diperas!**\nKamu harus mengaku di depan umum sebagai "
                    f"**{role.emoji} {role.name}** sebelum fase siang berakhir (termasuk voting). "
                    f"Jika tidak, kamu akan **dibunuh**.\n(Mengaku tidak harus jujur — tapi kamu harus mengatakannya.)",
                    view=ConfessView(self, b), ephemeral=True)
        await send("📭 Tidak ada pesan rahasia untukmu.", ephemeral=True)

    async def handle_bm_role(self, interaction: discord.Interaction, role_key: str):
        p = self.players.get(interaction.user.id)
        tid = self.bm_pending.get(interaction.user.id)
        if (not p or not p.alive or p.role != "blackmailer" or self.stage != "night"
                or tid is None or p.id in self.acted):
            return await interaction.response.edit_message(content="Aksi tidak valid.", view=None)
        self.blackmails.append({"bm": p.id, "target": tid, "role": role_key, "complied": False})
        self.acted.add(p.id)
        self.bm_pending.pop(p.id, None)
        r = ROLES[role_key]
        await interaction.response.edit_message(
            content=f"😈 **{self.players[tid].name}** akan diperas: dia harus mengaku sebagai **{r.emoji} {r.name}** besok siang.",
            view=None)
        self.check_done()

    # ─────────────── HANDLER SEMUA SELECT MENU ───────────────
    EDIT_KINDS = {"seer", "sorcerer", "doctor", "poison", "dpr", "sk", "blamer", "witch_save", "bm_target"}

    async def handle_select(self, interaction: discord.Interaction, kind: str, values: list[str]):
        async def reply(text: str):
            if kind in self.EDIT_KINDS:
                await interaction.response.edit_message(content=text, view=None)
            else:
                await interaction.response.send_message(text, ephemeral=True)

        p = self.players.get(interaction.user.id)
        if not p:
            return await reply("Kamu tidak ikut game ini.")
        targets = [self.players[int(v)] for v in values if v != "skip" and int(v) in self.players]
        target = targets[0] if targets else None
        night = self.stage == "night"

        if kind == "hunter":
            if self.stage != "hunter" or p.id != self.hunter_id or not target:
                return await reply("Ini bukan giliranmu.")
            self.hunter_choice = target.id
            await reply(f"🏹 Kamu menembak **{target.name}**!")
            return self.check_done()

        if not p.alive:
            return await reply("💀 Kamu sudah mati.")
        if any(not t.alive for t in targets):
            return await reply("Target sudah mati.")

        if kind == "wolf":
            if not night or p.role not in PACK:
                return await reply("Bukan waktunya / bukan rolemu.")
            if not targets or len(targets) > self.kills:
                return await reply(f"Pilih maksimal {self.kills} korban.")
            self.wolf_votes[p.id] = [t.id for t in targets]
            await reply("🐺 Kamu memilih " + ", ".join(f"**{t.name}**" for t in targets) + ".")
            c = Counter(t for ts in self.wolf_votes.values() for t in ts)
            tally = "\n".join(f"• {self.players[t].name}: {n} suara" for t, n in c.most_common())
            await self.wolf_channel.send(f"🐺 **{p.name}** memilih " + ", ".join(f"**{t.name}**" for t in targets) + f"\n{tally}")

        elif kind in ("seer", "sorcerer", "dpr", "sk"):
            need = {"seer": "seer", "sorcerer": "sorcerer", "dpr": "dpr", "sk": "serial_killer"}[kind]
            if not night or p.role != need or p.id in self.acted or not target:
                return await reply("Aksi tidak valid.")
            if kind == "seer":
                text = (f"🔮 Hasil: **{target.name}** adalah **{target.info.emoji} {target.info.name}** "
                        f"({TEAMS[target.team][0]} {TEAMS[target.team][1]}).")
            elif kind == "sorcerer":
                text = (f"🧿 **{target.name}** **ADALAH** Seer!" if target.role == "seer"
                        else f"🧿 **{target.name}** **bukan** Seer.")
            elif kind == "dpr":
                if p.last_silenced == target.id:
                    return await reply("Tidak boleh membungkam orang yang sama 2 malam berturut-turut.")
                p.last_silenced = target.id
                self.silence_targets.append(target.id)
                text = f"🤐 Kamu akan membungkam **{target.name}** besok."
            else:
                self.sk_targets.append(target.id)
                text = f"🔪 Kamu menyerang **{target.name}** malam ini."
            self.acted.add(p.id)
            await reply(text)

        elif kind == "doctor":
            if not night or p.role != "doctor" or p.id in self.acted or not target:
                return await reply("Aksi tidak valid.")
            if p.last_protected == target.id:
                return await reply("Tidak boleh melindungi orang yang sama 2 malam berturut-turut.")
            self.acted.add(p.id)
            self.doctor_targets.append(target.id)
            p.last_protected = target.id
            await reply(f"💉 Kamu melindungi **{target.name}** malam ini.")

        elif kind == "bm_target":
            if not night or p.role != "blackmailer" or p.id in self.acted or not target:
                return await reply("Aksi tidak valid.")
            self.bm_pending[p.id] = target.id
            v = discord.ui.View(timeout=120)
            v.add_item(RoleClaimSelect(self))
            return await interaction.response.edit_message(
                content=f"😈 Target: **{target.name}**. Role apa yang harus dia akui di depan semua orang?", view=v)

        elif kind == "blamer":
            if not night or p.role != "blamer" or p.blame_targets or len(targets) != 2:
                return await reply("Aksi tidak valid.")
            p.blame_targets = [t.id for t in targets]
            await reply("👉 Targetmu: " + ", ".join(f"**{t.name}**" for t in targets) + ". Pastikan mereka mati sebelum kamu!")

        elif kind == "poison":
            if self.stage != "witch" or p.role != "witch" or p.poison <= 0 or p.id in self.acted or not target:
                return await reply("Aksi tidak valid.")
            p.poison -= 1
            self.acted.add(p.id)
            self.witch_poisons.append(target.id)
            await reply(f"☠️ Kamu meracuni **{target.name}**.")

        elif kind == "witch_save":
            if (self.stage != "witch" or p.role != "witch" or p.heal <= 0 or p.id in self.acted
                    or not target or target.id not in self.night_victims):
                return await reply("Aksi tidak valid.")
            p.heal -= 1
            self.acted.add(p.id)
            self.witch_saved.append(target.id)
            await reply(f"💚 Kamu menyelamatkan **{target.name}**.")

        elif kind == "vote":
            if self.stage != "vote":
                return await reply("Voting sudah selesai.")
            if target is not None and target.id == p.id:
                return await reply("Tidak boleh memilih diri sendiri.")
            self.votes[p.id] = target.id if target else None
            await reply(f"🗳️ Pilihanmu: **{target.name if target else 'Skip'}** (bisa diganti selama waktu masih ada).")

        self.check_done()


# ════════════════════════════════════════════════════════════════════
#  BOT & COMMANDS
# ════════════════════════════════════════════════════════════════════
class WerewolfBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.games: dict[int, Game] = {}

    async def setup_hook(self):
        self.tree.add_command(ww)
        gid = os.getenv("GUILD_ID")
        if gid:
            guild = discord.Object(id=int(gid))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        log.info("Login sebagai %s", self.user)


bot = WerewolfBot()
ww = app_commands.Group(name="ww", description="Permainan Werewolf", guild_only=True)
role_group = app_commands.Group(name="role", description="Atur komposisi role", parent=ww)

ROLE_CHOICES = [app_commands.Choice(name=f"{r.emoji} {r.name} ({TEAMS[r.team][1]})", value=r.key)
                for r in ROLES.values() if r.key != "villager"]


async def lobby_game_for_host(interaction: discord.Interaction) -> Optional[Game]:
    g = bot.games.get(interaction.channel_id)
    if not g or g.state != "lobby":
        await interaction.response.send_message("Tidak ada lobby aktif di channel ini. Buat dengan `/ww create`.", ephemeral=True)
        return None
    if interaction.user.id != g.host.id:
        await interaction.response.send_message("Hanya host yang bisa mengatur ini.", ephemeral=True)
        return None
    return g


@ww.command(name="create", description="Buat lobby Werewolf di channel ini")
async def ww_create(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Gunakan di text channel biasa.", ephemeral=True)
    if interaction.channel_id in bot.games:
        return await interaction.response.send_message("Sudah ada game di channel ini.", ephemeral=True)
    g = Game(bot, interaction.channel, interaction.user)  # type: ignore[arg-type]
    bot.games[interaction.channel_id] = g
    await interaction.response.send_message(embed=g.lobby_embed(), view=LobbyView(g))
    g.lobby_message = await interaction.original_response()


@ww.command(name="start", description="Mulai game (host)")
async def ww_start(interaction: discord.Interaction):
    g = await lobby_game_for_host(interaction)
    if g:
        await g.begin(interaction)


@ww.command(name="cancel", description="Batalkan / hentikan game (host atau admin)")
async def ww_cancel(interaction: discord.Interaction):
    g = bot.games.get(interaction.channel_id)
    if not g:
        return await interaction.response.send_message("Tidak ada game di channel ini.", ephemeral=True)
    if interaction.user.id != g.host.id and not interaction.permissions.manage_guild:
        return await interaction.response.send_message("Hanya host / admin yang bisa membatalkan.", ephemeral=True)
    await interaction.response.send_message("🛑 Game dibatalkan.")
    if g.task:
        g.task.cancel()
    else:
        if g.lobby_message:
            try:
                await g.lobby_message.edit(view=None)
            except discord.HTTPException:
                pass
        await g.cleanup()


@ww.command(name="skip", description="Lewati sisa waktu diskusi (host)")
async def ww_skip(interaction: discord.Interaction):
    g = bot.games.get(interaction.channel_id)
    if not g or g.state != "running":
        return await interaction.response.send_message("Tidak ada game berjalan.", ephemeral=True)
    if interaction.user.id != g.host.id:
        return await interaction.response.send_message("Hanya host.", ephemeral=True)
    if g.stage != "discuss":
        return await interaction.response.send_message("Hanya bisa saat fase diskusi.", ephemeral=True)
    g.event.set()
    await interaction.response.send_message("⏩ Diskusi dilewati.")


@ww.command(name="status", description="Lihat status game")
async def ww_status(interaction: discord.Interaction):
    g = bot.games.get(interaction.channel_id)
    if not g:
        return await interaction.response.send_message("Tidak ada game di channel ini.", ephemeral=True)
    alive = ", ".join(p.member.mention for p in g.alive()) or "-"
    # role pemain yang mati TIDAK ditampilkan — tetap rahasia sampai game selesai
    dead = ", ".join(f"💀 {p.member.mention}" for p in g.players.values() if not p.alive) or "-"
    await interaction.response.send_message(
        f"**Status:** {g.state} • Ronde {g.round}\n❤️ Hidup: {alive}\n💀 Mati: {dead}", ephemeral=True)


@ww.command(name="setting", description="Atur durasi fase & malam mulai Werewolf boleh membunuh (host, saat lobby)")
@app_commands.describe(malam="Durasi malam", diskusi="Durasi diskusi", voting="Durasi voting",
                       malam_bunuh="Werewolf boleh membunuh mulai malam ke-berapa")
async def ww_setting(interaction: discord.Interaction,
                     malam: Optional[app_commands.Range[int, 20, 300]] = None,
                     diskusi: Optional[app_commands.Range[int, 20, 600]] = None,
                     voting: Optional[app_commands.Range[int, 15, 300]] = None,
                     malam_bunuh: Optional[app_commands.Range[int, 1, 10]] = None):
    g = await lobby_game_for_host(interaction)
    if not g:
        return
    g.night_time = malam or g.night_time
    g.day_time = diskusi or g.day_time
    g.vote_time = voting or g.vote_time
    if malam_bunuh:
        g.wolf_kill_night = malam_bunuh
    await interaction.response.send_message("⚙️ Pengaturan disimpan.", ephemeral=True)
    await g.refresh_lobby()


def _base(g: Game) -> dict[str, int]:
    return dict(g.custom_roles) if g.custom_roles is not None else auto_roles(len(g.players))


@role_group.command(name="add", description="Tambah role ke komposisi")
@app_commands.describe(role="Role yang ditambah", jumlah="Jumlah")
@app_commands.choices(role=ROLE_CHOICES)
async def role_add(interaction: discord.Interaction, role: app_commands.Choice[str],
                   jumlah: app_commands.Range[int, 1, 10] = 1):
    g = await lobby_game_for_host(interaction)
    if not g:
        return
    base = _base(g)
    base[role.value] = base.get(role.value, 0) + jumlah
    g.custom_roles = base
    await interaction.response.send_message(f"➕ {jumlah}× {ROLES[role.value].name} ditambahkan.\n{g.plan_text()}", ephemeral=True)
    await g.refresh_lobby()


@role_group.command(name="remove", description="Kurangi / hapus role dari komposisi")
@app_commands.describe(role="Role yang dikurangi", jumlah="Jumlah (kosongkan untuk hapus semua)")
@app_commands.choices(role=ROLE_CHOICES)
async def role_remove(interaction: discord.Interaction, role: app_commands.Choice[str],
                      jumlah: Optional[app_commands.Range[int, 1, 10]] = None):
    g = await lobby_game_for_host(interaction)
    if not g:
        return
    base = _base(g)
    base[role.value] = 0 if jumlah is None else max(0, cur - jumlah)
    g.custom_roles = {k: v for k, v in base.items() if v > 0}
    await interaction.response.send_message(f"➖ {ROLES[role.value].name} dikurangi.\n{g.plan_text()}", ephemeral=True)
    await g.refresh_lobby()


@role_group.command(name="auto", description="Kembali ke komposisi otomatis")
async def role_auto(interaction: discord.Interaction):
    g = await lobby_game_for_host(interaction)
    if not g:
        return
    g.custom_roles = None
    await interaction.response.send_message(f"♻️ Komposisi otomatis aktif.\n{g.plan_text()}", ephemeral=True)
    await g.refresh_lobby()


@role_group.command(name="list", description="Lihat komposisi role & deskripsi tiap role")
async def role_list(interaction: discord.Interaction):
    g = bot.games.get(interaction.channel_id)
    embeds = []
    for team, (te, tn) in TEAMS.items():
        e = discord.Embed(title=f"{te} {tn}", color={"wolf": discord.Color.red(), "village": discord.Color.green(),
                                                      "neutral": discord.Color.gold()}[team])
        for r in ROLES.values():
            if r.team == team:
                e.add_field(name=f"{r.emoji} {r.name}", value=r.desc, inline=False)
        embeds.append(e)
    if g:
        embeds[-1].add_field(name="📋 Komposisi saat ini", value=g.plan_text() or "-", inline=False)
    await interaction.response.send_message(embeds=embeds, ephemeral=True)


def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_TOKEN di environment / file .env")
    bot.run(token)


if __name__ == "__main__":
    main()
