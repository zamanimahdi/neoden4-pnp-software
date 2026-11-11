# The machine's PC (from the original XP disk image)

Read from the XP registry and `setupapi.log` of the original SSD image. The XP install is Chinese; it was
cloned onto several SSDs (SanDisk i100 / i110 8 GB and 16 GB, a "SSD SATAIII 16GB").

| part | what Windows saw |
|---|---|
| CPU | Intel Atom D525 1.8 GHz (64-bit capable) |
| chipset | Intel NM10 (ICH7 family): DMI bridge A000, LPC 27BC, PCIe root 27D0, SMBus 27DA, USB 27C8..27CC |
| graphics | Intel GMA 3150 (A001 / A002); Linux driver i915 |
| network | Realtek RTL8101E-family PCIe Fast Ethernet 10/100 (10EC:8136, subsystem 10EC:0123); Linux driver r8169 |
| Wi-Fi | **none** (no Wi-Fi device in the PCI or USB lists) |
| audio | Realtek ALC662 (HD audio) |
| disk controller | Intel 27C0 in **IDE mode** ("standard dual-channel PCI IDE"); Linux ata_piix |
| serial | two ACPI PNP0501 ports: **COM1 = 0x3F8 IRQ 4, COM2 = 0x2F8 IRQ 3** |
| BIOS | ACPI BIOS (ACPI HAL), `SystemBiosDate = 07/23/16`; legacy boot (no UEFI on this platform) |
| USB devices seen | Logitech and Rapoo keyboards / mice, 52CB:52CB (unknown; probably the NeoDen camera or panel) |

## Is the BIOS custom?

Nothing in the image points to a NeoDen-specific BIOS:
- The board reports generic IDs. The chipset bridges have subsystem 0000:0000, and the Realtek
  NIC and audio carry Realtek's reference subsystem IDs. That is typical of a generic Chinese
  industrial mini-ITX D525 board.
- The ACPI devices are all standard: PNP0501 serial ports, PNP0A08 PCIe, PNP0C0C power button,
  and so on. There is no vendor-specific ACPI device.
- The serial ports sit at the standard legacy addresses.

What the image cannot show:
- the BIOS vendor and version string (only the date is stored);
- a changed boot logo;
- settings such as "power on after AC loss", the boot order or the SATA mode.
Those are seen only in the BIOS setup on the machine.

## What it means for the Ubuntu image (build/image)

- Boot: MBR + GRUB i386-pc (legacy BIOS), as the image does. Put the new SSD first in the
  BIOS boot order.
- The SATA controller in IDE mode works (ata_piix). AHCI works too, if the BIOS is switched.
- The mainboard is on COM2 = `/dev/ttyS1` (0x2F8, IRQ 3). This is the mapping `neomach` and
  `boot.d/10-neomach.sh` use. Both ports must stay enabled in the BIOS (Super I/O setup).
- Network: wired Ethernet (DHCP on `en*`). The Wi-Fi part of the image needs a USB Wi-Fi
  adapter that Linux supports; the PC has no Wi-Fi of its own.
- Check in the BIOS on the machine:
  - COM1 / COM2 enabled at 3F8/IRQ4 and 2F8/IRQ3;
  - the boot device;
  - "Restore on AC power loss" (on, if the PC should start with the machine).
