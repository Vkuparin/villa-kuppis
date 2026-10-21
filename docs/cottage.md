# Cottage

## Inventory

| Item | Count | Role |
| --- | --- | --- |
| TP-Link NE200-Outdoor | 1 | 5G connection |
| TP-Link Archer BE230 | 1 | Wi-Fi router; the HP MP9 is wired to it |
| HP MP9 G4 mini-PC (Core i3-8100T, 8 GB, 256 GB NVMe) | 1 | `cottage-server`: Debian 13 with Tailscale, and Home Assistant OS as a virtual machine with the Matter Server app; bought 20 October 2026, not yet arrived (see [architecture](architecture.md#deployment)) |
| Seagate 5 TB portable USB drive (STGX5000400) | 1 | `/srv/archive` on the host: Home Assistant backups through a Samba share, plus the owner's offsite photo copy; arrives 27 October 2026 |
| Mill Invisible Gen 4 Wi-Fi heaters | 4 | Matter over Wi-Fi ([CSA-certified](https://csa-iot.org/csa_product/mill-wi-fi-panel-heater-gen4-40/)), controlled by the cottage Home Assistant |
| IKEA ALPSTUGA | 2 | Matter-over-Thread air-quality sensors with temperature |
| Google Home Speaker (2026) | 1 | Thread border router, voice and secondary controller ([specs](https://store.google.com/product/google_home_speaker_specs?hl=en-US)) |

The heaters and sensors arrive early November 2026.

## Rooms

| Room | Heaters | Sensor |
| --- | --- | --- |
| Large room | 2 × 1050 W, 1 × 500 W | ALPSTUGA |
| Small room | 1 × 1050 W | ALPSTUGA |

## Setup when the devices arrive (cards H2.1 and F1.6)

Everything can be set up at home first by bringing the cottage network home: the BE230 with the cottage's Wi-Fi name and password, its WAN port plugged into the home router, and the HP MP9 wired to the BE230. At the cottage only the BE230's uplink changes (to the NE200-Outdoor), so devices and addresses stay as they are.

- Give the BE230 a LAN range that differs from the home router's (for example 192.168.50.0/24) and reserve addresses for `cottage-server` and the Home Assistant VM there; the reservations travel with the router and keep the Tailscale subnet route valid.
- Keep `br0` on DHCP in Debian, so the host still gets an address on an unexpected network.
- Bridge setup needs a screen and keyboard on the MP9. Before leaving it at the cottage, pull the power once and check that Debian, the VM and Tailscale come back; after that, SSH over Tailscale is enough.

1. Install Debian 13 on the HP MP9, wire it to the BE230 and set the BIOS to power on after AC loss with virtualization on. Create the Home Assistant OS virtual machine on a wired bridge, add the Matter Server app in it, and route the VM's address to the tailnet from the host ([deploy/haos-vm.md](../deploy/haos-vm.md), [deploy/home/README.md](../deploy/home/README.md#cottage-connection)).
2. Set up the Google Home Speaker. Commission **one** heater and **one** ALPSTUGA into Google Home over Matter (Matter mode on the heater, not Mill Cloud), then share both to Home Assistant from Google Home (linked Matter apps and services). The Home Assistant Companion app on a phone or tablet can do the commissioning; the Villa Kuppis web app cannot.
3. Record what Home Assistant exposes: the heater's setpoint range (does it accept 5 °C?), modes and read-back, and the ALPSTUGA's temperature, humidity, CO2 and PM2.5.
4. Unplug the internet uplink and check that local control and sensor readings continue.
5. Add the other three heaters and the second sensor, and assign rooms (F1.6).

## Heating

- **Maintenance heating:** fixed price thresholds first, later the optimizer, base around 5 °C. Villa Kuppis computes each room's target; see [spot-price automation](spot-price.md).
- **Visit:** in Villa Kuppis, set the arrival time and target (e.g. Friday 18:00, 22 °C). It plans the cheapest heat-up that reaches the target in time. The end-visit button returns to maintenance heating.
- **Control:** the cottage Home Assistant follows each room's ALPSTUGA and sets the room's heaters locally with hysteresis; see [architecture](architecture.md#cottage-control).
- **Fallback:** if the home server, Tailscale or the 5G link is down, the cottage Home Assistant keeps controlling locally, and a watchdog returns to the safe maintenance setpoint when targets or sensor readings go stale. The heaters have built-in frost protection. There are no water pipes.

## Not pursued

- **Direct Matter from the home PC across the internet:** would need routed IPv6 and multicast into the cottage network, which 5G carrier NAT and the BE230 do not provide. The cottage Home Assistant controls the devices locally instead.
- **Google Home automations uploaded from an Android app:** see the [superseded route](platform-decision.md#superseded-android-and-google-home-route).
