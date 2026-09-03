# SPY benchmark R2

## Jalankan pertama kali

Commit/push lalu Actions → **SPY benchmark history** → Run workflow. Empat secrets R2 yang sama dipakai. Tidak ada perubahan universe, compliance, rolling atau ready saham.

Script membaca pointer jika tersedia. Jika belum ada, daftar key R2 diperiksa untuk kandidat SPY/ISIN SPY; kandidat yang sudah ada memblokir bootstrap untuk review, bukan ditimpa atau diunduh ulang. Pencarian key tidak menjamin menemukan object dengan penamaan arbitrer. Jika tidak ditemukan kandidat, satu pemanggilan Yahoo SPY mengambil histori dari start 1990-01-01; cakupan nyata tergantung hasil provider dan dilaporkan, bukan diasumsikan sejak tanggal tersebut.

Schedule 23:45 UTC Senin–Jumat (07:45 WITA Selasa–Sabtu) memperbarui histori yang sudah diinisialisasi. Schedule tidak menginisialisasi histori sendiri; manual run pertama diperlukan. Eksekusi aktual dapat terlambat dari jadwal. Window mengambil 10 hari kalender overlap dan bar baru; revisi volume/High/Low pada overlap diproses walaupun tidak ada tanggal baru. Tidak ada download ulang seluruh histori rutin.

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

Hari UTC berjalan dikecualikan sebelum 22:00 UTC untuk menghindari bar sesi AS yang belum selesai. Tidak memakai kalender bursa/halts resmi; last_date bukan sertifikat freshness. QC mencakup rentang OHLC, nilai finite/positif, volume nonnegatif, identitas dan duplikat tanggal.

Parquet baru ditulis ke key immutable, hash hasil readback diperiksa, baru pointer diganti secara kondisional. Kegagalan dapat menyisakan object versi yang belum ditunjuk; jangan hapus bukti secara otomatis. Pointer dan seluruh versi sebelumnya dipertahankan melalui manifest historis/previous_parquet_key.

Unduh artifact `spy-benchmark-<run_id>` dan kirim `report.json`. Status `complete` atau `unchanged` tidak otomatis menjamin tanggal terakhir sudah sesi terbaru. Jika gagal, pointer lama tidak boleh dianggap terbaru; periksa report. Dataset baru **belum tersedia sampai run berhasil**.

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
