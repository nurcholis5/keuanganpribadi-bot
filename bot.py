#!/usr/bin/env python3
"""
Bot Telegram - Pencatatan Keuangan Pribadi
Fitur: catat pengeluaran, kategori otomatis, ringkasan bulanan, peringatan budget
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, date
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# ── Konfigurasi ──────────────────────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_TOKEN", "ISI_TOKEN_BOT_KAMU_DISINI")
YOUR_CHAT_ID = int(os.environ.get("CHAT_ID", "0"))  # isi chat id kamu

# Budget bulanan per kategori (dalam rupiah)
BUDGET = {
    "makan":    945_000,
    "warkop":   220_000,
    "belanja":  200_000,  # kebutuhan pokok
    "bpjs":      75_000,
    "tabungan": 560_000,
    "lainnya":  100_000,
}

TOTAL_BUDGET = 2_000_000  # total pemasukan bulanan

# Kata kunci untuk kategori otomatis
KATEGORI_KATA = {
    "makan": [
        "makan", "nasi", "lauk", "ayam", "ikan", "tempe", "tahu",
        "sayur", "warung", "padang", "siang", "malam", "sarapan",
        "beli bahan", "pasar", "telur", "kangkung", "bayam"
    ],
    "warkop": [
        "warkop", "warung kopi", "kopi", "cafe", "nulis", "naskah",
        "konten", "ngopi", "wifi", "minum"
    ],
    "belanja": [
        "sabun", "shampo", "shampoo", "odol", "sikat gigi", "deterjen",
        "belanja", "indomaret", "alfamart", "minimarket", "toiletries",
        "gas", "lpg", "minyak goreng", "bumbu"
    ],
    "bpjs": [
        "bpjs", "kesehatan", "iuran", "asuransi"
    ],
    "tabungan": [
        "tabung", "nabung", "tabungan", "simpan"
    ],
}

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = "keuangan.db"

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaksi (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            tanggal   TEXT    NOT NULL,
            bulan     TEXT    NOT NULL,
            jumlah    INTEGER NOT NULL,
            kategori  TEXT    NOT NULL,
            catatan   TEXT,
            created   TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)
    con.commit()
    con.close()

def simpan_transaksi(jumlah: int, kategori: str, catatan: str):
    hari_ini = date.today().isoformat()
    bulan    = date.today().strftime("%Y-%m")
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO transaksi (tanggal, bulan, jumlah, kategori, catatan) VALUES (?,?,?,?,?)",
        (hari_ini, bulan, jumlah, kategori, catatan)
    )
    con.commit()
    con.close()

def get_pengeluaran_bulan(bulan: str = None) -> dict:
    if not bulan:
        bulan = date.today().strftime("%Y-%m")
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT kategori, SUM(jumlah) FROM transaksi WHERE bulan=? GROUP BY kategori",
        (bulan,)
    ).fetchall()
    con.close()
    return {r[0]: r[1] for r in rows}

def get_transaksi_terakhir(limit: int = 5) -> list:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT tanggal, jumlah, kategori, catatan FROM transaksi ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    con.close()
    return rows

def hapus_transaksi_terakhir() -> bool:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT id FROM transaksi ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        con.execute("DELETE FROM transaksi WHERE id=?", (row[0],))
        con.commit()
        con.close()
        return True
    con.close()
    return False

# ── Helper ────────────────────────────────────────────────────────────────────
def format_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")

def tebak_kategori(teks: str) -> str:
    teks_lower = teks.lower()
    for kategori, kata_list in KATEGORI_KATA.items():
        for kata in kata_list:
            if kata in teks_lower:
                return kategori
    return "lainnya"

def parse_pesan(teks: str):
    """
    Format yang diterima:
      - "makan siang 15000"
      - "15000 beli ayam"
      - "warkop 27500"
    Mengembalikan (jumlah, catatan) atau None kalau tidak bisa diparsing.
    """
    import re
    angka = re.search(r'\b(\d{3,7})\b', teks.replace(".", "").replace(",", ""))
    if not angka:
        return None
    jumlah  = int(angka.group(1))
    catatan = teks.strip()
    return jumlah, catatan

def bar_progress(persen: float, lebar: int = 10) -> str:
    filled = int(persen / 100 * lebar)
    filled = min(filled, lebar)
    return "█" * filled + "░" * (lebar - filled)

def cek_peringatan(kategori: str, total_bulan: dict) -> str | None:
    spent   = total_bulan.get(kategori, 0)
    budget  = BUDGET.get(kategori, 0)
    if budget == 0:
        return None
    persen = spent / budget * 100
    if persen >= 100:
        return f"🚨 Budget *{kategori}* sudah HABIS! ({format_rupiah(spent)} / {format_rupiah(budget)})"
    if persen >= 80:
        return f"⚠️ Budget *{kategori}* sudah {persen:.0f}% terpakai — hampir habis!"
    return None

# ── Keyboard ──────────────────────────────────────────────────────────────────
def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Ringkasan Bulan Ini")],
        [KeyboardButton("📋 Transaksi Terakhir"), KeyboardButton("🗑 Hapus Terakhir")],
        [KeyboardButton("💡 Cara Pakai")],
    ], resize_keyboard=True)

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    nama = update.effective_user.first_name
    await update.message.reply_text(
        f"Halo *{nama}*! 👋\n\n"
        "Aku adalah bot pencatat keuangan pribadimu.\n\n"
        "📌 *Cara catat pengeluaran:*\n"
        "Cukup ketik seperti ini:\n"
        "`makan siang 15000`\n"
        "`warkop nulis naskah 27500`\n"
        "`beli ayam 1kg 40000`\n"
        "`gas lpg 22000`\n\n"
        "Aku akan otomatis:\n"
        "✅ Mendeteksi kategori\n"
        "✅ Mencatat jumlah\n"
        "✅ Memberi peringatan kalau budget mepet\n\n"
        "Ketuk tombol di bawah untuk lihat ringkasan atau riwayat.",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def cara_pakai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💡 *Cara Pakai Bot Ini*\n\n"
        "*Catat pengeluaran:*\n"
        "Ketik nominal + keterangan, contoh:\n"
        "`15000 makan siang warung padang`\n"
        "`27500 warkop nulis naskah`\n"
        "`40000 beli ayam 1kg`\n"
        "`22000 gas lpg`\n"
        "`75000 bpjs`\n\n"
        "*Kategori otomatis:*\n"
        "🍽 makan — warung, nasi, lauk, bahan\n"
        "☕ warkop — kopi, nulis, naskah\n"
        "🛒 belanja — sabun, gas, bumbu\n"
        "🏥 bpjs — iuran kesehatan\n"
        "💰 tabungan — nabung, simpan\n"
        "📦 lainnya — sisanya\n\n"
        "*Tombol tersedia:*\n"
        "📊 Ringkasan — lihat pengeluaran bulan ini\n"
        "📋 Transaksi Terakhir — 5 transaksi terbaru\n"
        "🗑 Hapus Terakhir — batalkan input terakhir",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def ringkasan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bulan_ini   = date.today().strftime("%Y-%m")
    label_bulan = date.today().strftime("%B %Y")
    total_bulan = get_pengeluaran_bulan(bulan_ini)
    total_keluar = sum(total_bulan.values())
    sisa         = TOTAL_BUDGET - total_keluar

    teks = f"📊 *Ringkasan {label_bulan}*\n"
    teks += f"{'─' * 28}\n\n"

    for kategori, budget in BUDGET.items():
        spent  = total_bulan.get(kategori, 0)
        persen = min(spent / budget * 100, 100) if budget > 0 else 0
        bar    = bar_progress(persen)
        ikon   = {
            "makan": "🍽", "warkop": "☕", "belanja": "🛒",
            "bpjs": "🏥", "tabungan": "💰", "lainnya": "📦"
        }.get(kategori, "•")
        teks += f"{ikon} *{kategori.capitalize()}*\n"
        teks += f"  {format_rupiah(spent)} / {format_rupiah(budget)}\n"
        teks += f"  {bar} {persen:.0f}%\n\n"

    # kategori tak terduga
    for kat, jml in total_bulan.items():
        if kat not in BUDGET:
            teks += f"📦 *{kat}*: {format_rupiah(jml)}\n"

    teks += f"{'─' * 28}\n"
    teks += f"💸 Total keluar : *{format_rupiah(total_keluar)}*\n"
    teks += f"💵 Pemasukan    : *{format_rupiah(TOTAL_BUDGET)}*\n"

    if sisa >= 0:
        teks += f"✅ Sisa / nabung: *{format_rupiah(sisa)}*\n"
    else:
        teks += f"🚨 Melebihi budget: *{format_rupiah(abs(sisa))}*!\n"

    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_keyboard())

async def transaksi_terakhir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = get_transaksi_terakhir(5)
    if not rows:
        await update.message.reply_text("Belum ada transaksi yang tercatat.", reply_markup=main_keyboard())
        return

    teks = "📋 *5 Transaksi Terakhir*\n\n"
    for tgl, jml, kat, cat in rows:
        ikon = {
            "makan": "🍽", "warkop": "☕", "belanja": "🛒",
            "bpjs": "🏥", "tabungan": "💰", "lainnya": "📦"
        }.get(kat, "•")
        teks += f"{ikon} `{tgl}` — *{format_rupiah(jml)}*\n"
        teks += f"   _{cat}_\n\n"

    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_keyboard())

async def hapus_terakhir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ok = hapus_transaksi_terakhir()
    if ok:
        await update.message.reply_text("✅ Transaksi terakhir berhasil dihapus.", reply_markup=main_keyboard())
    else:
        await update.message.reply_text("Tidak ada transaksi yang bisa dihapus.", reply_markup=main_keyboard())

async def catat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Amankan: hanya terima dari chat ID kamu sendiri
    if YOUR_CHAT_ID and update.effective_user.id != YOUR_CHAT_ID:
        await update.message.reply_text("⛔ Maaf, bot ini hanya untuk pemiliknya.")
        return

    teks = update.message.text.strip()

    # Tombol shortcut
    if teks == "📊 Ringkasan Bulan Ini":
        await ringkasan(update, ctx); return
    if teks == "📋 Transaksi Terakhir":
        await transaksi_terakhir(update, ctx); return
    if teks == "🗑 Hapus Terakhir":
        await hapus_terakhir(update, ctx); return
    if teks == "💡 Cara Pakai":
        await cara_pakai(update, ctx); return

    hasil = parse_pesan(teks)
    if not hasil:
        await update.message.reply_text(
            "⚠️ Tidak bisa membaca nominal.\n\n"
            "Contoh format yang benar:\n"
            "`makan siang 15000`\n"
            "`27500 warkop nulis naskah`",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        return

    jumlah, catatan = hasil
    kategori = tebak_kategori(catatan)
    simpan_transaksi(jumlah, kategori, catatan)

    ikon = {
        "makan": "🍽", "warkop": "☕", "belanja": "🛒",
        "bpjs": "🏥", "tabungan": "💰", "lainnya": "📦"
    }.get(kategori, "📦")

    total_bulan = get_pengeluaran_bulan()
    peringatan  = cek_peringatan(kategori, total_bulan)

    balasan = (
        f"✅ *Tercatat!*\n\n"
        f"{ikon} Kategori  : *{kategori.capitalize()}*\n"
        f"💸 Jumlah    : *{format_rupiah(jumlah)}*\n"
        f"📝 Catatan   : _{catatan}_\n"
        f"📅 Tanggal   : {date.today().strftime('%d %B %Y')}\n"
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
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, catat))
    print("✅ Bot berjalan...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
