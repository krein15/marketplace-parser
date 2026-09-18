"""Entry point of the desktop application (also used by PyInstaller).

Started without arguments it opens the window; with arguments it behaves as the command-line interface,
so the built .exe can also be used from a scheduled task:

    MarketplaceParser.exe --wb -q "наушники" -n 100 -r 20 -o D:\\Отчёты
"""

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from mpparser.__main__ import main as cli_main

        sys.exit(cli_main())

    from mpparser.gui.app import main

    main()
