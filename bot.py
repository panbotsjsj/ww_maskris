"""
Werewolf Discord Bot  (v3 -- aksi malam via DM)
----------------------------------------------
- Lobby: Join / Leave / Start, host bisa ikut main atau spectate
- Semua aksi malam dikirim VIA DM ke pemilik role
  (tombol kirim-ulang di channel sebagai fallback bila DM gagal)
- Komposisi role: /ww role add|remove|auto|list
- Aksi tiap role malam bisa diatur mulai malam ke-berapa (/ww role night)
- Role tidak diumumkan saat pemain mati (dibuka saat game selesai)
- Channel private Werewolf untuk strategi; voting korban via DM
- Alur: Malam -> Siang (diskusi + pemerasan/bungkam) -> Voting
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
MAX_PLAYERS = 24  # select menu Discord maks 25 opsi
LOCK_CHAT_AT_NIGHT = os.getenv("LOCK_CHAT_AT_NIGHT", "1") == "1"
WOLF_KILL_NIGHT = max(1, int(os.getenv("WOLF_KILL_NIGHT", "1")))
SK_KILL_NIGHT = max(1, int(os.getenv("SK_KILL_NIGHT", "1")))
DPR_SILENCE_NIGHT = max(1, int(os.getenv("DPR_SILENCE_NIGHT", "1")))
HUNTER_TIME = 30


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
    # ───── Tim Warga ─────
    RoleInfo("villager", "👤", "Villager", "village",
             "Warga biasa tanpa kekuatan khusus. Andalkan diskusi dan voting."),
    RoleInfo("seer", "🔮", "Seer", "village",
             "Tiap malam **scan 1 orang** via DM. Hasil: **W** = dia tim Werewolf, **V** = bukan Werewolf."),
    RoleInfo("guardian", "🛡️", "Guardian", "village",
             "Tiap malam **lindungi 1 orang** via DM (boleh diri sendiri) dari serangan malam. "
             "Tidak boleh melindungi orang yang sama 2 malam berturut-turut."),
    RoleInfo("drunk", "🍺", "Drunk", "village",
             "Kamu **tidak tahu role aslimu**. Pada **malam ke-3** role aslimu diungkap via DM dan bisa kamu gunakan."),
    RoleInfo("hunter", "🏹", "Hunter", "village",
             "Saat mati (dibunuh/di-bakar/diracun/ditembak) kamu boleh menembak 1 pemain untuk ikut mati."),
    RoleInfo("gunner", "🔫", "Gunner", "village",
             "Kamu punya **1 peluru**. Tiap malam via DM kamu bisa memilih menembak 1 orang atau tidak. "
             "Sekali ditembakkan, peluru habis."),
    RoleInfo("prince", "👑", "Prince", "village",
             "Jika kamu **di-vote (bakar)**, role-mu terungkap ke semua orang dan **tidak ada yang dibakar** hari itu."),
    RoleInfo("diseased", "🤢", "Diseased", "village",
             "Jika kamu **dimakan Werewolf** (dan tewas), para Werewolf akan sakit dan "
             "**tidak bisa memangsa di malam berikutnya**."),
    RoleInfo("cursed", "🌚", "Cursed", "village",
             "Kamu seperti warga biasa. Tapi jika **dimakan Werewolf**, kamu tidak mati -- "
             "kamu **berubah menjadi Werewolf**!"),
    RoleInfo("aura_seer", "👁️", "Aura Seer", "village",
             "Tiap malam **cek 1 pemain** via DM. Kamu diberi tahu **tim**-nya: Warga, Werewolf, atau Netral."),
    RoleInfo("gravekeeper", "🪦", "Gravekeeper", "village",
             "Tiap malam **periksa 1 mayat** via DM untuk mengetahui **role asli** pemain yang sudah mati."),
    # ───── Tim Werewolf ─────
    RoleInfo("werewolf", "🐺", "Werewolf", "wolf",
             "Berkelompok dengan sesama Werewolf. Tiap malam **pilih korban via DM** (diskusikan dulu di channel pack)."),
    RoleInfo("werewolf_kid", "🐕", "Werewolf Kid", "wolf",
             "Sama seperti Werewolf biasa. Jika dia **dibakar (di-bakar)**, malam berikutnya "
             "Werewolf bisa memakan **2 orang**."),
    RoleInfo("sorcerer", "🧿", "Sorcerer", "wolf",
             "Kamu **tahu siapa saja Werewolf** (mereka tidak tahu kamu). Tiap malam via DM pilih 1 pemain "
             "untuk dicek apakah dia **Seer**."),
    RoleInfo("blackmailer", "😈", "Blackmailer", "wolf",
             "Tiap malam via DM pilih 1 target **dan role yang harus dia akui**. Target wajib mengaku di depan umum "
             "sebelum fase siang berakhir, kalau tidak dia **dibunuh**."),
    RoleInfo("dpr", "🤐", "DPR", "wolf",
             "Tiap malam via DM **membungkam 1 orang** (tidak bisa chat sepanjang siang berikutnya). "
             "Malam selanjutnya tidak boleh membungkam orang yang sama."),
    # ───── Netral ─────
    RoleInfo("tanner", "🤡", "Tanner", "neutral",
             "Menang **sendirian** jika kamu mati karena **voting (di-bakar)**. Game langsung selesai."),
    RoleInfo("blamer", "👉", "Blamer", "neutral",
             "Di **malam pertama** via DM pilih **2 orang** sebagai target. "
             "Jika keduanya mati dan kamu masih hidup, kamu menang."),
    RoleInfo("serial_killer", "🔪", "Serial Killer", "neutral",
             "Tiap malam via DM membunuh **1 orang**. Menang jika kamu adalah **orang terakhir yang bertahan**."),
]}

PACK = {"werewolf", "werewolf_kid"}         # anggota channel Werewolf & pemilih korban
KNOWS_WOLVES = {"sorcerer"}                 # role yang tahu siapa Werewolf
NIGHT_REQUIRED = {"seer", "guardian", "aura_seer", "gravekeeper", "gunner",
                  "sorcerer", "blackmailer", "dpr", "serial_killer"}
# role yang aksinya bisa diatur mulai malam ke-berapa
NIGHT_ACTION_ROLES = ["werewolf", "seer", "guardian", "aura_seer", "gravekeeper", "gunner",
                      "sorcerer", "blackmailer", "dpr", "serial_killer"]
# Pool role asli Drunk (diundi saat game mulai, terungkap malam ke-3)
DRUNK_POOL = ["seer", "guardian", "aura_seer", "gravekeeper", "gunner",
              "hunter", "prince", "diseased", "cursed"]


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
    for min_n, key in [(5, "seer"), (6, "guardian"), (7, "hunter"), (8, "aura_seer"),
                       (9, "gunner"), (10, "gravekeeper"), (11, "drunk"), (12, "prince"),
                       (13, "diseased"), (14, "cursed"), (15, "tanner"),
                       (16, "serial_killer"), (18, "blamer")]:
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
        self.bullet = 1                        # Gunner
        self.true_role: Optional[str] = None   # Drunk: role asli (terungkap malam ke-3)
        self.last_protected: Optional[int] = None   # Guardian
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


class VoteView(discord.ui.View):
    def __init__(self, game: "Game"):
        super().__init__(timeout=None)
        self.add_item(TargetSelect(game, "vote", game.alive(), "🗳️ Pilih pemain yang mau di-bakar", allow_skip=True))


class NightPanelView(discord.ui.View):
    """Tombol fallback: kirim ulang aksi malam ke DM (mis. DM pertama tidak masuk)."""

    def __init__(self, game: "Game"):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Kirim Ulang Aksi ke DM", emoji="🔔", style=discord.ButtonStyle.primary)
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
    """Fallback bila DM role tidak masuk."""

    def __init__(self, game: "Game"):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Lihat Role Saya", emoji="🎭", style=discord.ButtonStyle.secondary)
    async def show(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = self.game
        p = g.players.get(interaction.user.id)
        if not p:
            return await interaction.response.send_message("Kamu tidak ikut game ini.", ephemeral=True)
        await interaction.response.send_message(embed=g.role_embed(p), ephemeral=True)


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
        self.wolf_kill_night = WOLF_KILL_NIGHT      # Werewolf boleh membunuh mulai malam ke-N
        self.wolf_active_night = False  # di-set tiap malam di night_phase
        self.sk_kill_night = SK_KILL_NIGHT          # Serial Killer boleh membunuh mulai malam ke-N
        self.dpr_silence_night = DPR_SILENCE_NIGHT  # DPR boleh membungkam mulai malam ke-N
        self.role_start_night: dict[str, int] = {}  # role -> aksi mulai malam ke-N (default 1)
        self.wolf_sick = False          # True jika Diseased dimakan -> wolf tidak bisa memangsa besok
        self.kid_revenge = False        # True jika Werewolf Kid di-lynch -> 2 korban di malam aktif berikutnya
        self.kills = 1
        self.lobby_message: Optional[discord.Message] = None
        self.wolf_channel: Optional[discord.TextChannel] = None
        self.task: Optional[asyncio.Task] = None
        self.round = 0
        self._orig_send: Optional[bool] = None
        self._locked = False

        self.stage: Optional[str] = None  # night / discuss / vote / hunter
        self.event = asyncio.Event()

        # state malam
        self.wolf_votes: dict[int, list[int]] = {}
        self.acted: set[int] = set()
        self.guard_targets: list[int] = []
        self.night_victims: list[int] = []
        self.sk_targets: list[int] = []
        self.gunner_shots: list[int] = []
        self.silence_targets: list[int] = []
        self.silenced: list[int] = []
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

    async def dm(self, p: Player, *args, **kwargs):
        """Kirim DM ke pemain. Return message jika berhasil, None jika gagal (DM ditutup)."""
        try:
            return await p.member.send(*args, **kwargs)
        except (discord.Forbidden, discord.HTTPException):
            log.warning("DM gagal ke %s", p.name)
            return None

    def role_plan(self) -> dict[str, int]:
        n = len(self.players)
        base = dict(self.custom_roles) if self.custom_roles is not None else auto_roles(n)
        plan = {k: v for k, v in base.items() if v > 0}
        plan["villager"] = max(0, n - sum(plan.values()))
        return plan

    def plan_text(self, plan: Optional[dict[str, int]] = None) -> str:
        plan = plan or self.role_plan()
        return "\n".join(f"{ROLES[k].emoji} **{ROLES[k].name}** × {plan[k]}" for k in ROLES if plan.get(k))

    def role_start(self, role: str) -> int:
        """Malam ke-berapa aksi role mulai terbuka (default 1)."""
        if role == "werewolf":
            return self.wolf_kill_night
        if role == "serial_killer":
            return self.sk_kill_night
        if role == "dpr":
            return self.dpr_silence_night
        return self.role_start_night.get(role, 1)

    def role_unlocked(self, role: str) -> bool:
        """Apakah aksi role sudah terbuka pada ronde malam ini."""
        return self.round >= self.role_start(role)

    def night_overrides(self) -> list[tuple[str, int]]:
        """Daftar (role, malam) yang diubah dari default, untuk ditampilkan di lobby."""
        return [(k, self.role_start(k)) for k in NIGHT_ACTION_ROLES if self.role_start(k) > 1]

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
                         "Host bisa pakai tombol **👁️ Spectate** untuk tidak ikut main.\n"
                         "⚠️ **Pastikan DM dari server terbuka** — role & aksi malam dikirim via DM!")
        plist = "\n".join(f"{i}. {p.member.mention}" for i, p in enumerate(self.players.values(), 1))
        e.add_field(name=f"Pemain ({len(self.players)}/{MAX_PLAYERS})", value=plist or "-", inline=False)
        mode = "otomatis" if self.custom_roles is None else "kustom"
        e.add_field(name=f"Komposisi role ({mode})", value=self.plan_text() or "-", inline=False)
        ov = self.night_overrides()
        if ov:
            e.add_field(name="⏰ Aksi mulai malam ke-",
                        value="\n".join(f"{ROLES[k].emoji} {ROLES[k].name if k != 'werewolf' else 'Werewolf (bunuh)'}: malam {n}"
                                        for k, n in ov),
                        inline=False)
        err = self.validate(self.role_plan()) if len(self.players) >= MIN_PLAYERS else None
        if err:
            e.add_field(name="⚠️ Perhatian", value=err, inline=False)
        e.set_footer(text=f"Malam {self.night_time}s • Diskusi {self.day_time}s • Voting {self.vote_time}s  |  /ww role add|remove|auto|night")
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
            topic="Channel rahasia Werewolf. Diskusikan strategi di sini; pilih korban tiap malam via DM.")

    # ─────────────── embed role untuk DM ───────────────
    def role_embed(self, p: Player) -> discord.Embed:
        te, tn = TEAMS[p.team]
        color = {"wolf": discord.Color.red(), "village": discord.Color.green(),
                 "neutral": discord.Color.gold()}[p.team]
        e = discord.Embed(title=f"{p.info.emoji} Kamu adalah {p.info.name}",
                          description=p.info.desc, color=color)
        e.add_field(name="Tim", value=f"{te} {tn}")
        if p.role in PACK:
            mates = ", ".join(w.name for w in self.players.values() if w.role in PACK and w.id != p.id)
            e.add_field(name="🐺 Anggota pack", value=mates or "Hanya kamu", inline=False)
        elif p.role in KNOWS_WOLVES:
            wolves = ", ".join(w.name for w in self.players.values() if w.role in PACK)
            e.add_field(name="🐺 Werewolf yang kamu ketahui", value=wolves or "-", inline=False)
        if p.role == "drunk":
            e.add_field(name="🍺 Mabuk", value="Role aslimu masih dirahasiakan — akan terungkap di **malam ke-3**.",
                        inline=False)
        e.add_field(name="🌙 Aksi malam", value="Semua aksi malammu dikirim **via DM** oleh bot setiap malam.",
                    inline=False)
        return e

    # ─────────────── mulai game ───────────────
    async def begin(self, interaction: discord.Interaction):
        if self.state != "lobby":
            return await interaction.response.send_message("Game sudah berjalan.", ephemeral=True)
        plan = self.role_plan()
        err = self.validate(plan)
        if err:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)
        await interaction.response.send_message("✅ Membagikan role via DM...", ephemeral=True)

        roles: list[str] = []
        for k, c in plan.items():
            roles += [k] * c
        random.shuffle(roles)
        for p, r in zip(self.players.values(), roles):
            p.role = r
        # Drunk: undi role asli (disembunyikan sampai malam ke-3)
        for p in self.players.values():
            if p.role == "drunk":
                p.true_role = random.choice(DRUNK_POOL)

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
                          description="Role sudah dikirim **via DM** ke masing-masing pemain!\n"
                                      "Klik tombol 🎭 di bawah jika DM tidak masuk.")
        e.add_field(name="Komposisi", value=self.plan_text(plan))
        e.add_field(name="Pemain", value=", ".join(p.member.mention for p in self.players.values()), inline=False)
        await self.say(embed=e, view=RoleView(self))

        failed = []
        for p in self.players.values():
            if not await self.dm(p, embed=self.role_embed(p)):
                failed.append(p)
        if failed:
            await self.say("⚠️ " + ", ".join(x.member.mention for x in failed) +
                           "\nTidak bisa menerima DM dari bot! Aktifkan **Privacy Settings → "
                           "Allow direct messages from server members**, atau klik tombol 🎭 di atas untuk melihat role.")

        pack = [p for p in self.players.values() if p.role in PACK]
        await self.wolf_channel.send(
            "🐺 **Selamat datang di sarang Werewolf!**\n"
            f"Pack kamu: {', '.join(w.member.mention for w in pack)}\n"
            "Setiap malam, pilih korban **via DM** (bot akan mengirim pilihannya). "
            "Diskusikan strategi di sini — jangan ketahuan!")
        self.task = asyncio.create_task(self.run())

    # ─────────────── loop utama ───────────────
    async def run(self):
        try:
            await asyncio.sleep(10)
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

    def check_done(self):
        """Set event jika semua yang wajib bertindak pada stage ini sudah selesai."""
        done = False
        if self.stage == "night":
            need = [p for p in self.alive() if p.role in NIGHT_REQUIRED]
            blamers = [p for p in self.alive_role("blamer") if not p.blame_targets] if self.round == 1 else []
            # di malam saat Werewolf belum boleh membunuh / sedang sakit, vote korban tidak ditunggu
            wolves_ok = True if not self.wolf_active_night else all(w.id in self.wolf_votes for w in self.alive_pack())
            done = (wolves_ok
                    and all(p.id in self.acted for p in need) and not blamers)
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
            e = discord.Embed(title="🤡 TANNER MENANG!", description=f"{player.member.mention} berhasil membuat desa me-bakar dirinya.", color=discord.Color.gold())
        elif kind == "blamer":
            e = discord.Embed(title="👉 BLAMER MENANG!", description=f"{player.member.mention} sukses — kedua targetnya mati dan dia masih hidup.", color=discord.Color.gold())
        else:
            e = discord.Embed(title="☠️ Tidak ada pemenang", description="Semua pemain tewas.", color=discord.Color.dark_grey())
        lines = []
        for p in self.players.values():
            rname = p.info.name
            if p.role == "drunk" and p.true_role:
                rname = f"Drunk (aslinya {ROLES[p.true_role].name})"
            lines.append(f"{p.info.emoji} {p.member.mention} — **{rname}** ({TEAMS[p.team][1]}) "
                         f"{'❤️' if p.alive else '💀'}")
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
        self.guard_targets.clear()
        self.night_victims = []
        self.sk_targets.clear()
        self.gunner_shots.clear()
        self.silence_targets.clear()
        self.bm_pending.clear()
        self.blackmails.clear()

        # 🍺 Drunk: role asli terungkap di malam ke-3
        if self.round == 3:
            for d in self.alive_role("drunk"):
                if d.true_role:
                    d.role = d.true_role
                    r = ROLES[d.role]
                    await self.dm(d, f"🍺 **Malam ke-3!** Kepalamu sudah jernih...\n"
                                     f"Role aslimu ternyata adalah {r.emoji} **{r.name}**!\n{r.desc}")

        # 🤢 Werewolf sakit (efek Diseased) / belum mencapai malam boleh membunuh
        sick_tonight = self.wolf_sick
        self.wolf_sick = False
        self.wolf_active_night = self.round >= self.wolf_kill_night and not sick_tonight
        if self.wolf_active_night:
            self.kills = 2 if self.kid_revenge else 1
            self.kid_revenge = False  # balas dendam hanya dikonsumsi di malam bisa membunuh
        else:
            self.kills = 0

        # kunci aksi role yang belum mencapai malam mulainya
        # (otomatis dianggap selesai supaya fase malam tidak macet menunggu)
        for _p in self.alive():
            if _p.role in NIGHT_REQUIRED and not self.role_unlocked(_p.role):
                self.acted.add(_p.id)

        await self.set_lock(True)
        e = discord.Embed(title=f"🌙 Malam ke-{self.round}", color=discord.Color.dark_blue(),
                          description="Desa tertidur... 😴\nPemain dengan aksi malam, **cek DM** dari bot!\n"
                                      "_(DM tidak masuk? Klik tombol 🔔 di bawah untuk kirim ulang.)_\n"
                                      f"Berakhir {fmt_time(self.night_time)}")
        if sick_tonight:
            e.add_field(name="🤢 Werewolf Sakit!",
                        value="Para Werewolf keracunan dan **tidak bisa memangsa malam ini**.", inline=False)
        panel = await self.say(embed=e, view=NightPanelView(self))

        pack = self.alive_pack()
        if pack and not self.wolf_active_night:
            reason = (f"belum boleh membunuh (mulai malam ke-{self.wolf_kill_night})"
                      if self.round < self.wolf_kill_night else "sedang sakit 🤢")
            try:
                await self.wolf_channel.send(f"🌙 **Malam ke-{self.round}** — pack {reason}. "
                                             "Manfaatkan untuk strategi! 😴")
            except discord.HTTPException:
                pass

        self.stage = "night"
        self.event.clear()
        for p in self.alive():
            await self.send_night_action_dm(p)
        self.check_done()
        await self.wait(self.night_time)
        self.stage = None
        try:
            await panel.edit(view=None)
        except discord.HTTPException:
            pass

        # Gunner yang tidak memilih dianggap tidak menembak
        for g in self.alive_role("gunner"):
            if g.bullet > 0 and g.id not in self.acted and self.role_unlocked("gunner"):
                self.acted.add(g.id)

        # tentukan korban Werewolf (suara terbanyak, seri = acak)
        pool = dict(Counter(t for ts in self.wolf_votes.values() for t in ts))
        victims: list[int] = []
        while pool and len(victims) < self.kills:
            top = max(pool.values())
            pick = random.choice([k for k, v in pool.items() if v == top])
            victims.append(pick)
            pool.pop(pick)
        self.night_victims = victims

        # resolusi serangan malam
        protected = set(self.guard_targets)
        deaths: list[int] = []
        for v in self.night_victims:
            if v in protected or v in deaths:
                continue
            pv = self.players[v]
            if pv.role == "cursed":
                await self.transform_cursed(pv)
            else:
                deaths.append(v)
                if pv.role == "diseased":
                    self.wolf_sick = True  # Werewolf sakit di malam berikutnya
        for t in self.sk_targets:
            if t not in protected and t not in deaths:
                deaths.append(t)
        for t in self.gunner_shots:
            if t not in protected and t not in deaths:
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
        if self.wolf_sick:
            e.add_field(name="🤢", value="Para Werewolf terlihat sakit-sakitan... mereka tidak bisa memangsa besok malam!",
                        inline=False)

        # DPR membungkam
        self.silenced = [t for t in dict.fromkeys(self.silence_targets) if self.players[t].alive]
        if self.silenced:
            for pid in self.silenced:
                try:
                    await self.channel.set_permissions(self.players[pid].member, send_messages=False)
                except discord.HTTPException:
                    pass
            e.add_field(name="🤐 Dibungkam DPR",
                        value="\n".join(f"{self.players[t].member.mention} tidak boleh bicara sepanjang hari ini."
                                        for t in self.silenced),
                        inline=False)
        await self.say(embed=e)

        await self.hunter_chain(deaths)
        return await self.finish_if_ended()

    async def transform_cursed(self, p: Player):
        """Cursed yang dimakan Werewolf berubah menjadi Werewolf (tidak mati)."""
        p.role = "werewolf"
        if self.wolf_channel:
            try:
                await self.wolf_channel.set_permissions(p.member, view_channel=True,
                                                        send_messages=True, read_message_history=True)
            except discord.HTTPException:
                pass
            try:
                await self.wolf_channel.send(
                    f"🌚 **{p.member.mention}** dimakan... tapi kutukan mengubahnya! "
                    "Dia kini menjadi **Werewolf** dan bergabung dengan pack. 🤫")
            except discord.HTTPException:
                pass
        await self.dm(p, "🌚 Kamu dimakan Werewolf... tapi bukannya mati, **kutukan mengubahmu menjadi Werewolf**!\n"
                         "Cek channel werewolf-pack untuk berkoordinasi. Jangan sampai ketahuan!")

    # ─────────────── FASE SIANG ───────────────
    async def day_phase(self) -> bool:
        alive_txt = ", ".join(p.member.mention for p in self.alive())
        await self.say(f"💬 **Diskusi** dimulai! Berakhir {fmt_time(self.day_time)}\n"
                       f"Pemain hidup ({len(self.alive())}): {alive_txt}\n"
                       f"*(Host bisa `/ww skip` untuk langsung ke voting)*")

        # DM target pemerasan Blackmailer
        for b in self.blackmails:
            t = self.players[b["target"]]
            if t.alive:
                role = ROLES[b["role"]]
                await self.dm(t,
                              f"😈 **Kamu sedang diperas!**\nKamu harus mengaku di depan umum sebagai "
                              f"**{role.emoji} {role.name}** sebelum fase siang berakhir (termasuk voting). "
                              f"Jika tidak, kamu akan **dibunuh**.\n"
                              f"(Mengaku tidak harus jujur — tapi kamu harus mengatakannya.)",
                              view=ConfessView(self, b))
        panel = None
        if self.blackmails:
            panel = await self.say("📩 Ada pesan rahasia untukmu? Klik tombol ini (semua pemain bisa klik).",
                                   view=DayPanelView(self))

        self.stage = "discuss"
        self.event.clear()
        await self.wait(self.day_time)

        # voting
        self.votes.clear()
        msg = await self.say(f"🗳️ **Voting!** Pilih siapa yang di-bakar. Berakhir {fmt_time(self.vote_time)}\n"
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
            e.add_field(name="Hasil", value="Seri / mayoritas skip — tidak ada yang di-bakar.")
            await self.say(embed=e)
        else:
            p = self.players[lynched]
            if p.role == "prince":
                # 👑 Prince: role terungkap, tidak ada yang dibakar
                e.add_field(name="Hasil",
                            value=f"👑 {p.member.mention} ternyata adalah **Prince**! Tidak ada yang dibakar hari ini.")
                await self.say(embed=e)
            else:
                p.alive = False
                await self.mute(p)
                # role TIDAK diumumkan — tetap rahasia sampai game selesai
                e.add_field(name="Hasil", value=f"🔥 {p.member.mention} di-bakar!")
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
            await self.say(embed=discord.Embed(title="🩸 Korban Pemerasan", description="\n".join(lines),
                                               color=discord.Color.dark_red()))
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

    # ─────────────── PROMPT AKSI MALAM (via DM) ───────────────
    def _sel_view(self, kind: str, cands: list[Player], placeholder: str,
                  min_values: int = 1, max_values: int = 1, allow_skip: bool = False) -> discord.ui.View:
        v = discord.ui.View(timeout=None)
        v.add_item(TargetSelect(self, kind, cands, placeholder,
                                min_values=min_values, max_values=max_values, allow_skip=allow_skip))
        return v

    def build_night_prompt(self, p: Player) -> Optional[tuple[str, discord.ui.View]]:
        """Bangun (teks, view) aksi malam untuk pemain. None jika tidak ada aksi."""
        if not p.alive or self.state != "running":
            return None
        role = p.role
        rnd = self.round

        if role in PACK:
            if self.stage != "night" or not self.wolf_active_night:
                return None
            targets = [x for x in self.alive() if x.role not in PACK]
            if not targets:
                return None
            txt = (f"🐺 **Malam ke-{rnd}** — pilih korban (berakhir {fmt_time(self.night_time)}).\n"
                   "Suara terbanyak dipakai (seri = acak).")
            if self.kills > 1:
                txt += "\n🩸 **Balas dendam Werewolf Kid!** Kamu bisa memilih sampai 2 korban."
            ph = "🐺 Pilih korban" + (" (maks 2)" if self.kills > 1 else "")
            return txt, self._sel_view("wolf", targets, ph, max_values=self.kills)

        if self.stage != "night" or p.id in self.acted:
            return None
        if role in NIGHT_REQUIRED and not self.role_unlocked(role):
            return None

        others = [x for x in self.alive() if x.id != p.id]
        if role == "seer":
            if not others:
                return None
            return (f"🔮 **Malam ke-{rnd}** — siapa yang ingin kamu scan?\n"
                    "Hasil: **W** = tim Werewolf 🐺, **V** = bukan Werewolf.",
                    self._sel_view("seer", others, "🔮 Pilih pemain yang di-scan"))
        if role == "aura_seer":
            if not others:
                return None
            return (f"👁️ **Malam ke-{rnd}** — aura siapa yang ingin kamu cek?\n"
                    "Kamu akan diberi tahu timnya (Warga / Werewolf / Netral).",
                    self._sel_view("aura_seer", others, "👁️ Pilih pemain yang dicek"))
        if role == "guardian":
            cands = [x for x in self.alive() if x.id != p.last_protected]
            if not cands:
                return None
            note = "\n*(Tidak boleh melindungi orang yang sama 2 malam berturut-turut)*" if p.last_protected else ""
            return (f"🛡️ **Malam ke-{rnd}** — siapa yang ingin kamu lindungi?{note}",
                    self._sel_view("guardian", cands, "🛡️ Pilih yang dilindungi"))
        if role == "gravekeeper":
            dead = [x for x in self.players.values() if not x.alive]
            if not dead:
                return None
            return (f"🪦 **Malam ke-{rnd}** — mayat siapa yang ingin kamu periksa?",
                    self._sel_view("gravekeeper", dead, "🪦 Pilih mayat"))
        if role == "gunner":
            if p.bullet <= 0 or not others:
                return None
            return (f"🔫 **Malam ke-{rnd}** — kamu punya **1 peluru**.\n"
                    "Pilih target untuk ditembak, atau **Skip** bila tidak ingin menembak.",
                    self._sel_view("gunner", others, "🔫 Tembak seseorang? (atau lewati)", allow_skip=True))
        if role == "sorcerer":
            cands = [x for x in others if x.role not in PACK]
            if not cands:
                return None
            return (f"🧿 **Malam ke-{rnd}** — siapa yang kamu curigai sebagai **Seer**?",
                    self._sel_view("sorcerer", cands, "🧿 Pilih yang dicek"))
        if role == "dpr":
            cands = [x for x in others if x.id != p.last_silenced]
            if not cands:
                return None
            note = "\n*(Tidak boleh membungkam orang yang sama 2 malam berturut-turut)*" if p.last_silenced else ""
            return (f"🤐 **Malam ke-{rnd}** — siapa yang ingin kamu bungkam besok?{note}",
                    self._sel_view("dpr", cands, "🤐 Pilih yang dibungkam"))
        if role == "serial_killer":
            if not others:
                return None
            return (f"🔪 **Malam ke-{rnd}** — siapa yang akan kamu bunuh?",
                    self._sel_view("sk", others, "🔪 Pilih korban"))
        if role == "blackmailer":
            cands = [x for x in others if x.role not in PACK]
            if not cands:
                return None
            return (f"😈 **Malam ke-{rnd}** — siapa target pemerasanmu?\n"
                    "(Setelah ini pilih role yang harus dia akui.)",
                    self._sel_view("bm_target", cands, "😈 Pilih target"))
        if role == "blamer":
            if rnd != 1 or p.blame_targets or len(others) < 2:
                return None
            return (f"👉 **Malam pertama** — pilih **2 orang** yang harus mati agar kamu menang.",
                    self._sel_view("blamer", others, "👉 Pilih 2 target", min_values=2, max_values=2))
        return None

    async def send_night_action_dm(self, p: Player) -> bool:
        """Kirim prompt aksi malam via DM. Return True jika terkirim."""
        prompt = self.build_night_prompt(p)
        if not prompt:
            return False
        text, view = prompt
        try:
            await p.member.send(text, view=view)
            return True
        except (discord.Forbidden, discord.HTTPException):
            log.warning("DM aksi malam gagal ke %s", p.name)
            return False

    def _idle_text(self, role: str) -> str:
        return {
            "villager": "👤 Kamu warga biasa. Tidur nyenyak... 😴",
            "drunk": "🍺 Kamu masih mabuk... role aslimu terungkap di malam ke-3.",
            "hunter": "🏹 Hunter tidak punya aksi malam. Kekuatanmu aktif saat kamu mati.",
            "prince": "👑 Prince tidak punya aksi malam. Jika kamu di-vote, role-mu terungkap dan tidak ada yang dibakar!",
            "diseased": "🤢 Diseased tidak punya aksi malam. Jika kamu dimakan Werewolf, mereka akan sakit besok malamnya.",
            "cursed": "🌚 Kamu seperti warga biasa. Jika dimakan Werewolf, kamu akan berubah menjadi Werewolf!",
            "tanner": "🤡 Tanner tidak punya aksi malam. Buat desa me-bakar dirimu!",
            "werewolf": "🐺 Pack belum bisa memangsa (tunggu malam yang ditentukan host). Diskusikan strategi di channel pack!",
            "werewolf_kid": "🐺 Pack belum bisa memangsa (tunggu malam yang ditentukan host). Diskusikan strategi di channel pack!",
        }.get(role, "😴 Tidak ada aksi malam untukmu. Tidur nyenyak...")

    async def open_night_action(self, interaction: discord.Interaction):
        """Fallback: tombol 🔔 kirim ulang aksi malam ke DM (atau tampilkan langsung bila DM gagal)."""
        send = interaction.response.send_message
        p = self.players.get(interaction.user.id)
        if not p:
            return await send("Kamu tidak ikut game ini.", ephemeral=True)
        if not p.alive:
            return await send("💀 Kamu sudah mati.", ephemeral=True)
        if self.stage != "night":
            return await send("Bukan waktunya beraksi.", ephemeral=True)
        if p.id in self.acted:
            return await send("✅ Kamu sudah beraksi malam ini.", ephemeral=True)
        if p.role in PACK:
            if p.id in self.wolf_votes:
                return await send("🐺 Kamu sudah memilih — buka DM dariku untuk mengganti pilihan.", ephemeral=True)
        elif p.role in NIGHT_REQUIRED and not self.role_unlocked(p.role):
            r = ROLES[p.role]
            return await send(f"{r.emoji} Aksimu baru terbuka mulai malam ke-{self.role_start(p.role)}. Tidur dulu... 😴",
                              ephemeral=True)
        if await self.send_night_action_dm(p):
            return await send("📩 Cek DM kamu untuk beraksi!", ephemeral=True)
        # fallback terakhir: tampilkan langsung (ephemeral) bila DM tetap gagal
        prompt = self.build_night_prompt(p)
        if not prompt:
            return await send(self._idle_text(p.role), ephemeral=True)
        text, view = prompt
        await send(text, view=view, ephemeral=True)

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
            try:
                await interaction.response.edit_message(content="Aksi tidak valid.", view=None)
            except discord.HTTPException:
                pass
            return
        self.blackmails.append({"bm": p.id, "target": tid, "role": role_key, "complied": False})
        self.acted.add(p.id)
        self.bm_pending.pop(p.id, None)
        r = ROLES[role_key]
        try:
            await interaction.response.edit_message(
                content=f"😈 **{self.players[tid].name}** akan diperas: dia harus mengaku sebagai "
                        f"**{r.emoji} {r.name}** besok siang.",
                view=None)
        except discord.HTTPException:
            pass
        self.check_done()

    # ─────────────── HANDLER SEMUA SELECT MENU ───────────────
    async def handle_select(self, interaction: discord.Interaction, kind: str, values: list[str]):
        public = kind in ("vote", "hunter")

        async def reply(text: str):
            try:
                if public:
                    await interaction.response.send_message(text, ephemeral=True)
                else:
                    await interaction.response.edit_message(content=text, view=None)
            except discord.HTTPException:
                pass

        if self.state != "running":
            return await reply("Game sudah berakhir.")
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
            try:
                await self.wolf_channel.send(
                    f"🐺 **{p.name}** memilih " + ", ".join(f"**{t.name}**" for t in targets) + f"\n{tally}")
            except discord.HTTPException:
                pass

        elif kind in ("seer", "aura_seer", "guardian", "gravekeeper", "gunner", "sorcerer", "dpr", "sk"):
            need = {"seer": "seer", "aura_seer": "aura_seer", "guardian": "guardian",
                    "gravekeeper": "gravekeeper", "gunner": "gunner",
                    "sorcerer": "sorcerer", "dpr": "dpr", "sk": "serial_killer"}[kind]
            if not night or p.role != need or p.id in self.acted:
                return await reply("Aksi tidak valid.")
            if kind == "seer":
                if not target:
                    return await reply("Aksi tidak valid.")
                w = target.team == "wolf"
                text = (f"🔮 Hasil scan **{target.name}**: **{'W' if w else 'V'}**\n"
                        + ("🐺 Dia tim **Werewolf**!" if w else "🏘️ Dia **bukan** Werewolf."))
            elif kind == "aura_seer":
                if not target:
                    return await reply("Aksi tidak valid.")
                te, tn = TEAMS[target.team]
                text = f"👁️ Aura **{target.name}**: {te} **{tn}**"
            elif kind == "guardian":
                if not target:
                    return await reply("Aksi tidak valid.")
                if p.last_protected == target.id:
                    return await reply("Tidak boleh melindungi orang yang sama 2 malam berturut-turut.")
                p.last_protected = target.id
                self.guard_targets.append(target.id)
                text = f"🛡️ Kamu melindungi **{target.name}** malam ini."
            elif kind == "gravekeeper":
                if not target:
                    return await reply("Aksi tidak valid.")
                r = target.info
                te, tn = TEAMS[target.team]
                text = f"🪦 **{target.name}** adalah {r.emoji} **{r.name}** ({te} {tn})."
            elif kind == "gunner":
                if p.bullet <= 0:
                    return await reply("🔫 Pelurumu sudah habis.")
                if target:
                    p.bullet -= 1
                    self.gunner_shots.append(target.id)
                    text = f"🔫 💥 Kamu menembak **{target.name}**! (Pelurumu habis.)"
                else:
                    text = "🔫 Kamu memilih tidak menembak malam ini. Pelurumu masih tersimpan."
            elif kind == "sorcerer":
                if not target:
                    return await reply("Aksi tidak valid.")
                text = (f"🧿 **{target.name}** **ADALAH** Seer!" if target.role == "seer"
                        else f"🧿 **{target.name}** **bukan** Seer.")
            elif kind == "dpr":
                if not target:
                    return await reply("Aksi tidak valid.")
                if p.last_silenced == target.id:
                    return await reply("Tidak boleh membungkam orang yang sama 2 malam berturut-turut.")
                p.last_silenced = target.id
                self.silence_targets.append(target.id)
                text = f"🤐 Kamu akan membungkam **{target.name}** besok."
            else:  # sk
                if not target:
                    return await reply("Aksi tidak valid.")
                self.sk_targets.append(target.id)
                text = f"🔪 Kamu menyerang **{target.name}** malam ini."
            self.acted.add(p.id)
            await reply(text)

        elif kind == "bm_target":
            if not night or p.role != "blackmailer" or p.id in self.acted or not target:
                return await reply("Aksi tidak valid.")
            self.bm_pending[p.id] = target.id
            v = discord.ui.View(timeout=None)
            v.add_item(RoleClaimSelect(self))
            try:
                await interaction.response.edit_message(
                    content=f"😈 Target: **{target.name}**. Role apa yang harus dia akui di depan semua orang?", view=v)
            except discord.HTTPException:
                pass

        elif kind == "blamer":
            if not night or p.role != "blamer" or p.blame_targets or len(targets) != 2:
                return await reply("Aksi tidak valid.")
            p.blame_targets = [t.id for t in targets]
            await reply("👉 Targetmu: " + ", ".join(f"**{t.name}**" for t in targets)
                        + ". Pastikan mereka mati sebelum kamu!")

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
NIGHT_ROLE_CHOICES = [app_commands.Choice(
    name="🐺 Werewolf (bunuh pack)" if k == "werewolf" else f"{ROLES[k].emoji} {ROLES[k].name}", value=k)
    for k in NIGHT_ACTION_ROLES]


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


@ww.command(name="setting", description="Atur durasi fase & malam mulai beraksi (host, saat lobby)")
@app_commands.describe(malam="Durasi malam", diskusi="Durasi diskusi", voting="Durasi voting",
                       malam_bunuh="Werewolf boleh membunuh mulai malam ke-berapa",
                       malam_bunuh_sk="Serial Killer boleh membunuh mulai malam ke-berapa",
                       malam_bungkam="DPR boleh membungkam mulai malam ke-berapa")
async def ww_setting(interaction: discord.Interaction,
                     malam: Optional[app_commands.Range[int, 20, 300]] = None,
                     diskusi: Optional[app_commands.Range[int, 20, 600]] = None,
                     voting: Optional[app_commands.Range[int, 15, 300]] = None,
                     malam_bunuh: Optional[app_commands.Range[int, 1, 10]] = None,
                     malam_bunuh_sk: Optional[app_commands.Range[int, 1, 10]] = None,
                     malam_bungkam: Optional[app_commands.Range[int, 1, 10]] = None):
    g = await lobby_game_for_host(interaction)
    if not g:
        return
    g.night_time = malam or g.night_time
    g.day_time = diskusi or g.day_time
    g.vote_time = voting or g.vote_time
    if malam_bunuh:
        g.wolf_kill_night = malam_bunuh
    if malam_bunuh_sk:
        g.sk_kill_night = malam_bunuh_sk
    if malam_bungkam:
        g.dpr_silence_night = malam_bungkam
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
    cur = base.get(role.value, 0)
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


@role_group.command(name="night", description="Atur aksi role mulai malam ke-berapa (host, saat lobby)")
@app_commands.describe(role="Role yang diatur", malam="Aksi mulai aktif malam ke-berapa (1 = dari malam pertama)")
@app_commands.choices(role=NIGHT_ROLE_CHOICES)
async def role_night(interaction: discord.Interaction, role: app_commands.Choice[str],
                     malam: app_commands.Range[int, 1, 10]):
    g = await lobby_game_for_host(interaction)
    if not g:
        return
    key = role.value
    if key == "werewolf":
        g.wolf_kill_night = malam
    elif key == "serial_killer":
        g.sk_kill_night = malam
    elif key == "dpr":
        g.dpr_silence_night = malam
    elif malam == 1:
        g.role_start_night.pop(key, None)
    else:
        g.role_start_night[key] = malam
    label = "Werewolf (bunuh pack)" if key == "werewolf" else ROLES[key].name
    await interaction.response.send_message(f"⏰ Aksi **{label}** mulai aktif malam ke-**{malam}**.", ephemeral=True)
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
