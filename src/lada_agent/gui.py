"""lada-agent Windows Tkinter GUI entry point."""

import sys
import tkinter as tk

from lada_agent.lada_gui import LadaGUI


def main() -> None:
    root = tk.Tk()
    LadaGUI(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
