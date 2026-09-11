# SPY benchmark R2

## Jalankan pertama kali

Commit/push lalu Actions → **SPY benchmark history** → Run workflow. Empat secrets R2 yang sama dipakai. Tidak ada perubahan universe, compliance, rolling atau ready saham.

Script membaca pointer jika tersedia. Jika belum ada, daftar key R2 diperiksa untuk kandidat SPY/ISIN SPY; kandidat yang sudah ada memblokir bootstrap untuk review, bukan ditimpa atau diunduh ulang. Pencarian key tidak menjamin menemukan object dengan penamaan arbitrer. Jika tidak ditemukan kandidat, satu pemanggilan Yahoo SPY mengambil histori dari start 1990-01-01; cakupan nyata tergantung hasil provider dan dilaporkan, bukan diasumsikan sejak tanggal tersebut.

Schedule **03:45 UTC Selasa–Sabtu (11:45 WITA)** memperbarui histori yang sudah diinisialisasi. Waktu ini sengaja ditempatkan setelah workflow produksi saham dan memberi buffer lebih besar setelah penutupan sesi AS/Yahoo EOD. Schedule tidak menginisialisasi histori sendiri; manual run pertama diperlukan. Eksekusi aktual dapat terlambat dari jadwal. Window mengambil 10 hari kalender overlap dan bar baru; revisi volume/High/Low pada overlap diproses walaupun tidak ada tanggal baru. Tidak ada download ulang seluruh histori rutin.

## Kontrak data setelah run berhasil

- Bucket: nilai `R2_BUCKET_NAME` yang sudah dipakai proyek (ussy-data).
- Pointer: `benchmarks/SPY/current.json`.
- Parquet immutable: `benchmarks/SPY/runs/<uuid>.parquet` dari field `parquet_key`.
- Manifest immutable: field `run_manifest_key`.
- Kolom: date, security_id, ticker, open, high, low, close, adj_close, volume.
- security_id: `benchmark:SPY` (ID internal benchmark, bukan ISIN).
- Mata uang USD. `date` tanpa timezone, dinormalisasi menjadi tanggal bar; manifest menyimpan dtypes.
- Rentang tanggal nyata: `first_date`, `last_date`; jumlah `rows`, SHA-256, waktu fetch/publikasi, versi library, parameter fetch tersedia pada manifest/artifact.

**Basis adjustment:** Yahoo/yfinance `auto_adjust=False`, `repair=False`, OHLC provider dipertahankan dan Adj Close disimpan terpisah. Ini tidak menjamin OHLC historis sepenuhnya mentah sebelum stock split. Untuk perbandingan return, samakan basis saham dan ETF; jangan membandingkan Close saham dengan Adj Close ETF tanpa sengaja.

Jika rasio Adj Close/Close berubah pada overlap (misalnya pembaruan dividen), atau Close historis direvisi, update berhenti agar histori di luar overlap tidak tercampur dengan basis baru. Jangan otomatis mengunduh ulang semuanya; inspeksi artifact dan rencanakan revisi historis secara eksplisit. Ini batas konservatif pipeline, bukan penanganan semua corporate action secara otomatis.

Yahoo kadang mengembalikan placeholder bar harian yang belum lengkap. Sejak repair 11 September 2026, updater membedakan **row source valid** dari row incomplete. Row yang tidak memiliki tanggal atau salah satu OHLC wajib tidak boleh dipakai sebagai market bar; row tersebut disimpan eksplisit ke `discarded-incomplete-source-rows.csv` di artifact dan jumlahnya dicatat pada `report.json`/manifest. Duplicate valid dates atau kehilangan row valid yang tidak dapat dijelaskan tetap menghentikan publication. Safeguard revision/adjustment tetap aktif dan tidak dibypass.

Hari UTC berjalan dikecualikan sebelum 22:00 UTC untuk menghindari bar sesi AS yang belum selesai. Tidak memakai kalender bursa/halts resmi; last_date bukan sertifikat freshness. QC mencakup rentang OHLC, nilai finite/positif, volume nonnegatif, identitas dan duplikat tanggal.

Parquet baru ditulis ke key immutable, hash hasil readback diperiksa, baru pointer diganti secara kondisional. Kegagalan dapat menyisakan object versi yang belum ditunjuk; jangan hapus bukti secara otomatis. Pointer dan seluruh versi sebelumnya dipertahankan melalui manifest historis/previous_parquet_key.

## Repair 11 September 2026

Kegagalan workflow `34425500954` disebabkan `Normalization dropped rows; source requires review`, bukan adjustment-basis atau historical-close revision. Artifact membuktikan Yahoo memberikan row yang tidak lengkap.

Patch consumer upstream:

```text
97503299...  Handle incomplete Yahoo SPY rows with explicit audit
176d34e6...  Test audited SPY incomplete-row handling
f6789d2f...  Run SPY benchmark workflow on updater changes
2a1d5aa9...  Run SPY benchmark after upstream EOD refresh
```

Run `34545442393` = SUCCESS dan menerbitkan versi immutable baru dengan QC PASS. Dari 12 source rows, 11 valid dan 1 incomplete row diaudit/discard. Pointer maju dari `last_date=2026-09-04` menjadi `2026-09-09` tanpa perubahan adjustment basis atau historical close pada overlap. Row 2026-09-10 yang diterima saat run tersebut masih incomplete (Open/High/Low/Volume tersedia, Close/Adj Close belum tersedia), sehingga tidak dipaksakan menjadi market bar.

Run konfirmasi `34545529977` = SUCCESS / unchanged dengan kondisi source yang sama. Jadwal dipindahkan ke 03:45 UTC agar refresh berikutnya terjadi setelah buffer EOD yang lebih panjang. Sampai provider menyediakan Close valid untuk 2026-09-10, downstream yang mensyaratkan `market_data_asof >= 2026-09-10` harus tetap menunggu; ini adalah data-freshness state, bukan bukti tidak ada signal.

Unduh artifact `spy-benchmark-<run_id>` dan kirim `report.json`. Status `complete` atau `unchanged` tidak otomatis menjamin tanggal terakhir sudah sesi terbaru. Jika gagal, pointer lama tidak boleh dianggap terbaru; periksa report.

## Baca di notebook (client s3 dan R2_BUCKET sudah disiapkan)

```python
import io, json, hashlib
import pandas as pd
obj = s3.get_object(Bucket=R2_BUCKET, Key='benchmarks/SPY/current.json')
manifest = json.loads(obj['Body'].read())
payload = s3.get_object(Bucket=R2_BUCKET, Key=manifest['parquet_key'])['Body'].read()
assert hashlib.sha256(payload).hexdigest() == manifest['sha256']
spy = pd.read_parquet(io.BytesIO(payload))
print(manifest['first_date'], manifest['last_date'], manifest['rows'])
```

SPY tidak dimasukkan ke universe compliant atau hitungan ready saham. ETF sektor/industri belum ditambahkan oleh implementasi ini.
