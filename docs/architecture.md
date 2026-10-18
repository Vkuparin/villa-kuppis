# Tentative architecture

## Decided

- One Python service on the owner's home Windows 11 PC, which is almost always on. No Home Assistant box, VM or cottage computer; those are a last resort.
- One Finnish web app served by the PC, for the desktop browser. One native Kotlin Android app that shows the same web app inside it and adds the cottage functions that need Google Home.
- The goal is one app instead of many. Take the simplest working route to each device, even through the manufacturer's cloud.
- Home: three Mitsubishi indoor units, all in MELCloud Home, controlled from the PC through the MELCloud Home cloud. Their main job is summer cooling; the apartment's radiators are geothermal and outside the owner's control.
- Cottage (devices arriving early November 2026): four Mill heaters and two IKEA ALPSTUGA sensors in Google Home over Matter, with the Google Home Speaker as hub. The heaters follow the ALPSTUGA readings rather than their own sensors.
- Spot-price automation is a high priority: fixed thresholds first, then an optimizer. See [spot-price automation](spot-price.md).
- Household members use the app with full control. No app login: being on the owner's Tailscale network is the access control.

## Shape

```mermaid
flowchart LR
  Desktop[Desktop browser] --> PC[Home PC: Python service and web app]
  Phone[Villa Kuppis Android app] -->|Tailscale| PC
  PC -->|MELCloud Home cloud| Pumps[3 Mitsubishi indoor units]
  PC --> Prices[ENTSO-E spot prices]
  PC --> Weather[FMI forecast]
  PC --> DB[(SQLite)]
  Phone -->|Home APIs| Google[Google Home]
  Google --> Hub[Cottage speaker]
  Hub --> Mill[4 Mill heaters]
  Hub --> Sensors[2 ALPSTUGA]
```

**The PC** holds configuration, history, rules, prices, the forecast and every plan. It controls the pumps directly.

**The PC cannot reach Google Home.** Google offers its device API only to Android and iOS apps signed in as the user. The only route for PC programs is Home MCP, which needs Premium Advanced and is US-only; the speaker includes Premium Standard. So the PC never reads or writes cottage devices itself.

**The Android app** is the cottage link. It:

- reads cottage readings through the Home APIs and reports them to the PC;
- fetches the cottage plan from the PC and uploads it as Google Home automations, once a day after prices are published, plus a manual re-send button;
- starts and ends visit heating;
- shows notifications.

Google runs the uploaded automations, so the cottage follows its plan with the PC and phone off. Manual control through the Google Home app also works at any time, including when the PC is down.

## Rooms

| Site | Room | Devices |
| --- | --- | --- |
| Cottage | Large room | Mill heaters 2 × 1050 W and 1 × 500 W, one ALPSTUGA |
| Cottage | Small room | Mill heater 1 × 1050 W, one ALPSTUGA |
| Home | Three rooms | One Mitsubishi indoor unit each |

## Stack

- **PC:** Python. MELCloud Home through `aiomelcloudhome`. The service runs natively on Windows and starts at boot without an interactive login.
- **Android:** Kotlin with the Google Home APIs; the web app is shown in a WebView.
- **Shared data:** prices from ENTSO-E (Elering backup) and the outdoor forecast from the Finnish Meteorological Institute's free open data.

The MELCloud Home client is unofficial; if it breaks, REA on the current app is the repair tool.

## Model

Stable internal IDs for Site, Room, Device and Capability. Finnish display labels; language-neutral protocol identifiers. Store only capabilities a device actually reports.

Each reading has a value, a timestamp and a fresh/stale flag. Each command records who or which plan asked, the target, the requested value and its result. Show the device's reported state and a pending request separately until the device confirms it.

## Heating and cooling

Devices remain their own thermostats; plans only change setpoints and modes. If the PC, phone or internet is down, the last uploaded automations and the devices' own setpoints keep working. The Mill heaters also have built-in frost protection.

Plans use Europe/Helsinki local time; store timestamps in UTC.

## Remote access and security

[Tailscale](https://tailscale.com/pricing) connects phones to the PC. Each household member installs it and joins the owner's network. The free Personal plan should cover this: recent reports say it has allowed six users and unlimited personal devices since April 2026; confirm on the pricing page. Nothing is exposed to the public internet.

Keep MELCloud Home and Google credentials outside Git and back them up with the database.

No AI model is in the command path. Plans are deterministic code.
