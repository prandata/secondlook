"""Second Look Day-3 gate harness: offline, standard library only.

Nothing in this package opens a network connection, starts a process or
calls a model. It reads CSV and JSON files and writes CSV, JSON and Markdown.
"""

# The locked gate (DAT-795 5ab73cd8, from DAT-837 section 7 and Nova's ff13daca).
CONFIDENCE = 0.95            # one-sided, exact Clopper-Pearson
FP_UPPER_MAX = 0.02          # pass: false-positive upper bound <= 2%
CATCH_LOWER_MIN = 0.40       # pass: lower bound on AI images caught >= 40%
MIN_REAL = 400               # test-set denominators the gate was locked with
MIN_AI = 200
MIN_DEV_REAL = 100           # DAT-837 section 7.1: dev set of at least 100 + 100
MIN_DEV_AI = 100
DEGRADED_WARN_UPPER = 0.05   # DAT-837 section 7.5: condition C above 5% -> UI warning

# Condition names (DAT-837 section 7.3). Other arm names are accepted and
# reported after these, in alphabetical order.
ARM_ORIGINAL = "original"                 # (A)
ARM_FORMAT_MATCHED = "format_matched"     # (B) JPEG q90, long side 1,536 px
ARM_COMPRESSED = "compressed_q70_1080"    # (C) JPEG q70, long side 1,080 px
ARM_SCREENSHOT = "screenshot"             # (D) optional
KNOWN_ARMS = (ARM_ORIGINAL, ARM_FORMAT_MATCHED, ARM_COMPRESSED, ARM_SCREENSHOT)
# DAT-795 scope 2: A, B and C are each reported. A run without one is INVALID.
REQUIRED_ARMS = (ARM_ORIGINAL, ARM_FORMAT_MATCHED, ARM_COMPRESSED)
