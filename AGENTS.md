# Instruksi Agent — Toolkit Standar Universal SEM AMOS 23

Repositori ini adalah **pipeline standar universal untuk diagram & olah data SEM IBM SPSS AMOS 23** (`AmosGraphics.PathDiagram` + `AmosEngineLib`) yang dirancang agar agent mana pun langsung tahu langkah kerja yang benar saat pengguna mengirim **gambar diagram**, **rencana teks**, **dokumen `.docx`**, maupun **dataset `.sav`**.

Seluruh konstanta proporsi garis, *multi-line wrapping*, ukuran kotak/oval, jarak margin, titik tempel panah, dan kaidah statistik dikunci di [STANDARDS.md](STANDARDS.md) dan [spec/diagram_standard.json](spec/diagram_standard.json).

---

## 1. Alur Kerja Berdasarkan Masukan Pengguna

### Jalur A — Masukan Berupa Gambar (Screenshot / PDF / Foto Diagram)
1. **Ekstrak ke Teks Terstruktur (`presets/<nama>.txt`):**
   - Catat seluruh konstruk, indikator (tandai `*` pada indikator acuan berkoefisien `1`), jalur struktural (`H1: A -> B`), dan kovarians eksogen (`COV: all`, `COV: none`, atau pasangan eksplisit `A <-> B`) mengikuti format [spec/template_model.txt](spec/template_model.txt).
   - Tentukan urutan `LEFT:` dan `RIGHT:` agar searah alur kausal dan bebas persilangan panah.
   - Minta konfirmasi singkat hanya jika ada label yang buram/terpotong di gambar.
2. **Validasi & Build Pipeline:** Jalankan `amos-sem -SpecFile presets\<nama>.txt` (atau `scripts\amos_pipeline.ps1`). Periksa ringkasan `STANDARD_OK` dan `plan\assessment.html`.

### Jalur B — Masukan Sudah Berupa Teks / Rencana Diagram (`H1 -> H2`)
Jika pengguna memberikan daftar hipotesis atau rencana model dalam teks (format `H1: A -> B`, rantai `A -> M -> Y`, atau kalimat kausal Indonesia seperti `H1: X berpengaruh signifikan terhadap Y`):
1. Simpan ke `presets/<nama>.txt` (atau `spec/<nama>.txt`).
2. **Langsung eksekusi satu perintah:**
   ```powershell
   amos-sem -SpecFile presets\<nama>.txt
   ```
   *(Atau tanpa `PATH`: `powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile presets\<nama>.txt`.)*
3. Pipeline otomatis menjalankan `[0/5]` konversi `.txt -> .spec.json`, `[1/5]` validasi standar + 0-collision gate + `plan\assessment.html` + emit VB.NET, `[2/5]` kompilasi DLL x86, `[3/5]` refleksi 32-bit `IPlugin` + staging `.sav`, dan `[4/5]` `HeadlessFit.exe` (bila ada `.sav`).
4. Untuk mengganti ukuran font saja tanpa mengedit spec: tambahkan `-FontSize 15` atau `-FontSize 20`.

### Jalur C — Masukan Berupa Dokumen Word (`.docx`)
1. Ekstrak paragraf, tabel indikator/hipotesis, dan gambar lampiran (`word/media/*`) dalam satu pass Python (`zipfile`).
2. Petakan konstruk, indikator acuan (`*`), jalur `H1: A -> B`, dan `COV:` ke `presets/<nama>.txt`.
3. Jalankan `amos-sem -SpecFile presets\<nama>.txt` (jika belum ada `.sav`, tahap `[4/5]` otomatis dilewati dengan aman).

---

## 2. Aturan Audit & Pemetaan Dataset (`.sav`)

1. **Selalu periksa `.sav` terlebih dahulu** menggunakan Python (`pyreadstat`): cek jumlah observasi ($N$), jumlah kolom, nama kolom, dan label variabel.
2. **Jangan pernah mengasumsikan atau menimpa diam-diam jika nama kolom `.sav` berbeda dari model:**
   - Laporkan ketidaksesuaiannya kepada pengguna terlebih dahulu.
   - Apabila pengguna mengonfirmasi file `.sav` tersebut memang file yang benar (mis. dataset template dengan jumlah kelompok & indikator yang cocok), petakan secara konsisten **1-lawan-1 mengikuti urutan `e1`–`eN` pada diagram** (`NameOrCaption` = nama kolom `.sav`, `LongLabel` = teks label diagram) dan jelaskan pemetaan tersebut secara transparan.
3. **Sertakan data saat menjalankan pipeline** lewat `-SavFile "<path\ke\data.sav>"` (lebih disarankan daripada menaruh path lokal absolut di preset Git). Pipeline akan otomatis menyalin `.sav` ke `%LOCALAPPDATA%\AmosDevelopment\Amos\23\`.

---

## 3. Pengetahuan & Invarian Kritis AMOS 23 (Wajib Diingat Agent)

Aturan teknis berikut sudah terverifikasi langsung pada IBM SPSS Amos 23 dan telah ditanamkan ke generator — **jangan melanggarnya saat memodifikasi atau memperluas pipeline**:
- **AMOS 23 Tidak Auto-Wrap Teks:** `LongLabel` dan `NameOrCaption` satu baris tidak dibungkus otomatis oleh AMOS dan akan meluber menutupi lingkaran error (`e1..eN`). Pembungkusan teks wajib memakai pemisah baris eksplisit `vbCrLf` (sudah diotomatisasi oleh `generate_from_spec.py` berdasarkan `rect_wrap_chars` / `oval_wrap_chars`).
- **Koordinat 96 DPI & `Normal.amt`:** Seluruh koordinat `AmosGraphics.PathDiagram` adalah piksel logis **96 DPI**; kanvas, `$standardized`, dan `$smc` diinisialisasi dari klon `Normal.amt`; wajib memanggil `el.Width = el.Width` pada semua elemen sebelum `DiagramZoom(...)`.
- **Titik Tempel & Kovarians Maju:**
  - Persegi panjang: `0.125` (utara), `0.375` (timur), `0.625` (selatan), `0.875` (barat).
  - Elips/lingkaran: `0.25` (utara), `0.5` (timur), `0.75` (selatan), `1.0` (barat — **jangan pernah `0.0`**).
  - Kolom kiri dibuat **dari bawah ke atas**; kovarians eksogen digambar dengan `DragMouse(upper, lower)` dan `position1/2` (`0.58/0.42` bertetangga, `0.52/0.48` melompati baris).
- **API Terlarang:** Jangan pernah memanggil `ToolsSmart`, `EditTouchUp`, `EditFitToPage`, atau `DiagramDrawUniqueVariable`.
- **Toolchain 32-bit (`x86`):** Plugin `AmosGraphics` dan `AmosEngineLib` wajib dikompilasi dengan `vbc.exe /platform:x86`, diverifikasi refleksi `IPlugin` lewat `SysWOW64\WindowsPowerShell\v1.0\powershell.exe`, dan dijalankan dari `$amosDir` dengan `$env:AMOS_DIR`.
- **Jebakan `GenerateDefaultCovariances`:** Wajib memanggil `Sem.GenerateDefaultCovariances(False)` pada model struktural maupun model dengan kovarians eksogen `< C(k,2)` agar AMOS tidak diam-diam mengkorelasikan seluruh variabel eksogen.
- **Integritas Statistik & Urutan `Sem.Dispose()`:**
  - Jangan pernah menyalin angka kecocokan dari preset/contoh atau menyamakan CFA dengan model struktural.
  - Saat membuat laporan lengkap dari `AmosEngineLib`, jalankan Model Struktural (`NormalityCheck()`, `TotalEffects()`, `ResidualMoments()`, `Bootstrap(2000)`, `ConfidenceBC(95)`), CFA multi-faktor, CFA 1-faktor, dan ULMC langsung pada `.sav`.
  - **Penting:** Panggil `Sem.Dispose()` **sebelum** menyalin `Sem.TextOutputFileName()` agar isi file output benar-benar selesai ditulis ke disk untuk model yang bersangkutan.

---

## 4. Deploy & Kebersihan Repositori

- **Deploy ke Menu Plugins AMOS 23 (PowerShell Administrator, tutup AMOS terlebih dahulu):**
  ```powershell
  amos-sem -SpecFile "presets\<nama>.txt" -Deploy
  ```
  Langkah ini otomatis menghapus plugin kustom lama di `Amos\23\Plugins\` sambil tetap melindungi 6 plugin bawaan AMOS 23 (tambahkan `-KeepOldPlugins` jika ingin mempertahankan plugin kustom lain).
- **Kebersihan Kode & Git:**
  - Jangan mengedit hasil generate (`scripts/*SEM.vb`, `scripts/HeadlessFit.vb`, `plan/*`, `build/*`) secara langsung; ubah sumber `.txt`/`.json` atau generatornya.
  - Jangan menulis path absolut mesin lokal (`C:\...`, `D:\...`) di dalam skrip atau preset yang di-commit ke Git.
  - Jangan pernah commit file `.sav`, `build/`, `plan/`, `scratch/`, atau menjalankan `git push` tanpa perintah eksplisit dari pengguna.
