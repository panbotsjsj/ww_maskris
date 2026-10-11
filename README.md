# 🐺 Werewolf Discord Bot (v2)

Bot Werewolf otomatis: lobby, pembagian role, fase malam/siang/voting, dan UI rahasia per role.

## Setup
1. Buat bot di https://discord.com/developers/applications → **Bot** → copy token.
2. Invite dengan scope `bot` + `applications.commands`, permission: **Manage Channels, Manage Roles, View Channels, Send Messages, Embed Links** (atau Administrator). Tidak butuh privileged intent.
3. ```bash
   pip install -r requirements.txt
   cp .env.example .env     # isi DISCORD_TOKEN (dan GUILD_ID agar command langsung muncul)
   python bot.py
   ```
   Python 3.10+.

## Command
| Command | Fungsi |
|---|---|
| `/ww create` | Buat lobby (pembuat = host) |
| `/ww start` / tombol **Start** | Mulai game |
| `/ww role add <role> [jumlah]` | Tambah role |
| `/ww role remove <role> [jumlah]` | Kurangi/hapus role (tanpa jumlah = hapus semua) |
| `/ww role auto` | Kembali ke komposisi otomatis |
| `/ww role list` | Daftar & deskripsi semua role |
| `/ww setting` | Atur durasi malam/diskusi/voting |
| `/ww skip` | Host melewati sisa diskusi |
| `/ww status` | Pemain hidup/mati |
| `/ww cancel` | Hentikan game |

## Daftar Role

### 🐺 Tim Werewolf
| Role | Kemampuan | Interaksi |
|---|---|---|
| 🐺 Werewolf | Tiap malam makan 1 orang bersama pack | Channel private `#werewolf-pack` + select menu korban |
| 🐺 Werewolf Kid | Seperti Werewolf. Jika **di-lynch (dibakar)**, malam berikutnya Werewolf makan **2 orang** | Sama; select menu boleh pilih 2 korban pada malam balas dendam |
| 🧿 Sorcerer | Tahu siapa Werewolf; tiap malam cek apakah target adalah **Seer** | Tombol *Lihat Role Saya* menampilkan daftar Werewolf; Aksi Malam → select ephemeral → jawaban Ya/Bukan |
| 😈 Blackmailer | Tiap malam pilih target + role yang harus dia akui. Target tidak mengaku sampai akhir siang → **dibunuh** | Aksi Malam → pilih target → pilih role. Siang: target klik **📩 Cek Pesan Rahasia** → tombol **Aku Mengaku** yang mengumumkan pengakuan di channel |
| 🤐 DPR | Tiap malam bungkam 1 orang (tak bisa chat sepanjang siang). Tidak boleh orang yang sama 2 malam beruntun | Aksi Malam → select ephemeral; pembungkaman diumumkan di pagi hari (tanpa menyebut siapa DPR-nya) |

### 🏘️ Tim Warga
| Role | Kemampuan |
|---|---|
| 👤 Villager | Tanpa kekuatan khusus |
| 🔮 Seer | Cek role asli 1 pemain tiap malam |
| 💉 Doctor | Lindungi 1 pemain dari serangan malam (Werewolf & Serial Killer); tidak boleh orang sama 2 malam beruntun |
| 🧙 Witch | 1 ramuan selamat + 1 racun; diberi tahu korban Werewolf |
| 🏹 Hunter | Saat mati, tembak 1 pemain |

### ⚖️ Netral
| Role | Kondisi menang |
|---|---|
| 🤡 Tanner | Mati karena **voting** → menang sendirian, game langsung selesai |
| 👉 Blamer | Malam pertama pilih 2 target; jika keduanya mati dan Blamer hidup → menang |
| 🔪 Serial Killer | Tiap malam bunuh 1 orang; menang jika jadi **orang terakhir yang bertahan** |

## Kondisi menang
- **Warga:** semua Tim Werewolf mati dan tidak ada Serial Killer hidup.
- **Tim Werewolf:** jumlah Tim Werewolf hidup ≥ sisa pemain hidup (dan tidak ada Serial Killer hidup).
- **Serial Killer:** satu-satunya yang hidup. **Tanner / Blamer:** sesuai tabel di atas (menang solo, game selesai).
- Semua pemain mati → tidak ada pemenang.

## Pembagian & balancing
- Komposisi **otomatis** menyesuaikan jumlah pemain (role baru muncul bertahap: Sorcerer ≥9, Tanner & Kid ≥10, Serial Killer ≥12, Blackmailer ≥12/14, Blamer ≥14, DPR ≥18).
- Host bebas menambah/menghapus role di lobby; sisa slot jadi Villager. Embed lobby memperingatkan jika komposisi tidak valid.

## Catatan desain (mudah diubah di `bot.py`)
- Hanya **Werewolf & Werewolf Kid** masuk channel `#werewolf-pack` dan memilih korban. Sorcerer, Blackmailer, DPR bergerak sendiri.
- Yang tahu siapa Werewolf: konstanta `KNOWS_WOLVES = {"sorcerer"}` — tambahkan `"blackmailer"`, `"dpr"` jika ingin mereka juga tahu.
- Aksi malam tiap role: `Game.open_night_action` (UI) dan `Game.handle_select` (efek). Resolusi kematian: `night_phase` bagian *resolusi*.
- Doctor melindungi dari serangan Werewolf & Serial Killer; racun Witch tidak bisa dicegah; Witch hanya menyelamatkan korban Werewolf.
- Satu game per channel; maksimal 24 pemain; state hanya di memori.
