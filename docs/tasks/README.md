# Task lists

[Roadmap](../roadmap.md) holds goals and decisions. Version files hold the cards; Later holds work without a release. Read the card you are working on and the cards it depends on, not the whole backlog.

## Cards

Each card is one table row: ID, task and Done when, Agent, Depends on, Status.

- **ID** is stable, such as F1.2, and survives a move between files.
- **Agent** is the tier from the [routing table](../agents.md): Tier 1, Tier 2, Tier 3 or Owner.
- **Depends on** lists card IDs. A card is ready when those are Done; `node scripts/coordinate.mjs status` lists ready cards.
- **Status** starts with Planned, In progress, Needs decision, Done or Dropped.

Write `In progress: <agent>, <branch>, <scope>` when you start, so another harness can resume if your session ends. Write `Done: <commit>, <checks run>` when you finish, and say if it is not pushed. Add a note when something is unresolved or waiting for the owner.

Only the coordinator edits cards. A small fix can reuse an existing card. The dashboard reads these files as they are; nothing else needs maintaining.

## Index

| File | Scope |
| --- | --- |
| [0.1.0](0.1.0.md) | Prove the integrations |
| [0.2.0](0.2.0.md) | PC service, Finnish UI and cottage price automation |
| [0.3.0](0.3.0.md) | Pump cooling automation |
| [Later](later.md) | Unassigned work |
