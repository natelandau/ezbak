"""Backward-compatible shim. Superseded by ezbak.container."""

from ezbak.container import do_backup, do_restore, log_debug_info, main

__all__ = ["do_backup", "do_restore", "log_debug_info", "main"]


if __name__ == "__main__":
    main()
