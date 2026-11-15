# l5-platform-w9-015: import_argext.tsv

- `import_escape.py --argext` пишет `manifest/import_argext.tsv` (dll, name, slot, kind, extent) из того же winmd 8ca69fda (sha a74c9df5) и по тому же правилу слотов (`walk()` общий с escape-буквами; escape и writes воспроизводятся побайтно).
- fnv-nx-build `l5-platform/manifest` 66864da, tree_sha256 81ae8d11d50428d152961879b4dc089bee814b6445fa23ba36616406e676d37b. Корень не перепинован (это w10).
- Итого по видам (3980 строк, 1553 импорта): iface 1312, n 1175, struct 471, ref 383, opaque 241, buf 100, str8 81, str16 38, str8nn 1, str16nn 1, ? 177 (= 94 импорта без прототипа, как в escape).
- Размер struct считается `Meta.layout()` по таблице типов (x86 32-бит, Pack, FieldLayout, ClassSize); старый `size_of` давал 328 вместо 320 для WIN32_FIND_DATAA и остался только для слотов агрегатов.
- Тест: `manifest/tests/test_import_argext.py` (синтетические таблицы метаданных, все виды, Pack=1 против умолчания).
