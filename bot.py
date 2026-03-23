#!/usr/bin/env python3
"""
Bot Telegram - Pencatatan Keuangan Pribadi v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Changelog dari v1:
  ✅ Multi-user: setiap user punya data & budget sendiri
  ✅ Input fleksibel: support "15k", "15rb", "1.5jt", "15ribu"
  ✅ Perintah /setbudget  — atur budget kategori sendiri
  ✅ Perintah /setpemasukan — atur total pemasukan sendiri
  ✅ Perintah /statistik  — ringkasan 7 hari terakhir per hari
  ✅ Perintah /export     — laporan teks lengkap siap screenshot/salin
  ✅ Perintah /reset      — reset semua data bulan ini (konfirmasi dulu)
  ✅ Perintah /bulan      — lihat ringkasan bulan lalu
  ✅ Inline keyboard untuk konfirmasi hapus & reset
  ✅ Format angka lebih bersih (1.500 bukan 1,500)
  ✅ Logging transaksi lebih detail
"""

import os
import re
import logging
from datetime import datetime, date, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import sqlite3

# ── Konfigurasi ───────────────────────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_TOKEN", "ISI_TOKEN_BOT_KAMU_DISINI")

# Budget default untuk user baru (bisa di-override per user via /setbudget)
DEFAULT_BUDGET = {
    "makan":    945_000,
    "warkop":   220_000,
    "belanja":  200_000,
    "bpjs":      75_000,
    "tabungan": 560_000,
    "lainnya":  100_000,
}
DEFAULT_PEMASUKAN = 2_000_000

# Kata kunci kategori (sama seperti v1, ditambah beberapa)
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

# Ikon per kategori
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

    # Tabel transaksi (sekarang menyimpan user_id)
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

    # Tabel pengaturan per user (budget & pemasukan)
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
    """Ambil total pengeluaran per hari untuk N hari terakhir."""
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
    """Hapus transaksi terakhir. Return (True, detail) atau (False, None)."""
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
    """Hapus semua transaksi bulan ini. Return jumlah baris terhapus."""
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
    """Format angka ke Rp 1.500.000"""
    return f"Rp {n:,}".replace(",", ".")

def parse_angka(teks: str) -> int | None:
    """
    Parse angka dari teks dengan dukungan:
      15000, 15.000, 15,000
      15k, 15K
      15rb, 15ribu
      1.5jt, 1jt, 1.5 juta
    """
    teks = teks.lower().strip()

    # Cek suffix: jt / juta
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:jt|juta)', teks)
    if m:
        return int(float(m.group(1).replace(",", ".")) * 1_000_000)

    # Cek suffix: k / rb / ribu
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:k|rb|ribu)', teks)
    if m:
        return int(float(m.group(1).replace(",", ".")) * 1_000)

    # Angka biasa (bersihkan titik/koma sebagai pemisah ribuan)
    m = re.search(r'\b(\d{3,9})\b', teks.replace(".", "").replace(",", ""))
    if m:
        return int(m.group(1))

    return None

def parse_pesan(teks: str):
    """
    Parsing input bebas. Return (jumlah, catatan) atau None.
    Contoh valid:
      makan 15000 | 15k warkop nulis | beli ayam 40rb | gas 22.000
    """
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

# ── Keyboard ──────────────────────────────────────────────────────────────────
def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Ringkasan Bulan Ini")],
        [KeyboardButton("📋 Transaksi Terakhir"), KeyboardButton("🗑 Hapus Terakhir")],
        [KeyboardButton("📈 Statistik 7 Hari"),   KeyboardButton("📤 Export Laporan")],
        [KeyboardButton("⚙️ Pengaturan"),          KeyboardButton("💡 Cara Pakai")],
    ], resize_keyboard=True)

def konfirmasi_keyboard(action: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Ya, lanjutkan", callback_data=f"confirm_{action}"),
            InlineKeyboardButton("❌ Batal",         callback_data="cancel"),
        ]
    ])

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    nama = update.effective_user.first_name
    await update.message.reply_text(
        f"Halo *{nama}*! 👋\n\n"
        "Aku adalah bot pencatat keuangan pribadimu *v2.0*\n\n"
        "📌 *Cara catat pengeluaran:*\n"
        "`makan siang 15000`\n"
        "`warkop nulis 27.5rb`\n"
        "`beli ayam 40k`\n"
        "`gas lpg 22000`\n\n"
        "✨ *Fitur baru v2:*\n"
        "• Input `15k`, `15rb`, `1.5jt` sekarang didukung\n"
        "• Statistik harian 7 hari terakhir\n"
        "• Export laporan teks lengkap\n"
        "• Atur budget & pemasukan sendiri\n"
        "• Mendukung banyak pengguna\n\n"
        "Ketuk tombol di bawah untuk mulai! 👇",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def cara_pakai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💡 *Cara Pakai Bot Keuangan v2*\n\n"
        "*📝 Format input:*\n"
        "`15000 makan siang warung padang`\n"
        "`27.5rb warkop nulis naskah`\n"
        "`40k beli ayam 1kg`\n"
        "`1.5jt bayar sewa kos`\n\n"
        "*🏷 Kategori otomatis:*\n"
        "🍽 makan — warung, nasi, lauk, pasar\n"
        "☕ warkop — kopi, cafe, nulis, ngopi\n"
        "🛒 belanja — sabun, gas, indomaret\n"
        "🏥 bpjs — iuran, asuransi\n"
        "💰 tabungan — nabung, simpan\n"
        "🏍 transport — ojek, gojek, bensin\n"
        "📦 lainnya — sisanya\n\n"
        "*⚙️ Perintah tersedia:*\n"
        "`/setbudget makan 1000000` — ubah budget kategori\n"
        "`/setpemasukan 3000000` — ubah total pemasukan\n"
        "`/bulan 2026-02` — lihat ringkasan bulan lalu\n"
        "`/reset` — hapus semua data bulan ini\n\n"
        "*🎛 Tombol:*\n"
        "📊 Ringkasan — pengeluaran bulan ini\n"
        "📋 Transaksi Terakhir — 5 terbaru\n"
        "🗑 Hapus Terakhir — batalkan input terakhir\n"
        "📈 Statistik 7 Hari — tren harian\n"
        "📤 Export — ringkasan teks lengkap\n"
        "⚙️ Pengaturan — lihat budget & pemasukan",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

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

    # Kategori tak terduga (di luar budget)
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
    """Tampilkan pengeluaran per hari selama 7 hari terakhir."""
    uid  = update.effective_user.id
    rows = get_transaksi_per_hari(uid, 7)

    if not rows:
        await update.message.reply_text(
            "Belum ada data transaksi dalam 7 hari terakhir.",
            reply_markup=main_keyboard()
        )
        return

    data = {r[0]: r[1] for r in rows}
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
    """Export ringkasan bulan ini sebagai teks panjang."""
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
        kat_label = kategori.capitalize()
        teks += f"{kat_label:<12} {format_rupiah(spent):>12} {format_rupiah(budget):>12} {persen:>4.0f}%\n"

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
    """Contoh: /setbudget makan 1000000"""
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
    """Contoh: /setpemasukan 3000000"""
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
    """Contoh: /bulan 2026-02"""
    if not ctx.args:
        await update.message.reply_text(
            "📅 Lihat ringkasan bulan tertentu.\n"
            "Contoh: `/bulan 2026-02`",
            parse_mode="Markdown"
        )
        return
    await ringkasan(update, ctx, bulan=ctx.args[0])

async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Minta konfirmasi sebelum reset data bulan ini."""
    label = date.today().strftime("%B %Y")
    await update.message.reply_text(
        f"⚠️ *Hapus semua data {label}?*\n\n"
        "Tindakan ini tidak bisa dibatalkan.",
        parse_mode="Markdown",
        reply_markup=konfirmasi_keyboard("reset")
    )

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = query.from_user.id
    data  = query.data

    if data == "confirm_reset":
        deleted = reset_bulan_ini(uid)
        await query.edit_message_text(
            f"✅ *{deleted} transaksi* bulan ini berhasil dihapus.",
            parse_mode="Markdown"
        )
    elif data == "cancel":
        await query.edit_message_text("❌ Dibatalkan.")

async def catat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    teks = update.message.text.strip()

    # Routing tombol keyboard
    routing = {
        "📊 Ringkasan Bulan Ini":  ringkasan,
        "📋 Transaksi Terakhir":   transaksi_terakhir,
        "🗑 Hapus Terakhir":       hapus_terakhir,
        "📈 Statistik 7 Hari":    statistik,
        "📤 Export Laporan":       export_laporan,
        "⚙️ Pengaturan":           pengaturan,
        "💡 Cara Pakai":           cara_pakai,
    }
    if teks in routing:
        await routing[teks](update, ctx)
        return

    hasil = parse_pesan(teks)
    if not hasil:
        await update.message.reply_text(
            "⚠️ Tidak bisa membaca nominal.\n\n"
            "Format yang diterima:\n"
            "`makan siang 15000`\n"
            "`27.5rb warkop nulis`\n"
            "`40k beli ayam`\n"
            "`1.5jt bayar sewa`",
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

    app.add_handler(CommandHandler("start",         start))
    app.add_handler(CommandHandler("setbudget",     set_budget_cmd))
    app.add_handler(CommandHandler("setpemasukan",  set_pemasukan_cmd))
    app.add_handler(CommandHandler("bulan",         bulan_cmd))
    app.add_handler(CommandHandler("reset",         reset_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, catat))

    print("✅ Bot Keuangan v2.0 berjalan...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
