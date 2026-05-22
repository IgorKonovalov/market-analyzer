"""Per-domain repositories. New repositories land here as their own files; the
pre-existing `BarRepository` and `AnnotationsRepository` still live at the
package root (`persistence/repository.py`, `persistence/annotations_repository.py`)
for backwards compatibility — only new domain repositories adopt the
subdirectory layout introduced by Plan 0008 phase 3.
"""
