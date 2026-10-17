# Client and cottage access decision

Status: proposed, 17 October 2026. No provider account, subscription or live device has been configured. Google dependency and Android household devices are confirmed. Desktop control and a PC service remain requirements, not optional features.

Confirmed host: Windows 11, normally powered on. The owner reports a newly purchased Google speaker with a six-month Premium trial, but explicitly wants the long-term architecture to avoid requiring Premium. The trial's tier and activation are not verified; do not assume it grants Advanced access. Losing the trial must not disable an essential production feature.

## Options to test

| Route | Android | Desktop / unattended PC | New dependency | Current conclusion |
| --- | --- | --- | --- | --- |
| Standard Android Home APIs | Documented SDK route | No general PC SDK established | Google account, OAuth and online hub for remote use | First mobile experiment; no published usage fee found |
| Google Home MCP adapter on PC | App can call our PC API | Documented HTTP transport is promising | Early-access approval and Premium Advanced | Optional trial experiment only; excluded as a required production dependency |
| Android companion relaying to PC | Technically plausible | Depends on an available Android device | Always-on Android device and background-service design | Research fallback only; a normal household phone is not a reliable server |
| Direct routed VPN to cottage | Through our PC API | Potentially supports local Mill REST | Router capabilities, reachable tunnel endpoint | Alternative; Matter access remains separate |

An Android-only first release would be a staged compromise, not fulfillment of the requested desktop functionality. A Google Home web page is not evidence that its private browser calls form a supported API for our app. Do not scrape the Google Home UI or use an emulator as the assumed production backend.

## Newly identified PC candidate

[Google Home MCP](https://developers.home.google.com/mcp/home) is in early access and requires Google Home Premium Advanced plus access setup. It is distinct from both standard Android Home APIs and the documentation-focused Home Developer MCP. Premium is an explicit cost dependency; accepting Google's cloud does not authorize a subscription purchase. Finnish pricing and the owner's existing entitlement are unverified.

[The action reference](https://developers.home.google.com/reference/home/mcp/tools_list/run_home_actions) includes direct HTTP examples and structured commands. This makes a deterministic server adapter a plausible engineering route without an LLM in the command path. That is an inference about implementation, not proof that our account has access or that every thermostat command is supported. Confirm provider requirements for the intended custom application.

The proposed flow is desktop browser or Android app → Villa Kuppis PC API → Google Home MCP → cottage hub/devices. Device identities and parameter schemas must come from discovery, never from invented names. Inspect individual action results and confirm state separately. Never forward arbitrary browser-supplied MCP tool names or raw commands.

[The state reference](https://developers.home.google.com/reference/home/mcp/tools_list/list_home_states) exposes resource and trait timestamps, connectivity and freshness controls. Keep these distinctions in our state model; a new HTTP response may still contain cached readings. Partial connectivity must not be shown as every capability being healthy.

## Tentative client choice

Build the first Google experiment as a small Kotlin Android app using the standard Home APIs scope. Reuse its observed device capabilities when designing the final Finnish UI. Keep the PC's local Mitsubishi integration independent of Google. Delay choosing pure PWA versus native Android plus browser until PC cottage access is proved or the owner explicitly accepts a staged limitation.

Prioritize desktop cottage access that works without Premium: investigate direct networking or an Android relay with explicit availability requirements. Any relay is additional compute at the main residence, even if it is an existing Android device; it is not yet selected. A successful MCP trial does not resolve this production requirement. Do not silently remove desktop cottage control from the scope.

Production acceptance must include operation without a Premium entitlement. If no suitable PC transport can be proved, document the limitation and obtain a specific scope decision rather than relying on the trial until it expires.

No subscription, paid AI runtime, server hosting or additional hardware is authorized by this tentative decision.
