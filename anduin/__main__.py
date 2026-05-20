import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
warnings.filterwarnings("ignore", message=".*torchcodec.*")
warnings.filterwarnings("ignore", message=".*resource_tracker.*leaked semaphore.*", module="multiprocessing")

from anduin.ui.menubar import main

main()
