"""neokiosk: the web UI full-screen on the machine PC's own monitor (docs/KIOSK.md).

    python3 -m neokiosk --cage     start the Wayland kiosk compositor (cage) with the window in it
    python3 -m neokiosk            only the browser window (inside cage or any Wayland/X session)

Settings: /etc/neoden/kiosk.ini. Installed by neokiosk/install.sh (systemd neoden-kiosk.service).
"""
