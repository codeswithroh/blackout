# Blackout spike results

Tested on 2026-11-04 against Switch server 0.31.0 with bundled Mattermost, in Docker on macOS. Blackout is feasible as designed.

## Proven by running it

| Capability Blackout needs | Result |
|---|---|
| An agent creates private rooms in Mattermost and adds a human | Works |
| One connection watches every room, including messages that mention nobody | Works with `scope=all&filter=all` |
| Simulated agents can't read or post in rooms they aren't in | Works: 403 from Switch |
| A human mention reaches the right agent marked `addressed` | Works |
| An agent mention reaches another agent marked `addressed` | Works |
| Agents post under their own names without claiming a room | Works through `POST /agents/{id}/message` |
| The game master reads the timeline of each room | Works through `read_context` |
| Invite an agent mid-drill, then remove it and cut its access | Works |
| A real Claude Code turn behind a protocol agent, replying in Mattermost | Works with `claude -p`, 18s end to end |

## Rules to build by

1. **Subscribe after every membership change.** A live connection doesn't pick up a room joined after it opened, even with `scope=all`. Call `POST /agents/{id}/connection/subscribe` for that agent straight away. Note that this moved the connection to the named room only in the test, so build on reconnecting the watcher, which also worked, or test subscribing to several rooms first.
2. **Set addressing from the owner side.** The docs say an agent can call `update_agent_detail`. On 0.31.0 that returns 403. Use the Gateway instead: log in, then `PUT /gateway/agents/{id}/addressing-policy` with `{"policy": null}` for open.
3. **Register with a Gateway API key, not the bootstrap token.** Bootstrap agents belong to a separate account, so the admin isn't their owner.
4. **Don't trust late joiners with secrets.** An agent invited mid-drill can read the room's earlier history. Simulated agents only know what the game master feeds them, so this only matters for real agents.
5. **Keep Claude Code turns lean.** A one-word reply cost a reported $0.34 because `claude -p` loaded about 42k tokens of local config. Run it with a minimal config in the shop repo, and use scripted mode for rehearsals.
6. **`create_room` returns `id`**, not `room_id`.

## Not tested yet

- Switch Console running Claude Code as a first-class agent. The adapter above is the working fallback.
- Roles (`define_role`, `assume_role`) and templates (`run_template`).
- Several humans in one drill. Only the seeded `user` account was used.
- How messages look in the Mattermost UI. Checks went through its API.
