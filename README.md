# AMOS 23 SEM Diagram & Estimation Pipeline

Universal, project-agnostic automation pipeline for **IBM SPSS Amos 23** (`AmosGraphics.PathDiagram` + `AmosEngineLib`). Converts compact text DSL (`H1: A -> B`) or declarative JSON specs into collision-free, standardized AMOS 23 VB.NET plugins (`.dll`), interactive HTML layout previews, and headless statistical fit checks.

---

## Key Features

- **Text-First & JSON Specs:** Write models in compact text DSL (`.txt`, supporting `->` arrows and Indonesian causal phrasing) or structured JSON (`.json`).
- **Adaptive 96-DPI Layout Engine:** Automatically scales canvas dimensions, shapes, row gaps, center corridors, and Goodness-of-Fit placement from `font_size` and label length using [spec/diagram_standard.json](spec/diagram_standard.json).
- **Zero-Collision Geometric Gate:** Verifies zero shape overlaps, zero arrow-through-ellipse/box crossings, and clean east-side covariance arcs before compilation.
- **32-bit (x86) AMOS 23 Compilation & Reflection:** Compiles VB.NET `IPlugin` DLLs with `vbc.exe /platform:x86` and verifies `AmosGraphics.IPlugin` contracts via 32-bit PowerShell.
- **Headless Statistical Verification:** Compiles and executes `HeadlessFit.exe` (`AmosEngineLib`) when an SPSS `.sav` dataset is supplied (`-SavFile` or `SAV:`), reporting `FitStatus`, `Admissible`, `CMIN`, `DF`, and `RMSEA`.
- **Safe Plugin Deployment:** `-Deploy` copies the compiled DLL to AMOS 23 `Plugins/` and automatically cleans up old custom plugins while preserving all 6 built-in AMOS 23 plugins (opt out with `-KeepOldPlugins`).

---

## Prerequisites

- **OS:** Windows (PowerShell 5.1+)
- **IBM SPSS Amos 23** installed under `C:\Program Files (x86)\IBM\SPSS\Amos\23` (or `Program Files`)
- **.NET Framework 4.0 x86** (`%WINDIR%\Microsoft.NET\Framework\v4.0.30319\vbc.exe`)
- **Python 3.10+** (optional `pyreadstat` for `.sav` column pre-validation)

---

## Quick Start

### 1. Add `bin\` to your `PATH` (optional, for global `amos-sem` command)
Add `<repo-root>\bin` to your Windows User `PATH` so `amos-sem` can be invoked from any directory.

### 2. Run from a Text Model (`.txt`)
```powershell
amos-sem -SpecFile spec\template_model.txt
```
Or without adding `bin\` to `PATH`:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\amos_pipeline.ps1 -SpecFile spec\template_model.txt
```

### 3. Attach an SPSS `.sav` Dataset at Runtime
```powershell
amos-sem -SpecFile presets\green_transparency_advocacy.txt -SavFile "path\to\data.sav"
```

### 4. Override Font Size (`15` or `20`)
```powershell
amos-sem -SpecFile spec\template_model.txt -FontSize 15
```

### 5. Deploy to IBM SPSS Amos 23 (Administrator PowerShell)
Close AMOS 23 first, then run in an elevated PowerShell:
```powershell
amos-sem -SpecFile spec\template_model.txt -Deploy
```
Add `-KeepOldPlugins` if you want to keep previously deployed custom plugins alongside the new one.

---

## Pipeline Stages

| Stage | Script | Responsibility |
| :--- | :--- | :--- |
| `[0/5]` | `scripts/text_to_spec.py` | Converts `.txt` DSL to `.spec.json` (skipped for `.json` inputs) |
| `[1/5]` | `scripts/generate_from_spec.py` | Validates schema & `.sav` columns, runs 0-collision gate, emits `plan/assessment.html`, `<Namespace>.vb`, and `HeadlessFit.vb` |
| `[2/5]` | `vbc.exe /platform:x86` | Compiles `<Namespace>.vb` into `build/<Namespace>.dll` |
| `[3/5]` | 32-bit PowerShell (`SysWOW64`) | Verifies `AmosGraphics.IPlugin` reflection & stages `.sav` to `%LOCALAPPDATA%\AmosDevelopment\Amos\23\` |
| `[4/5]` | `HeadlessFit.exe` (`AmosEngineLib`) | Runs headless ML estimation when a `.sav` file is provided |
| `[5/5]` | `scripts/amos_pipeline.ps1 -Deploy` | Cleans old custom plugins (preserving built-in Amos 23 DLLs) and deploys to `Amos\23\Plugins\` |

---

## Text DSL Syntax (`.txt`)

```text
PLUGIN: My SEM Model
NAMESPACE: MyCustomSEM
FONT: 20
COV: all
LEFT: Construct A, Construct B
RIGHT: Mediator M, Outcome Y

H1: Construct A -> Mediator M
H2: Construct B -> Mediator M
H3: Mediator M -> Outcome Y

Construct A: *A1 | Indicator A1, A2 | Indicator A2, A3 | Indicator A3
Construct B: *B1 | Indicator B1, B2 | Indicator B2, B3 | Indicator B3
Mediator M: *M1 | Indicator M1, M2 | Indicator M2, M3 | Indicator M3
Outcome Y: *Y1 | Indicator Y1, Y2 | Indicator Y2, Y3 | Indicator Y3
```
- Prefix `*` marks the fixed-`1` reference indicator for each construct.
- `| Display Label` separates the technical `.sav` column name from the diagram box text.
- `COV:` supports `all` ($C(k,2)$ exogenous pairs), `none`, or explicit `A <-> B` lines.

---

## Repository Structure

```text
amos-sem-pipeline/
├── bin/
│   └── amos-sem.cmd               # CLI wrapper for PATH invocation
├── spec/
│   ├── diagram_standard.json      # Universal 96-DPI proportions, anchors & rules
│   ├── template_model.txt         # Neutral text DSL starter
│   └── template_spec.json         # Neutral JSON spec starter
├── presets/
│   ├── sustainable_business.json  # Example 7-construct preset
│   ├── nmax_bisdig.txt            # Example 4-construct mediation preset
│   ├── digital_ecosystem_national.txt
│   └── green_transparency_advocacy.txt
├── scripts/
│   ├── amos_pipeline.ps1          # 5-stage PowerShell orchestrator
│   ├── generate_from_spec.py      # Layout engine, collision gate, VB & HTML emitter
│   └── text_to_spec.py            # Text DSL -> JSON spec parser
├── AGENTS.md                      # Agent rules for working in this repo
├── PIPELINE.md                    # Quick usage notes (Indonesian)
├── STANDARDS.md                   # Formal visual & statistical standards (Indonesian)
└── README.md
```

Generated artifacts (`build/`, `plan/`, compiled `.dll`/`.exe`, generated `*SEM.vb`/`HeadlessFit.vb`, and `.sav` datasets) are excluded via `.gitignore`.
