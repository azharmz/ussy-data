# Data untuk project strategi

Bucket private `ussy-data`, pointer `production/ready/current.json` menunjuk
Parquet versi tertentu berisi hanya anggota compliant aktif yang ada dalam
`ready_security_ids`. Jumlah tidak dikunci ke 1024. Ticker insufficient/unavailable
dilewati, bukan dihapus atau diubah status compliant-nya.

Workflow production daily dan universe update mengekspor otomatis. Untuk ekspor
pertama tanpa Yahoo, jalankan **Publish web status only** setelah commit/push.
Workflow itu sekarang juga membaca rolling Parquet yang sudah ada, bukan hanya JSON.

Data ini rolling (maksimum 300 bar per ticker sesuai konfigurasi), bukan full history.
Ready berarti memenuhi jumlah bar, bukan sertifikasi kelengkapan/kebaruan harga.
Periksa tanggal per ticker sebelum memakai untuk strategi. Audit freshness syariah
tidak menjadi filter tambahan. File asli dan histori tetap disimpan.

## Membaca dari Python / Colab

Gunakan boto3, pandas, pyarrow yang tersedia di lingkungan project. Tambahkan folder
`src` repo ke Python path, lalu:

```python
import boto3
from load_ready import load_ready

# Ambil nilai dari secret store environment, jangan tulis credential di notebook.
import os
s3 = boto3.client('s3', endpoint_url=os.environ['R2_ENDPOINT'],
    aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'], region_name='auto')
prices, metadata = load_ready(s3, 'ussy-data')
print(metadata['snapshot_date'], metadata['securities'], len(prices))
```

Untuk project konsumen gunakan token read-only khusus bucket utama; jangan berikan
token write pipeline atau menyimpan credential dalam frontend Pages. Keluaran
ready tidak diunggah ke bucket publik web. Versi Parquet disimpan per ekspor untuk
mencegah pointer dan file tidak cocok; versi lama belum dibersihkan otomatis.
