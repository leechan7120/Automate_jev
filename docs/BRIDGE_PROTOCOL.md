# Windows Host JSON-lines protocol

The Python orchestrator launches the trusted Windows Host directly without a shell. One UTF-8 JSON object is written per line and one response with the same numeric `id` is required.

## Observe

```json
{"id":1,"action":"observe","payload":{}}
```

The host returns a schema version, host-generated state revision, foreground surface, and compact facts.

```json
{"id":1,"ok":true,"result":{"schema_version":"1.0","state_revision":"sha256:...","foreground_surface":"desktop","facts":{"required_text_present":false}}}
```

## Register actions

The parent process registers only IDs and hashes for the current session before execution. Registration is limited to twelve actions and replaces the previous registry. Within one session, the registry version must strictly increase; a repeated or lower version is rejected. Idempotency history is retained across those registry upgrades and is reset only when a new session is registered.

```json
{"id":2,"action":"register_actions","payload":{"session_id":"demo-session","registry_version":1,"actions":[{"id":"notepad.fill-required-text","action_hash":"sha256:..."}]}}
```

```json
{"id":2,"ok":true,"result":{"registered":1}}
```

## Execute registered action

```json
{"id":3,"action":"execute_registered","payload":{"session_id":"demo-session","registry_version":1,"action_id":"notepad.fill-required-text","action_hash":"sha256:...","expires_at":"...","idempotency_key":"run-1:step-1","expected_state_revision":"sha256:...","target":{},"arguments":{},"preconditions":[],"expected_effects":[]}}
```

Before any input, the host must atomically verify the session registry, action hash, expiry, idempotency key, target and current state revision. A successful response is:

```json
{"id":3,"ok":true,"result":{"execution_state":"confirmed"}}
```

Failures use a bounded code and no raw secret or UI text:

```json
{"id":3,"ok":false,"error":{"code":"STALE_ACTION","message":"state revision changed"}}
```

Requests and responses are limited to 64 KiB. A timeout, malformed line, mismatched response ID, or process exit invalidates the bridge process. The orchestrator never retries a mutating request automatically.
