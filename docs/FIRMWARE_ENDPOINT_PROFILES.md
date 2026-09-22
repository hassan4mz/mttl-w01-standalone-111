# Current firmware profiles — 2026-09-12

| Profile | User label | Role | Device OTA | Main changes |
|---|---|---|---|---|
| `official-111` | UP + Child Lock · 1.0.111 | current production | TCP49217 primary, TCP80 connect fallback | 110 + physical Child Lock persistence, SoftAP 0.1.55 |
| `official-110` | UP · 1.0.110 | hardware-verified rollback | TCP80 | 66 + UP TCP10086, physical event push, connected orange LED OFF |
| `official-66` | LG U+ Stock · 1.0.66 | stock-compatible rollback | TCP80 | LG U+ baseline |

Selecting a profile changes only the target selection. It never automatically queues OTA. 1.0.112/113/114 are historical/withdrawn and must not be exposed as normal profiles.
