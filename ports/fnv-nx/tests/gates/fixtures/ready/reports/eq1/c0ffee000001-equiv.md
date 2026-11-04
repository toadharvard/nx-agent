# equivalence c0ffee000001 shard 1/1

- units: 4 compared; 3 identical, 1 differ, 1 prepared side does not compile, 0 pristine does not compile
- functions: 50 compared, 2 differ (EH-only 0, retarget 0, code 2)

## Differing functions

```
unit_a.cpp  0x00401000  code  CTOR1
unit_b.cpp  0x00402000  code  STATIC1
```

## Units the prepared side does not compile

```
unit_c.cpp  ENUM1
```
