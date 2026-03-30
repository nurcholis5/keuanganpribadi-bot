#!/usr/bin/env python3
"""
Bot Telegram - Pencatatan Keuangan Pribadi v5.1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Changelog dari v5.0:
  ✅ Migrasi AI dari Gemini ke Z.ai GLM-5.1 (lebih canggih)
  ✅ API key tidak lagi di-hardcode — wajib pakai environment variable
  ✅ Fix error Conflict: drop_pending_updates=True di run_polling
  ✅ Penanganan error API lebih detail (timeout, auth, dll)

Changelog dari v4:
  ✅ Integrasi AI — 4 mode AI via tombol keyboard:
     💬 Konsultasi Keuangan — tanya jawab bebas seputar keuangan
     🧠 Strategi Keuangan   — rencana menabung & investasi personal
     🛒 Saran Beli AI       — evaluasi pembelian lebih dalam dari /simulasi
     📊 Analisis AI         — AI baca data transaksimu & beri insight
  ✅ Setiap mode AI punya system prompt & karakter berbeda
  ✅ Mode AI aktif disimpan per user — bisa ganti kapan saja
  ✅ Tombol 🔙 Kembali ke Menu Utama di setiap mode AI
  ✅ Perintah /aimode untuk lihat mode AI aktif saat ini

Cara set environment variable:
  export TELEGRAM_TOKEN="token_bot_kamu"
  export GLM_API_KEY="api_key_dari_z.ai"
"""

import os
import re
import json
import logging
import httpx
from datetime import datetime, date, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import sqlite3

# ── Konfigurasi ───────────────────────────────────────────────────────────────
TOKEN   = os.environ.get("TELEGRAM_TOKEN")
GLM_KEY = os.environ.get("GLM_API_KEY")

if not TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN belum diset! Jalankan: export TELEGRAM_TOKEN='token_kamu'")
if not GLM_KEY:
    raise ValueError("❌ GLM_API_KEY belum diset! Jalankan: export GLM_API_KEY='apikey_kamu'")

# ── Definisi Mode AI ──────────────────────────────────────────────────────────
AI_MODES = {
    "konsultasi": {
        "label":  "💬 Konsultasi Keuangan",
        "desc":   "Tanya jawab bebas seputar keuangan pribadi",
        "system": (
            "Kamu adalah konsultan keuangan pribadi yang ramah, sabar, dan berbicara "
            "dalam Bahasa Indonesia yang santai namun profesional. "
            "Kamu membantu pengguna memahami konsep keuangan, menjawab pertanyaan "
            "seputar pengelolaan uang harian, hutang, pengeluaran, dan gaya hidup hemat. "
            "Berikan jawaban yang praktis, jelas, dan mudah dipahami orang awam. "
            "Gunakan contoh angka dalam Rupiah jika relevan. "
            "Jangan terlalu panjang — fokus dan to the point."
        ),
    },
    "strategi": {
        "label":  "🧠 Strategi Keuangan",
        "desc":   "Rencana menabung, investasi & tujuan finansial",
        "system": (
            "Kamu adalah perencana keuangan strategis yang berpengalaman. "
            "Kamu berbicara dalam Bahasa Indonesia yang lugas dan terstruktur. "
            "Tugasmu adalah membantu pengguna membuat rencana keuangan jangka pendek "
            "dan jangka panjang: menabung untuk tujuan tertentu, mulai investasi, "
            "memilih instrumen keuangan yang sesuai (deposito, reksa dana, emas, saham), "
            "dan membangun kebiasaan finansial yang sehat. "
            "Selalu tanyakan konteks: berapa pemasukan, pengeluaran rutin, dan tujuan finansialnya. "
            "Berikan langkah konkret yang bisa langsung diterapkan."
        ),
    },
    "saran_beli": {
        "label":  "🛒 Saran Beli AI",
        "desc":   "Evaluasi mendalam sebelum beli barang",
        "system": (
            "Kamu adalah advisor belanja cerdas yang membantu pengguna berpikir jernih "
            "sebelum membeli sesuatu. Berbicara dalam Bahasa Indonesia yang jujur dan supportif. "
            "Ketika pengguna menyebut barang yang ingin dibeli, evaluasi dari sudut: "
            "1) Apakah ini kebutuhan atau keinginan? "
            "2) Apakah ada alternatif lebih murah/hemat? "
            "3) Dampak ke keuangan bulanan jika dibeli sekarang vs ditunda. "
            "4) Apakah timing pembeliannya tepat? "
            "Bersikap jujur tapi tidak menghakimi. Berikan perspektif yang membantu, "
            "bukan sekadar melarang atau membolehkan."
        ),
    },
    "analisis": {
        "label":  "📊 Analisis Keuangan AI",
        "desc":   "AI analisis data transaksimu & beri insight",
        "system": (
            "Kamu adalah analis keuangan personal yang cermat dan analitis. "
            "Kamu akan menerima data transaksi pengeluaran pengguna dalam format terstruktur, "
            "lalu memberikan analisis mendalam: pola pengeluaran, kategori paling boros, "
            "perbandingan budget vs realisasi, tren positif/negatif, dan rekomendasi konkret. "
            "Berbicara dalam Bahasa Indonesia yang profesional namun mudah dipahami. "
            "Gunakan emoji secukupnya agar mudah dibaca di Telegram. "
            "Selalu akhiri dengan 2-3 saran actionable yang spesifik."
        ),
    },
}

AI_MODE_KEY = "ai_mode"

# ── Budget Default ─────────────────────────────────────────────────────────────
DEFAULT_BUDGET = {
    "makan":    945_000,
    "warkop":   220_000,
    "belanja":  200_000,
    "bpjs":      75_000,
    "tabungan": 560_000,
    "lainnya":  100_000,
}
DEFAULT_PEMASUKAN = 2_000_000

# ── Proporsi Alokasi Budget Otomatis ──────────────────────────────────────────
BPJS_TETAP = 75_000

PROPORSI_BUDGET = {
    "makan":     0.40,
    "warkop":    0.10,
    "transport": 0.10,
    "belanja":   0.12,
    "tabungan":  0.18,
    "lainnya":   0.10,
}

# ── Kata Kunci Kategori ────────────────────────────────────────────────────────
KATEGORI_KATA = {
    "makan": [
        "makan", "nasi", "lauk", "ayam", "ikan", "tempe", "tahu",
        "sayur", "warung", "padang", "siang", "malam", "sarapan",
        "beli bahan", "pasar", "telur", "kangkung", "bayam", "soto",
        "bakso", "mie", "indomie", "goreng", "rendang", "pecel"
    ],
    "warkop": [
        "warkop", "warung kopi", "kopi", "cafe", "nulis", "naskah",
        "konten", "ngopi", "wifi", "minum", "teh", "susu", "jus"
    ],
    "belanja": [
        "sabun", "shampo", "shampoo", "odol", "sikat gigi", "deterjen",
        "belanja", "indomaret", "alfamart", "minimarket", "toiletries",
        "gas", "lpg", "minyak goreng", "bumbu", "gula", "kopi sachet",
        "tisu", "pembalut", "popok"
    ],
    "bpjs": [
        "bpjs", "kesehatan", "iuran", "asuransi"
    ],
    "tabungan": [
        "tabung", "nabung", "tabungan", "simpan", "invest", "deposito"
    ],
    "transport": [
        "ojek", "gojek", "grab", "bensin", "bbm", "motor", "bus",
        "angkot", "parkir", "tol", "taksi", "pertalite", "pertamax"
    ],
}

# ── Ikon Kategori ──────────────────────────────────────────────────────────────
IKON = {
    "makan":    "🍽",
    "warkop":   "☕",
    "belanja":  "🛒",
    "bpjs":     "🏥",
    "tabungan": "💰",
    "transport":"🏍",
    "lainnya":  "📦",
}

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = "keuangan.db"

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaksi (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            tanggal   TEXT    NOT NULL,
            bulan     TEXT    NOT NULL,
            jumlah    INTEGER NOT NULL,
            kategori  TEXT    NOT NULL,
            catatan   TEXT,
            created   TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pengaturan (
            user_id     INTEGER NOT NULL,
            key         TEXT    NOT NULL,
            value       TEXT    NOT NULL,
            PRIMARY KEY (user_id, key)
        )
    """)
    con.commit()
    con.close()

# ── Pengaturan per User ───────────────────────────────────────────────────────
def get_budget(user_id: int) -> dict:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT key, value FROM pengaturan WHERE user_id=? AND key LIKE 'budget_%'",
        (user_id,)
    ).fetchall()
    con.close()
    if not rows:
        return DEFAULT_BUDGET.copy()
    result = DEFAULT_BUDGET.copy()
    for key, val in rows:
        kategori = key.replace("budget_", "")
        result[kategori] = int(val)
    return result

def set_budget(user_id: int, kategori: str, jumlah: int):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR REPLACE INTO pengaturan (user_id, key, value) VALUES (?,?,?)",
        (user_id, f"budget_{kategori}", str(jumlah))
    )
    con.commit()
    con.close()

def get_pemasukan(user_id: int) -> int:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT value FROM pengaturan WHERE user_id=? AND key='pemasukan'",
        (user_id,)
    ).fetchone()
    con.close()
    return int(row[0]) if row else DEFAULT_PEMASUKAN

def set_pemasukan(user_id: int, jumlah: int):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR REPLACE INTO pengaturan (user_id, key, value) VALUES (?,?,?)",
        (user_id, "pemasukan", str(jumlah))
    )
    con.commit()
    con.close()

# ── Operasi Transaksi ─────────────────────────────────────────────────────────
def simpan_transaksi(user_id: int, jumlah: int, kategori: str, catatan: str):
    hari_ini = date.today().isoformat()
    bulan    = date.today().strftime("%Y-%m")
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO transaksi (user_id, tanggal, bulan, jumlah, kategori, catatan) VALUES (?,?,?,?,?,?)",
        (user_id, hari_ini, bulan, jumlah, kategori, catatan)
    )
    con.commit()
    con.close()

def get_pengeluaran_bulan(user_id: int, bulan: str = None) -> dict:
    if not bulan:
        bulan = date.today().strftime("%Y-%m")
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT kategori, SUM(jumlah) FROM transaksi WHERE user_id=? AND bulan=? GROUP BY kategori",
        (user_id, bulan)
    ).fetchall()
    con.close()
    return {r[0]: r[1] for r in rows}

def get_transaksi_terakhir(user_id: int, limit: int = 5) -> list:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT tanggal, jumlah, kategori, catatan FROM transaksi WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    con.close()
    return rows

def get_transaksi_per_hari(user_id: int, hari: int = 7) -> list:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        """SELECT tanggal, SUM(jumlah) FROM transaksi
           WHERE user_id=? AND tanggal >= date('now', ?)
           GROUP BY tanggal ORDER BY tanggal ASC""",
        (user_id, f"-{hari} days")
    ).fetchall()
    con.close()
    return rows

def hapus_transaksi_terakhir(user_id: int) -> tuple:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, jumlah, kategori, catatan FROM transaksi WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    if row:
        con.execute("DELETE FROM transaksi WHERE id=?", (row[0],))
        con.commit()
        con.close()
        return True, {"jumlah": row[1], "kategori": row[2], "catatan": row[3]}
    con.close()
    return False, None

def reset_bulan_ini(user_id: int) -> int:
    bulan = date.today().strftime("%Y-%m")
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "DELETE FROM transaksi WHERE user_id=? AND bulan=?",
        (user_id, bulan)
    )
    deleted = cur.rowcount
    con.commit()
    con.close()
    return deleted

# ── Helper ────────────────────────────────────────────────────────────────────
def format_rupiah(n: int) -> str:
    return f"Rp {n:,}".replace(",", ".")

def parse_angka(teks: str) -> int | None:
    teks = teks.lower().strip()
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:jt|juta)', teks)
    if m:
        return int(float(m.group(1).replace(",", ".")) * 1_000_000)
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:k|rb|ribu)', teks)
    if m:
        return int(float(m.group(1).replace(",", ".")) * 1_000)
    m = re.search(r'\b(\d{3,9})\b', teks.replace(".", "").replace(",", ""))
    if m:
        return int(m.group(1))
    return None

def parse_pesan(teks: str):
    jumlah = parse_angka(teks)
    if not jumlah or jumlah < 100 or jumlah > 100_000_000:
        return None
    return jumlah, teks.strip()

def tebak_kategori(teks: str) -> str:
    teks_lower = teks.lower()
    for kategori, kata_list in KATEGORI_KATA.items():
        for kata in kata_list:
            if kata in teks_lower:
                return kategori
    return "lainnya"

def bar_progress(persen: float, lebar: int = 10) -> str:
    filled = min(int(persen / 100 * lebar), lebar)
    return "█" * filled + "░" * (lebar - filled)

def cek_peringatan(user_id: int, kategori: str, total_bulan: dict) -> str | None:
    spent  = total_bulan.get(kategori, 0)
    budget = get_budget(user_id).get(kategori, 0)
    if budget == 0:
        return None
    persen = spent / budget * 100
    if persen >= 100:
        return f"🚨 Budget *{kategori}* sudah HABIS!\n({format_rupiah(spent)} / {format_rupiah(budget)})"
    if persen >= 80:
        return f"⚠️ Budget *{kategori}* sudah {persen:.0f}% terpakai — hampir habis!"
    return None

def hitung_alokasi_otomatis(pemasukan: int) -> dict:
    sisa_setelah_bpjs = max(pemasukan - BPJS_TETAP, 0)
    hasil = {"bpjs": BPJS_TETAP}
    for kategori, proporsi in PROPORSI_BUDGET.items():
        hasil[kategori] = int(sisa_setelah_bpjs * proporsi)
    return hasil

def format_alokasi_preview(pemasukan: int, alokasi: dict) -> str:
    total_alokasi       = sum(alokasi.values())
    sisa_tak_teralokasi = pemasukan - total_alokasi

    teks  = f"📐 *Saran Alokasi Budget Otomatis*\n"
    teks += f"💵 Pemasukan: *{format_rupiah(pemasukan)}*\n"
    teks += f"{'─' * 28}\n\n"

    urutan = ["makan", "warkop", "transport", "belanja", "bpjs", "tabungan", "lainnya"]
    label_extra = {
        "makan":     "makan harian",
        "warkop":    "kerja di warkop",
        "transport": "ojek / bensin",
        "belanja":   "kebutuhan rumah",
        "bpjs":      "iuran tetap BPJS ✱",
        "tabungan":  "tabungan / investasi",
        "lainnya":   "tak terduga / hiburan",
    }
    for kat in urutan:
        if kat not in alokasi:
            continue
        jml    = alokasi[kat]
        ikon   = IKON.get(kat, "•")
        persen = (jml / pemasukan * 100) if pemasukan > 0 else 0
        extra  = label_extra.get(kat, "")
        teks  += f"{ikon} *{kat.capitalize()}*  _{extra}_\n"
        teks  += f"   → *{format_rupiah(jml)}*  ({persen:.1f}%)\n\n"

    teks += f"{'─' * 28}\n"
    teks += f"📊 Total dialokasi : *{format_rupiah(total_alokasi)}*\n"
    if sisa_tak_teralokasi > 0:
        teks += f"💚 Sisa cadangan   : *{format_rupiah(sisa_tak_teralokasi)}*\n"
    teks += f"\n✱ _BPJS selalu tetap Rp 75.000/bulan_\n\n"
    teks += "Terapkan alokasi ini sebagai budget kamu?"
    return teks

# ── Keyboard ──────────────────────────────────────────────────────────────────
def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Ringkasan Bulan Ini")],
        [KeyboardButton("📋 Transaksi Terakhir"), KeyboardButton("🗑 Hapus Terakhir")],
        [KeyboardButton("📈 Statistik 7 Hari"),   KeyboardButton("📤 Export Laporan")],
        [KeyboardButton("🧮 Simulasi Beli"),       KeyboardButton("📐 Atur Budget Otomatis")],
        [KeyboardButton("🤖 Menu AI Keuangan")],
        [KeyboardButton("⚙️ Pengaturan"),          KeyboardButton("💡 Cara Pakai")],
    ], resize_keyboard=True)

def ai_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💬 Konsultasi Keuangan"), KeyboardButton("🧠 Strategi Keuangan")],
        [KeyboardButton("🛒 Saran Beli AI"),        KeyboardButton("📊 Analisis Keuangan AI")],
        [KeyboardButton("🔙 Kembali ke Menu Utama")],
    ], resize_keyboard=True)

def konfirmasi_keyboard(action: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Ya, lanjutkan", callback_data=f"confirm_{action}"),
            InlineKeyboardButton("❌ Batal",         callback_data="cancel"),
        ]
    ])

# ── GLM-5.1 AI Integration ────────────────────────────────────────────────────
async def tanya_claude(system_prompt: str, user_message: str) -> str:
    """
    Kirim pesan ke Z.ai GLM-5.1 API dan kembalikan responnya.
    Menggunakan OpenAI-compatible endpoint dari Z.ai.
    """
    url = "https://api.z.ai/api/paas/v4/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GLM_KEY}"
    }
    payload = {
        "model": "glm-5.1",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message}
        ],
        "max_tokens": 1024,
        "temperature": 0.7,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        return "⏱ Maaf, koneksi ke AI timeout. Coba lagi sebentar ya."
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 401:
            return "❌ API Key tidak valid. Pastikan GLM_API_KEY sudah benar."
        elif status == 429:
            return "⚠️ Terlalu banyak request. Tunggu sebentar lalu coba lagi."
        else:
            logging.error(f"GLM HTTP error {status}: {e}")
            return f"❌ Gagal menghubungi AI (error {status}). Coba lagi nanti."
    except Exception as e:
        logging.error(f"GLM API error: {e}")
        return "❌ Gagal menghubungi AI. Pastikan GLM_API_KEY sudah diset dengan benar."

def buat_konteks_keuangan(uid: int) -> str:
    pemasukan    = get_pemasukan(uid)
    bulan_ini    = date.today().strftime("%Y-%m")
    total_bulan  = get_pengeluaran_bulan(uid, bulan_ini)
    budget_user  = get_budget(uid)
    total_keluar = sum(total_bulan.values())
    sisa         = pemasukan - total_keluar
    label_bulan  = date.today().strftime("%B %Y")

    baris = [f"=== DATA KEUANGAN USER ({label_bulan}) ==="]
    baris.append(f"Pemasukan bulanan : Rp {pemasukan:,}")
    baris.append(f"Total pengeluaran : Rp {total_keluar:,}")
    baris.append(f"Sisa budget       : Rp {sisa:,}")
    baris.append("")
    baris.append("Pengeluaran per kategori:")
    for kat, budget in budget_user.items():
        spent  = total_bulan.get(kat, 0)
        persen = (spent / budget * 100) if budget > 0 else 0
        baris.append(f"  {kat:<12}: Rp {spent:>10,} / budget Rp {budget:>10,} ({persen:.0f}%)")
    baris.append("=" * 40)
    return "\n".join(baris)

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    nama = update.effective_user.first_name
    await update.message.reply_text(
        f"Halo *{nama}*! 👋\n\n"
        "Aku adalah bot pencatat keuangan pribadimu *v5.1*\n\n"
        "📌 *Cara catat pengeluaran:*\n"
        "`makan siang 15000`\n"
        "`warkop nulis 27.5rb`\n"
        "`beli ayam 40k`\n\n"
        "🤖 *Fitur AI (GLM-5.1):*\n"
        "• 💬 Konsultasi keuangan bebas\n"
        "• 🧠 Strategi menabung & investasi\n"
        "• 🛒 Saran beli barang lebih mendalam\n"
        "• 📊 Analisis laporan bulananmu\n\n"
        "Ketuk *🤖 Menu AI Keuangan* untuk mulai! 👇",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def cara_pakai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💡 *Cara Pakai Bot Keuangan v5.1*\n\n"
        "*📝 Format input pengeluaran:*\n"
        "`15000 makan siang warung padang`\n"
        "`27.5rb warkop nulis naskah`\n"
        "`40k beli ayam 1kg`\n"
        "`1.5jt bayar sewa kos`\n\n"
        "*🧮 Simulasi Pembelian Barang:*\n"
        "`/simulasi baju 250000`\n"
        "`/simulasi sepatu 500rb`\n"
        "`/simulasi tas 1.2jt`\n\n"
        "*🏷 Kategori otomatis:*\n"
        "🍽 makan — warung, nasi, lauk, pasar\n"
        "☕ warkop — kopi, cafe, nulis, ngopi\n"
        "🛒 belanja — sabun, gas, indomaret\n"
        "🏥 bpjs — iuran, asuransi\n"
        "💰 tabungan — nabung, simpan\n"
        "🏍 transport — ojek, gojek, bensin\n"
        "📦 lainnya — sisanya\n\n"
        "*⚙️ Perintah tersedia:*\n"
        "`/aturbudget 2500000` — auto-bagi budget\n"
        "`/simulasi baju 250000` — cek worth it\n"
        "`/setbudget makan 1000000` — ubah budget\n"
        "`/setpemasukan 3000000` — ubah pemasukan\n"
        "`/bulan 2026-02` — ringkasan bulan lalu\n"
        "`/reset` — hapus semua data bulan ini\n"
        "`/aimode` — cek mode AI aktif",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def aturbudget_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pemasukan = get_pemasukan(update.effective_user.id)
    await update.message.reply_text(
        "📐 *Atur Budget Otomatis*\n\n"
        "Bot akan menghitung pembagian budget ideal berdasarkan pemasukanmu.\n\n"
        "*Format:* `/aturbudget <pemasukan>`\n\n"
        "*Contoh:*\n"
        "`/aturbudget 2000000`\n"
        "`/aturbudget 2.5jt`\n\n"
        f"💡 Pemasukan kamu saat ini: *{format_rupiah(pemasukan)}*\n"
        f"Coba: `/aturbudget {pemasukan}`",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def aturbudget_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = ctx.args

    if not args:
        await update.message.reply_text(
            "❌ Masukkan nominal pemasukan.\n\n"
            "Contoh: `/aturbudget 2500000` atau `/aturbudget 2.5jt`",
            parse_mode="Markdown"
        )
        return

    pemasukan = parse_angka(args[0])
    if not pemasukan or pemasukan < 100_000:
        await update.message.reply_text(
            "❌ Nominal tidak valid atau terlalu kecil (min Rp 100.000).\n"
            "Contoh: `2500000`, `2.5jt`, `3jt`",
            parse_mode="Markdown"
        )
        return

    alokasi = hitung_alokasi_otomatis(pemasukan)
    preview = format_alokasi_preview(pemasukan, alokasi)

    ctx.user_data["pending_alokasi"]   = alokasi
    ctx.user_data["pending_pemasukan"] = pemasukan

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Terapkan Semua", callback_data="alokasi_terapkan"),
            InlineKeyboardButton("❌ Batal",          callback_data="cancel"),
        ],
        [
            InlineKeyboardButton("✏️ Ubah Manual (via /setbudget)", callback_data="alokasi_manual"),
        ]
    ])

    await update.message.reply_text(preview, parse_mode="Markdown", reply_markup=keyboard)

async def ringkasan(update: Update, ctx: ContextTypes.DEFAULT_TYPE, bulan: str = None):
    uid         = update.effective_user.id
    bulan_query = bulan or date.today().strftime("%Y-%m")
    try:
        label = datetime.strptime(bulan_query, "%Y-%m").strftime("%B %Y")
    except ValueError:
        await update.message.reply_text("❌ Format bulan salah. Contoh: `2026-02`", parse_mode="Markdown")
        return

    total_bulan  = get_pengeluaran_bulan(uid, bulan_query)
    total_keluar = sum(total_bulan.values())
    pemasukan    = get_pemasukan(uid)
    budget_user  = get_budget(uid)
    sisa         = pemasukan - total_keluar

    teks = f"📊 *Ringkasan {label}*\n"
    teks += f"{'─' * 28}\n\n"

    for kategori, budget in budget_user.items():
        spent  = total_bulan.get(kategori, 0)
        persen = min(spent / budget * 100, 100) if budget > 0 else 0
        bar    = bar_progress(persen)
        ikon   = IKON.get(kategori, "•")
        teks += f"{ikon} *{kategori.capitalize()}*\n"
        teks += f"  {format_rupiah(spent)} / {format_rupiah(budget)}\n"
        teks += f"  {bar} {persen:.0f}%\n\n"

    for kat, jml in total_bulan.items():
        if kat not in budget_user:
            teks += f"📦 *{kat}*: {format_rupiah(jml)}\n"

    teks += f"{'─' * 28}\n"
    teks += f"💸 Total keluar : *{format_rupiah(total_keluar)}*\n"
    teks += f"💵 Pemasukan    : *{format_rupiah(pemasukan)}*\n"
    if sisa >= 0:
        teks += f"✅ Sisa / nabung: *{format_rupiah(sisa)}*\n"
    else:
        teks += f"🚨 Melebihi budget: *{format_rupiah(abs(sisa))}*!\n"

    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_keyboard())

async def transaksi_terakhir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    rows = get_transaksi_terakhir(uid, 5)
    if not rows:
        await update.message.reply_text("Belum ada transaksi yang tercatat.", reply_markup=main_keyboard())
        return
    teks = "📋 *5 Transaksi Terakhir*\n\n"
    for tgl, jml, kat, cat in rows:
        ikon = IKON.get(kat, "•")
        teks += f"{ikon} `{tgl}` — *{format_rupiah(jml)}*\n   _{cat}_\n\n"
    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_keyboard())

async def hapus_terakhir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    ok, det = hapus_transaksi_terakhir(uid)
    if ok:
        teks = (
            f"✅ *Transaksi terakhir dihapus:*\n\n"
            f"{IKON.get(det['kategori'], '•')} *{det['kategori'].capitalize()}*\n"
            f"💸 {format_rupiah(det['jumlah'])}\n"
            f"📝 _{det['catatan']}_"
        )
        await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_keyboard())
    else:
        await update.message.reply_text("Tidak ada transaksi yang bisa dihapus.", reply_markup=main_keyboard())

async def statistik(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    rows = get_transaksi_per_hari(uid, 7)

    if not rows:
        await update.message.reply_text(
            "Belum ada data transaksi dalam 7 hari terakhir.",
            reply_markup=main_keyboard()
        )
        return

    data    = {r[0]: r[1] for r in rows}
    max_val = max(data.values()) if data else 1

    teks = "📈 *Statistik 7 Hari Terakhir*\n\n"
    for i in range(6, -1, -1):
        tgl   = (date.today() - timedelta(days=i)).isoformat()
        label = datetime.strptime(tgl, "%Y-%m-%d").strftime("%a %d/%m")
        jml   = data.get(tgl, 0)
        persen = (jml / max_val * 100) if max_val > 0 else 0
        bar   = bar_progress(persen, 8)
        today_mark = " ← hari ini" if i == 0 else ""
        teks += f"`{label}` {bar}\n"
        teks += f"          {format_rupiah(jml)}{today_mark}\n\n"

    total_7 = sum(data.values())
    avg_7   = total_7 // max(len(data), 1)
    teks += f"{'─' * 28}\n"
    teks += f"📊 Total 7 hari : *{format_rupiah(total_7)}*\n"
    teks += f"📉 Rata-rata/hari: *{format_rupiah(avg_7)}*"

    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_keyboard())

async def export_laporan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid          = update.effective_user.id
    nama         = update.effective_user.first_name
    bulan_ini    = date.today().strftime("%Y-%m")
    label_bulan  = date.today().strftime("%B %Y")
    total_bulan  = get_pengeluaran_bulan(uid, bulan_ini)
    total_keluar = sum(total_bulan.values())
    pemasukan    = get_pemasukan(uid)
    budget_user  = get_budget(uid)
    sisa         = pemasukan - total_keluar
    sekarang     = datetime.now().strftime("%d %B %Y, %H:%M")

    teks  = f"╔{'═'*30}╗\n"
    teks += f"║  📊 LAPORAN KEUANGAN PRIBADI  ║\n"
    teks += f"╚{'═'*30}╝\n\n"
    teks += f"👤 Nama      : {nama}\n"
    teks += f"📅 Periode   : {label_bulan}\n"
    teks += f"🕐 Dicetak   : {sekarang}\n\n"
    teks += f"{'─'*32}\n"
    teks += f"{'KATEGORI':<12} {'PAKAI':>12} {'BUDGET':>12} {'%':>5}\n"
    teks += f"{'─'*32}\n"

    for kategori, budget in budget_user.items():
        spent  = total_bulan.get(kategori, 0)
        persen = (spent / budget * 100) if budget > 0 else 0
        teks += f"{kategori.capitalize():<12} {format_rupiah(spent):>12} {format_rupiah(budget):>12} {persen:>4.0f}%\n"

    teks += f"{'─'*32}\n"
    teks += f"{'TOTAL':<12} {format_rupiah(total_keluar):>12} {format_rupiah(pemasukan):>12}\n"
    teks += f"{'─'*32}\n\n"

    if sisa >= 0:
        teks += f"✅ Sisa / ditabung : {format_rupiah(sisa)}\n"
        persen_hemat = (sisa / pemasukan * 100) if pemasukan > 0 else 0
        teks += f"💡 Tingkat hemat   : {persen_hemat:.1f}%\n"
    else:
        teks += f"🚨 Melebihi anggaran: {format_rupiah(abs(sisa))}\n"

    teks += f"\n{'─'*32}\n"
    teks += f"📌 Dibuat otomatis oleh @{ctx.bot.username}"

    await update.message.reply_text(
        f"```\n{teks}\n```",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def pengaturan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid         = update.effective_user.id
    budget_user = get_budget(uid)
    pemasukan   = get_pemasukan(uid)

    teks  = "⚙️ *Pengaturan Kamu Saat Ini*\n\n"
    teks += f"💵 *Pemasukan bulanan:* {format_rupiah(pemasukan)}\n"
    teks += f"   Ubah: `/setpemasukan 3000000`\n\n"
    teks += f"🎯 *Budget per kategori:*\n"
    for kat, val in budget_user.items():
        ikon = IKON.get(kat, "•")
        teks += f"  {ikon} {kat.capitalize():<12}: {format_rupiah(val)}\n"
    teks += f"\n   Ubah: `/setbudget makan 1000000`"

    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_keyboard())

async def set_budget_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = ctx.args
    if len(args) != 2:
        await update.message.reply_text(
            "❌ Format: `/setbudget <kategori> <jumlah>`\n"
            "Contoh: `/setbudget makan 1000000`",
            parse_mode="Markdown"
        )
        return

    kategori = args[0].lower()
    jumlah   = parse_angka(args[1])
    if not jumlah:
        await update.message.reply_text("❌ Jumlah tidak valid. Contoh: `1000000` atau `1jt`", parse_mode="Markdown")
        return

    set_budget(uid, kategori, jumlah)
    await update.message.reply_text(
        f"✅ Budget *{kategori.capitalize()}* diubah ke *{format_rupiah(jumlah)}*",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def set_pemasukan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = ctx.args
    if len(args) != 1:
        await update.message.reply_text(
            "❌ Format: `/setpemasukan <jumlah>`\nContoh: `/setpemasukan 3000000`",
            parse_mode="Markdown"
        )
        return
    jumlah = parse_angka(args[0])
    if not jumlah:
        await update.message.reply_text("❌ Jumlah tidak valid.", parse_mode="Markdown")
        return
    set_pemasukan(uid, jumlah)
    await update.message.reply_text(
        f"✅ Pemasukan bulanan diubah ke *{format_rupiah(jumlah)}*",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def bulan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "📅 Lihat ringkasan bulan tertentu.\nContoh: `/bulan 2026-02`",
            parse_mode="Markdown"
        )
        return
    await ringkasan(update, ctx, bulan=ctx.args[0])

async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    label = date.today().strftime("%B %Y")
    await update.message.reply_text(
        f"⚠️ *Hapus semua data {label}?*\n\nTindakan ini tidak bisa dibatalkan.",
        parse_mode="Markdown",
        reply_markup=konfirmasi_keyboard("reset")
    )

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    if data == "confirm_reset":
        deleted = reset_bulan_ini(uid)
        await query.edit_message_text(
            f"✅ *{deleted} transaksi* bulan ini berhasil dihapus.",
            parse_mode="Markdown"
        )
    elif data == "cancel":
        await query.edit_message_text("❌ Dibatalkan.")

    elif data == "alokasi_terapkan":
        alokasi   = ctx.user_data.get("pending_alokasi")
        pemasukan = ctx.user_data.get("pending_pemasukan")
        if not alokasi or not pemasukan:
            await query.edit_message_text("❌ Data alokasi tidak ditemukan. Coba `/aturbudget` lagi.", parse_mode="Markdown")
            return

        set_pemasukan(uid, pemasukan)
        for kat, jml in alokasi.items():
            set_budget(uid, kat, jml)

        teks  = "✅ *Budget berhasil diterapkan!*\n\n"
        teks += f"💵 Pemasukan : *{format_rupiah(pemasukan)}*\n"
        teks += f"{'─' * 24}\n"
        urutan = ["makan", "warkop", "transport", "belanja", "bpjs", "tabungan", "lainnya"]
        for kat in urutan:
            if kat in alokasi:
                ikon = IKON.get(kat, "•")
                teks += f"{ikon} {kat.capitalize():<12}: *{format_rupiah(alokasi[kat])}*\n"
        teks += f"\n💡 Gunakan `/setbudget` untuk ubah kategori tertentu."

        ctx.user_data.pop("pending_alokasi",   None)
        ctx.user_data.pop("pending_pemasukan", None)
        await query.edit_message_text(teks, parse_mode="Markdown")

    elif data == "alokasi_manual":
        alokasi   = ctx.user_data.get("pending_alokasi", {})
        pemasukan = ctx.user_data.get("pending_pemasukan", 0)
        teks  = "✏️ *Ubah Manual via /setbudget*\n\n"
        teks += "Salin dan edit perintah di bawah:\n\n"
        urutan = ["makan", "warkop", "transport", "belanja", "bpjs", "tabungan", "lainnya"]
        for kat in urutan:
            if kat in alokasi:
                teks += f"`/setbudget {kat} {alokasi[kat]}`\n"
        teks += f"\n`/setpemasukan {pemasukan}`"
        await query.edit_message_text(teks, parse_mode="Markdown")

async def simulasi_beli_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧮 *Simulasi Beli Barang*\n\n"
        "Format: `/simulasi <nama barang> <harga>`\n\n"
        "*Contoh:*\n"
        "`/simulasi baju 250000`\n"
        "`/simulasi sepatu 500rb`\n"
        "`/simulasi tas 1.2jt`\n\n"
        "Bot akan menilai apakah pembelian worth it berdasarkan pemasukan & sisa budget. 💡",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def simulasi_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = ctx.args

    if not args or len(args) < 2:
        await update.message.reply_text(
            "❌ *Format kurang lengkap.*\n\n"
            "Contoh: `/simulasi baju 250000`\n"
            "Format: `/simulasi <nama barang> <harga>`",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        return

    harga_raw   = args[-1]
    nama_barang = " ".join(args[:-1]).strip()
    harga       = parse_angka(harga_raw)

    if not harga or harga < 1000:
        await update.message.reply_text(
            "❌ Harga tidak valid. Contoh: `250000`, `500rb`, `1.2jt`",
            parse_mode="Markdown"
        )
        return

    pemasukan    = get_pemasukan(uid)
    total_bulan  = get_pengeluaran_bulan(uid)
    total_keluar = sum(total_bulan.values())
    sisa_budget  = pemasukan - total_keluar

    persen_dari_pemasukan = (harga / pemasukan * 100) if pemasukan > 0 else 100
    persen_dari_sisa      = (harga / sisa_budget * 100) if sisa_budget > 0 else 100
    sisa_setelah_beli     = sisa_budget - harga

    if sisa_budget <= 0:
        verdict        = "🚨 *JANGAN DULU!*"
        verdict_detail = "Budget bulan ini sudah habis atau minus. Tunda pembelian ini."
        skor           = "❌❌❌"
    elif persen_dari_sisa >= 80:
        verdict        = "🚨 *BERISIKO TINGGI*"
        verdict_detail = "Pembelian ini akan menguras hampir seluruh sisa budgetmu bulan ini."
        skor           = "❌❌❌"
    elif persen_dari_sisa >= 50:
        verdict        = "⚠️ *PERTIMBANGKAN LAGI*"
        verdict_detail = "Bisa dibeli, tapi akan menyedot lebih dari setengah sisa budgetmu."
        skor           = "⚠️⚠️"
    elif persen_dari_pemasukan >= 20:
        verdict        = "⚠️ *CUKUP BESAR*"
        verdict_detail = "Harga ini cukup besar dibanding pemasukanmu. Pastikan memang perlu."
        skor           = "⚠️"
    elif persen_dari_sisa >= 20:
        verdict        = "✅ *AMAN, TAPI HEMAT*"
        verdict_detail = "Masih dalam batas wajar. Pastikan ini kebutuhan, bukan keinginan sesaat."
        skor           = "✅✅"
    else:
        verdict        = "✅ *WORTH IT!*"
        verdict_detail = "Pembelian ini aman dan tidak mengganggu keuanganmu bulan ini."
        skor           = "✅✅✅"

    teks  = f"🧮 *Simulasi Beli: {nama_barang.title()}*\n"
    teks += f"{'─' * 28}\n\n"
    teks += f"🏷 Harga barang     : *{format_rupiah(harga)}*\n\n"
    teks += f"📊 *Kondisi Keuangan Bulan Ini:*\n"
    teks += f"  💵 Pemasukan       : {format_rupiah(pemasukan)}\n"
    teks += f"  💸 Sudah keluar    : {format_rupiah(total_keluar)}\n"
    if sisa_budget >= 0:
        teks += f"  💚 Sisa budget     : {format_rupiah(sisa_budget)}\n\n"
    else:
        teks += f"  🔴 Defisit budget  : -{format_rupiah(abs(sisa_budget))}\n\n"

    teks += f"📈 *Analisis Pembelian:*\n"
    teks += f"  % dari pemasukan   : *{persen_dari_pemasukan:.1f}%*\n"
    if sisa_budget > 0:
        teks += f"  % dari sisa budget : *{persen_dari_sisa:.1f}%*\n"
        teks += f"  Sisa setelah beli  : *{format_rupiah(sisa_setelah_beli)}*\n\n"
    else:
        teks += f"\n"

    teks += f"{'─' * 28}\n"
    teks += f"🎯 *Verdict:* {verdict}\n"
    teks += f"   {skor}\n\n"
    teks += f"💬 _{verdict_detail}_\n\n"

    if sisa_setelah_beli < 0 or sisa_budget <= 0:
        teks += "💡 *Tips:* Tunggu bulan depan atau cek apakah ada pos yang bisa dikurangi."
    elif persen_dari_pemasukan >= 10:
        teks += "💡 *Tips:* Tanya dirimu — ini kebutuhan atau keinginan? Kalau keinginan, coba tunda 3 hari."
    else:
        teks += "💡 *Tips:* Kalau memang butuh, silakan beli! Jangan lupa catat pengeluarannya ya 😊"

    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_keyboard())

# ── AI Handlers ───────────────────────────────────────────────────────────────
async def menu_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    teks  = "🤖 *Menu AI Keuangan* _(GLM-5.1)_\n\n"
    teks += "Pilih mode AI yang ingin kamu gunakan:\n\n"
    for key, mode in AI_MODES.items():
        teks += f"{mode['label']}\n"
        teks += f"  _{mode['desc']}_\n\n"
    teks += "Ketuk tombol di bawah untuk mulai 👇"
    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=ai_keyboard())

async def set_ai_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE, mode_key: str):
    ctx.user_data[AI_MODE_KEY] = mode_key
    mode = AI_MODES[mode_key]
    contoh = {
        "konsultasi": "Bagaimana cara mulai menabung dengan gaji 2 juta?",
        "strategi":   "Aku mau beli motor 18 bulan lagi, harganya 20 juta. Gimana strateginya?",
        "saran_beli": "Aku mau beli HP baru seharga 3 juta, boleh gak ya?",
        "analisis":   "Tolong analisis pengeluaranku bulan ini",
    }
    teks  = f"{mode['label']} *aktif!* 🟢\n\n"
    teks += f"_{mode['desc']}_\n\n"
    teks += f"Langsung ketik pertanyaan atau permintaanmu.\n\n"
    teks += f"💡 *Contoh:*\n`{contoh.get(mode_key, '')}`\n\n"
    teks += "Ketuk *🔙 Kembali ke Menu Utama* untuk keluar dari mode AI."
    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=ai_keyboard())

async def proses_pesan_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    mode_key = ctx.user_data.get(AI_MODE_KEY)
    teks     = update.message.text.strip()

    if not mode_key or mode_key not in AI_MODES:
        return False

    mode          = AI_MODES[mode_key]
    system_prompt = mode["system"]

    if mode_key == "analisis":
        konteks      = buat_konteks_keuangan(uid)
        user_message = f"{konteks}\n\nPermintaan user: {teks}"
    else:
        user_message = teks

    await update.message.reply_text("⏳ _AI sedang memproses..._", parse_mode="Markdown")

    balasan = await tanya_claude(system_prompt, user_message)

    if len(balasan) > 4000:
        balasan = balasan[:4000] + "\n\n_...jawaban dipotong karena terlalu panjang_"

    await update.message.reply_text(
        f"🤖 *{mode['label']}*\n{'─' * 24}\n\n{balasan}",
        parse_mode="Markdown",
        reply_markup=ai_keyboard()
    )
    return True

async def aimode_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mode_key = ctx.user_data.get(AI_MODE_KEY)
    if not mode_key:
        await update.message.reply_text(
            "ℹ️ Tidak ada mode AI yang aktif saat ini.\n"
            "Ketuk *🤖 Menu AI Keuangan* untuk mulai.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
    else:
        mode = AI_MODES[mode_key]
        await update.message.reply_text(
            f"🟢 Mode AI aktif: *{mode['label']}*\n_{mode['desc']}_",
            parse_mode="Markdown",
            reply_markup=ai_keyboard()
        )

async def catat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    teks = update.message.text.strip()

    routing = {
        "📊 Ringkasan Bulan Ini":      ringkasan,
        "📋 Transaksi Terakhir":       transaksi_terakhir,
        "🗑 Hapus Terakhir":           hapus_terakhir,
        "📈 Statistik 7 Hari":        statistik,
        "📤 Export Laporan":           export_laporan,
        "🧮 Simulasi Beli":            simulasi_beli_info,
        "📐 Atur Budget Otomatis":     aturbudget_info,
        "🤖 Menu AI Keuangan":         menu_ai,
        "⚙️ Pengaturan":               pengaturan,
        "💡 Cara Pakai":               cara_pakai,
    }
    if teks in routing:
        await routing[teks](update, ctx)
        return

    ai_routing = {
        "💬 Konsultasi Keuangan":  "konsultasi",
        "🧠 Strategi Keuangan":    "strategi",
        "🛒 Saran Beli AI":        "saran_beli",
        "📊 Analisis Keuangan AI": "analisis",
    }
    if teks in ai_routing:
        await set_ai_mode(update, ctx, ai_routing[teks])
        return

    if teks == "🔙 Kembali ke Menu Utama":
        ctx.user_data.pop(AI_MODE_KEY, None)
        await update.message.reply_text("✅ Kembali ke menu utama.", reply_markup=main_keyboard())
        return

    if ctx.user_data.get(AI_MODE_KEY):
        await proses_pesan_ai(update, ctx)
        return

    hasil = parse_pesan(teks)
    if not hasil:
        await update.message.reply_text(
            "⚠️ Tidak bisa membaca nominal.\n\n"
            "Format yang diterima:\n"
            "`makan siang 15000`\n"
            "`27.5rb warkop nulis`\n"
            "`40k beli ayam`\n"
            "`1.5jt bayar sewa`\n\n"
            "Atau ketuk *🤖 Menu AI Keuangan* untuk konsultasi.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        return

    jumlah, catatan = hasil
    kategori = tebak_kategori(catatan)
    simpan_transaksi(uid, jumlah, kategori, catatan)

    total_bulan = get_pengeluaran_bulan(uid)
    peringatan  = cek_peringatan(uid, kategori, total_bulan)
    ikon        = IKON.get(kategori, "📦")

    balasan = (
        f"✅ *Tercatat!*\n\n"
        f"{ikon} Kategori : *{kategori.capitalize()}*\n"
        f"💸 Jumlah  : *{format_rupiah(jumlah)}*\n"
        f"📝 Catatan : _{catatan}_\n"
        f"📅 Tanggal : {date.today().strftime('%d %B %Y')}\n"
    )
    if peringatan:
        balasan += f"\n{peringatan}"

    await update.message.reply_text(balasan, parse_mode="Markdown", reply_markup=main_keyboard())


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("aimode",       aimode_cmd))
    app.add_handler(CommandHandler("aturbudget",   aturbudget_cmd))
    app.add_handler(CommandHandler("simulasi",     simulasi_cmd))
    app.add_handler(CommandHandler("setbudget",    set_budget_cmd))
    app.add_handler(CommandHandler("setpemasukan", set_pemasukan_cmd))
    app.add_handler(CommandHandler("bulan",        bulan_cmd))
    app.add_handler(CommandHandler("reset",        reset_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, catat))

    print("✅ Bot Keuangan v5.1 berjalan...")
    print(f"🤖 AI Engine : GLM-5.1 (Z.ai)")

    # ✅ drop_pending_updates=True — fix error Conflict
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
