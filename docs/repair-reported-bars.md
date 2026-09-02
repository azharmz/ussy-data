# Repair 34 bar tanggal 1 September 2026

Commit/push, tunggu workflow universe dan produksi selesai, lalu jalankan Actions → **Repair 34 reported OHLC bars**. Jangan menjalankan update universe secara bersamaan. Workflow menggunakan concurrency produksi yang sama. Tidak perlu menjalankan bootstrap universe.

Urutan: capture histori 34 sekuritas, daily, rolling, readiness, pointer dan ready aktif → backup byte asli ke R2 `audit/ohlcv-repairs/<id>/before/` dan artifact → fetch hanya 1 hari untuk masing-masing 34 ticker → validasi seluruh kandidat dan rolling → conditional write pada object yang berubah → ekspor ready → verifikasi hash dan QC seluruh ready.

Daftar sumber insiden disimpan di `audits/2026-09-01-invalid-bars.csv`. Tidak ada penambahan/penghapusan bar atau perubahan compliance/readiness. Hanya enam nilai OHLCV pada pasangan security_id/tanggal yang dilaporkan dan masih invalid yang diganti dengan nilai fetch ulang yang valid. Bar yang sudah valid tidak diubah. High/Low tidak pernah diperlebar secara sintetis. Bila bar invalid sekarang berbeda dari bukti CSV, proses berhenti untuk review.

Semua fetch dan pemeriksaan dilakukan sebelum repair pertama. Jika sumber masih invalid/kosong, atau ditemukan invalid lain pada rolling, tidak ada repair diterapkan. Backup audit tetap tersimpan. Sumber diperiksa ETag sebelum perubahan dan setiap write memakai IfMatch. R2 tidak menyediakan transaksi multi-object: kegagalan di tengah write dapat meninggalkan repair parsial; daftar `writes` pada report menunjukkan object yang sudah ditulis. Jangan mengembalikan semua backup otomatis karena bisa menimpa pembaruan yang lebih baru. Simpan artifact dan minta review bila gagal.

Setelah selesai, unduh artifact `ohlcv-repair-...` dan kirim `report.json`. Hasil sukses harus menunjukkan `status: complete` dan `ready_qc: passed`. Pointer ready lama dan Parquet immutable tidak dihapus. Jumlah ready tidak otomatis berubah karena repair ini tidak mengganti keanggotaan atau jumlah bar.

Fetch ulang bukan bukti respons Yahoo saat insiden; repair ini mengganti data yang terbukti tidak konsisten dengan keluaran sumber terbaru yang lolos pemeriksaan, tanpa mengklaim penyebab awal sudah pasti diketahui.

Pengaman permanen: normalisasi menolak rentang OHLC invalid/nonfinite/harga nonpositif/volume negatif, dan export ready memeriksa seluruh bar terpilih sebelum publikasi. Jika ditolak, pointer lama tetap ada—ini tidak membuat dataset lama otomatis bersih atau terbaru. Pemeriksaan tidak menjamin akurasi ekonomi harga atau freshness pasar.

Artifact dan backup berisi data privat, bukan secrets. Jangan publikasikan; artifact disimpan 30 hari, backup R2 tetap ada untuk audit. Workflow tidak menerbitkan ulang web status.
