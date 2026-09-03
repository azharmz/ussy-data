# Status investigasi di web

Kolom Investigasi sekarang terpisah dari kategori masalah otomatis:

- **Terdeteksi · belum ada catatan investigasi**: tidak ada bukti review untuk identitas, jenis masalah dan snapshot tersebut. Bukan klaim bahwa tidak pernah dibahas di tempat lain.
- **Sudah diteliti · perlu tindak lanjut**: ada temuan, tanggal review dan tautan sumber; bukan pernyataan bahwa data sudah diperbaiki.
- **Informasi investigasi belum dimuat**: JSON lama belum memiliki field baru. Jangan menafsirkannya sebagai belum pernah diteliti.

Registry `config/investigation-records.json` mengimpor 27 catatan deferred operasional yang sudah terdokumentasi di konfigurasi bootstrap dan dokumen review 2 September. Ini bukan riset ulang atau konfirmasi keadaan terkini. Lima pemetaan alias yang telah diterapkan tetap dikelola di provider_symbols; tidak dilabeli deferred oleh registry ini.

Pencocokan menggunakan security ID + ticker + scope + snapshot. Catatan operasional hanya berlaku pada DATA_UNAVAILABLE, INSUFFICIENT_HISTORY, DAILY_UPDATE_FAILED. UNKNOWN/POTENTIALLY_STALE berkaitan dengan screening; catatan corporate action tidak menyelesaikannya. SLAI tidak diberi label reviewed karena belum ada bukti review tersimpan. Kategori, reason otomatis, eligibility, readiness, histori dan status pipeline tidak diubah.

Publikasi tetap memakai CF Pages dan single `web/index.html`. Commit/push perubahan, tunggu deployment CF Pages, kemudian jalankan **Publish web status only** dengan pilihan rebuild/download tidak dicentang. Refresh web setelah status.json baru terbit. Tidak perlu run produksi, bootstrap atau repair. Sebelum JSON baru tersedia, HTML menampilkan status investigasi belum dimuat.
