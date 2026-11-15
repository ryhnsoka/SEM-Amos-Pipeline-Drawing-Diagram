# Pipeline Universal Diagram & Olah Data SEM AMOS 23

Toolkit netral berbasis standar untuk mengubah deskripsi model SEM (dari **teks** ataupun hasil ekstraksi **gambar → teks**) menjadi plugin diagram **IBM SPSS AMOS 23** yang rapi, bebas tabrakan, dan terverifikasi secara statistik.

## Komponen Utama
1. **Standar Universal:** [STANDARDS.md](STANDARDS.md) & [spec/diagram_standard.json](spec/diagram_standard.json)
2. **Panduan Agent:** [AGENTS.md](AGENTS.md)
3. **Input Cepat Berbasis Teks (`.txt`):**
   - Template contoh: [spec/template_model.txt](spec/template_model.txt)
   - Parser teks → JSON: [scripts/text_to_spec.py](scripts/text_to_spec.py)
4. **Input JSON Langsung & Preset:**
   - Template netral: [spec/template_spec.json](spec/template_spec.json)
   - Contoh preset: [presets/sustainable_business.json](presets/sustainable_business.json)
5. **Generator Standar & Pipeline:**
   - [scripts/generate_from_spec.py](scripts/generate_from_spec.py) & [scripts/amos_pipeline.ps1](scripts/amos_pipeline.ps1)

## Cara Menjalankan
- **Dari teks langsung (`.txt`):**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\template_model.txt
  ```
- **Dari JSON spec langsung:**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\template_spec.json
  ```
- **Ubah ukuran font via CLI (`15` atau `20`):**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\template_model.txt -FontSize 15
  ```
- **Validasi saja tanpa kompilasi (`-ValidateOnly`) atau lewati `HeadlessFit` (`-SkipFit`):**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile presets\green_transparency_advocacy.txt -SavFile "data\riset.sav" -ValidateOnly
  powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\template_model.txt -SkipFit
  ```
- **Deploy ke Amos 23 (PowerShell Administrator, non-destruktif):**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\proyek_kamu.txt -Deploy -WhatIf
  powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\proyek_kamu.txt -Deploy
  ```
  *(Plugin lain tetap dipertahankan secara default; tambahkan `-PruneOldPlugins` hanya jika memang ingin membersihkan plugin kustom lama.)*
