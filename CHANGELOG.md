# Changelog: radiation-sensor

Format: `YYYY-MM-DD [origin] summary`. Origin is `hub`, `section` or `import`.

- 2026-10-10 [import] Imported all 16 knowledge files and the instructions from the Claude
  Project "Radiation Sensor" (export 2026-10-10). The duplicate `claude/polismart_feature_analysis.md`
  (byte-identical) was dropped. Identifying values tokenized. `ingest/` matches the copy
  in `~/pm1630-ingest` (the only difference is one comment).
- 2026-10-10 [section] Instructions: the user's 2026-10-10 refresh of `pm1630_hospital_poc_project_instructions.md`
  (GCP backend, dashboard phases P0–P7, the `gcp-bridge` plan, the alarm model) is now the single
  `claude/project-instructions.md`. Two edits: the last "Relationship" bullet (which still called `~/onc-hq-lab`
  a public empty scaffold) was replaced with a Repository/sync bullet, and a tokens rule was added. The stale
  2026-09-26 `project-instructions-pending.md` was removed. **Open conflict:** these instructions say "Site A has
  2× MR33 plus one MR18", but the hub's `ip-vlan-plan.md` lists Site A as Rancho AP (MR18) + Aparta AP (MR33),
  and AP Pool JoTe (Q2PD serial, same family as the MR33) at Site B. Needs a dashboard check.
