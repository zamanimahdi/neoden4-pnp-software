# boot.d

Scripts run once at every boot of the Ubuntu machine PC, before neoserver starts
(systemd `neoden-boot.service`, as root, working directory `/opt/neoden/app`).

- Only executable `*.sh` files run, sorted by name (`10-neomach.sh`, `20-...`).
- A failing script is logged (`journalctl -u neoden-boot`) and does not stop the boot.
- Each script gets 120 s.
