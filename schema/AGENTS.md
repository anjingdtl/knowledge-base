# schema/

Canonical Wiki v2 的权威数据契约(JSON Schema)。供人类审阅与未来严格校验接入。

- `wiki-page-v2.schema.json` — 页面 frontmatter 契约(spec §5.1)
- `wiki-claim-v1.schema.json` — Claim YAML 契约(spec §5.2)

运行时校验由 `src/services/wiki_validator.py` 的 `WikiValidator` 完成(模型层
`from_dict(strict=True)` + 跨对象 invariant),不强制依赖 `jsonschema` 库。
如安装了 `jsonschema`,可选用其做额外严格校验。
