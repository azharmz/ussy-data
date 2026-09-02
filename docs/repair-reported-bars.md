# Repair 37 bar tanggal 1 September 2026 (reuse 34)

Commit/push, tunggu workflow universe dan produksi selesai, lalu jalankan Actions → **Repair 37 OHLC bars (reuse 34)**. Jangan menjalankan update universe secara bersamaan. Workflow menggunakan concurrency produksi yang sama. Tidak perlu menjalankan bootstrap universe.

Urutan: validasi 34 hasil fetch yang tersimpan → capture histori 37 sekuritas, daily, rolling, readiness, pointer dan ready aktif → backup byte asli ke R2 `audit/ohlcv-repairs/<id>/before/` dan artifact → fetch hanya CHOW, POAS, PXED untuk tanggal 1 September → validasi seluruh kandidat dan rolling → conditional write pada object yang berubah → ekspor ready → verifikasi hash dan QC seluruh ready.

Cache `audits/2026-09-01-cached-fetches.json` berasal dari report run yang dimulai 2026-09-02T15:37:08.733902+00:00. Waktu fetch, parameter, versi library dan asal backup dipertahankan, tidak diberi label sebagai fetch baru. Cache harus lengkap dan valid: jika gagal, proses berhenti tanpa fallback download. Hash cache dicatat pada report dan salinannya masuk artifact.

Tambahan CHOW (242 bar), POAS (200), PXED (225) ditemukan pada rolling yang SHA-256-nya `3df37f7223385eedb8127277107520dc9a2c13155d86c5fb4d184da84192b010`. Ketiganya belum ready pada capture tersebut. Jika tidak ada bar mereka di daily, daily tidak ditambahi; `daily_absent_ids_unchanged` melaporkannya.

Laporan mencatat tahap, object aktif, detail validation error yang disaring dari secrets, dan daftar bar yang masih invalid bila full rolling QC gagal. Tetap kirim report jika merah; jangan langsung mengulang run.

Daftar sumber insiden disimpan di `audits/2026-09-01-invalid-bars.csv`. Tidak ada penambahan/penghapusan bar atau perubahan compliance/readiness. Hanya enam nilai OHLCV pada pasangan security_id/tanggal yang dilaporkan dan masih invalid yang diganti dengan nilai fetch ulang yang valid. Bar yang sudah valid tidak diubah. High/Low tidak pernah diperlebar secara sintetis. Bila bar invalid sekarang berbeda dari bukti CSV, proses berhenti untuk review.

Semua fetch dan pemeriksaan dilakukan sebelum repair pertama. Jika sumber masih invalid/kosong, atau ditemukan invalid lain pada rolling, tidak ada repair diterapkan. Backup audit tetap tersimpan. Sumber diperiksa ETag sebelum perubahan dan setiap write memakai IfMatch. R2 tidak menyediakan transaksi multi-object: kegagalan di tengah write dapat meninggalkan repair parsial; daftar `writes` pada report menunjukkan object yang sudah ditulis. Jangan mengembalikan semua backup otomatis karena bisa menimpa pembaruan yang lebih baru. Simpan artifact dan minta review bila gagal.

Setelah selesai, unduh artifact `ohlcv-repair-...` dan kirim `report.json`. Hasil sukses harus menunjukkan `status: complete` dan `ready_qc: passed`. Pointer ready lama dan Parquet immutable tidak dihapus. Jumlah ready tidak otomatis berubah karena repair ini tidak mengganti keanggotaan atau jumlah bar.

Fetch ulang bukan bukti respons Yahoo saat insiden; repair ini mengganti data yang terbukti tidak konsisten dengan keluaran sumber terbaru yang lolos pemeriksaan, tanpa mengklaim penyebab awal sudah pasti diketahui.

Pengaman permanen: normalisasi menolak rentang OHLC invalid/nonfinite/harga nonpositif/volume negatif, dan export ready memeriksa seluruh bar terpilih sebelum publikasi. Jika ditolak, pointer lama tetap ada—ini tidak membuat dataset lama otomatis bersih atau terbaru. Pemeriksaan tidak menjamin akurasi ekonomi harga atau freshness pasar.

Artifact dan backup berisi data privat, bukan secrets. Jangan publikasikan; artifact disimpan 30 hari, backup R2 tetap ada untuk audit. Workflow tidak menerbitkan ulang web status.
