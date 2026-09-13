# Diana M3 — bounded runtime/browser verification

Normative spec: [`docs/architecture/HERMES-RUNTIME-M3.md`](../../docs/architecture/HERMES-RUNTIME-M3.md).

Adds runtime reproduction to an existing **static** advisory finding, without
granting Hermes any new authority and without touching the M1/M2 boundary.

## Ownership

```text
Diana owns                          @playwright/mcp owns
----------                          --------------------
which finding is verified           browser control
how the payload is delivered         navigation, DOM access
the network boundary (proxy)         screenshots, snapshots
the outcome (REPRODUCED / …)
the record and its binding
```

The driver runs the browser and reports **observations**. It never names an
outcome, a depth, a severity or a rule_id — Diana derives those. A driver that
tries yields `INCONCLUSIVE`, not a value anyone honours. The component that
handles untrusted page content is deliberately not the component that decides
what that content proved (M3-D7).

## Static vs runtime

`runtime-verification.json` **references** a finding by `rule_id`/`file`/`line`.
It never contains an editable copy, and `ADVISORY_SECURITY_REVIEW` is byte-
identical whether or not verification ran. A failed reproduction is
**`NOT_REPRODUCED` under this bounded runtime**, never "finding disproven".

This is not hypothetical. M1's own `fx01` finding (`el.innerHTML =
location.hash`) is real and does **not** reproduce: Chromium percent-encodes
`<`, `>`, `"` and space in the fragment, so the sink receives inert text. The
record shows the encoded value, so the reason is legible rather than mysterious.

## Three outcomes

| Outcome | Meaning |
|---|---|
| `REPRODUCED` | an injected node was observed in the live DOM **and** the payload's own handler executed |
| `NOT_REPRODUCED` | the verification ran and the payload did not fire |
| `INCONCLUSIVE` | it could not be tested — launch failure, timeout, navigation error, unsupported source channel, unparseable or hostile driver output |

`INCONCLUSIVE` is first class on purpose. "We could not test it" and "we tested
it and it did not fire" are different facts, and folding the first into the
second manufactures reassurance.

## The network boundary

`@playwright/mcp` 0.0.80 documents, for `--allowed-origins` and
`--blocked-origins`: *"does not serve as a security boundary and does not affect
redirects."* That is the vendor disclaiming the property. It was also falsified
behaviorally — a 302 from the in-scope origin to an off-origin host **is
followed**, and only failed in the probe because the hostname does not resolve.

So Diana runs its own proxy and that is the boundary:

- `--proxy-server` points Chromium at a Diana process;
- `--proxy-bypass "<-loopback>"` cancels Chromium's implicit localhost bypass,
  so *loopback traffic is proxied too* and confinement is to the **target
  origin**, not to "localhost";
- the proxy allowlists exactly one `host:port` and refuses everything else,
  including every `CONNECT`;
- any request it cannot parse is refused, never passed through;
- it keeps an authoritative ledger of every request and decision.

`--allowed-origins` is still passed as defence in depth — it gives a clean
client-side refusal for direct navigation that a proxy cannot — but no
acceptance criterion rests on it.

A single bounded run reliably shows Chromium attempting egress to
`safebrowsingohttpgateway.googleapis.com`, `clients2.google.com`,
`accounts.google.com` and `www.google.com`. Without the proxy those leave the
machine. That traffic is the clearest argument that origin flags are not an
egress boundary.

## Reading the boundary claim correctly

The claim is **"no bytes crossed"**, not "the navigation errored". A refused
redirect still lands the document on the off-origin URL — the browser just
receives Diana's `403` instead of the site. Asserting on the tool's error status
would be asserting on the wrong thing, so acceptance asserts on the proxy ledger
plus the observed page content.

## Declared limitations

Pinned by assertions in `test-m3-runtime-verify.sh` so they cannot silently
close or silently widen without a test changing.

- **WebRTC is not covered.** The proxy is an HTTP proxy. `RTCPeerConnection` is
  reachable from a verified page — the acceptance suite observes it and asserts
  it, rather than leaving the gap undocumented — and its UDP traffic does not
  traverse the proxy. `@playwright/mcp` 0.0.80 exposes no flag to disable it and
  no way to pass arbitrary Chromium arguments, so M3 cannot close this; it
  declares it. The proxy bounds **HTTP/HTTPS egress the browser initiates**, and
  that is the whole of the claim. It is not a sandbox.
- **This assertion is configuration-shaped**, not behavioral: it observes that
  the API exists, not that an exfiltration succeeded. It is labelled as such
  rather than presented as proof of exploitation.
- **One configuration only.** A `REPRODUCED` result is evidence about one
  browser engine, one payload and one delivery channel. A `NOT_REPRODUCED`
  result is evidence about that configuration and never about the finding.
- **Three source channels.** Any other source kind is `INCONCLUSIVE` with a
  reason, never `NOT_REPRODUCED`.

## The static server

Bound to `127.0.0.1:0`, serves only inside the contract's `read_scope`, resolves
every request path with the canonicalize-then-contain discipline of M1 C2 (never
a string-prefix test), denies `.git`/`.env*` per component, and implements `GET`
and `HEAD` only — the method check runs before any filesystem access, so there
is no write path to reach.

Diana-owned harness pages live under the reserved `/__diana__/` prefix, checked
before the target tree so a target file can never shadow one.

## Termination

One MCP server owns ~9 Chromium processes. `child.kill()` leaves them running.
The driver spawns detached and signals the **process group**; the record carries
the observed descendant count before and after, so "it was terminated" is an
observation rather than an intention.

## Delivery channels

The payload travels the channel the **finding** names (M3-D12):

| source kind | delivery |
|---|---|
| `window.name` | `--init-script`, evaluated before the page's own scripts (opener-controlled, no URL encoding) |
| `location.hash` | in the request URL after `#` |
| `location.search` | in the request URL after `?` |

Any other source kind is `INCONCLUSIVE` with a reason — never `NOT_REPRODUCED`.

Diana supplies only the *source value*. The injected node appears because the
**target's own code** moves it into an HTML sink; `rv02` is the control that
proves it — identical payload, identical delivery, `textContent` sink, nothing
injected.

## Files

| File | Role |
|---|---|
| `runtime_verify.py` | Diana-side: outcome derivation, closed schemas, record binding |
| `driver.mjs` | static server + egress proxy + bounded browser; observations only |
| `fixtures/rv01` | `el.innerHTML = window.name` — static finding **and** reproduces |
| `fixtures/rv02` | `el.textContent = window.name` — control, no finding, no reproduction |
| `test-m3-runtime-verify.sh` | the 16 M3 acceptance criteria |

## Running

```sh
./diana/runtime_verify/test-m3-runtime-verify.sh
```

Requires Node ≥ 20 and `@playwright/mcp`. Absent either it **skips with a
message** rather than substituting a simulated browser.
