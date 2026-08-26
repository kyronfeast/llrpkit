# Reader policies: ignore tags by antenna and item category

A policy answers a warehouse question llrpkit hears constantly: *line 4
should only see pails — it should ignore pickles-fresh, ingredients, and
everything else.* That is an **allow-list per antenna, keyed by item
category**, and llrpkit enforces it **host-side** — ignored tags are dropped
in llrpkit, next to the readers, before they ever reach MQTT, a webhook, a
capture file, or the dashboard. Downstream systems (an ERP on modest
hardware) only receive the tags they should act on.

## The two pieces

A policy is a catalog plus per-antenna rules.

The **catalog** says what category a tag belongs to. Each rule matches by
one of, most specific first: exact `epc`, GS1 `gtin`, GS1 `company_prefix`
(both read from the [EPC decode](api.md)), or an `epc_prefix` for raw
non-GS1 tags. So a catalog can be your product export — a GTIN per SKU —
rather than hand-written hex. Anything unmatched is category `unknown`.

Each **antenna** gets a rule: `allow` keeps only the listed categories;
`deny` keeps everything except them. Antennas with no rule pass everything —
a reader is never silenced by omission. Optional floors — a per-antenna or
global `min_rssi_dbm`, and a global `ignore_unknown` — round it out.

```json
{
  "ignore_unknown": false,
  "min_rssi_dbm": null,
  "catalog": [
    { "match": "epc_prefix", "value": "e200aa", "category": "pails" },
    { "match": "epc_prefix", "value": "e200bb", "category": "pickles-fresh" },
    { "match": "gtin", "value": "80614141123458", "category": "ingredients" }
  ],
  "antennas": {
    "4": { "mode": "allow", "categories": ["pails"] },
    "1": { "mode": "deny",  "categories": ["pickles-fresh"] }
  }
}
```

## Using it

From the CLI, apply a policy file to any inventory — it flows to whichever
sink you use, so ignored tags never leave the host:

```console
$ llrpkit inventory 192.168.1.10 --policy line4.json --mqtt-broker 10.0.0.5
...
policy ignored 214 tag(s): pickles-fresh 190, ingredients 24
```

From Python:

```python
from llrpkit import Reader, ReaderPolicy

policy = ReaderPolicy.load("line4.json")
async with Reader("192.168.1.10") as reader:
    async for tag in reader.inventory(session=1, policy=policy):
        handle(tag)            # only pails on antenna 4; tag.category is set
print(policy.counters())       # {"kept": ..., "dropped": ..., "by_category": {...}}
```

From the **dashboard Control tab**: build per-antenna rules with the
add/update form, paste your catalog, press Apply — and watch the live
"kept vs ignored" table fill in as the policy filters the running stream.
Nothing is dropped silently: every ignore is counted by antenna, category,
and reason.

## Where to enforce

Policies are enforced in software, on the reader host. That is deliberate:
category rules ("allow these three categories on this antenna") can't be
expressed as reader RF filters, and putting the work next to the readers —
not on your ERP — is what keeps a modest server light. For the narrow case
of a single EPC-prefix cut, the reader-side `--filter-epc` select filter is
still available and drops those tags in RF; a policy is the tool when the
rule is about *what an item is*, per antenna.
