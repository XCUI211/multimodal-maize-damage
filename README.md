# Multimodal Maize Damage

Purpose

This repository investigates whether fusing images with environmental data improves classification accuracy for crop health drought weed damage and other damage types At this stage no data processing or model training is run

Minimal layout

- `data/` Data related folders
  - `data/raw/` Publisher original files read only not tracked by git
  - `downloads/` Temporarily stored download packages not tracked
- `src/` Source code
  - `src/data/` download inspect and matching utilities to be implemented
  - `src/checks/` structural and schema inspection scripts
- `notebooks/` Analysis notebooks if needed but follow project rules
- `tests/` Unit tests and checks

Files to create next (no processing yet)

- `src/data/download.py` script to download publisher provided archives and place them under `downloads`
- `src/checks/inspect_json_schema.py` script to examine actual JSON examples list field names and unique label values
- `src/checks/match_images_labels.py` script to match images to label records and produce class count reports

Next steps (implement in order)

1. Add download script that only writes into `downloads` and never overwrites `data/raw`
2. Add schema inspection script that opens sample JSONs and reports field names types and unique label values
3. Add matching script that joins image files to publisher labels and reports per class counts

Constraints and conventions

- Use relative paths and command line arguments in all scripts
- All source code must be written in English only
- Comments in source files must avoid punctuation characters and numeric ordered lists such as 1 2 3 with separators
- Do not hardcode environment or user specific paths
- Keep dependencies minimal and focused
