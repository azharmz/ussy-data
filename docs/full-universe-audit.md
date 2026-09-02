# Audit seluruh universe (read-only)

Commit/push lalu Actions → **Audit full universe freshness (read-only)** → Run workflow. Tunggu workflow universe/produksi lain selesai; jangan menjalankan universe-update bersamaan. Audit berbagi concurrency group produksi, serta memeriksa ETag/pointer dan daftar histori pada akhir pembacaan.

Tidak ada Yahoo download, update histori, forward-fill, perubahan compliance/readiness, ekspor ready, atau write R2. GitHub Actions membaca seluruh histori eligible dari R2 (bisa ratusan MB di runner), bukan mengunduhnya ke laptop. Artifact hanya laporan, bukan seluruh Parquet.

Unduh artifact `universe-freshness-audit-...` dan kirim `report.json` serta `securities.csv`. CSV memuat semua anggota yang sharia_compliance-nya COMPLIANT pada snapshot aktif, tanpa pembatasan ke universe Swing, jumlah 1.327, atau ready. Rating kedua hanya dicatat sebagai bukti dampak filter lama.

Laporan membedakan histori hilang, histori gagal dibaca, histori pendek, tanggal histori/rolling/ready berbeda, perbedaan keanggotaan readiness/export, tanggal rolling tanpa pasangan pada histori, serta tanggal tertinggal terhadap sekuritas pada exchange yang sama. Ada perbandingan dengan ekspor referensi 78808fe... dan ringkasan kegagalan pada sepuluh manifest produksi terbaru.

**Batas interpretasi:** peer latest bukan kalender sesi resmi. Label `behind_exchange_peers_calendar_unverified` hanya kandidat review; bukan kesimpulan bahwa provider gagal atau perdagangan dihentikan. Audit ini tidak mendeteksi pasar yang seluruh datanya sama-sama tertinggal, dan tidak memeriksa kesamaan semua nilai OHLC. Kalender, jam tutup, listing ganda, serta halt harus diperiksa sebelum memutuskan download tambahan. Tidak ada pembaruan otomatis dari daftar temuan.

`complete` berarti pembacaan selesai, bukan semua data fresh. Jika `incomplete_or_sources_changed`, jangan memakai hasil sebagai snapshot atomik. Jika merah, tetap unduh artifact untuk diagnosis. SPY tidak ditambahkan oleh audit ini.
