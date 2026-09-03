# USSY - status dan panduan melanjutkan

> **Handoff terbaru: 4 September 2026 — baca [docs/HANDOFF-2026-09-04.md](docs/HANDOFF-2026-09-04.md) terlebih dahulu.**
> Bagian di bawah dipertahankan sebagai catatan historis 2 September sebelum repair OHLC dan audit freshness selesai; bukan status terkini. Handoff terbaru mencatat ekspor yang sudah diverifikasi, 18 kandidat tertinggal, perubahan web yang sudah dicommit (publikasi belum dikonfirmasi), dan SPY yang belum diambil.

Diperbarui: 2 September 2026. Repo: D:\Stock\Claude\ussy-data.
Angka R2 di bawah bersumber dari log yang dikirim user, bukan pemeriksaan langsung terbaru.

## Keputusan tetap

- Eligibility hanya exact `sharia_compliance == "COMPLIANT"`; kosong/nilai lain dikecualikan.
- `musaffaHalalRating` disimpan untuk audit, tidak menentukan eligibility.
- Jangan mengubah label sumber, menghapus histori, atau mengunduh ulang histori bootstrap yang sudah tersedia.
- Minimum ready saat ini 250 bar, rolling maksimal 300 bar. Ini ambang ketersediaan data, bukan jaminan konvergensi EMA200 atau kesegaran screening.
- Status perdagangan, identitas ticker dan freshness screening dibedakan dari label compliance.
- User memakai GitHub Desktop untuk commit/push dan memicu workflow. Web single HTML pada Cloudflare Pages; ringkasan publik pada bucket ussy-data-web, histori utama di bucket ussy-data.

## Hasil terakhir yang dilaporkan

Snapshot membership: 2026-08-28.
Run follow-up: 33623976189-1.
Bootstrap: uploaded 1, existing 234, unprocessed 0; tidak ada kegagalan dilaporkan.

| Status rolling | Jumlah |
|---|---:|
| confirmed_compliant | 1327 |
| included_in_rolling | 1300 |
| ready | 1222 |
| insufficient_history | 78 |
| data_unavailable | 27 |
| rolling_rows | 375648 |

Readiness dibuat 2026-09-02T11:27:47.687268+00:00.
Object rolling/readiness berhasil diverifikasi oleh workflow menurut log user.
Kenaikan 46 rolling rows konsisten dengan histori MBGL yang baru masuk; rincian per-ticker belum dibaca langsung.

Laporan R2:

- backtest/manifests/bootstrap/2026-08-28/policy-bootstrap-33623976189-1.json
- universe/policy_audits/2026-08-28/33623976189-1.json
- production/rolling/latest.parquet
- production/rolling/readiness.json

Export ready terakhir yang lognya dikirim langsung: 2026-09-02T10:35:56.056058+00:00,
1222 sekuritas, 366074 rows, pointer production/ready/current.json,
parquet production/ready/runs/1e7fa0c8e0ee47e3b783b22929e52baf.parquet.
Log tahap export/publish run 33623976189-1 belum diberikan; jangan mengklaim pointer itu sudah diperbarui lagi.

## Perbaikan yang sudah dipakai

- Provider alias berbasis security ID + ticker: CMPO -> GPGI, PSTG -> P, SCVL -> SHOE, USEG -> BSIN, MBGL WI -> MBGL.
- Ticker sumber dan security ID Parquet tetap dipertahankan.
- 27 kasus tertunda dicatat beserta alasan/sumber pada config/bootstrap-policy-2026-08-28.json. Bukan 27 yang persis sama dengan kelompok awal: empat sudah dipulihkan, empat kegagalan baru ditunda.
- NBY/SDEV perlu review perubahan bisnis dan identitas/screening, IMG berada pada OTC CIMG, SLNO telah diakuisisi, JMG halt/proses delisting. Jangan retry massal kode lama.
- ESGL tidak dialiaskan ke OIO; snapshot memuat keduanya dengan security ID berbeda. SGN tidak dialiaskan ke AIB. Rekonsiliasi identitas masih terbuka.
- Optimasi MBGL-only yang belum dipush telah dibatalkan; workflow kembali memproses antrean reviewed dan skip object yang sudah tersedia. Tidak perlu bootstrap lagi untuk pekerjaan yang sudah selesai.

## Langkah berikutnya

1. Commit/push dokumentasi status setelah pemeriksaan lokal; tidak perlu trigger bootstrap.
2. Periksa bahwa tahap export ready dan publish web pada run terakhir hijau. Ini pemeriksaan hasil, bukan permintaan menjalankan ulang.
3. Biarkan Production daily OHLCV berjalan sesuai jadwal repo: 23:30 UTC Senin-Jumat (07:30 WITA Selasa-Sabtu). Konfigurasi ini bukan bukti run terjadwal terbaru berhasil.
4. Pada proyek strategi/Colab, baca production/ready/current.json untuk menemukan dataset; jangan hardcode UUID Parquet atau jumlah ready.
5. Jika pekerjaan dilanjutkan, baca file ini dahulu lalu periksa repo/log terbaru. Jangan ulangi bootstrap atau riset yang sudah selesai tanpa alasan.

## Pekerjaan yang belum dinyatakan selesai

- Rekonsiliasi identitas ESGL/OIO dan kasus NBY/SDEV.
- Bukti earnings dan screening Musaffa benar-benar terbaru; timestamp scrape bukan bukti screening pasca-earnings.
- Keanggotaan ready masing-masing 13 ticker Swing belum dilaporkan per-ID setelah bootstrap; jangan menyimpulkan hanya dari jumlah agregat.
- 300 bar rolling dan minimum 250 belum menjamin EMA200 identik dengan perhitungan seluruh histori. Seed, metode dan warm-up indikator perlu ditentukan pada proyek strategi.

Referensi: docs/27-ticker-review-2026-09-02.md dan docs/bootstrap-followup-2026-09-02.md.
