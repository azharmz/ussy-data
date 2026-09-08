# Pre-backtest gate

Workflow **Pre-backtest gate (R2 read-only)** membaca pointer ready dan SPY resmi, lalu mengaudit seluruh histori `backtest/ohlcv/{security_id}.parquet` untuk tepat 1.223 anggota ready. Tidak ada Yahoo download dan tidak ada R2 write.

Adjusted OHLC dibentuk dengan `factor = adj_close / close`, kemudian faktor yang sama dikalikan ke open, high, low, dan close. Volume tidak diubah. RS 20 hari hanya dihitung sesudah inner join tanggal saham dengan tanggal SPY.

Parity lifecycle dikunci ke `ussy-swing-actions` commit `7c4dc4040f2ac7602fd8dae5204d0b6e4c81c623`. Fixture mencakup entry, entry+SL satu candle, gap stop, TP, dan timeout pada bar ke-10. Jika source engine berubah, fixture dan hash provenance wajib ditinjau ulang.

Gate gagal tertutup bila jumlah ready bukan 1.223, checksum/pointer tidak cocok, object berubah selama audit, full-history QC atau adjusted OHLC gagal, kalender bersama kurang dari 21 bar, SPY tidak valid, atau fixture parity gagal. Script tidak menghitung atau mengekspor metrik performa. Artifact berisi `report.json` dan `tickers.csv` dengan provenance serta daftar ticker gagal/di-skip.
