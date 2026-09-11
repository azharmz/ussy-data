# USSY Data — project status

> **Status operasional terbaru: 11 September 2026.**  
> Baca `docs/HANDOFF-2026-09-11.md` sebagai handoff utama. `docs/HANDOFF-2026-09-04.md` tetap disimpan sebagai catatan historis dan tidak lagi menjadi status terkini.

Repo: `azharmz/ussy-data`  
Branch: `main`

## Fungsi repo

`ussy-data` adalah data infrastructure bersama untuk proyek-proyek USSY. Tanggung jawab utamanya:

- membership/universe Musaffa yang dibekukan untuk reproducibility;
- histori dan update harian OHLCV;
- rolling dataset;
- ready-only dataset untuk consumer/backtest;
- QC dan audit data;
- benchmark SPY terpisah;
- read-only web status.

Repo ini adalah source of truth teknis untuk data/pipeline. Consumer strategy seperti Swing atau CAN SLIM tidak boleh mengarang ulang semantics dataset atau meng-hardcode run artifact.

## Baseline produksi terbaru yang terverifikasi

Bukti terakhir: `Production daily OHLCV` run `34563561158`, **success**, 11 September 2026.

| Status | Nilai |
|---|---:|
| Membership snapshot | 2026-08-28 |
| Confirmed compliant | 1327 |
| Operational securities | 1300 |
| Histories updated pada run | 1284 |
| Latest daily market date | 2026-09-10 |
| Daily rows | 1284 |
| Rolling rows | 376199 |
| Ready securities | 1226 |
| Insufficient history | 74 |
| Data unavailable | 27 |
| Ready rows | 367203 |

Production manifest: `production/manifests/run-34563561158-1.json`.

Ready pointer resmi: `production/ready/current.json`.

Ready run terbaru yang dibuktikan oleh workflow:

- `production/ready/runs/bf56bd71a2bb4c4c8f794a0958082238.parquet`
- SHA-256 `b5e485fad9dd7dab426e39163e98127132c0702089b861ee172787f74ecb93f9`
- 1226 securities / 367203 rows

**Consumer harus membaca pointer `production/ready/current.json`; jangan hardcode UUID parquet.**

## SPY benchmark

SPY sekarang sudah terverifikasi tersedia dan bukan lagi sekadar implementasi yang menunggu run.

Bukti terakhir: `SPY benchmark history` run `34566034032`, **success**, 11 September 2026.

- 89 unit tests: OK
- rows: 8461
- first date: 1993-01-29
- last date: 2026-09-10
- QC: passed
- role: `market_benchmark_only`
- run parquet: `benchmarks/SPY/runs/47b11f071e11417abca7412f9d0e302d.parquet`

SPY tidak termasuk universe compliant.

## Workflow produksi saat ini

### Production daily OHLCV

- cron: `0 3 * * 1-5`
- workflow comment: 10:00 WIB / 11:00 WITA
- update daily + rolling → export ready → audit recorded earnings freshness → publish web status
- mempunyai recovery push path bila workflow/updater terkait berubah

### SPY benchmark history

- cron: `45 3 * * 2-6`
- 03:45 UTC / 11:45 WITA Selasa–Sabtu
- dijalankan dengan buffer setelah upstream production refresh
- menjalankan unit tests sebelum update SPY
- mempunyai recovery push path untuk maintenance/recovery

Workflow lain yang tersedia termasuk universe update, universe freshness audit, pre-backtest gate, invalid-bar audit, dan web-status publish.

## Kebijakan data yang tidak berubah

- Eligibility hanya exact `sharia_compliance == "COMPLIANT"`.
- `musaffaHalalRating` hanya audit, bukan syarat eligibility.
- Minimum ready 250 bar; rolling target 300 bar.
- Compliance, identity, trading status, availability, dan freshness harus dipisahkan.
- Jangan forward-fill/sintesis harga atau memperbaiki OHLC secara spekulatif.
- Jangan menghapus histori atau menyambung ticker penerus tanpa verifikasi identity.
- Audit/diagnosis sebelum repair; hindari mass-redownload tanpa evidence.
- Fallback/provider alternatif tidak otomatis menjadi kebenaran; repair berbasis evidence alternatif tetap memerlukan review sesuai guardrail repo.

## Caveat penting saat ini

### Membership dan screening freshness

Snapshot membership aktif masih `2026-08-28`.

Audit 11 September memakai `recorded_earnings_date_review_only_v2`, **bukan live earnings feed**. Hasilnya:

- confirmed compliant: 1327
- live earnings verified: false
- unknown: 1327

Jadi price data yang fresh sampai 10 September **tidak membuktikan** Musaffa screening sudah diperbarui setelah earnings terbaru.

### Data unavailable

Masih ada 27 securities berstatus `data_unavailable`. Pada production run terbaru, log secara eksplisit memperlihatkan failure Yahoo pada beberapa contoh seperti `EMPG`, `MPX`, `BLD`, dan `PTNM`; jangan menganggap empat nama itu sebagai daftar lengkap 27 kasus.

### Temuan historis 4 September

Daftar 18 kandidat yang saat itu tertinggal tidak boleh dianggap sebagai current stale list. Banyak ticker yang sebelumnya tertinggal sekarang terlihat berhasil diperbarui hingga 2026-09-10. Jika status seluruh 18 diperlukan, jalankan/audit universe freshness terbaru dan nilai evidence saat ini.

Rekonsiliasi identity lama (termasuk ESGL/OIO dan NBY/SDEV) juga tidak otomatis dianggap selesai hanya karena production workflow hijau.

## Prinsip melanjutkan pekerjaan

1. Gunakan `docs/HANDOFF-2026-09-11.md` + HEAD repo sebagai baseline.
2. Consumer strategy membaca `production/ready/current.json`.
3. Masalah harga → audit/pre-backtest gate dahulu, lalu repair terbatas jika evidence cukup.
4. Jangan ulang bootstrap atau repair lama tanpa kebutuhan baru.
5. Treat universe/compliance freshness dan OHLCV freshness sebagai dua pekerjaan berbeda.
6. Bila workflow terbaru menghasilkan angka baru, perbarui dokumen status ini berdasarkan bukti run tersebut.
