# Catatan pengujian

Semua pengujian di bawah memakai data sintetis dari simulator. Hasilnya menunjukkan sistem bekerja sesuai rancangan, bukan validasi keselamatan untuk motor industri sungguhan.

## Unit test

Jalankan dari folder proyek:

```bash
python -m unittest discover -s tests -v
```

Hasil terakhir: 23 test lulus. Yang diperiksa:

- **Simulator dan pemrosesan getaran:** RMS dan frekuensi puncak FFT sesuai sinyal uji, dan pengacak simulator bisa diulang dengan seed yang sama.
- **Penggabungan MA10:** laptop menunggu 10 sampel berurutan, menolak MA10 dari sesi lama, duplikat, atau rata-rata yang tidak cocok, dan tidak pernah menghitung MA10 sendiri dalam mode MQTT.
- **Status data basi:** umur data dihitung dari waktu sampel diambil, dan hasil AI tidak mundur ke sampel lama.
- **Alarm dan stop otomatis:** batas suhu tepat di tepi batas, hitung mundur dibatalkan saat kondisi pulih, Stop manual menghentikan hitung mundur, dan stop otomatis tetap berjalan walaupun browser ditutup.
- **Fase start:** saat RPM mode Auto naik dari nol, batas bawah RPM ditahan paling lama 5 sampel supaya tidak terbaca sebagai gangguan tinggi. RPM mode Manual yang terlalu rendah tetap langsung memicu alarm.
- **Input model:** label skenario dan batas alarm tidak pernah masuk ke model.

## Evaluasi model

Model dilatih dengan 96 sesi normal (16.320 baris) dan ambangnya ditentukan dari 32 sesi normal lain (5.440 baris). Uji akhir memakai sesi baru yang tidak dipakai saat pengembangan.

| Data uji | Sampel | Alarm dari AI | Alarm batas |
|---|---|---|---|
| Normal | 3.400 | 96 (2,82%) | 0 |
| Getaran berlebih | 2.200 | 2.200 | 2.200 |
| Panas berlebih | 2.200 | 2.200 | 2.200 |
| Putaran berlebih | 2.200 | 2.200 | 2.200 |

Contoh nilai di dalam batas alarm yang tetap dinilai janggal oleh AI:

| Suhu MA10 | RMS getaran | RPM | Alarm batas | Skor AI |
|---|---|---|---|---|
| 30 °C | 0,18 g | 1.500 | NORMAL | 0,68 (normal) |
| 37 °C | 0,18 g | 1.500 | NORMAL | 1,88 (anomali) |
| 30 °C | 0,25 g | 1.500 | NORMAL | 3,77 (anomali) |
| 35 °C | 0,25 g | 1.300 | NORMAL | 5,04 (anomali) |

Angka lengkap ada di `hasil-evaluasi.json`. Setiap kali model dilatih, `train_model.py` menulis ulang `evaluation.json` di folder proyek.

Skor AI di browser (mode demo) dibandingkan dengan skor Python untuk vektor acak dan contoh di atas; hasilnya sama sampai pembulatan angka desimal terakhir.

## Uji dua edge dan MQTT

Diuji dengan laptop, ESP32 di Wokwi, dan broker HiveMQ publik:

- **Build ESP32:** PlatformIO `SUCCESS`.
- **Koneksi:** ESP32 terhubung ke HiveMQ, menerima suhu, dan mengirim MA10 setiap sampel.
- **Round-trip:** jumlah MA10 yang cocok di laptop bertambah sekitar satu per detik, tanpa pesan yang ditolak. Nomor sampel yang dinilai AI sama dengan nomor sampel di terminal Wokwi.
- **ESP32 berhenti:** setelah tab Wokwi ditutup, MA10 berhenti masuk dan AI berhenti menilai. Dashboard tidak menyatakan NORMAL.
- **Stop otomatis:** gangguan tinggi yang bertahan 10 detik mematikan mesin dan menampilkan "Mesin dimatikan otomatis".

`tests/check_mqtt_tls.py` menjalankan uji serupa secara lokal (HTTP, MQTT dengan TLS, dan ESP32 tiruan) tanpa membutuhkan internet. Uji ini membutuhkan `openssl`.

## Uji tampilan dashboard

Diuji dengan Chrome:

- Layar 1366×768, 1440×900, dan 1920×1080: dashboard muat satu layar tanpa scroll.
- Layar HP 390×844: tampilan bertumpuk tanpa scroll ke samping.
- Alur normal → suhu 37 °C (AI anomali) → suhu 43 °C (gangguan ringan) → suhu 65 °C (hitung mundur) → stop otomatis → Start lagi.
- Kelima panel (Skenario, Sensor, Grafik, Riwayat, Sistem) bisa dibuka dan ditutup dengan tombol X atau Esc.
- Mode demo browser lewat file lokal: banner demo tampil, AI menilai, dan skenario panas berlebih menaikkan status.
- Tidak ada error JavaScript di semua alur di atas.
