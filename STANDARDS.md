# Standar Baku Diagram SEM & Olah Data IBM SPSS AMOS 23 (Universal Toolkit)

Dokumen ini mendefinisikan **aturan standar universal** untuk merancang, memvalidasi, mengompilasi, dan memverifikasi diagram SEM AMOS 23 dari proyek penelitian mana pun — tidak terikat pada satu topik atau jumlah konstruk tertentu.

Semua konstanta proporsi garis, ukuran objek, dan konvensi titik tempel disimpan secara terpusat di [spec/diagram_standard.json](spec/diagram_standard.json).

---

## 1. Standar Pemodelan Statistik

1. **Kovarians Eksogen Lengkap (Default Wajib):**
   - Seluruh konstruk bertipe `exogenous` dihubungkan berpasangan dengan panah dua arah (`<->`), yaitu sebanyak $\binom{k}{2}$ kovarians agar korelasi antarprediktor tidak dipaksa nol.
   - Hanya boleh dinonaktifkan (`"enforce_full_exogenous_covariances": false`) apabila spesifikasi riset/pengguna secara eksplisit melarang garis kovarians.
2. **Pemisahan Identitas Data vs Label Tampilan:**
   - `NameOrCaption` pada kotak indikator memakai nama kolom teknis `.sav` (`sav_var`, pengenal valid AMOS tanpa spasi), sedangkan `LongLabel` menampilkan teks indikator riset pada diagram.
   - Setiap konstruk memiliki tepat satu *reference indicator* (`ref_row`, koefisien panah `"1"`).
   - Setiap error indikator (`err`) dan disturbance endogen (`disturbance`) bertipe panah bernilai `"1"`.
3. **Pre-Flight Statistik via `AmosEngineLib`:**
   - Setiap model yang memiliki file `.sav` diverifikasi terlebih dahulu melalui `HeadlessFit.exe` sebelum digunakan di GUI Amos guna memastikan `FitStatus = 0`, `Admissible = True`, serta kesesuaian `DF`, `CMIN`, dan `RMSEA`.

---

## 2. Standar Visual, Garis, dan Proporsi Diagram (96 DPI)

1. **Satuan Koordinat:**
   - Seluruh API `AmosGraphics.PathDiagram` beroperasi pada piksel logis **96 DPI** (`1 inci = 96 px`), baik `PageWidth`, `PageHeight`, `DiagramDrawObserved/Unobserved`, maupun `PDElement.originX/Y/Width/Height`.
   - Halaman diinisialisasi dari salinan `Normal.amt` dengan ukuran halaman otomatis (`page_width`, `page_height`) dan opsi `$standardized` serta `$smc`.
2. **Proporsi Otomatis Compact Berbasis `font_size` ($F$) dan Multi-Line Wrapping:**
   - Ukuran font rekomendasi: `15` (padat/klasik) atau `20` (jelas/presentasi), seluruhnya dihitung proporsional dari [spec/diagram_standard.json](spec/diagram_standard.json) dengan **multi-line text wrapping** agar diagram tetap ringkas (~16 in lebar) dan tidak melebar berlebihan:
     - Lebar kotak indikator: $\min(18{,}5 \times F,\; \max(180,\; 13{,}5 \times F,\; 0{,}56 \times \lceil \text{maks\_karakter}/2{,}2 \rceil \times F))$; tinggi otomatis menyesuaikan jumlah baris teks.
     - Lebar oval konstruk: $\min(13{,}5 \times F,\; \max(150,\; 9{,}5 \times F,\; 0{,}68 \times \lceil \text{maks\_karakter}/2{,}4 \rceil \times F))$; tinggi menyesuaikan jumlah baris.
     - Diameter lingkaran error/disturbance: $\max(30,\; 2{,}0 \times F)$; jarak ke kotak: $\max(18,\; 1{,}2 \times F)$.
     - Jarak antarbaris indikator: `rectHeight + max(6, 0.35 * F)`; koridor tengah minimum $\max(210,\; 12{,}5 \times F,\; 1{,}15 \times \text{oval\_w})$.
3. **Aturan Garis, Panah, dan Lengkung Kovarians:**
   - Kolom kiri digambar **dari bawah ke atas** agar `DiagramDrawCovariance()` + `DragMouse(upper, lower)` melengkungkan busur **menghadap ke depan (koridor tengah)**.
   - Rasio kelengkungan (`cov_sagitta_ratio`) = `0.25` terhadap panjang tali busur; pasangan berdekatan memakai titik tempel `[0.58, 0.42]`, sedangkan pasangan melompati baris memakai `[0.52, 0.48]`.
   - Jarak antara sisi kanan kotak kiri dan pusat oval kiri otomatis dijaga lebih lebar dari sagita busur terpanjang supaya lengkungan bersih dari kotak indikator baik di sisi timur maupun barat.
   - Konvensi titik tempel persegi panjang: `utara = 0.125`, `timur = 0.375`, `selatan = 0.625`, `barat = 0.875`.
   - Konvensi titik tempel elips/lingkaran: `utara = 0.25`, `timur = 0.5`, `selatan = 0.75`, `barat = 1.0` (**jangan gunakan `0.0`**).
4. **Blok `Goodness of Fit` & Refresh Geometri:**
   - Memakai makro dinamis AMOS (`\cmin`, `\p`, `\cmindf`, `\df`, `\rmsea`, `\gfi`, `\AGFI`, `\tli`, `\cfi`, `\nfi`) dan diletakkan otomatis pada ruang kosong terverifikasi bebas tabrakan (kolom kanan bawah jika asimetris, atau koridor tengah bawah jika seimbang).
   - Setelah seluruh elemen tercipta, lakukan `el.Width = el.Width` pada semua variabel lalu `DiagramZoom(...)`.
5. **Larangan Keras API AMOS:**
   - Jangan memanggil `ToolsSmart`, `EditTouchUp`, `EditFitToPage`, atau `DiagramDrawUniqueVariable`.

---

## 3. Gerbang Kualitas Wajib Sebelum Deploy

Setiap perubahan pada model/standar harus lolos keempat gerbang berikut (`exit code = 0`):
1. **Validasi Skema & Kolom `.sav`:** ID unik valid, `ref_row` valid, kovarians eksogen lengkap $\binom{k}{2}$, seluruh `sav_var` ada di `.sav` (bila `.sav` disetel).
2. **Gerbang Tabrakan Geometri (`0` masalah):** tidak ada elemen keluar halaman, tumpang tindih bentuk, panah menabrak kotak/oval lain, panah *loading* bersilangan, busur kovarians menyentuh kotak/oval, atau blok `Goodness of Fit` menabrak elemen.
3. **Kompilasi x86 & Kontrak `IPlugin`:** `vbc.exe /platform:x86` berhasil dan refleksi PowerShell 32-bit menemukan `AmosGraphics.IPlugin` publik.
4. **Verifikasi Statistik Headless (jika ada `.sav`):** `HeadlessFit.exe` selesai dengan `FitStatus = 0` dan `Admissible = True`.
