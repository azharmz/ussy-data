# Twelve Data fallback audit

Workflow **Audit Twelve Data fallback (read-only)** membandingkan daily raw OHLCV dari Twelve Data dan Yahoo untuk `SPY`, `BHP`, `FSI`, `NVS`, `AMAT`, dan `PODD`.

Workflow hanya manual dan hanya menerima secret `TWELVE_DATA_API_KEY`. Workflow tidak menerima credential R2, tidak membaca atau menulis R2, tidak mengubah universe/compliance, dan tidak menambal histori. Request Twelve Data memakai `adjust=none`, window 20 hari kalender, serta jeda 9 detik agar berada di bawah batas Basic 8 kredit per menit.

Jalankan setelah secret GitHub `TWELVE_DATA_API_KEY` tersedia. Unduh artifact `twelve-data-audit-<run_id>-<attempt>` dan periksa `report.json` serta CSV per provider. `dates_only_in_twelve` menunjukkan tanggal yang tersedia di Twelve Data tetapi tidak tersedia pada respons Yahoo saat audit. Perbedaan nilai pada overlap adalah bukti perbedaan provider, bukan izin otomatis untuk mengganti harga.

Keputusan menulis fallback ke R2 memerlukan review terpisah atas identitas simbol, basis adjustment, lisensi penyimpanan, dan hasil overlap. Jangan mengisi `adj_close`, forward-fill harga, atau mengubah OHLC berdasarkan audit ini.
