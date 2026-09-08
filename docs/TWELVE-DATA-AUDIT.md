# Twelve Data fallback audit (retired)

Workflow audit ini telah dihentikan. Dokumen ini dipertahankan hanya sebagai catatan hasil evaluasi provider; Twelve Data tidak dipakai oleh production daily.

Workflow **Audit Twelve Data fallback (read-only)** dahulu membandingkan daily raw OHLCV dari Twelve Data dan Yahoo untuk `SPY`, `BHP`, `FSI`, `NVS`, `AMAT`, dan `PODD`. Ia hanya menerima secret `TWELVE_DATA_API_KEY`, tanpa credential R2, dan memakai `adjust=none` dengan window 20 hari kalender.

Hasil evaluasi: `dates_only_in_twelve` dapat menunjukkan tanggal yang tersedia di Twelve Data tetapi tidak tersedia pada respons Yahoo. Perbedaan nilai overlap bukan izin otomatis untuk mengganti harga.

Keputusan menulis fallback ke R2 memerlukan review terpisah atas identitas simbol, basis adjustment, lisensi penyimpanan, dan hasil overlap. Jangan mengisi `adj_close`, forward-fill harga, atau mengubah OHLC berdasarkan audit ini.
