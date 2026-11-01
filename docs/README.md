# Villa Kuppis design documents

Implementation began on 19 October 2026. On 20 October 2026 the design moved to one Home Assistant per site on two always-on mini-PCs. Since 25 October 2026 Villa Kuppis runs as a Home Assistant app in the home VM with the pumps through Home Assistant; the first Windows install is shut down and archived. The cottage's real devices were commissioned on its Home Assistant on 1 November 2026, before the move. What changed in each app release: [ha-app/CHANGELOG.md](../ha-app/CHANGELOG.md).

| Document | Purpose |
| --- | --- |
| [Architecture](architecture.md) | Two-site Home Assistant design, ownership, cottage control, graphs, deployment and security |
| [Platform decision](platform-decision.md) | How each device is reached, the gates before relying on it, and the superseded Android route |
| [Cottage](cottage.md) | Hardware, rooms, setup steps and heating |
| [Mitsubishi](mitsubishi.md) | Three indoor units, the MELCloud Home route and first experiment |
| [Spot-price automation](spot-price.md) | Prices, fixed thresholds, the optimizer, visit heating and summer cooling |
| [Integration experiments](integration-experiments.md) | Steps and pass criteria for the first tests |
| [Feasibility](feasibility.md) | Main unknowns and where REA helps |
| [Finnish UI](ui-fi.md) | Screens, terms and state feedback |
| [Questions](questions.md) | Open owner decisions |
| [Roadmap](roadmap.md) | Releases and decisions |
| [Setup record](setup.md) | Repository origin and development tools to install |

Private addresses, account identifiers, pairing codes and captures belong outside Git.
