# Design

Add a pure `cnc_file_findings(path, where)` helper. Normalize separators only to isolate the basename; preserve the original path in output. Combine case-insensitive basename patterns with category-specific extensions. Program hints use extension alone. Add `cnc_findings` alongside the existing `cnc_files` list. Render reasons and hardware-check notes in the runbook, which also feeds the HTML report.

These rules are intentionally incomplete hints, not a software database. Configuration-content hardware checks remain separate existing behavior. Synthetic tests cover positive matches, directory/content false positives, extension/case handling, mock inventory wiring and a real generated FAT image through runbook output.
