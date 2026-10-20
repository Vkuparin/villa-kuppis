# Home Assistant OS virtual machine (both sites)

Both servers run Debian 13 as the host and Home Assistant OS (HAOS) as a
KVM/libvirt virtual machine. The host keeps SSH, Tailscale, Docker and the
5 TB USB drive at `/srv/archive`. The VM gets its own LAN address and HAOS
keeps its apps: the Matter Server at the cottage, MELCloud Home at home.
Villa Kuppis runs only at home, on the host, next to the home VM.

| | Home | Cottage |
| --- | --- | --- |
| Host | `home-server`, HP OmniBook | `cottage-server`, HP MP9 G4 |
| VM memory / vCPU (start) | 4 GB / 2 | 3 GB / 2 |
| Home Assistant does | Pumps (MELCloud Home, cloud) | Mill heaters and ALPSTUGA (Matter), room controller |
| Backup drive label | `HOME_BACKUP` (WD) | `COTTAGE_BACKUP` (Seagate) |

This page covers only what Villa Kuppis depends on. Partitioning the drives,
photo copies and family shares are in the owner's own server guide.

## Network

- Give the VM a bridge `br0` on **wired Ethernet**: Matter needs local IPv6
  and mDNS between Home Assistant and the devices, which libvirt's default
  NAT network does not pass. A Wi-Fi interface usually cannot be a bridge port.
  Make bridge changes with a keyboard and screen attached; SSH can drop.
- Reserve two addresses in each router: the host and the VM
  (`HOME_HA_IP`, `COTTAGE_HA_IP`). Villa Kuppis uses the VM's address, not a
  `.local` name.
- Home exception: if the OmniBook has no Ethernet port, use a USB Ethernet
  adapter. Until home has Matter devices, libvirt's default NAT network also
  works there: MELCloud Home only needs the internet, and the host reaches the
  VM at its `192.168.122.x` address.

## Create the VM

Use the official KVM `.qcow2` image from
<https://www.home-assistant.io/installation/alternative> (`unxz` it if it ends
in `.xz`), on the internal SSD:

```
sudo qemu-img resize /var/lib/libvirt/images/haos.qcow2 64G
sudo virt-install --name haos --description 'Home Assistant OS' \
  --memory 3072 --vcpus 2 --osinfo generic --import \
  --disk path=/var/lib/libvirt/images/haos.qcow2,format=qcow2,bus=scsi \
  --controller type=scsi,model=virtio-scsi \
  --network bridge=br0,model=virtio --boot uefi \
  --graphics vnc,listen=127.0.0.1 --noautoconsole
sudo virsh autostart haos
```

Use `--memory 4096` at home. If it does not boot, follow the official
virt-manager import steps with non-Secure-Boot OVMF firmware.

Then open `http://<HA_IP>:8123`, finish onboarding, set the time zone to
Europe/Helsinki, and create the non-admin user `villa-kuppis` with a long-lived
access token (the home server stores it, see
[deploy/home/README.md](home/README.md)). Do not forward port 8123 on any
router.

## Backups to the host drive

HAOS backs up to a Samba share on its own host, so a VM or SSD failure does not
take the backups with it. On the host, with the drive mounted:

```
findmnt /srv/archive    # must show the USB drive before you go on
sudo adduser --system --no-create-home --group haos-backup
sudo install -d -o haos-backup -g haos-backup -m 700 /srv/archive/ha-backups
sudo smbpasswd -a haos-backup
```

Add to `/etc/samba/smb.conf` and `sudo systemctl restart smbd`:

```
[ha-backups]
    path = /srv/archive/ha-backups
    valid users = haos-backup
    hosts allow = <HA_IP>
    read only = no
```

In Home Assistant: Settings > System > Storage > Add network storage (usage
Backup, Samba/CIFS, server = the host's LAN address, share `ha-backups`), then
Settings > System > Backups: daily automatic backups to that location, with the
encryption key saved in the password manager. If the drive is unplugged,
`/srv/archive/ha-backups` does not exist, so the share fails and the backup
reports an error instead of filling the SSD.

Before a Home Assistant update, take a manual backup. To copy the whole VM
offline: `sudo virsh shutdown haos` and copy the `.qcow2`.

## After a power cut

Check that the host boots on its own (BIOS: power on after AC loss), the drive
is at `/srv/archive` (`findmnt /srv/archive`), `sudo virsh list --all` shows
`haos` running, and Home Assistant answers at `http://<HA_IP>:8123`. At the
cottage do this before leaving it unattended (H2.1).
