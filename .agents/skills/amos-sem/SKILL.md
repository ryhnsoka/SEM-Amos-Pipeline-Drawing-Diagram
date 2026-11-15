---
name: amos-sem
description: >-
  Directly invoke IBM SPSS AMOS 23 SEM diagram & estimation pipeline functions
  (`scripts/amos_pipeline.ps1`, `scripts/text_to_spec.py`, `scripts/generate_from_spec.py`,
  `scripts/sav_utils.py`, and `tests/`) via `/amos-sem` or when automating AMOS 23 SEM tasks.
---

# `/amos-sem` — Perintah Langsung Pipeline SEM AMOS 23

Saat skill ini dipanggil, agent dapat langsung mengeksekusi fungsi pipeline berikut dari root repositori:

| Fungsi | Perintah Langsung |
| :--- | :--- |
| Inspeksi `.sav` & cek indikator | `python scripts\sav_utils.py --sav "<path.sav>" [--spec <spec.json>]` |
| Konversi `.txt` DSL -> `.json` | `python scripts\text_to_spec.py --in <model.txt> --out plan\<nama>.spec.json` |
| Validasi spec, `.sav`, & geometri saja | `powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile <spec> [-SavFile "<path.sav>"] -ValidateOnly` |
| Build DLL x86 & refleksi tanpa `HeadlessFit` | `powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile <spec> [-SavFile "<path.sav>"] -SkipFit` |
| Pipeline penuh (validasi + build + `HeadlessFit`) | `powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile <spec> [-SavFile "<path.sav>"]` |
| Pratinjau / Deploy non-destruktif ke AMOS 23 | `powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile <spec> [-SavFile "<path.sav>"] -Deploy [-WhatIf] [-PruneOldPlugins]` |
| Jalankan seluruh test regresi | `python -m unittest discover -s tests -v` |

## Aturan Eksekusi Agent
1. Selalu periksa `.sav` terlebih dahulu menggunakan `python scripts\sav_utils.py --sav "<path.sav>"` sebelum memetakan kolom.
2. Jangan pernah menebak pemetaan indikator berdasarkan posisi kolom.
3. Periksa `plan\dataset_report.json` dan `plan\execution_report.json` untuk memisahkan status build dari status estimasi statistik (`succeeded`, `inadmissible`, `estimation_failed`, `runtime_error`, `skipped`).
4. Sebelum `-Deploy`, pastikan `AmosGraphicsCLI.exe` tertutup; jalankan `-WhatIf` terlebih dahulu bila perlu pratinjau.
