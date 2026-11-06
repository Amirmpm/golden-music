"""Thin importable entry point (v3.0).

Windows can `import main` for the .pyw file directly; on Linux/macOS (and in
CI) this .py shim provides the same module surface.  main.pyw remains the
double-click entry point and simply delegates here.
"""
from gm.core import (  # noqa: F401
    RepeatMode, View, SortMode, _is_scratch_path, _track_item_text,
    _list_subdirs, _dir_has_subdirs, _rgba, log,
)
from gm.ui.widgets import IconButton, CoverLabel, MarqueeLabel, LoadingOverlay  # noqa: F401
from gm.ui.delegates import TrackRowDelegate  # noqa: F401
from gm.ui.player_bar import PlayerBar  # noqa: F401
from gm.ui.rail import SideRail  # noqa: F401
from gm.ui.stats_widgets import StatsChartBar, StatTrackRow  # noqa: F401
from gm.workers import CoverLoaderThread, TagLoaderThread, AlbumsWorkerThread  # noqa: F401
from gm.window import MainWindow, APP_NAME  # noqa: F401
from gm.app import (  # noqa: F401
    main, _acquire_single_instance_lock, _forward_to_running,
    _install_crash_guard, _enable_windows_glass, _audio_files_from_argv,
)
from random import choice, randrange  # noqa: F401  (legacy module surface)

if __name__ == "__main__":
    raise SystemExit(main())
