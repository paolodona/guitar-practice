# Intentionally empty. Do not add re-exports here: this module is imported by
# every entry point (CLI, server, tests), so anything imported at this level
# is imported eagerly by all of them. A convenience re-export of a heavy
# module (analyze.py's librosa, render.py's subprocess-to-rubberband path)
# would silently pull that weight into every import, and no test would
# notice until someone without the optional extra installed hit an
# ImportError far from where they could explain it. See CLAUDE.md's
# "Layering" section.
