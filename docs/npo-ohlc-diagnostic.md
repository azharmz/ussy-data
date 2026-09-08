# Diagnostik NPO — read-only

Tujuan: membandingkan bar NPO (`US29355X1072`) tanggal 2026-09-01 pada ready yang dilaporkan, histori, rolling, daily, serta hasil fetch ulang Yahoo sebelum/sesudah normalisasi.

## Catatan historis

Workflow diagnostik NPO telah retired setelah investigasi selesai. Dokumen ini dipertahankan sebagai bukti historis; jangan mencoba menjalankan ulang workflow tersebut. Artifact yang dihasilkan dahulu bernama `npo-ohlc-evidence-<run_id>-<attempt>`.

## Batas operasi

- Memakai empat repository secrets R2 yang sudah ada; nilai secrets tidak dicetak.
- Hanya `get_object` dan `head_object` pada R2. Tidak ada upload, penghapusan, repair, perubahan compliance, rebuild readiness, atau ekspor ready.
- Salinan object R2 disimpan ke artifact **sebelum** menghubungi Yahoo.
- Hanya dua pemanggilan yfinance untuk **NPO**, dengan rentang 2026-08-25 sampai sebelum 2026-09-03: layout single dan layout batch satu ticker. Ini bukan download ulang universe dan bukan jaminan hanya dua HTTP request internal.
- Artifact berisi salinan data dari bucket privat. Jangan publikasikan artifact; masa simpan yang diminta 30 hari.

## Membaca bukti

- `objects`: SHA-256, ETag, LastModified, waktu pembacaan dan pemeriksaan perubahan selama capture.
- `stages`: bar tanggal sasaran di setiap tahap beserta hasil pemeriksaan rentang OHLC.
- `deltas_vs_reported_ready`: nilai tahap terkait dikurangi nilai ready yang dilaporkan.
- `normalization_deltas`: nilai sesudah dikurangi sebelum normalisasi untuk fetch ulang.
- `fetches` dan `versions`: parameter, waktu fetch ulang, versi dependensi dan commit diagnostik.
- `errors`: kegagalan pembacaan/pengolahan tanpa pesan transport yang berpotensi memuat detail sensitif.

File yfinance raw adalah keluaran library sebelum normalisasi, bukan respons HTTP mentah. Fetch ulang dapat berisi revisi Yahoo dan **tidak membuktikan** isi respons pada run bermasalah. Versi yfinance run lama harus diperiksa pada log instalasi run tersebut. Histori, rolling, dan daily yang dibaca adalah versi saat diagnostik, bukan otomatis versi saat insiden.

Jika hash ready tidak cocok dengan laporan QC, atau bukti wajib tidak terbaca, fetch ulang dibatalkan. Sampel kosong/duplikat atau object berubah selama capture membuat perbandingan perlu ditinjau; jangan menyimpulkan penyebab hanya dari warna workflow. Layout batch satu ticker tidak mereproduksi seluruh kondisi batch produksi.

Repair baru diputuskan setelah hasil ini diperiksa; jangan memperlebar High/Low secara otomatis agar mencakup Open.
