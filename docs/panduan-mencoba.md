# Panduan mencoba MachineGuard AI

Ada tiga cara, dari yang paling mudah.

## Cara 1: langsung di browser, tanpa instalasi

Buka [link GitHub Pages]. Dashboard berjalan sepenuhnya di browser: simulator motor, pemrosesan getaran, alarm, dan model AI yang sama dengan versi laptop. ESP32 dan MQTT diemulasikan, dan banner di atas dashboard menyatakannya. Cara ini tidak perlu satu jaringan dengan kami.

Yang bisa dicoba:

1. **Tunggu sekitar 10 detik.** AI baru menilai setelah 10 sampel suhu pertama terkumpul. Setelah itu cincin di kanan menunjukkan skor anomali; di bawah 1,0 berarti normal.
2. **Gangguan bertahap.** Buka panel **Skenario**, pilih **Panas berlebih** atau **Getaran berlebih**. Nilai sensor berubah perlahan, status naik dari ringan ke sedang, dan skor AI ikut naik.
3. **Stop otomatis.** Buka panel **Sensor**, geser suhu ke sekitar 65 °C. Layar menjadi merah dan hitung mundur 10 detik dimulai. Kalau tidak ditekan **Stop sekarang**, mesin dimatikan otomatis. Tekan **Auto** pada suhu, lalu **Start** untuk menyalakan lagi.
4. **AI melihat yang tidak terlihat alarm.** Di panel **Sensor**, geser suhu ke 37 °C. Alarm batas masih normal (batasnya 40 °C), tetapi AI menandai pola tidak biasa dan cahaya di bawah motor berubah ungu.
5. **Grafik.** Panel **Grafik** menampilkan suhu dibandingkan rata-rata 10 sampel (MA10) dan gelombang getaran 2 detik terakhir.
6. **Reset** mengembalikan semua ke awal.

Gangguan acak otomatis menyala sejak awal. Matikan di panel **Skenario** kalau ingin menguji satu skenario saja.

## Cara 2: sistem lengkap di komputer sendiri

Cara ini menjalankan dua edge sungguhan: laptop dan ESP32 di Wokwi, yang bertukar data lewat broker HiveMQ.

### Yang perlu disiapkan

- Windows 10 atau 11 dengan koneksi internet
- [Python](https://www.python.org/downloads/) 3.10 atau lebih baru. Saat memasang, centang **Add python.exe to PATH**.
- [VS Code](https://code.visualstudio.com/) dengan ekstensi **PlatformIO IDE** dan **Wokwi Simulator**. Wokwi meminta aktivasi lisensi gratis lewat browser saat pertama kali dipakai.

### Langkah

1. **Unduh kode.** Di halaman GitHub, klik **Code → Download ZIP**, lalu ekstrak. Bisa juga lewat `git clone`.
2. **Jalankan ESP32 (edge B).**
   1. Di VS Code, pilih **File → Open Folder**, lalu buka folder `edge_esp32`.
   2. Tunggu PlatformIO selesai menyiapkan. Saat pertama kali, PlatformIO mengunduh toolchain ESP32 dan bisa memakan 5–10 menit.
   3. Klik tombol centang (**PlatformIO: Build**) di status bar bawah, tunggu sampai muncul `SUCCESS`.
   4. Tekan `Ctrl+Shift+P`, pilih **Wokwi: Start Simulator**.
   5. Biarkan tab **Wokwi Simulator** tetap terbuka. Terminal Wokwi menampilkan `ESP32 MQTT SIAP | HiveMQ`, lalu baris `seq ... | suhu ...` setiap detik setelah laptop berjalan.
3. **Jalankan laptop (edge A).** Di folder proyek, klik dua kali `jalankan.cmd`. Saat pertama kali, peluncur menyiapkan environment Python, mengunduh dependensi (beberapa menit), dan melatih model. Setelah itu browser terbuka di `http://127.0.0.1:8080`.
4. **Klik Start** di dashboard.

### Tanda sistem berjalan

- Tiga indikator di kanan atas (Server lokal, MQTT HiveMQ, ESP32 Wokwi) berwarna hijau.
- Angka **MA10 dari ESP32** di bawah cincin AI terisi.
- Nomor sampel yang dinilai AI sama dengan nomor `seq` di terminal Wokwi.

Untuk membuktikan dua edge benar-benar bekerja, hentikan simulator Wokwi. Dalam 10 detik dashboard menampilkan "Data ESP32 basi" dan AI berhenti menilai. Nyalakan lagi simulatornya, dan data kembali masuk.

Skenario pada Cara 1 bisa dicoba dengan cara yang sama di sini.

## Cara 3: laptop saja, tanpa Wokwi

Klik dua kali `jalankan-offline.cmd`. Dashboard terbuka di `http://127.0.0.1:8081` dan MA10 dihitung oleh laptop. Mode ini diberi label jelas dan tidak dipakai sebagai bukti dua edge.

## Kalau ada masalah

| Masalah | Yang dicoba |
|---|---|
| Dashboard terus "Menunggu ESP32" atau "Data ESP32 basi" | Pastikan tab Wokwi Simulator masih terbuka dan timernya berjalan. Klik tombol hijau ↻ di simulator untuk memulai ulang. |
| **Wokwi: Start Simulator** tidak memunculkan apa pun | Tekan `Ctrl+Shift+P`, pilih **Developer: Reload Window**, lalu coba lagi. |
| `Port 8080 sedang dipakai` | Ada jendela CMD MachineGuard lain yang masih berjalan. Tutup dengan `Ctrl+C`, lalu jalankan lagi. |
| Pemasangan dependensi gagal | Periksa koneksi internet, lalu klik dua kali `jalankan.cmd` lagi. |
| `Python tidak ditemukan` | Pasang Python dan centang **Add python.exe to PATH**, lalu buka ulang jendela. |
| Ada orang lain menjalankan proyek ini di waktu yang sama | Semua salinan memakai topik MQTT yang sama, jadi datanya bisa tercampur. Ganti akhiran `PROJECT_ID` di `config.py` dan tiga baris topik di `edge_esp32/src/main.cpp` dengan teks lain yang sama, lalu build ulang ESP32. |
