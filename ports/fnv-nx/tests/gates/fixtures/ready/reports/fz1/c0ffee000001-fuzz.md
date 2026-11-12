# fuzz c0ffee000001 shard 1/2

- functions catalogued 20, in this shard 10, no native object 0, control flow not modelled (indirect) 0
- fuzzable 10, pending 0
- cumulative (verdicts valid for this head): 10 functions
- fuzzed this head: crash 0

## Hard divergences, cumulative

| function | cause | kind | name |
|---|---|---|---|
| 00401000 | x87-precision | f32 | sub_401000 |

## Causes by verdict class, cumulative (fnvnx-causes/1)

| cause | class | owner | divergences | functions | worst of |
|---|---|---|---:|---:|---:|
| x87-precision | by-design | - | 1 | 1 | 1 |

```json fnvnx-causes
{"causes": {"x87-precision": {"divergences": 1, "functions": 1, "hard": false, "kinds": {"return": 1}, "owner": "-", "verdict": "by-design", "worst_of": 1}}, "format": "fnvnx-causes/1", "functions_by_class": {"artifact": 0, "by-design": 1, "real-bug": 0, "triage": 0}, "functions_diverging": 1, "functions_ok": 10}
```
