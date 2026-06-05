import os
# Suppress resource_tracker semaphore leak warning (fires in a subprocess, so
# warnings.filterwarnings can't reach it — must be set via env before it spawns)
_pw = os.environ.get("PYTHONWARNINGS", "")
_extra = "ignore::UserWarning:multiprocessing"
if _extra not in _pw:
    os.environ["PYTHONWARNINGS"] = f"{_pw},{_extra}" if _pw else _extra

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
warnings.filterwarnings("ignore", message=".*torchcodec.*")
warnings.filterwarnings("ignore", message=".*resource_tracker.*leaked semaphore.*", module="multiprocessing")
warnings.filterwarnings("ignore", message=".*unauthenticated.*HF Hub.*")

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # we use our own

from anduin.ui.menubar import main

main()
