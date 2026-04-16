"""Variant of sensitivity_analysis.py that uses the trained meta-model's
*actual* threshold from logs/meta_config.pkl (0.75), matching what the live
bot uses, rather than the hard-coded 0.50 in sensitivity_analysis.py.

Reads all other logic from sensitivity_analysis.py — just overrides the
META_THRESHOLD module global before main() runs. Read-only analysis, does
not modify production files.
"""
import pickle
from pathlib import Path

import sensitivity_analysis as sa

# Load the real threshold from the trained-model config
cfg = pickle.loads((Path("logs") / "meta_config.pkl").read_bytes())
live_threshold = float(cfg["best_threshold"])
print(f"Using live meta-filter threshold from meta_config.pkl: {live_threshold}")
sa.META_THRESHOLD = live_threshold

# Redirect the output path so we don't clobber the 0.50-threshold report
orig_report_path = sa.ROOT / "sensitivity_report.md"
target_path = sa.ROOT / "sensitivity_report_meta_live.md"


def _patched_main():
    # Monkey-patch Path.write_text only for the report file
    orig_write = Path.write_text
    def redirected_write(self, data, *args, **kwargs):
        if self == orig_report_path:
            return orig_write(target_path, data, *args, **kwargs)
        return orig_write(self, data, *args, **kwargs)
    Path.write_text = redirected_write
    try:
        sa.main()
    finally:
        Path.write_text = orig_write


if __name__ == "__main__":
    _patched_main()
    print(f"\nReport written to {target_path}")
